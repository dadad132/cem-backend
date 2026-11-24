"""MeshCentral integration helpers: SSO login tokens, embed URLs, and WebSocket inventory sync.

Follows the official MeshCentral integration contract (https://docs.meshcentral.com/meshcentral/):
- Embedded views use `?login=<token>&gotonode=<id>&viewmode=<10..15>&hide=<bitmask>`
  with `"allowFraming": true` in MeshCentral's `config.json`.
- Short-lived SSO tokens replicate MeshCentral's `encodeCookie` AES-256-GCM layout:
  `base64(iv[12] + tag[16] + ciphertext)` with `+/` mapped to `@$` and `=` stripped.
- Live device & group discovery connects to `/control.ashx` over WebSocket using
  `x-meshauth: <b64(user)>,<b64(pass)>` or `?auth=<token>` and sends `meshes` + `nodes`.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.meshcentral import MeshCentralDevice, MeshCentralGroup, MeshCentralSettings

logger = logging.getLogger(__name__)

# MeshCentral's local-only listener when no explicit local_port was configured.
DEFAULT_LOCAL_MESHCENTRAL_PORT = 4430
LOOPBACK_HOST = "127.0.0.1"


def _require_aiohttp():
    try:
        import aiohttp
        return aiohttp
    except ImportError as exc:
        raise RuntimeError(
            "MeshCentral live sync requires 'aiohttp'. Run: pip install aiohttp"
        ) from exc


def _require_cryptography():
    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        return Fernet, InvalidToken, default_backend, Cipher, algorithms, modes
    except ImportError as exc:
        raise RuntimeError(
            "MeshCentral encryption/SSO requires 'cryptography'. Run: pip install cryptography"
        ) from exc

# Official MeshCentral viewmode IDs (https://docs.meshcentral.com/meshcentral/)
VIEW_MODE_MAP: Dict[str, int] = {
    "summary": 10,
    "desktop": 11,
    "terminal": 12,
    "files": 13,
    "console": 15,
}
VIEW_MODE_BY_CODE: Dict[int, str] = {code: name for name, code in VIEW_MODE_MAP.items()}

# Bitmask: 1=header, 2=tabs, 4=footer, 8=title, 16=left toolbar, 32=back buttons
HIDE_CHROME_BITMASK = 63

# Supported MeshAgent client installers proxied through CEM
AGENT_PLATFORMS: Dict[str, Dict[str, Any]] = {
    "windows64": {
        "label": "Windows 64-bit (.exe)",
        "short_label": "Windows 64-bit",
        "agent_id": 4,
        "ext": ".exe",
        "content_type": "application/octet-stream",
        "icon": "fab fa-windows",
    },
    "windows32": {
        "label": "Windows 32-bit (.exe)",
        "short_label": "Windows 32-bit",
        "agent_id": 3,
        "ext": ".exe",
        "content_type": "application/octet-stream",
        "icon": "fab fa-windows",
    },
    "macos": {
        "label": "macOS Universal (.pkg)",
        "short_label": "macOS",
        "agent_id": 10005,
        "fallback_agent_id": 29,
        # Some MeshCentral builds serve the legacy macOS agent here instead of
        # /meshagents. Try it after the regular fallback before giving up.
        "fallback_endpoint": "meshosxagent",
        "ext": ".pkg",
        "content_type": "application/octet-stream",
        "icon": "fab fa-apple",
    },
    "linux": {
        "label": "Linux Installer (.sh)",
        "short_label": "Linux",
        "agent_id": None,
        "ext": ".sh",
        "content_type": "text/x-shellscript",
        "icon": "fab fa-linux",
    },
}

_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9_-]{0,64}$")
_NODE_ID_RE = re.compile(r"^[a-zA-Z0-9_@$/+=.-]{3,256}$")
_HEX_KEY_RE = re.compile(r"\b([0-9a-fA-F]{64,256})\b")
_AUTH_QUERY_RE = re.compile(r"([?&](?:auth|login|token)=)[^&\s]+", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"(x-meshauth(?:\s*[:=]\s*|\s+))\S+", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"(password|passwd|pwd)\s*[:=]\s*[^\s,;]+", re.IGNORECASE)

# Installer bodies are streamed to clients with a trusted brand name, so refuse
# absurdly large or clearly non-installer responses from an upstream server.
MAX_AGENT_INSTALLER_BYTES = 200 * 1024 * 1024
# Trusted installer prefixes per platform. macOS is deliberately permissive:
# the modern agent is a `xar!` .pkg, while legacy agent id=29 can be a Mach-O
# binary (including universal/fat) or even a shell installer from
# /meshosxagent. Accepting those keeps the fallback paths usable.
_AGENT_MAGIC = {
    "windows64": (b"MZ",),
    "windows32": (b"MZ",),
    "macos": (
        b"xar!",                 # signed installer package (xar archive)
        b"\xcf\xfa\xed\xfe",     # 64-bit Mach-O
        b"\xce\xfa\xed\xfe",     # 32-bit Mach-O
        b"\xfe\xed\xfa\xcf",     # swapped 64-bit Mach-O
        b"\xfe\xed\xfa\xce",     # swapped 32-bit Mach-O
        b"\xca\xfe\xba\xbe",     # universal / fat Mach-O
        b"#!",                   # legacy shell installer
    ),
}


def _installer_body_is_valid(
    platform: str, payload: bytes, endpoint: str = "meshagents"
) -> bool:
    """Cheap magic-byte gate before streaming an upstream body to a client.

    A shell installer is only expected from the legacy macOS endpoint. If the
    primary `/meshagents?id=10005` answers with a script, keep walking the
    fallback chain instead of silently serving the wrong installer as a .pkg.
    """
    if platform == "macos" and payload.startswith(b"#!") and endpoint != "meshosxagent":
        return False
    return any(payload.startswith(prefix) for prefix in _AGENT_MAGIC.get(platform, ()))


def redact_meshcentral_text(value: Any) -> str:
    """Strip live SSO tokens and credentials from anything shown to users/logs."""
    text = str(value)
    text = _AUTH_QUERY_RE.sub(r"\1<redacted>", text)
    text = _AUTH_HEADER_RE.sub(r"\1<redacted>", text)
    return _PASSWORD_RE.sub(r"\1=<redacted>", text)


# ---------------------------------------------------------------------------
# At-rest secret encryption
# ---------------------------------------------------------------------------

def _fernet():
    Fernet, *_ = _require_cryptography()
    raw_secret = (get_settings().secret_key or "change-me-in-production").encode("utf-8")
    digest = hashlib.sha256(raw_secret + b":meshcentral-secret-v1").digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
    if not plaintext:
        return None
    cleaned = plaintext.strip()
    if not cleaned:
        return None
    return _fernet().encrypt(cleaned.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: Optional[str]) -> Optional[str]:
    if not ciphertext:
        return None
    _, InvalidToken, *_ = _require_cryptography()
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.warning("Unable to decrypt stored MeshCentral secret (SECRET_KEY may have changed)")
        return None


# ---------------------------------------------------------------------------
# Input validation & normalization
# ---------------------------------------------------------------------------

def normalize_server_url(raw: Optional[str]) -> str:
    """Validate and normalize a MeshCentral server base URL (http/https only)."""
    if not raw or not isinstance(raw, str):
        raise ValueError("MeshCentral server URL is required.")
    value = raw.strip()
    if any(ch in value for ch in ("\r", "\n", "\t", " ")):
        raise ValueError("Server URL must not contain spaces or control characters.")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Server URL must start with https:// or http://.")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("Server URL must include a valid hostname.")
    if parsed.username or parsed.password:
        raise ValueError("Do not embed credentials directly in the server URL.")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def normalize_domain(raw: Optional[str]) -> str:
    value = (raw or "").strip().strip("/")
    if not _DOMAIN_RE.match(value):
        raise ValueError("MeshCentral domain may only contain letters, numbers, hyphens, and underscores.")
    return value


def normalize_node_id(raw: Optional[str]) -> str:
    """Normalize a MeshCentral node ID for `gotonode=` while preserving raw base64 IDs.

    MeshCentral node identifiers appear as `node//<id>`, `node/<domain>/<id>`, or `<id>`.
    """
    if not raw or not isinstance(raw, str):
        raise ValueError("Device Node ID is required.")
    value = raw.strip()
    if not _NODE_ID_RE.match(value):
        raise ValueError("Invalid MeshCentral Node ID format.")
    if value.startswith("node/"):
        parts = value.split("/", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
    return value


def normalize_mesh_id(raw: Optional[str]) -> str:
    """Normalize a MeshCentral group ID (`mesh//<id>`, `mesh/<domain>/<id>`, or `<id>`)."""
    if not raw or not isinstance(raw, str):
        raise ValueError("Device Group (Mesh) ID is required.")
    value = raw.strip()
    if not _NODE_ID_RE.match(value):
        raise ValueError("Invalid MeshCentral Group ID format.")
    if value.startswith("mesh/"):
        parts = value.split("/", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
    return value


def generate_random_login_token_key() -> str:
    """Generate a MeshCentral-compatible 80-byte (160 hex char) loginTokenKey."""
    return secrets.token_hex(80)


def build_login_user_id(
    username: Optional[str],
    domain: Optional[str] = "",
    explicit_user_id: Optional[str] = None,
) -> Optional[str]:
    """Return MeshCentral canonical user ID (`user/<domain>/<username>` or `user//<username>`)."""
    if explicit_user_id and explicit_user_id.strip():
        uid = explicit_user_id.strip()
        if uid.startswith("user/"):
            return uid
        dom = (domain or "").strip().strip("/")
        return f"user/{dom}/{uid}"
    if not username or not username.strip():
        return None
    dom = (domain or "").strip().strip("/")
    return f"user/{dom}/{username.strip()}"


def detect_os_family(os_desc: Optional[str], icon_code: Optional[int] = None) -> str:
    text = (os_desc or "").lower()
    if "windows" in text or "win10" in text or "win11" in text or "microsoft" in text:
        return "windows"
    if "mac" in text or "darwin" in text or "osx" in text or "os x" in text:
        return "macos"
    if any(tok in text for tok in ("linux", "ubuntu", "debian", "centos", "alma", "rocky", "fedora", "arch", "redhat", "suse")):
        return "linux"
    if icon_code in (1, 2):
        return "windows"
    if icon_code == 6:
        return "macos"
    if icon_code in (3, 4, 5):
        return "linux"
    return "other"


# ---------------------------------------------------------------------------
# MeshCentral AES-256-GCM login token (encodeCookie compatible)
# ---------------------------------------------------------------------------

def _derive_token_key_bytes(login_token_key: str | bytes) -> bytes:
    """Convert MeshCentral's `--loginTokenKey` (hex string or raw bytes) to 32-byte AES-256 key."""
    if isinstance(login_token_key, bytes):
        raw = login_token_key
    else:
        text = login_token_key.strip()
        if not text:
            raise ValueError("loginTokenKey is empty.")
        try:
            raw = bytes.fromhex(text)
        except ValueError:
            raw = text.encode("utf-8")
    if len(raw) < 32:
        raw = raw.ljust(32, b"\x00")
    elif len(raw) > 32:
        raw = raw[:32]
    return raw


