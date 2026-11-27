"""Permission scopes and client-identity filters for external API keys.

A key with no scopes is a legacy key: it retains full workspace access. Once
scopes are configured, each endpoint checks the one it needs. Ticket visibility
is narrowed further when the key names a client company, email address or email
domain; the SQL expression and the Python predicate below are deliberately
equivalent so list/detail/create checks cannot disagree.
"""
from __future__ import annotations

import string
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, or_

from app.models.api_key import APIKey
from app.models.ticket import Ticket

VALID_API_SCOPES = frozenset({
    "tickets:read", "tickets:create", "tickets:update", "tickets:comment",
    "tasks:read", "tasks:write", "projects:read", "users:read", "time:read", "overview:read",
})

# Private notes create a history row for staff, but a partner without internal
# note access must not see that row (or the activity time it carries). Mark the
# two writers' rows so the external API can hide them from those keys.
WEB_INTERNAL_NOTE_HISTORY = "Added an internal note"
API_INTERNAL_NOTE_HISTORY_PREFIX = "api-internal:"
INTERNAL_NOTE_HISTORY_MARKERS = (WEB_INTERNAL_NOTE_HISTORY, API_INTERNAL_NOTE_HISTORY_PREFIX)

# Ready-made choices for a partner integration. "full" is the legacy behaviour.
API_SCOPE_PRESETS: dict[str, Optional[str]] = {
    "full": None,
    "partner_tickets": "tickets:read,tickets:create,tickets:update,tickets:comment",
    "readonly_tickets": "tickets:read",
}


def _values(raw: Optional[str]) -> tuple[str, ...]:
    """Trimmed, non-empty comma-separated entries, preserving their original case."""
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def configured_values(raw: Optional[str]) -> tuple[str, ...]:
    """The configured names as typed, for auto-filling a new ticket."""
    return _values(raw)


def parse_scopes(raw: Optional[str]) -> Optional[frozenset[str]]:
    """None (or blank) means a legacy key with unrestricted permissions."""
    values = _values(raw)
    if not values:
        return None
    return frozenset(value.lower() for value in values)


def has_scope(api_key: APIKey, scope: str) -> bool:
    scopes = parse_scopes(api_key.scopes)
    return scopes is None or scope in scopes


def require_scope(api_key: APIKey, scope: str) -> None:
    if not has_scope(api_key, scope):
        raise HTTPException(status_code=403, detail=f"API key lacks required scope: {scope}")


def can_access_internal_comments(api_key: APIKey) -> bool:
    """Legacy keys keep their old behaviour; scoped keys must opt in."""
    return parse_scopes(api_key.scopes) is None or bool(api_key.allow_internal_comments)


def resolve_include_internal(api_key: APIKey, include_internal: Optional[bool]) -> bool:
    """Default a read request's internal-note visibility to the key's setting.

    An explicit query value wins for keys that may read notes at all; keys that
    lack that permission never see them, whatever the caller asks for.
    """
    if not can_access_internal_comments(api_key):
        return False
    if include_internal is not None:
        return bool(include_internal)
    return bool(api_key.allow_internal_comments)


def has_ticket_filter(api_key: APIKey) -> bool:
    return any((_values(api_key.allowed_company), _values(api_key.allowed_email),
                _values(api_key.allowed_email_domain)))


# SQLite's lower(trim(x)) folds ASCII letters and strips literal spaces. Use
# the same normalization in Python so list and single-ticket checks agree.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _identity_key(value: str) -> str:
    return value.strip(" ").translate(_ASCII_LOWER)


def _companies(api_key: APIKey) -> tuple[str, ...]:
    return tuple(_identity_key(value) for value in _values(api_key.allowed_company))


def _emails(api_key: APIKey) -> tuple[str, ...]:
    return tuple(_identity_key(value) for value in _values(api_key.allowed_email))


def _domains(api_key: APIKey) -> tuple[str, ...]:
    domains = []
    for value in _values(api_key.allowed_email_domain):
        domain = _identity_key(value)
        domain = domain[1:] if domain.startswith("@") else domain
        if domain:
            domains.append(domain)
    return tuple(domains)


def _escape_like(value: str) -> str:
    """A configured domain is literal text, not a SQL LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ticket_scope_sql_filter(api_key):
    """A SQLAlchemy filter for tickets belonging to ANY configured identity.

    Company, exact email and email domain are OR-ed: a partner that supplies
    either its company name or an @domain on the ticket is still its own
    client, while every other company/email is excluded.
    """
    parts = []
    companies = _companies(api_key)
    if companies:
        parts.append(func.lower(func.trim(Ticket.guest_company)).in_(companies))
    emails = _emails(api_key)
    if emails:
        parts.append(func.lower(func.trim(Ticket.guest_email)).in_(emails))
    for domain in _domains(api_key):
        parts.append(func.lower(func.trim(Ticket.guest_email)).like(
            f"%@{_escape_like(domain)}", escape="\\"))
    return or_(*parts) if parts else None


def ticket_matches_key_scope(
    api_key: APIKey, *, guest_company: Optional[str], guest_email: Optional[str],
) -> bool:
    """Python mirror of ``ticket_scope_sql_filter`` for single-ticket checks."""
    if not has_ticket_filter(api_key):
        return True
    company = _identity_key(guest_company or "")
    email = _identity_key(guest_email or "")
    if company and company in _companies(api_key):
        return True
    if email and email in _emails(api_key):
        return True
    domain = email.rpartition("@")[2] if "@" in email else ""
    return bool(domain and domain in _domains(api_key))
