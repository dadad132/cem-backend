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
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

import aiohttp
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.meshcentral import MeshCentralDevice, MeshCentralSettings

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# At-rest secret encryption
# ---------------------------------------------------------------------------

def _fernet() -> Fernet:
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

def build_control_ws_url(settings_row: MeshCentralSettings) -> Tuple[str, Dict[str, str]]:
    """Build the `/control.ashx` WebSocket URL and auth headers."""
    if not settings_row.server_url:
        raise ValueError("MeshCentral server URL is not configured.")
    base = normalize_server_url(settings_row.server_url)
    parsed = urlparse(base)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    dom = normalize_domain(settings_row.domain)
    path_prefix = parsed.path.rstrip("/")
    ws_path = f"{path_prefix}/{dom}/control.ashx" if dom else f"{path_prefix}/control.ashx"
    ws_url = f"{ws_scheme}://{parsed.netloc}{ws_path}"

    headers: Dict[str, str] = {}
    password = decrypt_secret(settings_row.password_encrypted)
    if settings_row.username and password:
        u_b64 = base64.b64encode(settings_row.username.strip().encode("utf-8")).decode("ascii")
        p_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
        headers["x-meshauth"] = f"{u_b64},{p_b64}"
        return ws_url, headers

    token_key = decrypt_secret(settings_row.login_token_key_encrypted)
    login_uid = build_login_user_id(
        settings_row.username,
        settings_row.domain,
        settings_row.login_user_id,
    )
    if token_key and login_uid:
        token = generate_login_token(token_key, login_uid)
        ws_url = f"{ws_url}?{urlencode({'auth': token}, safe='@$')}"
        return ws_url, headers

    raise ValueError(
        "Configure either a MeshCentral username + password or a loginTokenKey + username before syncing."
    )


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
    ws_url, headers = build_control_ws_url(settings_row)
    ssl_param: Any = None if settings_row.verify_ssl else False
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    collected: List[Dict[str, Any]] = []
    got_meshes = False
    got_nodes = False

    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.ws_connect(
            ws_url,
            headers=headers,
            ssl=ssl_param,
            heartbeat=10.0,
            max_msg_size=16 * 1024 * 1024,
        ) as ws:
            await ws.send_json({"action": "meshes"})
            await ws.send_json({"action": "nodes"})

            deadline = time.monotonic() + timeout
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
                        cause = data.get("cause") or data.get("msg") or "Authentication failed"
                        raise RuntimeError(f"MeshCentral closed the connection: {cause}")
                    if action == "meshes":
                        got_meshes = True
                        collected.append(data)
                    elif action == "nodes":
                        got_nodes = True
                        collected.append(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    if not got_meshes and not got_nodes:
        raise RuntimeError(
            "Connected to MeshCentral but received no inventory data. Check credentials and user permissions."
        )
    return parse_inventory_messages(collected)


async def sync_workspace_devices(
    db: AsyncSession,
    workspace_id: int,
    settings_row: MeshCentralSettings,
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Fetch live MeshCentral devices and upsert them into the workspace's local fleet cache."""
    meshes, remote_devices = await fetch_meshcentral_inventory(settings_row, timeout=timeout)
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
    ws_url, headers = build_control_ws_url(settings_row)
    ssl_param: Any = None if settings_row.verify_ssl else False
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.ws_connect(
            ws_url,
            headers=headers,
            ssl=ssl_param,
            heartbeat=10.0,
            max_msg_size=4 * 1024 * 1024,
        ) as ws:
            await ws.send_json({"action": "authcookie"})
            deadline = time.monotonic() + timeout
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
                        return None
                    if action == "authcookie" and data.get("cookie"):
                        return str(data["cookie"])
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
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
            logger.debug("Live MeshCentral authcookie fallback skipped: %s", exc)
    return embed


async def create_meshcentral_group(
    settings_row: MeshCentralSettings,
    name: str,
    description: str = "",
    timeout: float = 10.0,
) -> Dict[str, str]:
    """Create a new Agent-Only device group (mesh) directly on the MeshCentral server."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Group name is required.")

    ws_url, headers = build_control_ws_url(settings_row)
    ssl_param: Any = None if settings_row.verify_ssl else False
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    async with aiohttp.ClientSession(timeout=client_timeout) as session:
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
            })
            await asyncio.sleep(0.25)
            await ws.send_json({"action": "meshes"})

            deadline = time.monotonic() + timeout
            created_id: Optional[str] = None
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
                        cause = data.get("cause") or data.get("msg") or "Authentication failed"
                        raise RuntimeError(f"MeshCentral closed the connection: {cause}")
                    if action == "createmesh" and data.get("meshid"):
                        created_id = str(data["meshid"])
                        break
                    if action == "meshes":
                        for mesh in data.get("meshes") or []:
                            if (mesh.get("name") or "").strip().lower() == clean_name.lower() and mesh.get("_id"):
                                created_id = str(mesh["_id"])
                                break
                        if created_id:
                            break
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    if not created_id:
        raise RuntimeError(f"MeshCentral did not confirm creation of group '{clean_name}'.")
    return {"mesh_id": created_id, "mesh_name": clean_name}


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
            "notes": [],
        }

    notes: List[str] = []
    config_updated = False
    suggested_url: Optional[str] = None

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
                port = mc_sec.get("port") or mc_sec.get("Port") or 443
                if isinstance(cert_host, str) and cert_host.strip() and not cert_host.startswith("_"):
                    if int(port) == 443:
                        suggested_url = f"https://{cert_host.strip()}"
                    else:
                        suggested_url = f"https://{cert_host.strip()}:{int(port)}"
    except Exception as exc:
        notes.append(f"Could not edit local config.json: {exc}")

    login_token_key: Optional[str] = None
    mc_entry = found_dir / "node_modules" / "meshcentral"
    node_bin = shutil.which("node")
    if node_bin and mc_entry.exists():
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
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Client Installer Proxy (everything stays on our website)
# ---------------------------------------------------------------------------

def _safe_brand_slug(brand_name: Optional[str]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", (brand_name or "RemoteSupport").strip()).strip("-")
    return (cleaned or "RemoteSupport")[:32]


def build_agent_download_url(
    settings_row: MeshCentralSettings,
    mesh_id: str,
    platform: str = "windows64",
    install_mode: str = "install",
    agent_id_override: Optional[int] = None,
) -> str:
    """Build the upstream MeshCentral `/meshagents` URL for a device group and OS."""
    if not settings_row.server_url:
        raise ValueError("MeshCentral server URL is not configured.")
    spec = AGENT_PLATFORMS.get(platform)
    if not spec:
        raise ValueError(f"Unsupported installer platform: {platform}")
    clean_mesh = normalize_mesh_id(mesh_id)
    base = _base_with_domain(settings_row.server_url, settings_row.domain)
    if platform == "linux":
        return f"{base}meshagents?script=1"

    agent_id = agent_id_override if agent_id_override is not None else spec["agent_id"]
    install_flags = 2 if install_mode == "connect" else 0
    query = urlencode(
        {"id": str(agent_id), "meshid": clean_mesh, "installflags": str(install_flags)},
        safe="@$",
    )
    return f"{base}meshagents?{query}"


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
        script = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'echo "Downloading {brand_name} Remote Support agent..."\n'
            f'TMP_SCRIPT="$(mktemp /tmp/meshinstall.XXXXXX.sh)"\n'
            f'trap \'rm -f "$TMP_SCRIPT"\' EXIT\n'
            f'curl -fsSL{insecure_flag} "{script_url}" -o "$TMP_SCRIPT"\n'
            'chmod 755 "$TMP_SCRIPT"\n'
            f'sudo "$TMP_SCRIPT" "{server_base}" "{clean_mesh}"\n'
        )
        return script.encode("utf-8"), filename, spec["content_type"]

    ssl_param: Any = None if settings_row.verify_ssl else False
    headers: Dict[str, str] = {}
    password = decrypt_secret(settings_row.password_encrypted)
    if settings_row.username and password:
        u_b64 = base64.b64encode(settings_row.username.strip().encode("utf-8")).decode("ascii")
        p_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
        headers["x-meshauth"] = f"{u_b64},{p_b64}"

    agent_ids = [spec["agent_id"]]
    if spec.get("fallback_agent_id"):
        agent_ids.append(spec["fallback_agent_id"])

    last_error = "Could not fetch installer from MeshCentral."
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        for aid in agent_ids:
            url = build_agent_download_url(
                settings_row,
                clean_mesh,
                platform=platform,
                install_mode=install_mode,
                agent_id_override=aid,
            )
            async with session.get(url, headers=headers, ssl=ssl_param) as resp:
                if resp.status == 200:
                    payload = await resp.read()
                    if payload:
                        return payload, filename, spec["content_type"]
                    last_error = "MeshCentral returned an empty installer file."
                else:
                    last_error = f"MeshCentral returned HTTP {resp.status} while fetching the client installer."

    raise RuntimeError(last_error)