def generate_login_token(
    login_token_key: str | bytes,
    user_id: str,
    now: Optional[int] = None,
) -> str:
    """Mint a short-lived MeshCentral SSO login token compatible with `?login=` and `?auth=`.

    Matches MeshCentral's `encodeCookie` format:
    - JSON payload: `{"u": "<user_id>", "a": 3, "time": <unix_seconds>}`
    - AES-256-GCM with 12-byte random IV
    - Wire bytes: `iv (12B) + tag (16B) + ciphertext`
    - Base64 with MeshCentral URL-safe substitution (`+` -> `@`, `/` -> `$`, `=` stripped)
    """
    if not user_id or not user_id.strip():
        raise ValueError("A MeshCentral user ID (e.g. user//admin) is required to mint a login token.")
    key = _derive_token_key_bytes(login_token_key)
    iv = os.urandom(12)
    payload = {
        "u": user_id.strip(),
        "a": 3,
        "time": int(time.time() if now is None else now),
    }
    _, _, default_backend, Cipher, algorithms, modes = _require_cryptography()
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(iv),
        backend=default_backend(),
    ).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    blob = iv + encryptor.tag + ciphertext
    encoded = base64.b64encode(blob).decode("ascii")
    return encoded.translate(str.maketrans("+/", "@$")).rstrip("=")


def decode_login_token(token: str, login_token_key: str | bytes) -> Dict[str, Any]:
    """Decode and verify a MeshCentral login token (used for diagnostics and tests)."""
    _, _, default_backend, Cipher, algorithms, modes = _require_cryptography()
    key = _derive_token_key_bytes(login_token_key)
    restored = token.translate(str.maketrans("@$", "+/"))
    pad = len(restored) % 4
    if pad:
        restored += "=" * (4 - pad)
    blob = base64.b64decode(restored)
    if len(blob) < 28:
        raise ValueError("Token payload too short.")
    iv = blob[:12]
    tag = blob[12:28]
    ciphertext = blob[28:]
    decryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(iv, tag),
        backend=default_backend(),
    ).decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    return json.loads(plaintext.decode("utf-8"))


# ---------------------------------------------------------------------------
# Embedded & direct launch URLs
# ---------------------------------------------------------------------------

def resolve_view_mode(mode: Optional[str], fallback_code: int = 11) -> Tuple[str, int]:
    if mode and mode.lower() in VIEW_MODE_MAP:
        name = mode.lower()
        return name, VIEW_MODE_MAP[name]
    name = VIEW_MODE_BY_CODE.get(fallback_code, "desktop")
    return name, VIEW_MODE_MAP[name]


def _base_with_domain(server_url: str, domain: Optional[str]) -> str:
    base = normalize_server_url(server_url)
    dom = normalize_domain(domain)
    if dom:
        return f"{base}/{dom}/"
    return f"{base}/"


def build_embed_url(
    settings_row: MeshCentralSettings,
    node_id: str,
    mode: Optional[str] = None,
    *,
    hide_chrome: Optional[bool] = None,
    include_login: bool = True,
) -> Dict[str, Any]:
    """Build an embedded iframe URL (and metadata) for a client PC session."""
    if not settings_row.server_url:
        raise ValueError("MeshCentral server URL is not configured.")
    clean_node = normalize_node_id(node_id)
    mode_name, view_code = resolve_view_mode(mode, settings_row.default_view_mode or 11)

    params: List[Tuple[str, str]] = []
    sso_active = False
    if include_login:
        token_key = decrypt_secret(settings_row.login_token_key_encrypted)
        login_uid = build_login_user_id(
            settings_row.username,
            settings_row.domain,
            settings_row.login_user_id,
        )
        if token_key and login_uid:
            params.append(("login", generate_login_token(token_key, login_uid)))
            sso_active = True

    params.append(("gotonode", clean_node))
    params.append(("viewmode", str(view_code)))

    should_hide = settings_row.hide_mesh_chrome if hide_chrome is None else hide_chrome
    if should_hide:
        params.append(("hide", str(HIDE_CHROME_BITMASK)))

    base = _base_with_domain(settings_row.server_url, settings_row.domain)
    url = f"{base}?{urlencode(params, safe='@$')}"
    return {
        "url": url,
        "mode": mode_name,
        "viewmode": view_code,
        "node_id": clean_node,
        "sso_active": sso_active,
        "hide_chrome": bool(should_hide),
    }


def build_direct_url(
    settings_row: MeshCentralSettings,
    node_id: str,
    mode: Optional[str] = None,
) -> str:
    """Build a full-page MeshCentral URL for opening a session in a new browser tab."""
    return build_embed_url(
        settings_row,
        node_id,
        mode=mode,
        hide_chrome=False,
        include_login=True,
    )["url"]


# ---------------------------------------------------------------------------
# WebSocket live inventory sync (`/control.ashx`)
# ---------------------------------------------------------------------------

