"""Signed outbound webhooks for partner API keys.

Someone else's change to a ticket is delivered to every *other* active key in
the workspace that subscribes to the event and is scoped to that client. The
caller's own key (and any rotated key sharing its endpoint) is skipped, so a
partner cannot accidentally talk to itself in a loop. Delivery is best effort:
validation, transport and database failures are logged, never allowed to break
a ticket route. The address validated here is the address the transport dials,
so a short-TTL name cannot rebinding its way to an internal service.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import ipaddress
import json
import logging
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit

import httpx
from sqlmodel import select

from app.core.api_scopes import can_access_internal_comments, ticket_matches_key_scope
from app.models.api_key import APIKey

logger = logging.getLogger(__name__)

VALID_WEBHOOK_EVENTS = (
    "ticket.created", "ticket.updated", "ticket.commented", "ticket.closed",
)

Resolver = Callable[[str, int], Any]
Sender = Callable[[str, dict, bytes], Optional[Awaitable[None]]]

_BLOCKED_HOSTNAMES = frozenset({
    "localhost", "metadata", "metadata.google.internal", "metadata.goog",
    "instance-data", "instance-data.ec2.internal",
})
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16",
        "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
        "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8",
    )
)
_DEFAULT_PORTS = {"http": 80, "https": 443}

# Keeping the tasks referenced prevents them being garbage-collected mid-flight.
_DELIVERIES: set[asyncio.Task] = set()


def _safe_url(url: Optional[str]) -> str:
    """Log a URL without credentials, query tokens or fragments."""
    try:
        parts = urlsplit(url or "")
        host = parts.hostname or "<invalid>"
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{host}{port}"
    except Exception:
        return "<invalid webhook url>"


def _endpoint_key(url: Optional[str]):
    """Normalised endpoint identity for loop prevention (query tokens ignored)."""
    try:
        parts = urlsplit(url or "")
        return (parts.scheme.lower(), (parts.hostname or "").lower().rstrip("."),
                parts.port, (parts.path or "").rstrip("/"))
    except Exception:
        return (url or "",)


def _blocked_ip(address: Any) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if not ip.is_global:
        return True
    return any(ip in network for network in _BLOCKED_NETWORKS)


def validate_webhook_url(url: str, *, resolver: Optional[Resolver] = None) -> str:
    """Return a safe http(s) URL, or raise ``ValueError``.

    DNS is resolved here (with an injectable resolver for tests) so a hostname
    pointing at loopback, RFC1918, link-local/metadata or any other non-public
    address is refused before a request is made. ``_send`` reuses those exact
    addresses rather than resolving a second time, so validation cannot be
    defeated by DNS rebinding.
    """
    if not isinstance(url, str) or not (text := url.strip()):
        raise ValueError("Webhook URL is required")
    try:
        parts = urlsplit(text)
        if parts.scheme.lower() not in _DEFAULT_PORTS or not parts.hostname:
            raise ValueError("Webhook URL must be http or https")
        if parts.username or parts.password:
            raise ValueError("Webhook URL must not contain credentials")
        port = parts.port or _DEFAULT_PORTS[parts.scheme.lower()]
        host = parts.hostname.strip(".").lower()
        if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
            raise ValueError("Webhook URL host is not allowed")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if _blocked_ip(literal):
                raise ValueError("Webhook URL host is not public")
        else:
            lookup = resolver or (lambda name, number: socket.getaddrinfo(
                name, number, type=socket.SOCK_STREAM))
            addresses = lookup(host, port) or ()
            if not addresses or any(_blocked_ip(info[-1][0]) for info in addresses):
                raise ValueError("Webhook URL resolves to a private address")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Webhook URL could not be validated") from exc
    return text


def sign_webhook_payload(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256 over ``timestamp + '.' + raw body``, as documented for partners."""
    signed = f"{timestamp}.".encode() + body
    return "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def _utc(value) -> Optional[str]:
    """Same wire format as the REST API: UTC timestamp with an explicit offset."""
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.isoformat()
    return value.isoformat()


def _subscribed(events: Optional[str], event: str) -> bool:
    configured = [value.strip().lower() for value in (events or "").split(",") if value.strip()]
    return not configured or event in configured


def _ticket_data(ticket, comment=None) -> dict:
    data = {
        "id": getattr(ticket, "id", None),
        "ticket_number": getattr(ticket, "ticket_number", None),
        "subject": getattr(ticket, "subject", None),
        "status": getattr(ticket, "status", None),
        "priority": getattr(ticket, "priority", None),
        "category": getattr(ticket, "category", None),
        "external_ref": getattr(ticket, "external_ref", None),
        "guest_company": getattr(ticket, "guest_company", None),
        "guest_email": getattr(ticket, "guest_email", None),
        "updated_at": _utc(getattr(ticket, "updated_at", None)),
        "closed_at": _utc(getattr(ticket, "closed_at", None)),
        "close_reason": getattr(ticket, "closing_notes", None),
        "coverage": getattr(ticket, "close_type", None),
        "billing": {
            f"billable_{name}": getattr(ticket, f"billable_{name}", None)
            for name in ("traveling", "labour_onsite", "remote_labour", "equipment_used")
        },
    }
    if comment is not None:
        data["comment"] = {
            "id": getattr(comment, "id", None),
            "body": getattr(comment, "content", None),
            "is_internal": bool(getattr(comment, "is_internal", False)),
            "created_at": _utc(getattr(comment, "created_at", None)),
        }
    return data


