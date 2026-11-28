"""Client subdomain detection and company autofill for the guest ticket portal.

Client companies can point a subdomain (e.g. companya.hubconnect.co.za) at CEM.
We detect that host, resolve it to a Company name when possible, and feed the
guest ticket form so clients land directly on a clean log-ticket experience.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from typing import Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.company import Company

logger = logging.getLogger(__name__)

# Reserved labels that are infrastructure / product hosts, never client portals.
RESERVED_SUBDOMAINS = {
    "tickets", "ticket", "www", "app", "crm", "cem", "admin", "api", "mail",
    "smtp", "imap", "pop", "remote", "mesh", "portal", "support", "status", "docs",
}

# Common multi-label public suffixes we may be deployed under. Checked longest-first.
_MULTI_LABEL_SUFFIXES = (
    "co.za", "org.za", "net.za", "ac.za", "gov.za", "co.uk", "com.au", "org.uk",
)

_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _clean_host(raw: str) -> str:
    host = (raw or "").strip().lower().rstrip(".")
    if not host:
        return ""
    # Tolerate scheme-prefixed config values (e.g. https://tickets.example.com).
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)
    host = host.split("/", 1)[0].rstrip(".")
    # Strip port for host:port and [ipv6]:port; keep plain ipv6 literal as-is.
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return host[1:end]
    if host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _valid_dns_label(label: str) -> bool:
    return bool(label) and bool(_DNS_LABEL_RE.match(label))


def _root_domain_from_host(host: str) -> str:
    """Return registrable-style root domain, honoring common multi-label suffixes."""
    h = _clean_host(host)
    if not h or _is_ip_literal(h) or h == "localhost":
        return ""
    parts = [p for p in h.split(".") if p]
    if len(parts) < 2:
        return h
    for suffix in _MULTI_LABEL_SUFFIXES:
        suffix_parts = suffix.split(".")
        n = len(suffix_parts)
        if len(parts) >= n + 1 and parts[-n:] == suffix_parts:
            # include one label before suffix
            return ".".join(parts[-(n + 1):])
    # default: last two labels
    return ".".join(parts[-2:])


def extract_client_subdomain(host: str, app_domain: str) -> Optional[str]:
    """Extract a client subdomain label from host, or None if not a client portal host."""
    clean = _clean_host(host)
    if not clean or clean == "localhost" or _is_ip_literal(clean):
        return None

    app_host = _clean_host(app_domain)
    if not app_host:
        app_host = "tickets.hubconnect.co.za"

    primary_host = app_host
    root_domain = _root_domain_from_host(app_host)
    if not root_domain:
        return None

    # Host must be under the same root domain.
    if clean == primary_host or clean == root_domain:
        return None
    if not clean.endswith("." + root_domain):
        return None

    prefix = clean[: -(len(root_domain) + 1)]  # strip .root_domain
    raw_labels = prefix.split(".")
    if any(not p for p in raw_labels):
        return None
    labels = [p for p in raw_labels if p]
    if len(labels) != 1:
        return None

    label = labels[0]
    if not _valid_dns_label(label):
        return None
    if label in RESERVED_SUBDOMAINS:
        return None
    return label


def humanize_subdomain(label: str) -> str:
    """Convert a subdomain label into a readable company name."""
    words = [w for w in re.split(r"[-_]+", (label or "").strip().lower()) if w]
    if not words:
        return ""
    return " ".join(w[:1].upper() + w[1:] if w else w for w in words)


def slugify_company(name: str) -> set[str]:
    """Return normalized slug forms for matching a Company.name to a subdomain label.

    Includes both hyphenated and compact alphanumeric forms so "Company A"
    matches "company-a" and "companya".
    """
    raw = (name or "").strip().lower()
    if not raw:
        return set()
    # Replace non-alnum with separators, collapse
    cleaned = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    compact = re.sub(r"[^a-z0-9]+", "", raw)
    forms = {cleaned, compact}
    # also a form collapsing repeated hyphens already handled; include no-hyphen around single chars
    forms.discard("")
    return forms


def get_request_host(request: Request) -> str:
    """Extract effective host, trusting X-Forwarded-Host only from trusted proxies.

    Trust is based on the direct peer connection. CapturePeerMiddleware stores
    the original peer in request.scope["original_peer"] before ProxyHeadersMiddleware
    rewrites request.client.host from X-Forwarded-For.
    """
    settings = get_settings()
    trusted = {
        h.strip() for h in (getattr(settings, "forwarded_allow_ips", "") or "").split(",") if h.strip()
    }
    peer = ""
    try:
        peer = (request.scope.get("original_peer") or "").strip()
        if not peer:
            peer = (request.client.host or "").strip() if request.client else ""
    except Exception:
        peer = ""

    host = ""
    if peer and peer in trusted:
        fwd = request.headers.get("x-forwarded-host") or request.headers.get("X-Forwarded-Host")
        if fwd:
            # first comma-separated value
            host = fwd.split(",")[0].strip()
    if not host:
        host = request.headers.get("host") or request.headers.get("Host") or ""
    if not host:
        try:
            host = request.url.hostname or ""
        except Exception:
            host = ""
    return _clean_host(host)


async def resolve_subdomain_company(request: Request, db: AsyncSession, workspace_id: int = 1) -> dict:
    """Resolve request host to client subdomain portal context."""
    settings = get_settings()
    host = get_request_host(request)
    label = extract_client_subdomain(host, getattr(settings, "app_domain", "tickets.hubconnect.co.za"))
    if not label:
        return {"is_client_subdomain": False, "subdomain": None, "host": host, "company_name": ""}

    # Try to match a Company in this workspace.
    company_name = humanize_subdomain(label)
    try:
        rows = (await db.execute(
            select(Company.name).where(Company.workspace_id == workspace_id)
        )).all()
        for (name,) in rows:
            if not name:
                continue
            forms = slugify_company(name)
            if label in forms:
                company_name = name
                break
    except Exception:
        # Resolution should never break the guest portal; fall back to humanized label.
        logger.warning("subdomain company resolution failed for host=%s workspace_id=%s", host, workspace_id, exc_info=True)

    return {
        "is_client_subdomain": True,
        "subdomain": label,
        "host": host,
        "company_name": company_name,
    }