def _local_loopback_port(settings_row: MeshCentralSettings) -> Optional[int]:
    """Return the server-side MeshCentral listener port, if loopback is available."""
    raw = getattr(settings_row, "local_port", None)
    port: Optional[int] = None
    if raw is not None:
        try:
            port = int(raw)
        except (TypeError, ValueError):
            port = None
        if port is not None and not (1 <= port <= 65535):
            port = None
    if port is None:
        install_path = getattr(settings_row, "local_install_path", None)
        # Only infer the default port for a real local installation; an arbitrary
        # configured path must not cause credentials to be sent to localhost.
        if install_path:
            data_dir = os.path.join(str(install_path), "meshcentral-data")
            config_path = os.path.join(str(install_path), "config.json")
            if os.path.isdir(data_dir) or os.path.isfile(config_path):
                port = DEFAULT_LOCAL_MESHCENTRAL_PORT
    return port


def _loopback_base(
    settings_row: MeshCentralSettings,
    dom: str = "",
    path_prefix: str = "",
) -> Optional[str]:
    """Build a server-only loopback base URL (never used for browser iframes).

    The public URL's path prefix (e.g. ``/meshcentral``) must be preserved: a
    local listener serves MeshCentral at the same mount point.
    """
    port = _local_loopback_port(settings_row)
    if port is None:
        return None
    parts = [f"http://{LOOPBACK_HOST}:{port}"]
    prefix = (path_prefix or "").strip("/")
    if prefix:
        parts.append(prefix)
    if dom:
        parts.append(dom.strip("/"))
    return "/".join(parts)


def build_control_ws_candidates(settings_row: MeshCentralSettings) -> List[Tuple[str, Dict[str, str]]]:
    """Return ordered `(ws_url, headers)` auth attempts for `/control.ashx`.

    Tries both `x-meshauth` (username + password, including a lowercase
    username) and `?auth=<loginToken>`. When MeshCentral is installed locally,
    a `ws://127.0.0.1:<local_port>/control.ashx` candidate is appended for each
    auth method so a cloud VPS cannot hairpin its own TLS endpoint. The public
    `server_url` remains untouched for browser iframes and download links.
    """
    if not settings_row.server_url:
        raise ValueError("MeshCentral server URL is not configured.")
    base = normalize_server_url(settings_row.server_url)
    parsed = urlparse(base)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    dom = normalize_domain(settings_row.domain)
    path_prefix = parsed.path.rstrip("/")
    ws_path = f"{path_prefix}/{dom}/control.ashx" if dom else f"{path_prefix}/control.ashx"
    ws_url = f"{ws_scheme}://{parsed.netloc}{ws_path}"

    endpoints = [ws_url]
    local_base = _loopback_base(settings_row, dom, path_prefix)
    if local_base:
        local_ws = local_base.replace("http://", "ws://", 1) + "/control.ashx"
        if local_ws not in endpoints:
            endpoints.append(local_ws)

    password = decrypt_secret(settings_row.password_encrypted)
    token_key = decrypt_secret(settings_row.login_token_key_encrypted)
    login_uid = build_login_user_id(
        settings_row.username,
        settings_row.domain,
        settings_row.login_user_id,
    )

    candidates: List[Tuple[str, Dict[str, str]]] = []
    for endpoint in endpoints:
        if settings_row.username and password:
            raw_user = settings_row.username.strip()
            p_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
            for uname in dict.fromkeys([raw_user, raw_user.lower()]):
                u_b64 = base64.b64encode(uname.encode("utf-8")).decode("ascii")
                candidates.append((endpoint, {"x-meshauth": f"{u_b64},{p_b64}"}))

        if token_key and login_uid:
            for uid in dict.fromkeys([login_uid, login_uid.lower()]):
                token = generate_login_token(token_key, uid)
                candidates.append((f"{endpoint}?{urlencode({'auth': token}, safe='@$')}", {}))

    if not candidates:
        raise ValueError(
            "Configure either a MeshCentral username + password or a loginTokenKey + username before syncing."
        )
    return candidates


def build_control_ws_url(settings_row: MeshCentralSettings) -> Tuple[str, Dict[str, str]]:
    """Build the primary `/control.ashx` WebSocket URL and auth headers."""
    return build_control_ws_candidates(settings_row)[0]


def build_control_http_bases(settings_row: MeshCentralSettings) -> List[str]:
    """Ordered server-side HTTP bases: public URL first, loopback fallback last."""
    dom = normalize_domain(settings_row.domain)
    bases = [_base_with_domain(settings_row.server_url or "", dom)]
    parsed = urlparse(normalize_server_url(settings_row.server_url or ""))
    local_base = _loopback_base(settings_row, dom, parsed.path.rstrip("/"))
    if local_base and local_base not in bases:
        bases.append(f"{local_base}/")
    return bases


def classify_meshcentral_error(
    exc: BaseException,
    settings_row: Optional[MeshCentralSettings] = None,
) -> Tuple[str, str]:
    """Turn a low-level connection/auth failure into a kind and an actionable hint."""
    text = redact_meshcentral_text(f"{type(exc).__name__}: {exc}").lower()
    host = (settings_row.server_url if settings_row else None) or "the configured MeshCentral host"

    # aiohttp connection errors often contain `ssl:default` even for DNS
    # failures, so certificate-specific markers must be checked before DNS.
    if any(token in text for token in (
        "certificate verify failed", "self-signed", "sslcert", "cert_verify", "certverify",
    )):
        return "ssl", (
            "TLS certificate verification failed for "
            f"{host}. Fix the server certificate or disable verify_ssl for this workspace."
        )
    if any(token in text for token in (
        "name or service not known", "getaddrinfo", "gaierror", "nodename nor servname",
        "name resolution", "temporary failure", "dns",
    )):
        return "dns", (
            f"DNS lookup failed for {host}. Check the hostname/DNS, or set local_port "
            "to use the local 127.0.0.1 MeshCentral listener."
        )
    if "noauth" in text:
        return "noauth", (
            "MeshCentral rejected every auth method (noauth). Check the MeshCentral "
            "username/password (x-meshauth) and loginTokenKey/user ID, then retry."
        )
    connection_tokens = ("refused", "unreachable", "timed out", "timeout", "connect")
    if any(token in text for token in connection_tokens):
        return "connection", (
            f"Could not connect to {host}. Check the server port/firewall, or set local_port "
            "for server-side loopback access."
        )
    return "unknown", f"Check the MeshCentral settings for {host} and retry."


def actionable_meshcentral_error(
    exc: BaseException,
    settings_row: Optional[MeshCentralSettings] = None,
) -> RuntimeError:
    """Wrap a MeshCentral failure with a stable, redacted, user-facing diagnosis."""
    kind, hint = classify_meshcentral_error(exc, settings_row)
    return RuntimeError(
        f"MeshCentral {kind} error: {redact_meshcentral_text(exc)}. Hint: {hint}"
    )


def _group_upsert_statement(
    dialect: str,
    workspace_id: int,
    mesh_id: str,
    name: str,
    now: datetime,
):
    """Return an atomic INSERT ... ON CONFLICT UPDATE for one cached group."""
    values = {
        "workspace_id": workspace_id,
        "mesh_id": mesh_id,
        "name": name,
        "created_at": now,
        "updated_at": now,
    }
    table = MeshCentralGroup.__table__
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(table).values(**values).on_conflict_do_update(
            constraint="uq_meshcentralgroup_ws_mesh",
            set_={"name": name, "updated_at": now},
        )
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert(table).values(**values).on_conflict_do_update(
        index_elements=["workspace_id", "mesh_id"],
        set_={"name": name, "updated_at": now},
    )