def _body(event: str, ticket, comment, delivery_id: str, timestamp: str) -> bytes:
    payload = {
        "event": event,
        "timestamp": timestamp,
        "delivery_id": delivery_id,
        "data": _ticket_data(ticket, comment),
    }
    return json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")


def _headers(key: APIKey, event: str, body: bytes, delivery_id: str, timestamp: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-CEM-Event": event,
        "X-CEM-Delivery-Id": delivery_id,
        "X-CEM-Timestamp": timestamp,
        "User-Agent": "CEM-Partner-Webhook/1.0",
    }
    if key.webhook_secret:
        headers["X-CEM-Signature"] = sign_webhook_payload(key.webhook_secret, timestamp, body)
    return headers


async def _resolve(host: str, port: int, resolver: Optional[Resolver]) -> list:
    """Resolve off the event loop; production uses the loop's async DNS."""
    if resolver is not None:
        return await asyncio.to_thread(resolver, host, port)
    return await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)


async def _send(url: str, headers: dict, body: bytes, resolver: Optional[Resolver]) -> None:
    """One delivery, pinned to the IP that passed validation.

    httpx connects to the literal address while the original hostname is sent
    as ``Host``/SNI. This closes the validate-then-resolve-again rebinding gap.
    Never raises: a broken partner must not cost a ticket update.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname.strip(".").lower()
        port = parts.port or _DEFAULT_PORTS[parts.scheme.lower()]
        addresses = await _resolve(host, port, resolver)
        validate_webhook_url(url, resolver=lambda *_: addresses)
        address = ipaddress.ip_address(addresses[0][-1][0])
        literal = f"[{address}]" if address.version == 6 else str(address)
        authority = literal if not parts.port else f"{literal}:{parts.port}"
        target = f"{parts.scheme.lower()}://{authority}{parts.path or '/'}"
        if parts.query:
            target += f"?{parts.query}"
        request_headers = dict(headers)
        host_header = f"[{host}]" if ":" in host else host      # RFC 7230 IPv6 literal
        request_headers["Host"] = host_header if not parts.port else f"{host_header}:{parts.port}"
        extensions = {"sni_hostname": host} if parts.scheme.lower() == "https" else None
        async with httpx.AsyncClient(timeout=5.0) as client:
            request = client.build_request("POST", target, content=body,
                                           headers=request_headers, extensions=extensions)
            response = await client.send(request)
            if response.status_code >= 400:
                logger.warning("Partner webhook %s returned %s", _safe_url(url), response.status_code)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Partner webhook %s failed: %s", _safe_url(url), type(exc).__name__)


async def dispatch_ticket_webhook(
    db,
    workspace_id: int,
    event: str,
    ticket,
    *,
    comment=None,
    source_api_key_id: Optional[int] = None,
    sender: Optional[Sender] = None,
    resolver: Optional[Resolver] = None,
) -> list[dict]:
    """Notify every other subscribed API key scoped to this ticket.

    ``sender`` is a test seam: when supplied it is awaited instead of spawning
    a background httpx delivery. URL validation happens before either path, so
    tests can mock the transport without weakening production SSRF checks.
    """
    if event not in VALID_WEBHOOK_EVENTS:
        return []
    ticket_workspace = getattr(ticket, "workspace_id", None)
    if ticket_workspace is not None and ticket_workspace != workspace_id:
        return []
    try:
        rows = (await db.execute(
            select(APIKey).where(
                APIKey.workspace_id == workspace_id,
                APIKey.is_active == True,  # noqa: E712
                APIKey.webhook_url.is_not(None),
                APIKey.webhook_url != "",
            )
        )).scalars().all()
    except Exception as exc:
        logger.warning("Could not load partner webhooks: %s", exc)
        return []

    source_url = None
    if source_api_key_id is not None:
        source_url = next((k.webhook_url for k in rows if k.id == source_api_key_id), None)

    timestamp = str(int(time.time()))
    deliveries = []
    for key in rows:
        if source_api_key_id is not None and key.id == source_api_key_id:
            continue                                      # loop safety
        if (source_url and key.webhook_url
                and _endpoint_key(key.webhook_url) == _endpoint_key(source_url)):
            continue                                      # rotated key, same endpoint
        if not _subscribed(key.webhook_events, event):
            continue
        if not ticket_matches_key_scope(
            key, guest_company=getattr(ticket, "guest_company", None),
            guest_email=getattr(ticket, "guest_email", None),
        ):
            continue                                      # never another client's ticket
        if (event == "ticket.commented" and comment is not None
                and getattr(comment, "is_internal", False)
                and not can_access_internal_comments(key)):
            continue
        try:
            url = await asyncio.to_thread(validate_webhook_url, key.webhook_url, resolver=resolver)
        except ValueError as exc:
            logger.warning("Skipping webhook for key %s: %s", key.id, exc)
            continue
        if not key.webhook_secret:
            # Every delivery must carry a verifiable signature; an unsigned
            # endpoint is worse than no endpoint at all.
            logger.warning("Skipping webhook for key %s: no signing secret configured", key.id)
            continue

        delivery_id = str(uuid.uuid4())
        body = _body(event, ticket, comment, delivery_id, timestamp)
        headers = _headers(key, event, body, delivery_id, timestamp)
        deliveries.append({"url": url, "headers": headers, "body": body, "api_key_id": key.id})
        if sender is not None:
            try:
                result = sender(url, headers, body)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.warning("Partner webhook %s failed: %s", _safe_url(url), type(exc).__name__)
        else:
            task = asyncio.create_task(_send(url, headers, body, resolver))
            _DELIVERIES.add(task)
            task.add_done_callback(_DELIVERIES.discard)
    return deliveries
