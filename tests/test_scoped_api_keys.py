"""Scoped API keys: permission scopes and strict client/email ticket isolation.

Legacy keys (no scopes, no client filters) keep the old full-workspace access;
partner keys may only touch tickets belonging to their own company/email/domain.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from app.api.routes import external
from app.core.api_scopes import (
    API_SCOPE_PRESETS,
    VALID_API_SCOPES,
    can_access_internal_comments,
    has_scope,
    has_ticket_filter,
    parse_scopes,
    require_scope,
    ticket_matches_key_scope,
    ticket_scope_sql_filter,
)
from app.core.database import API_KEY_SCOPE_COLUMNS, get_session
from app.models.api_key import APIKey
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.ticket import Ticket, TicketComment, TicketHistory, TicketTimeEntry
from app.models.user import User
from app.models.workspace import Workspace

KEY_LEGACY = "cem_legacy0123456789abcd"
KEY_PARTNER = "cem_partner0123456789abc"
KEY_EMAIL = "cem_emailkey0123456789ab"
KEY_DOMAIN = "cem_domainkey0123456789a"
KEY_READONLY = "cem_readonly0123456789ab"
KEY_INTERNAL = "cem_internal0123456789ab"
KEY_WEBHOOK = "cem_webhook0123456789ab"
KEY_TIME = "cem_timekey0123456789ab"
KEY_MULTI = "cem_multikey0123456789ab"
NOW = datetime.utcnow().replace(microsecond=0)

ALL_API_SCOPES = {
    "tickets:read", "tickets:create", "tickets:update", "tickets:comment",
    "tasks:read", "tasks:write", "projects:read", "users:read", "time:read", "overview:read",
}


def _key(raw: str, workspace_id: int, name: str, **fields) -> APIKey:
    return APIKey(
        workspace_id=workspace_id, name=name, key_prefix=raw[:12],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(), **fields,
    )


def _key_row(**fields) -> APIKey:
    """A detached key row for the pure helper tests."""
    return APIKey(workspace_id=1, name="k", key_prefix="cem_x", key_hash="h", **fields)


def _headers(key: str) -> dict:
    return {"X-API-Key": key}


# ---------------------------------------------------------------------------
# Pure scope helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [None, "", "   ", " , , "])
def test_parse_scopes_none_means_legacy_full_access(raw):
    assert parse_scopes(raw) is None


def test_parse_scopes_normalizes_tokens():
    assert parse_scopes(" Tickets:Read , tickets:create ,, ") == frozenset(
        {"tickets:read", "tickets:create"})


def test_scope_constants_include_all_canonical_scopes_and_presets():
    assert ALL_API_SCOPES <= set(VALID_API_SCOPES)
    assert set(API_SCOPE_PRESETS) >= {"full", "partner_tickets", "readonly_tickets"}
    assert parse_scopes(API_SCOPE_PRESETS["partner_tickets"]) == frozenset({
        "tickets:read", "tickets:create", "tickets:update", "tickets:comment"})
    assert parse_scopes(API_SCOPE_PRESETS["full"]) is None


def test_has_scope_and_require_scope():
    legacy, scoped = _key_row(), _key_row(scopes="tickets:read, tickets:create")
    for scope in ALL_API_SCOPES:
        assert has_scope(legacy, scope) is True
        require_scope(legacy, scope)          # a legacy key is never refused
    assert has_scope(scoped, "tickets:read") is True
    assert has_scope(scoped, "tickets:create") is True
    assert has_scope(scoped, "tickets:update") is False
    with pytest.raises(HTTPException) as exc:
        require_scope(scoped, "users:read")
    assert exc.value.status_code == 403
    assert exc.value.detail == "API key lacks required scope: users:read"


def test_can_access_internal_comments():
    assert can_access_internal_comments(_key_row()) is True
    assert can_access_internal_comments(_key_row(allow_internal_comments=False)) is True
    assert can_access_internal_comments(_key_row(scopes="tickets:read")) is False
    assert can_access_internal_comments(
        _key_row(scopes="tickets:read", allow_internal_comments=True)) is True


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (_key_row(), False),
        (_key_row(allowed_company="   "), False),
        (_key_row(allowed_company="Mobilis"), True),
        (_key_row(allowed_email="leon@mobilis.co.za"), True),
        (_key_row(allowed_email_domain=" mobilis.co.za "), True),
        (_key_row(scopes="tickets:read"), False),
    ],
)
def test_has_ticket_filter(key, expected):
    assert has_ticket_filter(key) is expected


def test_ticket_matches_key_scope_company_email_and_domain():
    company = _key_row(allowed_company=" Mobilis , Acme ")
    assert ticket_matches_key_scope(company, guest_company="  mobilis ", guest_email=None)
    assert ticket_matches_key_scope(company, guest_company="ACME", guest_email=None)
    assert not ticket_matches_key_scope(company, guest_company="Other Co", guest_email=None)

    email = _key_row(allowed_email="leon@mobilis.co.za, other@acme.test")
    assert ticket_matches_key_scope(email, guest_company=None, guest_email="LEON@MOBILIS.CO.ZA")
    assert ticket_matches_key_scope(email, guest_company=None, guest_email=" other@acme.test ")
    assert not ticket_matches_key_scope(email, guest_company=None, guest_email="boss@acme.test")

    domain = _key_row(allowed_email_domain="@Mobilis.co.za")
    assert ticket_matches_key_scope(domain, guest_company=None, guest_email="a@MOBILIS.CO.ZA")
    assert not ticket_matches_key_scope(domain, guest_company=None,
                                        guest_email="a@evil-mobilis.co.za")
    assert not ticket_matches_key_scope(domain, guest_company=None,
                                        guest_email="a@mobilis.co.za.attacker.test")

    both = _key_row(allowed_company="Mobilis", allowed_email_domain="mobilis.co.za")
    assert ticket_matches_key_scope(both, guest_company="Mobilis", guest_email="x@other.test")
    assert ticket_matches_key_scope(both, guest_company="Different", guest_email="x@mobilis.co.za")
    assert not ticket_matches_key_scope(both, guest_company="Different", guest_email="x@other.test")

    unfiltered = _key_row()
    assert ticket_matches_key_scope(unfiltered, guest_company=None, guest_email=None)
    assert ticket_scope_sql_filter(unfiltered) is None


@pytest.mark.asyncio
async def test_sql_filter_is_an_exact_mirror_of_the_python_check(tmp_path):
    import app.models  # noqa: F401  (register all tables)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mirror.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    rows = {
        "TKT-2026-10000": ("Mobilis", "leon@mobilis.co.za"),
        "TKT-2026-10001": ("  MOBILIS  ", "staff@mobilis.co.za"),
        "TKT-2026-10002": ("Acme", "jane@acme.test"),
        "TKT-2026-10003": ("Acme", "LEON@MOBILIS.CO.ZA"),
        "TKT-2026-10004": ("Other Co", None),
        "TKT-2026-10005": (None, None),
        "TKT-2026-20000": ("  Mobilis\t", "leon@mobilis.co.za"),   # SQLite trim is space-only
        "TKT-2026-20001": ("Mobilis", "a@mobilis.co.za@b.test"),   # '@' splits on the last one
    }
    for number, (company, email) in rows.items():
        db.add(Ticket(ticket_number=number, subject=number, workspace_id=1,
                      is_guest=True, guest_company=company, guest_email=email))
    await db.commit()


    keys = [
        _key_row(allowed_company="Mobilis"),
        _key_row(allowed_email="leon@mobilis.co.za"),
        _key_row(allowed_email_domain=" mobilis.co.za "),
        _key_row(allowed_company="Mobilis", allowed_email_domain="mobilis.co.za"),
        _key_row(allowed_email_domain="mobilis.co.za%"),   # LIKE wildcards must be escaped
        _key_row(allowed_email_domain="mobilis_co.za"),    # '_' is a wildcard too
        _key_row(allowed_email_domain="mobilis\\co.za"),   # backslash
        _key_row(),
    ]
    try:
        for key in keys:
            query = select(Ticket.ticket_number)
            expression = ticket_scope_sql_filter(key)
            if expression is not None:
                query = query.where(expression)
            found = set((await db.execute(query)).scalars().all())
            expected = {
                number for number, (company, email) in rows.items()
                if ticket_matches_key_scope(key, guest_company=company, guest_email=email)
            }
            assert found == expected
    finally:
        await db.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# API integration fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def api(tmp_path):
    import app.models  # noqa: F401  (register every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    mobilis_ws, other_ws = Workspace(name="Mobilis", timezone="UTC"), Workspace(name="Other Co")
    db.add_all([mobilis_ws, other_ws])
    await db.flush()
    ada = User(username="ada", hashed_password="x", is_admin=True, workspace_id=mobilis_ws.id)
    olive = User(username="olive", hashed_password="x", workspace_id=other_ws.id)
    db.add_all([ada, olive])
    await db.flush()
    project = Project(name="Mobilis project", workspace_id=mobilis_ws.id, owner_id=ada.id)
    db.add(project)
    await db.flush()
    db.add(Task(title="Internal work", project_id=project.id, creator_id=ada.id,
                status=TaskStatus.todo))
    tickets = {
        "mobilis": Ticket(ticket_number="TKT-2026-10001", subject="Printer down",
                          workspace_id=mobilis_ws.id, is_guest=True, guest_company="Mobilis",
                          guest_email="leon@mobilis.co.za", created_at=NOW, updated_at=NOW),
        "mobilis_variant": Ticket(ticket_number="TKT-2026-10002", subject="Second printer",
                                  workspace_id=mobilis_ws.id, is_guest=True,
                                  guest_company="  MOBILIS  ", guest_email="staff@mobilis.co.za",
                                  created_at=NOW, updated_at=NOW),
        "upper_email": Ticket(ticket_number="TKT-2026-10003", subject="Uppercase mail",
                              workspace_id=mobilis_ws.id, is_guest=True, guest_company="Acme",
                              guest_email="LEON@MOBILIS.CO.ZA", created_at=NOW, updated_at=NOW),
        "acme": Ticket(ticket_number="TKT-2026-10004", subject="Not yours",
                       workspace_id=mobilis_ws.id, is_guest=True, guest_company="Acme",
                       guest_email="jane@acme.test", created_at=NOW, updated_at=NOW),
        "foreign": Ticket(ticket_number="TKT-2026-10005", subject="Other workspace",
                          workspace_id=other_ws.id, is_guest=True, guest_company="Mobilis",
                          guest_email="leon@mobilis.co.za", created_at=NOW, updated_at=NOW),
    }
    db.add_all(tickets.values())
    await db.flush()
    db.add_all([
        TicketComment(ticket_id=tickets["mobilis"].id, user_id=None, content="Mobilis public",
                      created_at=NOW + timedelta(minutes=1)),
        TicketComment(ticket_id=tickets["mobilis"].id, user_id=ada.id, content="Mobilis private",
                      is_internal=True, created_at=NOW + timedelta(minutes=2)),
        TicketComment(ticket_id=tickets["mobilis_variant"].id, user_id=None,
                      content="Mobilis second", created_at=NOW + timedelta(minutes=3)),
        TicketComment(ticket_id=tickets["upper_email"].id, user_id=None,
                      content="Uppercase mail", created_at=NOW + timedelta(minutes=4)),
        TicketComment(ticket_id=tickets["acme"].id, user_id=None, content="Acme secret",
                      created_at=NOW + timedelta(minutes=5)),
        TicketComment(ticket_id=tickets["foreign"].id, user_id=olive.id, content="Foreign secret",
                      created_at=NOW + timedelta(minutes=6)),
    ])
    keys = {
        "legacy": _key(KEY_LEGACY, mobilis_ws.id, "Legacy full access"),
        "partner": _key(KEY_PARTNER, mobilis_ws.id, "Mobilis partner",
                        scopes="tickets:read,tickets:create,tickets:update",
                        allowed_company="Mobilis"),
        "email": _key(KEY_EMAIL, mobilis_ws.id, "Leon",
                      scopes="tickets:read", allowed_email="leon@mobilis.co.za"),
        "domain": _key(KEY_DOMAIN, mobilis_ws.id, "Mobilis domain",
                       scopes="tickets:read,tickets:create",
                       allowed_email_domain="mobilis.co.za"),
        "readonly": _key(KEY_READONLY, mobilis_ws.id, "Read only", scopes="tickets:read"),
        "internal": _key(KEY_INTERNAL, mobilis_ws.id, "Internal notes allowed",
                         scopes="tickets:read", allowed_company="Mobilis",
                         allow_internal_comments=True),
        "webhook": _key(KEY_WEBHOOK, mobilis_ws.id, "Webhook key", scopes="tickets:read",
                        allowed_company="Mobilis", webhook_url="https://partner.test/hook",
                        webhook_secret="secret-that-must-never-be-returned",
                        webhook_events="ticket.created,ticket.updated"),
        "time": _key(KEY_TIME, mobilis_ws.id, "Time reader", scopes="time:read",
                     allowed_company="Mobilis"),
        "multi": _key(KEY_MULTI, mobilis_ws.id, "Two clients",
                      scopes="tickets:read,tickets:create", allowed_company="Mobilis, Acme"),
    }
    db.add_all(keys.values())
    db.add_all([
        # Deliberately on the *second* Mobilis ticket so ticket 10001's activity
        # timestamp in the metadata test is driven only by its comments.
        TicketTimeEntry(ticket_id=tickets["mobilis_variant"].id, user_id=ada.id,
                        started_at=NOW, ended_at=NOW + timedelta(minutes=10)),
        TicketTimeEntry(ticket_id=tickets["acme"].id, user_id=ada.id,
                        started_at=NOW, ended_at=NOW + timedelta(minutes=10)),
    ])
    await db.commit()

    app = FastAPI()
    app.include_router(external.router, prefix="/api")

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                         headers=_headers(KEY_LEGACY))
    yield client, tickets, keys, mobilis_ws
    await client.aclose()
    await db.close()
    await engine.dispose()


async def _numbers(client, key, query="") -> list[str]:
    response = await client.get(f"/api/v1/tickets{query}", headers=_headers(key))
    assert response.status_code == 200, response.text
    return [t["ticket_number"] for t in response.json()["tickets"]]


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_key_keeps_full_workspace_access(api):
    client, tickets, _, _ = api
    assert set(await _numbers(client, KEY_LEGACY)) == {
        "TKT-2026-10001", "TKT-2026-10002", "TKT-2026-10003", "TKT-2026-10004"}
    with_notes = (await client.get(
        "/api/v1/tickets/TKT-2026-10001?include_internal=true",
    )).json()
    assert "Mobilis private" in [c["body"] for c in with_notes["comments"]]
    assert (await client.get("/api/v1/tickets/TKT-2026-10005")).status_code == 404
    created = await client.post("/api/v1/tickets", json={"subject": "From the monitor"})
    assert created.status_code == 201
    assert created.json()["guest_company"] is None
    for path in ("/api/v1/users", "/api/v1/tasks", "/api/v1/projects",
                 "/api/v1/time-entries", "/api/v1/overview"):
        assert (await client.get(path)).status_code == 200, path
    me = (await client.get("/api/v1/me")).json()
    assert me["scopes"] == ["*"]


# ---------------------------------------------------------------------------
# Company-scoped partner key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partner_key_sees_only_its_company_even_with_client_filters(api):
    client, _, _, _ = api
    seen = await _numbers(client, KEY_PARTNER)
    assert set(seen) == {"TKT-2026-10001", "TKT-2026-10002"}
    assert await _numbers(client, KEY_PARTNER, "?guest_company=Acme") == []
    assert await _numbers(client, KEY_PARTNER, "?guest_email=jane@acme.test") == []
    assert set(await _numbers(client, KEY_PARTNER, "?guest_company=mobilis")) == set(seen)
    assert await _numbers(client, KEY_PARTNER, "?status=open") == seen


@pytest.mark.asyncio
async def test_partner_cannot_read_or_patch_other_clients_tickets(api):
    client, _, _, _ = api
    for method, url, body in (
        ("get", "/api/v1/tickets/TKT-2026-10004", None),
        ("patch", "/api/v1/tickets/TKT-2026-10004", {"status": "closed"}),
        ("get", "/api/v1/tickets/TKT-2026-10005", None),     # other workspace
        ("patch", "/api/v1/tickets/TKT-2026-10003", {}),     # Mobilis domain, other company
    ):
        response = await client.request(method, url, headers=_headers(KEY_PARTNER), json=body)
        assert response.status_code == 404, (method, url, response.text)
        assert response.json()["detail"] == "Ticket not found"
    own = await client.patch("/api/v1/tickets/TKT-2026-10001", headers=_headers(KEY_PARTNER),
                             json={"status": "in_progress"})
    assert own.status_code == 200, own.text
    assert own.json()["status"] == "in_progress"
    assert (await client.get("/api/v1/tickets/TKT-2026-10001",
                             headers=_headers(KEY_PARTNER))).status_code == 200


@pytest.mark.asyncio
async def test_partner_create_autofills_company_and_rejects_other_clients(api):
    client, _, _, _ = api
    made = await client.post("/api/v1/tickets", headers=_headers(KEY_PARTNER),
                             json={"subject": "Partner ticket"})
    assert made.status_code == 201, made.text
    assert made.json()["guest_company"] == "Mobilis"
    assert await _numbers(client, KEY_PARTNER) and made.json()["ticket_number"] in \
        await _numbers(client, KEY_PARTNER)
    refused = await client.post("/api/v1/tickets", headers=_headers(KEY_PARTNER),
                                json={"subject": "Not allowed", "guest_company": "Other Co"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "API key is restricted to specific client/email scope"
    assert "Not allowed" not in [x["subject"] for x in (
        (await client.get("/api/v1/tickets", headers=_headers(KEY_LEGACY))).json()["tickets"])]


@pytest.mark.asyncio
async def test_partner_comments_are_public_and_company_scoped(api):
    client, _, _, _ = api
    page = (await client.get("/api/v1/comments?limit=100", headers=_headers(KEY_PARTNER))).json()
    assert {c["body"] for c in page["comments"]} == {"Mobilis public", "Mobilis second"}
    with_notes = (await client.get("/api/v1/comments?include_internal=true&limit=100",
                                   headers=_headers(KEY_PARTNER))).json()
    assert {c["body"] for c in with_notes["comments"]} == {"Mobilis public", "Mobilis second"}
    assert all(not c["is_internal"] for c in with_notes["comments"])
    detail = (await client.get("/api/v1/tickets/TKT-2026-10001?include_internal=true",
                               headers=_headers(KEY_PARTNER))).json()
    assert "Mobilis private" not in [c["body"] for c in detail["comments"]]


@pytest.mark.asyncio
async def test_key_with_explicit_internal_comment_permission(api):
    client, _, _, _ = api
    normal = (await client.get("/api/v1/comments?include_internal=true&limit=100",
                               headers=_headers(KEY_PARTNER))).json()
    allowed = (await client.get("/api/v1/comments?include_internal=true&limit=100",
                                headers=_headers(KEY_INTERNAL))).json()
    assert "Mobilis private" in [c["body"] for c in allowed["comments"]]
    assert "Mobilis private" not in [c["body"] for c in normal["comments"]]
    # Even the permitted key stays inside its company scope.
    assert "Acme secret" not in [c["body"] for c in allowed["comments"]]


@pytest.mark.asyncio
async def test_partner_key_is_denied_every_non_ticket_resource(api):
    client, _, _, _ = api
    for path in ("/api/v1/users", "/api/v1/tasks", "/api/v1/projects",
                 "/api/v1/time-entries", "/api/v1/overview"):
        response = await client.get(path, headers=_headers(KEY_PARTNER))
        assert response.status_code == 403, (path, response.text)
    for path in ("/api/v1/tasks/archive-done", "/api/v1/tasks/1/archive"):
        response = await client.post(path, headers=_headers(KEY_PARTNER))
        assert response.status_code == 403, (path, response.text)


@pytest.mark.asyncio
async def test_me_returns_safe_key_metadata_without_secrets(api):
    client, _, _, _ = api
    me = (await client.get("/api/v1/me", headers=_headers(KEY_PARTNER))).json()
    assert me["key_name"] == "Mobilis partner"
    assert me["key_prefix"] == KEY_PARTNER[:12]
    assert me["scopes"] == ["tickets:create", "tickets:read", "tickets:update"]
    assert me["allowed_company"] == "Mobilis"
    assert me["allowed_email"] is None and me["allowed_email_domain"] is None
    assert me["allow_internal_comments"] is False
    assert me["webhook_configured"] is False
    assert "key_hash" not in me and "webhook_secret" not in me

    hooked = (await client.get("/api/v1/me", headers=_headers(KEY_WEBHOOK))).json()
    assert hooked["webhook_configured"] is True
    assert hooked["webhook_events"] == "ticket.created,ticket.updated"
    assert "secret-that-must-never-be-returned" not in str(hooked)
    assert "webhook_secret" not in hooked and "key_hash" not in hooked
    assert hooked["workspace"]["name"] == "Mobilis"


# ---------------------------------------------------------------------------
# Email/domain scoping and permission-only scopes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_scoped_key_matches_only_its_address_case_insensitively(api):
    client, _, _, _ = api
    assert set(await _numbers(client, KEY_EMAIL)) == {"TKT-2026-10001", "TKT-2026-10003"}
    page = (await client.get("/api/v1/comments?limit=100", headers=_headers(KEY_EMAIL))).json()
    assert {c["body"] for c in page["comments"]} == {"Mobilis public", "Uppercase mail"}
    assert (await client.get("/api/v1/tickets/TKT-2026-10002",
                             headers=_headers(KEY_EMAIL))).status_code == 404


@pytest.mark.asyncio
async def test_domain_scoped_key_matches_every_address_in_the_domain(api):
    client, _, _, _ = api
    assert set(await _numbers(client, KEY_DOMAIN)) == {
        "TKT-2026-10001", "TKT-2026-10002", "TKT-2026-10003"}
    assert (await client.get("/api/v1/tickets/TKT-2026-10004",
                             headers=_headers(KEY_DOMAIN))).status_code == 404
    created = await client.post("/api/v1/tickets", headers=_headers(KEY_DOMAIN),
                                json={"subject": "Domain partner", "guest_email": "x@mobilis.co.za"})
    assert created.status_code == 201


@pytest.mark.asyncio
async def test_readonly_key_can_read_clients_but_cannot_write(api):
    client, _, _, _ = api
    assert len(await _numbers(client, KEY_READONLY)) == 4
    assert (await client.post("/api/v1/tickets", headers=_headers(KEY_READONLY),
                              json={"subject": "Denied"})).status_code == 403
    assert (await client.patch("/api/v1/tickets/TKT-2026-10001", headers=_headers(KEY_READONLY),
                               json={"status": "closed"})).status_code == 403
    assert (await client.get("/api/v1/users", headers=_headers(KEY_READONLY))).status_code == 403


# ---------------------------------------------------------------------------
# Metadata, time entries and auto-fill edge cases (regressions)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_with_comments_never_exposes_private_notes_or_their_timestamps(api):
    client, _, _, _ = api
    partner = (await client.get("/api/v1/tickets?include=comments&include_internal=true",
                                headers=_headers(KEY_PARTNER))).json()["tickets"]
    mobilis = next(t for t in partner if t["ticket_number"] == "TKT-2026-10001")
    assert [c["body"] for c in mobilis["comments"]] == ["Mobilis public"]
    assert all(not c["is_internal"] for c in mobilis["comments"])
    # The private note was added two minutes after the public reply; a scoped
    # key must still see the public reply's time as the last activity.
    assert mobilis["last_activity_at"].startswith((NOW + timedelta(minutes=1)).isoformat()[:19])

    permitted = (await client.get("/api/v1/tickets?include=comments&include_internal=true",
                                  headers=_headers(KEY_INTERNAL))).json()["tickets"]
    with_notes = next(t for t in permitted if t["ticket_number"] == "TKT-2026-10001")
    assert "Mobilis private" in [c["body"] for c in with_notes["comments"]]
    assert with_notes["last_activity_at"].startswith((NOW + timedelta(minutes=2)).isoformat()[:19])


@pytest.mark.asyncio
async def test_updated_since_does_not_reveal_internal_note_activity(api):
    client, _, _, _ = api
    # Only the private note of TKT-2026-10001 is newer than this cutoff.
    cutoff = (NOW + timedelta(seconds=90)).isoformat()
    seen = set(await _numbers(client, KEY_PARTNER, f"?updated_since={cutoff}"))
    assert "TKT-2026-10001" not in seen
    allowed = (await client.get(f"/api/v1/tickets?updated_since={cutoff}",
                                headers=_headers(KEY_INTERNAL))).json()["tickets"]
    assert "TKT-2026-10001" in {t["ticket_number"] for t in allowed}


@pytest.mark.asyncio
async def test_time_entries_with_client_key_only_expose_that_clients_tickets(api):
    client, _, _, _ = api
    response = await client.get("/api/v1/time-entries?since=2026-01-01&until=2030-01-01",
                                headers=_headers(KEY_TIME))
    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    ticket_numbers = {e["ticket"]["ticket_number"] for e in entries if e["ticket"]}
    assert ticket_numbers == {"TKT-2026-10002"}
    assert not any(e["task"] for e in entries)   # staff tasks are not a client's business

    legacy = (await client.get("/api/v1/time-entries?since=2026-01-01&until=2030-01-01")).json()
    assert "TKT-2026-10004" in {e["ticket"]["ticket_number"] for e in legacy["entries"]
                                if e["ticket"]}


@pytest.mark.asyncio
async def test_create_checks_every_client_identity_even_when_one_is_omitted(api):
    client, _, _, _ = api
    # A domain-only key cannot guess a company name to bypass the email check.
    refused = await client.post("/api/v1/tickets", headers=_headers(KEY_DOMAIN),
                                json={"subject": "No address", "guest_company": "Mobilis"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "API key is restricted to specific client/email scope"

    # Several configured companies are ambiguous: never silently pick one.
    ambiguous = await client.post("/api/v1/tickets", headers=_headers(KEY_MULTI),
                                  json={"subject": "Which client?"})
    assert ambiguous.status_code == 403
    assert "guest_company" not in ambiguous.json()

    explicit = await client.post("/api/v1/tickets", headers=_headers(KEY_MULTI),
                                 json={"subject": "Acme job", "guest_company": "acme"})
    assert explicit.status_code == 201, explicit.text
    assert explicit.json()["guest_company"] == "acme"


# ---------------------------------------------------------------------------
# Startup / PRAGMA migration
# ---------------------------------------------------------------------------

def _legacy_apikey_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            'CREATE TABLE "apikey" ('
            'id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, '
            'name VARCHAR NOT NULL, key_prefix VARCHAR NOT NULL, '
            'key_hash VARCHAR NOT NULL, is_active BOOLEAN NOT NULL DEFAULT 1, '
            'created_at TIMESTAMP)'
        )
        conn.execute(
            "INSERT INTO apikey (workspace_id, name, key_prefix, key_hash) "
            "VALUES (7, 'Old key', 'cem_old', 'old-hash')"
        )
        conn.commit()
    finally:
        conn.close()


def _apikey_columns(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute('PRAGMA table_info("apikey")')]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_init_models_migrates_old_apikey_table_idempotently(tmp_path, monkeypatch):
    import app.core.database as dbmod

    path = tmp_path / "legacy.db"
    _legacy_apikey_db(path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    monkeypatch.setattr(dbmod, "engine", engine)
    try:
        await dbmod.init_models()
        await dbmod.init_models()
    finally:
        await engine.dispose()

    columns = _apikey_columns(path)
    for _, column, _ in API_KEY_SCOPE_COLUMNS:
        assert columns.count(column) == 1, column
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT name, scopes, allow_internal_comments FROM apikey WHERE key_hash = 'old-hash'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("Old key", None, 0)


def test_startup_helper_migrates_old_apikey_table_idempotently(tmp_path):
    from app.core.database import _ensure_api_key_scope_columns

    path = tmp_path / "legacy-startup.db"
    _legacy_apikey_db(path)
    _ensure_api_key_scope_columns(path)
    _ensure_api_key_scope_columns(path)
    columns = _apikey_columns(path)
    for _, column, _ in API_KEY_SCOPE_COLUMNS:
        assert columns.count(column) == 1, column
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("SELECT name FROM apikey WHERE key_hash = 'old-hash'").fetchone()[0] \
            == "Old key"
    finally:
        conn.close()