async def upsert_workspace_groups(
    db: AsyncSession,
    workspace_id: int,
    meshes_dict: Dict[str, str],
) -> Dict[str, int]:
    """Cache MeshCentral groups for one workspace without touching other tenants."""
    if not meshes_dict:
        return {"created": 0, "updated": 0}
    now = datetime.utcnow()
    existing = (
        await db.execute(
            select(MeshCentralGroup).where(MeshCentralGroup.workspace_id == workspace_id)
        )
    ).scalars().all()
    by_mesh_id = {row.mesh_id: row for row in existing}

    bind = getattr(db, "bind", None)
    dialect = getattr(getattr(bind, "dialect", None), "name", "sqlite") or "sqlite"

    created = 0
    updated = 0
    for mesh_id, name in meshes_dict.items():
        clean_id = str(mesh_id).strip()
        if not clean_id:
            continue
        clean_name = str(name or clean_id).strip() or clean_id
        row = by_mesh_id.get(clean_id)
        if row is None:
            await db.execute(
                _group_upsert_statement(dialect, workspace_id, clean_id, clean_name, now)
            )
            created += 1
        elif row.name != clean_name:
            # The unique constraint protects concurrent inserts; an existing row
            # can be updated in the identity map so callers see the new name.
            row.name = clean_name
            row.updated_at = now
            updated += 1
    # Flush only: callers own the surrounding transaction and commit once their
    # whole sync/settings operation has succeeded.
    await db.flush()
    return {"created": created, "updated": updated}


def parse_inventory_messages(messages: List[Dict[str, Any]]) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Parse MeshCentral `meshes` and `nodes` WebSocket JSON frames into normalized records."""
    mesh_names: Dict[str, str] = {}
    raw_nodes: List[Dict[str, Any]] = []

    for msg in messages:
        action = msg.get("action")
        if action == "meshes":
            for mesh in msg.get("meshes") or []:
                mid = mesh.get("_id")
                if mid:
                    mesh_names[mid] = mesh.get("name") or mid
        elif action == "nodes":
            nodes_field = msg.get("nodes")
            if isinstance(nodes_field, dict):
                for mesh_id, items in nodes_field.items():
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                entry = dict(item)
                                entry.setdefault("meshid", mesh_id)
                                raw_nodes.append(entry)
            elif isinstance(nodes_field, list):
                for item in nodes_field:
                    if isinstance(item, dict):
                        raw_nodes.append(dict(item))

    devices: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for node in raw_nodes:
        raw_id = node.get("_id")
        if not raw_id:
            continue
        try:
            clean_id = normalize_node_id(raw_id)
        except ValueError:
            continue
        if clean_id in seen:
            continue
        seen.add(clean_id)

        mesh_id = node.get("meshid") or ""
        conn = int(node.get("conn") or 0)
        pwr = int(node.get("pwr") or 0)
        is_online = bool((conn & 1) or pwr == 1)
        os_desc = node.get("osdesc") or None
        icon = node.get("icon")
        name = (node.get("name") or node.get("rname") or clean_id[:16]).strip()

        devices.append({
            "node_id": clean_id,
            "mesh_id": mesh_id or None,
            "mesh_name": mesh_names.get(mesh_id) or None,
            "name": name,
            "hostname": (node.get("rname") or None),
            "os_desc": os_desc,
            "os_family": detect_os_family(os_desc, icon if isinstance(icon, int) else None),
            "ip_address": node.get("ip") or None,
            "power_state": pwr,
            "is_online": is_online,
        })

    return mesh_names, devices


async def fetch_meshcentral_inventory(
    settings_row: MeshCentralSettings,
    timeout: float = 12.0,
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Connect to MeshCentral `/control.ashx` over WebSocket and fetch live groups + devices."""
    aiohttp = _require_aiohttp()
    candidates = build_control_ws_candidates(settings_row)
    ssl_param: Any = None if settings_row.verify_ssl else False
    overall_deadline = time.monotonic() + timeout

    last_error: Optional[Exception] = None
    for ws_url, headers in candidates:
        remaining_overall = overall_deadline - time.monotonic()
        if remaining_overall <= 0:
            break
        attempt_timeout = min(timeout, remaining_overall)
        client_timeout = aiohttp.ClientTimeout(total=attempt_timeout)
        collected: List[Dict[str, Any]] = []
        got_meshes = False
        got_nodes = False
        noauth_failed = False

        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            try:
                async with session.ws_connect(
                    ws_url,
                    headers=headers,
                    ssl=ssl_param,
                    heartbeat=10.0,
                    max_msg_size=16 * 1024 * 1024,
                ) as ws:
                    await ws.send_json({"action": "meshes"})
                    await ws.send_json({"action": "nodes"})

                    deadline = time.monotonic() + attempt_timeout
                    while not (got_meshes and got_nodes):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            msg = await ws.receive(timeout=remaining)
                        except asyncio.TimeoutError:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except ValueError:
                                continue
                            if not isinstance(data, dict):
                                continue
                            action = data.get("action")
                            if action == "close":
                                cause = (
                                    data.get("cause")
                                    or data.get("msg")
                                    or "Authentication failed"
                                )
                                err = actionable_meshcentral_error(
                                    RuntimeError(f"MeshCentral closed the connection: {cause}"),
                                    settings_row,
                                )
                                noauth_failed = (
                                    classify_meshcentral_error(err, settings_row)[0] == "noauth"
                                )
                                last_error = last_error or err
                                break
                            if action == "meshes":
                                got_meshes = True
                                collected.append(data)
                            elif action == "nodes":
                                got_nodes = True
                                collected.append(data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except RuntimeError as exc:
                if str(exc).startswith("MeshCentral "):
                    raise
                last_error = last_error or actionable_meshcentral_error(exc, settings_row)
                continue
            except Exception as exc:
                last_error = last_error or actionable_meshcentral_error(exc, settings_row)
                continue

        if got_meshes or got_nodes:
            return parse_inventory_messages(collected)
        if not noauth_failed:
            break

    if last_error:
        raise last_error
    raise RuntimeError(
        "Connected to MeshCentral but received no inventory data. Check credentials and user permissions."
    )


async def sync_workspace_devices(
    db: AsyncSession,
    workspace_id: int,
    settings_row: MeshCentralSettings,
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Fetch live MeshCentral devices and upsert them into the workspace's local fleet cache."""
    meshes, remote_devices = await fetch_meshcentral_inventory(settings_row, timeout=timeout)
    await upsert_workspace_groups(db, workspace_id, meshes)
    now = datetime.utcnow()

    existing_rows = (
        await db.execute(
            select(MeshCentralDevice).where(MeshCentralDevice.workspace_id == workspace_id)
        )
    ).scalars().all()
    by_node_id = {normalize_node_id(row.node_id): row for row in existing_rows}

    seen_ids: set[str] = set()
    created = 0
    updated = 0
    online_count = 0

    for item in remote_devices:
        nid = item["node_id"]
        seen_ids.add(nid)
        if item["is_online"]:
            online_count += 1

        row = by_node_id.get(nid)
        if row is None:
            row = MeshCentralDevice(
                workspace_id=workspace_id,
                node_id=nid,
                mesh_id=item["mesh_id"],
                mesh_name=item["mesh_name"],
                name=item["name"],
                hostname=item["hostname"],
                client_name=item["mesh_name"],
                os_desc=item["os_desc"],
                os_family=item["os_family"],
                ip_address=item["ip_address"],
                power_state=item["power_state"],
                is_online=item["is_online"],
                last_synced_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            created += 1
        else:
            row.node_id = nid
            row.mesh_id = item["mesh_id"]
            row.mesh_name = item["mesh_name"]
            row.name = item["name"]
            row.hostname = item["hostname"]
            if not row.client_name and item["mesh_name"]:
                row.client_name = item["mesh_name"]
            row.os_desc = item["os_desc"]
            row.os_family = item["os_family"]
            row.ip_address = item["ip_address"]
            row.power_state = item["power_state"]
            row.is_online = item["is_online"]
            row.last_synced_at = now
            row.updated_at = now
            updated += 1

    # Devices previously synced that weren't returned are marked offline (never deleted, preserving notes/pins)
    for nid, row in by_node_id.items():
        if nid not in seen_ids and row.last_synced_at is not None:
            row.is_online = False
            row.power_state = 0
            row.updated_at = now

    await db.commit()
    return {
        "groups": len(meshes),
        "synced": len(remote_devices),
        "created": created,
        "updated": updated,
        "online": online_count,
    }


async def diagnose_meshcentral_connection(
    settings_row: MeshCentralSettings,
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """Probe MeshCentral and return a structured, actionable diagnostics report."""
    has_password = bool(settings_row.username and decrypt_secret(settings_row.password_encrypted))
    has_token = bool(decrypt_secret(settings_row.login_token_key_encrypted))
    auth_method = "x-meshauth" if has_password else ("loginToken" if has_token else "none")
    report: Dict[str, Any] = {
        "ok": False,
        "auth_method": auth_method,
        "meshes": {},
        "groups": 0,
        "devices": 0,
        "online": 0,
        "sso_mode": "token_key" if has_token else "none",
        "error": None,
        "hint": None,
    }

    try:
        meshes, devices = await fetch_meshcentral_inventory(settings_row, timeout=timeout)
    except Exception as exc:
        error = (
            exc
            if str(exc).startswith("MeshCentral ")
            else actionable_meshcentral_error(exc, settings_row)
        )
        _kind, hint = classify_meshcentral_error(error, settings_row)
        message = redact_meshcentral_text(error)
        # The wrapped error already ends with "Hint: ..."; UIs render the
        # structured `hint` field separately, so avoid printing it twice.
        if ". Hint: " in message:
            message = message.split(". Hint: ", 1)[0]
        report.update({"error": message, "hint": hint})
        return report

    report.update({
        "ok": True,
        "meshes": dict(meshes),
        "groups": len(meshes),
        "devices": len(devices),
        "online": sum(1 for device in devices if device.get("is_online")),
        "hint": f"Connected to {settings_row.server_url}.",
    })
    if has_token:
        report["sso_mode"] = "token_key"
    elif has_password:
        try:
            cookie = await fetch_meshcentral_auth_cookie(settings_row, timeout=min(timeout, 6.0))
            report["sso_mode"] = "authcookie" if cookie else "none"
        except Exception:
            report["sso_mode"] = "none"
    return report


# ---------------------------------------------------------------------------
# Automatic Live Auth Cookie SSO, Group Creation & Local Auto-Configuration
# ---------------------------------------------------------------------------

async def fetch_meshcentral_auth_cookie(
    settings_row: MeshCentralSettings,
    timeout: float = 8.0,
) -> Optional[str]:
    """Request a short-lived signed auth cookie from MeshCentral `/control.ashx`.

    Allows automatic passwordless SSO into client PCs using only the configured
    MeshCentral Server URL + Username + Password, without requiring a manual CLI
    `--loginTokenKey` copy-paste.
    """
    aiohttp = _require_aiohttp()
    candidates = build_control_ws_candidates(settings_row)
    ssl_param: Any = None if settings_row.verify_ssl else False
    overall_deadline = time.monotonic() + timeout

    for ws_url, headers in candidates:
        remaining_overall = overall_deadline - time.monotonic()
        if remaining_overall <= 0:
            break
        attempt_timeout = min(timeout, remaining_overall)
        client_timeout = aiohttp.ClientTimeout(total=attempt_timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            try:
                async with session.ws_connect(
                    ws_url,
                    headers=headers,
                    ssl=ssl_param,
                    heartbeat=10.0,
                    max_msg_size=4 * 1024 * 1024,
                ) as ws:
                    await ws.send_json({"action": "authcookie"})
                    deadline = time.monotonic() + attempt_timeout
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            msg = await ws.receive(timeout=remaining)
                        except asyncio.TimeoutError:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except ValueError:
                                continue
                            if not isinstance(data, dict):
                                continue
                            action = data.get("action")
                            if action == "close":
                                cause = redact_meshcentral_text(
                                    data.get("cause") or data.get("msg") or ""
                                )
                                logger.debug("MeshCentral authcookie attempt rejected: %s", cause)
                                break
                            if action == "authcookie" and data.get("cookie"):
                                return str(data["cookie"])
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except Exception as exc:
                logger.debug(
                    "MeshCentral authcookie attempt failed: %s", redact_meshcentral_text(exc)
                )
                continue
    return None


async def build_embed_url_async(
    settings_row: MeshCentralSettings,
    node_id: str,
    mode: Optional[str] = None,
    *,
    hide_chrome: Optional[bool] = None,
    include_login: bool = True,
) -> Dict[str, Any]:
    """Build an embedded session URL, automatically falling back to live `authcookie` SSO."""
    embed = build_embed_url(
        settings_row,
        node_id,
        mode=mode,
        hide_chrome=hide_chrome,
        include_login=include_login,
    )
    if embed["sso_active"] or not include_login:
        return embed

    # No local loginTokenKey saved: try minting a live auth cookie via /control.ashx
    if settings_row.username and decrypt_secret(settings_row.password_encrypted):
        try:
            cookie = await fetch_meshcentral_auth_cookie(settings_row, timeout=6.0)
            if cookie:
                params: List[Tuple[str, str]] = [
                    ("auth", cookie),
                    ("gotonode", embed["node_id"]),
                    ("viewmode", str(embed["viewmode"])),
                ]
                if embed["hide_chrome"]:
                    params.append(("hide", str(HIDE_CHROME_BITMASK)))
                base = _base_with_domain(settings_row.server_url or "", settings_row.domain)
                embed["url"] = f"{base}?{urlencode(params, safe='@$')}"
                embed["sso_active"] = True
                embed["sso_mode"] = "authcookie"
        except Exception as exc:
            logger.debug(
                "Live MeshCentral authcookie fallback skipped: %s", redact_meshcentral_text(exc)
            )
    return embed


async def create_meshcentral_group(
    settings_row: MeshCentralSettings,
    name: str,
    description: str = "",
    timeout: float = 10.0,
) -> Dict[str, str]:
    """Create a new Agent-Only device group (mesh) directly on the MeshCentral server."""
    aiohttp = _require_aiohttp()
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Group name is required.")

    candidates = build_control_ws_candidates(settings_row)
    ssl_param: Any = None if settings_row.verify_ssl else False
    overall_deadline = time.monotonic() + timeout

    last_error: Optional[Exception] = None
    noauth_error: Optional[Exception] = None
    for ws_url, headers in candidates:
        remaining_overall = overall_deadline - time.monotonic()
        if remaining_overall <= 0:
            break
        attempt_timeout = min(timeout, remaining_overall)
        client_timeout = aiohttp.ClientTimeout(total=attempt_timeout)
        noauth_failed = False
        created_id: Optional[str] = None
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            try:
                async with session.ws_connect(
                    ws_url,
                    headers=headers,
                    ssl=ssl_param,
                    heartbeat=10.0,
                    max_msg_size=8 * 1024 * 1024,
                ) as ws:
                    await ws.send_json({
                        "action": "createmesh",
                        "meshname": clean_name,
                        "meshtype": 2,  # 2 = Agent-only standard device group
                        "desc": (description or "").strip(),
                        "responseid": "cem",
                    })
                    await ws.send_json({"action": "meshes"})

                    deadline = time.monotonic() + attempt_timeout
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            msg = await ws.receive(timeout=remaining)
                        except asyncio.TimeoutError:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except ValueError:
                                continue
                            if not isinstance(data, dict):
                                continue
                            action = data.get("action")
                            response_id = data.get("responseid")
                            # Ignore replies to somebody else's request on a
                            # shared MeshCentral server.
                            if response_id is not None and response_id != "cem":
                                continue
                            if action == "close":
                                cause = (
                                    data.get("cause")
                                    or data.get("msg")
                                    or "Authentication failed"
                                )
                                err = actionable_meshcentral_error(
                                    RuntimeError(f"MeshCentral closed the connection: {cause}"),
                                    settings_row,
                                )
                                if classify_meshcentral_error(err, settings_row)[0] == "noauth":
                                    noauth_failed = True
                                    noauth_error = noauth_error or err
                                else:
                                    last_error = last_error or err
                                break
                            if action == "event":
                                event = data.get("event")
                                # Uncorrelated createmesh events are broadcasts
                                # from other users and must not be attributed to
                                # this workspace.
                                if (
                                    response_id == "cem"
                                    and isinstance(event, dict)
                                    and event.get("action") == "createmesh"
                                    and event.get("meshid")
                                ):
                                    created_id = str(event["meshid"])
                                    break
                            if action == "createmesh":
                                if data.get("meshid"):
                                    created_id = str(data["meshid"])
                                    break
                                # `{"result": "ok"}` has no id: wait for the meshes frame.
                            if action == "meshes":
                                for mesh in data.get("meshes") or []:
                                    if (
                                        isinstance(mesh, dict)
                                        and (mesh.get("name") or "").strip().lower()
                                        == clean_name.lower()
                                        and mesh.get("_id")
                                    ):
                                        created_id = str(mesh["_id"])
                                        break
                                if created_id:
                                    break
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except RuntimeError as exc:
                if str(exc).startswith("MeshCentral "):
                    last_error = last_error or exc
                else:
                    last_error = last_error or actionable_meshcentral_error(exc, settings_row)
                continue
            except Exception as exc:
                last_error = last_error or actionable_meshcentral_error(exc, settings_row)
                continue

        if created_id:
            return {"mesh_id": created_id, "mesh_name": clean_name}
        # Both noauth and transport failures must fall through to the next auth
        # candidate (and, for local installs, to the loopback endpoint).
        if noauth_failed or last_error:
            continue

    if noauth_error:
        raise noauth_error
    if last_error:
        raise last_error
    raise RuntimeError(f"MeshCentral did not confirm creation of group '{clean_name}'.")


def _strip_json_comments(text: str) -> str:
    """Allow MeshCentral config.json files that include // or /* */ comments."""
    out: List[str] = []
    i = 0
    n = len(text)
    in_str = False
    escape = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
                i += 1
            elif ch == "/" and i + 1 < n and text[i + 1] == "/":
                i += 2
                while i < n and text[i] not in ("\n", "\r"):
                    i += 1
            elif ch == "/" and i + 1 < n and text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
            else:
                out.append(ch)
                i += 1
    return "".join(out)


async def autodetect_local_meshcentral(custom_path: Optional[str] = None) -> Dict[str, Any]:
    """Detect a MeshCentral install on this server, enable framing/tokens, and read loginTokenKey."""
    candidates: List[Path] = []
    if custom_path and custom_path.strip():
        candidates.append(Path(custom_path.strip()).expanduser())
    env_dir = os.getenv("MESHCENTRAL_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.extend([
        Path("/opt/meshcentral"),
        Path("/usr/local/meshcentral"),
        Path("/srv/meshcentral"),
        Path("/var/lib/meshcentral"),
        Path.home() / "meshcentral",
    ])

    found_dir: Optional[Path] = None
    config_file: Optional[Path] = None
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        cfg = candidate / "meshcentral-data" / "config.json"
        if cfg.is_file():
            found_dir = candidate
            config_file = cfg
            break

    if not found_dir or not config_file:
        return {
            "found": False,
            "path": None,
            "config_updated": False,
            "login_token_key": None,
            "suggested_url": None,
            "autolink": None,
            "notes": [],
        }

    notes: List[str] = []
    config_updated = False
    suggested_url: Optional[str] = None
    autolink: Optional[Dict[str, Any]] = None

    # Read optional cem-autolink.json written by setup_meshcentral.sh
    autolink_file = found_dir / "meshcentral-data" / "cem-autolink.json"
    if autolink_file.is_file():
        try:
            al_data = json.loads(autolink_file.read_text(encoding="utf-8"))
            if isinstance(al_data, dict):
                autolink = al_data
                if al_data.get("server_url"):
                    suggested_url = str(al_data["server_url"]).strip()
        except Exception as exc:
            notes.append(f"Could not read cem-autolink.json: {exc}")

    try:
        raw_text = config_file.read_text(encoding="utf-8")
        cfg_data = json.loads(_strip_json_comments(raw_text))
        if isinstance(cfg_data, dict):
            mc_sec = cfg_data.setdefault("settings", {})
            if isinstance(mc_sec, dict):
                changed = False
                if mc_sec.get("allowFraming") is not True and mc_sec.get("AllowFraming") is not True:
                    mc_sec["AllowFraming"] = True
                    changed = True
                if mc_sec.get("AllowLoginToken") is not True and mc_sec.get("allowLoginToken") is not True:
                    mc_sec["AllowLoginToken"] = True
                    changed = True
                if changed:
                    backup = config_file.with_suffix(".json.bak")
                    if not backup.exists():
                        shutil.copy2(config_file, backup)
                    config_file.write_text(json.dumps(cfg_data, indent=2) + "\n", encoding="utf-8")
                    config_updated = True
                    notes.append("Enabled allowFraming and AllowLoginToken in local meshcentral-data/config.json.")
                cert_host = mc_sec.get("cert") or mc_sec.get("Cert")
                port = (
                    mc_sec.get("aliasPort")
                    or mc_sec.get("AliasPort")
                    or mc_sec.get("port")
                    or mc_sec.get("Port")
                    or 443
                )
                if isinstance(cert_host, str) and cert_host.strip() and not cert_host.startswith("_"):
                    if int(port) == 443:
                        suggested_url = f"https://{cert_host.strip()}"
                    else:
                        suggested_url = f"https://{cert_host.strip()}:{int(port)}"
    except Exception as exc:
        notes.append(f"Could not edit local config.json: {exc}")

    login_token_key: Optional[str] = (
        str(autolink.get("login_token_key") or "").strip() if autolink else None
    ) or None
    mc_entry = found_dir / "node_modules" / "meshcentral"
    node_bin = shutil.which("node")
    if not login_token_key and node_bin and mc_entry.exists():
        try:
            proc = await asyncio.create_subprocess_exec(
                node_bin,
                str(mc_entry),
                "--loginTokenKey",
                cwd=str(found_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            output = stdout.decode("utf-8", errors="ignore")
            match = _HEX_KEY_RE.search(output)
            if match:
                login_token_key = match.group(1)
                notes.append("Auto-retrieved loginTokenKey from local MeshCentral.")
        except Exception as exc:
            notes.append(f"Could not run node --loginTokenKey locally: {exc}")

    return {
        "found": True,
        "path": str(found_dir),
        "config_updated": config_updated,
        "login_token_key": login_token_key,
        "suggested_url": suggested_url,
        "autolink": autolink,
        "notes": notes,
    }


async def auto_import_local_meshcentral_if_present(
    db: AsyncSession,
    workspace_id: int,
    custom_path: Optional[str] = None,
) -> Optional[MeshCentralSettings]:
    """Auto-populate MeshCentralSettings once from setup_meshcentral.sh's cem-autolink.json.

    Never overwrites an existing workspace configuration — only bootstraps a workspace
    that has not configured MeshCentral yet so admins don't have to re-type anything.
    """
    existing = (
        await db.execute(
            select(MeshCentralSettings).where(MeshCentralSettings.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    candidates: List[Path] = []
    if custom_path and custom_path.strip():
        candidates.append(Path(custom_path.strip()).expanduser())
    env_dir = os.getenv("MESHCENTRAL_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.extend([
        Path("/opt/meshcentral"),
        Path("/usr/local/meshcentral"),
        Path("/srv/meshcentral"),
        Path("/var/lib/meshcentral"),
    ])

    autolink: Optional[Dict[str, Any]] = None
    found_dir: Optional[Path] = None
    for cand in candidates:
        al_file = cand / "meshcentral-data" / "cem-autolink.json"
        if al_file.is_file():
            try:
                data = json.loads(al_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("server_url"):
                    autolink = data
                    found_dir = cand
                    break
            except Exception:
                continue

    if not autolink or not found_dir:
        return None

    try:
        clean_url = normalize_server_url(str(autolink["server_url"]))
    except ValueError:
        return None

    username = (str(autolink.get("username") or "")).strip() or None
    password = (str(autolink.get("password") or "")).strip() or None
    token_key = (str(autolink.get("login_token_key") or "")).strip() or None
    verify_ssl = bool(autolink.get("verify_ssl", True))
    default_mesh_id = (str(autolink.get("default_mesh_id") or "")).strip() or None
    default_mesh_name = (str(autolink.get("default_mesh_name") or "")).strip() or None
    local_port: Optional[int] = None
    if autolink.get("local_port") is not None:
        try:
            candidate_port = int(autolink["local_port"])
            if 1 <= candidate_port <= 65535:
                local_port = candidate_port
        except (TypeError, ValueError):
            pass
    now = datetime.utcnow()

    row = MeshCentralSettings(
        workspace_id=workspace_id,
        enabled=True,
        server_url=clean_url,
        domain="",
        username=username,
        password_encrypted=encrypt_secret(password) if password else None,
        login_token_key_encrypted=encrypt_secret(token_key) if token_key else None,
        login_user_id=build_login_user_id(username, ""),
        default_view_mode=11,
        hide_mesh_chrome=True,
        verify_ssl=verify_ssl,
        default_mesh_id=default_mesh_id,
        default_mesh_name=default_mesh_name,
        local_install_path=str(found_dir),
        local_port=local_port,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    if default_mesh_id:
        await upsert_workspace_groups(
            db, workspace_id, {default_mesh_id: default_mesh_name or "Default Client Group"}
        )
        group = (
            await db.execute(
                select(MeshCentralGroup).where(
                    MeshCentralGroup.workspace_id == workspace_id,
                    MeshCentralGroup.mesh_id == default_mesh_id,
                )
            )
        ).scalars().first()
        if group is not None:
            group.is_default = True
            group.updated_at = now
            await db.commit()
    return row


# ---------------------------------------------------------------------------
# Client Installer Proxy (everything stays on our website)
# ---------------------------------------------------------------------------

def _safe_brand_slug(brand_name: Optional[str]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", (brand_name or "RemoteSupport").strip()).strip("-")
    return (cleaned or "RemoteSupport")[:32]


_AGENT_ENDPOINTS = frozenset({"meshagents", "meshosxagent"})


def _agent_download_url_for_base(
    base: str,
    mesh_id: str,
    platform: str,
    install_mode: str,
    agent_id_override: Optional[int],
    endpoint: str = "meshagents",
) -> str:
    """Build a MeshCentral agent URL against a public or loopback base."""
    spec = AGENT_PLATFORMS.get(platform)
    if not spec:
        raise ValueError(f"Unsupported installer platform: {platform}")
    if endpoint not in _AGENT_ENDPOINTS:
        raise ValueError(f"Unsupported MeshCentral agent endpoint: {endpoint}")
    clean_mesh = normalize_mesh_id(mesh_id)
    if platform == "linux":
        return f"{base}meshagents?script=1"

    agent_id = agent_id_override if agent_id_override is not None else spec["agent_id"]
    install_flags = 2 if install_mode == "connect" else 0
    query = urlencode(
        {"id": str(agent_id), "meshid": clean_mesh, "installflags": str(install_flags)},
        safe="@$",
    )
    return f"{base}{endpoint}?{query}"


def build_agent_download_url(
    settings_row: MeshCentralSettings,
    mesh_id: str,
    platform: str = "windows64",
    install_mode: str = "install",
    agent_id_override: Optional[int] = None,
) -> str:
    """Build the browser-facing MeshCentral `/meshagents` URL for a group and OS."""
    if not settings_row.server_url:
        raise ValueError("MeshCentral server URL is not configured.")
    base = _base_with_domain(settings_row.server_url, settings_row.domain)
    return _agent_download_url_for_base(base, mesh_id, platform, install_mode, agent_id_override)


async def proxy_agent_installer(
    settings_row: MeshCentralSettings,
    mesh_id: str,
    platform: str = "windows64",
    install_mode: str = "install",
    brand_name: str = "RemoteSupport",
    timeout: float = 30.0,
) -> Tuple[bytes, str, str]:
    """Download the client MeshAgent installer from MeshCentral to stream through CEM."""
    spec = AGENT_PLATFORMS.get(platform)
    if not spec:
        raise ValueError(f"Unsupported installer platform: {platform}")
    clean_mesh = normalize_mesh_id(mesh_id)
    slug = _safe_brand_slug(brand_name)
    plat_suffix = {
        "windows64": "Windows64",
        "windows32": "Windows32",
        "macos": "macOS",
        "linux": "Linux",
    }.get(platform, platform)
    filename = f"{slug}-Support-{plat_suffix}{spec['ext']}"

    if platform == "linux":
        server_base = _base_with_domain(settings_row.server_url or "", settings_row.domain).rstrip("/")
        script_url = f"{server_base}/meshagents?script=1"
        insecure_flag = " -k" if not settings_row.verify_ssl else ""
        q_brand = shlex.quote(f"Downloading {brand_name} Remote Support agent...")
        q_script_url = shlex.quote(script_url)
        q_server_base = shlex.quote(server_base)
        q_mesh_id = shlex.quote(clean_mesh)
        script = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"echo {q_brand}\n"
            'TMP_DIR="$(mktemp -d /tmp/meshinstall.XXXXXX)"\n'
            'trap \'rm -rf "$TMP_DIR"\' EXIT\n'
            'TMP_SCRIPT="$TMP_DIR/meshinstall.sh"\n'
            f'curl -fsSL{insecure_flag} {q_script_url} | tr -d \'\\r\' > "$TMP_SCRIPT"\n'
            'chmod 755 "$TMP_SCRIPT"\n'
            'cd "$TMP_DIR"\n'
            'if [ "$(id -u)" -eq 0 ]; then\n'
            f'  bash "$TMP_SCRIPT" {q_server_base} {q_mesh_id}\n'
            'else\n'
            f'  sudo bash "$TMP_SCRIPT" {q_server_base} {q_mesh_id}\n'
            'fi\n'
        )
        return script.encode("utf-8"), filename, spec["content_type"]

    aiohttp = _require_aiohttp()
    ssl_param: Any = None if settings_row.verify_ssl else False
    headers: Dict[str, str] = {}
    password = decrypt_secret(settings_row.password_encrypted)
    if settings_row.username and password:
        u_b64 = base64.b64encode(settings_row.username.strip().encode("utf-8")).decode("ascii")
        p_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
        headers["x-meshauth"] = f"{u_b64},{p_b64}"

    # (agent id, endpoint) attempts, in priority order. Windows uses a single
    # known agent; macOS tries modern id=10005, then legacy id=29 on both the
    # standard and legacy `/meshosxagent` endpoints.
    attempts: list[tuple[int, str]] = [(spec["agent_id"], "meshagents")]
    if spec.get("fallback_agent_id"):
        fallback_id = spec["fallback_agent_id"]
        attempts.append((fallback_id, "meshagents"))
        if spec.get("fallback_endpoint"):
            attempts.append((fallback_id, spec["fallback_endpoint"]))

    default_error = "Could not fetch installer from MeshCentral."
    last_error = default_error
    bases = build_control_http_bases(settings_row)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        for aid, endpoint in attempts:
            for base in bases:
                url = _agent_download_url_for_base(
                    base,
                    clean_mesh,
                    platform=platform,
                    install_mode=install_mode,
                    agent_id_override=aid,
                    endpoint=endpoint,
                )
                try:
                    async with session.get(url, headers=headers, ssl=ssl_param) as resp:
                        if resp.status == 200:
                            headers_map = getattr(resp, "headers", None)
                            declared = (
                                headers_map.get("Content-Length")
                                if hasattr(headers_map, "get")
                                else None
                            )
                            if declared:
                                try:
                                    if int(declared) > MAX_AGENT_INSTALLER_BYTES:
                                        last_error = "MeshCentral installer exceeds size limit."
                                        continue
                                except (TypeError, ValueError):
                                    pass
                            payload = await resp.read()
                            if not payload:
                                last_error = "MeshCentral returned an empty installer file."
                                continue
                            if len(payload) > MAX_AGENT_INSTALLER_BYTES:
                                last_error = "MeshCentral installer exceeds the allowed size."
                                continue
                            if not _installer_body_is_valid(platform, payload, endpoint):
                                last_error = f"unexpected {platform} installer body."
                                continue
                            body_filename = filename
                            body_content_type = spec["content_type"]
                            if platform == "macos" and payload.startswith(b"#!"):
                                body_filename = f"{slug}-Support-macOS.sh"
                                body_content_type = "text/x-shellscript"
                            return payload, body_filename, body_content_type
                        elif resp.status == 404:
                            last_error = (
                                "MeshCentral returned HTTP 404 fetching installer. "
                                "On first startup MeshCentral downloads ~119 MB of agent binaries from GitHub (takes ~1 minute); "
                                "wait 30 seconds and try again."
                            )
                        else:
                            last_error = (
                                f"MeshCentral returned HTTP {resp.status} fetching installer."
                            )
                except (OSError, RuntimeError, getattr(aiohttp, "ClientError", OSError)) as exc:
                    # Programming errors must surface; keep the first meaningful
                    # upstream error rather than the last fallback's failure.
                    if last_error == default_error:
                        last_error = str(actionable_meshcentral_error(exc, settings_row))

    raise RuntimeError(last_error)


# ---------------------------------------------------------------------------
# Admin Diagnostic Report (zero CLI copy-paste required!)
# ---------------------------------------------------------------------------

async def build_meshcentral_diagnostic_report(
    db: AsyncSession,
    workspace_id: int,
    mc_settings: Optional[MeshCentralSettings],
) -> Dict[str, Any]:
    """Assemble a secret-redacted diagnostic report for admins right inside the dashboard."""
    import subprocess
    from app.models.meshcentral import MeshCentralGroup

    groups = (
        await db.execute(
            select(MeshCentralGroup)
            .where(MeshCentralGroup.workspace_id == workspace_id)
            .order_by(MeshCentralGroup.name.asc())
        )
    ).scalars().all()
    devices = (
        await db.execute(
            select(MeshCentralDevice).where(MeshCentralDevice.workspace_id == workspace_id)
        )
    ).scalars().all()

    secrets_to_redact: List[str] = []
    if mc_settings:
        for enc in (mc_settings.password_encrypted, mc_settings.login_token_key_encrypted):
            dec = decrypt_secret(enc)
            if dec:
                secrets_to_redact.append(dec)

    def _redact(text: str) -> str:
        out = text
        for sec in secrets_to_redact:
            if sec and len(sec) >= 4:
                out = out.replace(sec, "<redacted>")
        return out

    # Inspect local MeshCentral directory (read-only, never mutates config.json)
    local_dir: Optional[Path] = None
    candidates: List[Path] = []
    if mc_settings and mc_settings.local_install_path:
        candidates.append(Path(mc_settings.local_install_path).expanduser())
    env_dir = (os.getenv("MESHCENTRAL_DIR") or get_settings().meshcentral_dir or "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.extend([
        Path("/opt/meshcentral"),
        Path("/usr/local/meshcentral"),
        Path("/srv/meshcentral"),
    ])
    for cand in candidates:
        if (cand / "meshcentral-data").is_dir():
            local_dir = cand
            break

    local_summary: Dict[str, Any] = {
        "found": bool(local_dir),
        "path": str(local_dir) if local_dir else None,
        "config_json": None,
        "autolink_json": None,
        "service_status": "unknown",
        "installer_log_tail": [],
    }

    if local_dir:
        cfg_file = local_dir / "meshcentral-data" / "config.json"
        if cfg_file.is_file():
            try:
                raw_cfg = json.loads(_strip_json_comments(cfg_file.read_text(encoding="utf-8")))
                sec = raw_cfg.get("settings") or {}
                dom = (raw_cfg.get("domains") or {}).get("") or {}
                local_summary["config_json"] = {
                    "Cert": sec.get("Cert") or sec.get("cert"),
                    "Port": sec.get("Port") or sec.get("port"),
                    "AliasPort": sec.get("AliasPort") or sec.get("aliasPort"),
                    "TlsOffload": sec.get("TlsOffload") or sec.get("tlsOffload"),
                    "AllowFraming": sec.get("AllowFraming", sec.get("allowFraming")),
                    "AllowLoginToken": sec.get("AllowLoginToken", sec.get("allowLoginToken")),
                    "NewAccounts": dom.get("NewAccounts", dom.get("newAccounts")),
                    "certUrl": dom.get("certUrl"),
                }
            except Exception as exc:
                local_summary["config_json"] = {"error": str(exc)}

        al_file = local_dir / "meshcentral-data" / "cem-autolink.json"
        if al_file.is_file():
            try:
                al_raw = json.loads(al_file.read_text(encoding="utf-8"))
                for k in ("password", "login_token_key"):
                    val = str(al_raw.get(k) or "").strip()
                    if val:
                        secrets_to_redact.append(val)
                local_summary["autolink_json"] = {
                    "server_url": al_raw.get("server_url"),
                    "username": al_raw.get("username"),
                    "has_password": bool(al_raw.get("password")),
                    "has_login_token_key": bool(al_raw.get("login_token_key")),
                    "local_port": al_raw.get("local_port"),
                    "default_mesh_id": al_raw.get("default_mesh_id"),
                    "default_mesh_name": al_raw.get("default_mesh_name"),
                    "verify_ssl": al_raw.get("verify_ssl"),
                }
            except Exception as exc:
                local_summary["autolink_json"] = {"error": str(exc)}

    if shutil.which("systemctl"):
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", "meshcentral"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            local_summary["service_status"] = (proc.stdout or proc.stderr or "unknown").strip()
        except Exception:
            local_summary["service_status"] = "unavailable"

    user_log = Path("/tmp/meshcentral-user.log")
    if user_log.is_file():
        try:
            lines = user_log.read_text(encoding="utf-8", errors="replace").splitlines()
            local_summary["installer_log_tail"] = [_redact(ln) for ln in lines[-25:]]
        except Exception as exc:
            local_summary["installer_log_tail"] = [f"(unreadable: {exc})"]

    # Live connection probe
    live_probe: Dict[str, Any]
    if mc_settings and mc_settings.server_url:
        try:
            live_probe = await diagnose_meshcentral_connection(mc_settings, timeout=8.0)
        except Exception as exc:
            live_probe = {"ok": False, "error": _redact(str(exc)), "hint": ""}
    else:
        live_probe = {"ok": False, "error": "MeshCentral Server URL is not configured yet.", "hint": ""}

    settings_summary = (
        {
            "enabled": bool(mc_settings.enabled),
            "server_url": mc_settings.server_url,
            "domain": mc_settings.domain or "",
            "username": mc_settings.username,
            "has_password": bool(mc_settings.password_encrypted),
            "has_login_token_key": bool(mc_settings.login_token_key_encrypted),
            "login_user_id": mc_settings.login_user_id,
            "local_install_path": mc_settings.local_install_path,
            "local_port": mc_settings.local_port,
            "default_mesh_id": mc_settings.default_mesh_id,
            "default_mesh_name": mc_settings.default_mesh_name,
            "verify_ssl": bool(mc_settings.verify_ssl),
        }
        if mc_settings
        else None
    )

    lines_out = [
        f"=== CEM MeshCentral Diagnostic Report ({datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}) ===",
        f"Workspace ID: {workspace_id}",
        f"App Domain:   {get_settings().app_domain}",
        "",
        "[1. Saved CEM Settings]",
        json.dumps(settings_summary, indent=2),
        "",
        f"[2. Live Connection Probe] ok={live_probe.get('ok')}",
        _redact(json.dumps(live_probe, indent=2)),
        "",
        f"[3. Cached Groups ({len(groups)}) & Devices ({len(devices)}, online={sum(1 for d in devices if d.is_online)})]",
        json.dumps([{"mesh_id": g.mesh_id, "name": g.name, "is_default": g.is_default} for g in groups], indent=2),
        "",
        "[4. Local Server Engine Inspection]",
        json.dumps(local_summary, indent=2),
    ]
    report_text = _redact("\n".join(lines_out)) + "\n"

    return {
        "success": True,
        "ok": bool(live_probe.get("ok")),
        "settings": settings_summary,
        "live_probe": live_probe,
        "groups_count": len(groups),
        "devices_count": len(devices),
        "local": local_summary,
        "report_text": report_text,
    }
