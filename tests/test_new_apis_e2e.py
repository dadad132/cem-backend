"""Slice 02: end-to-end verification of the whole new API surface.

Journeys through the API and Web UI use a real FastAPI application
(SessionMiddleware, real form posts and cookies, the real routers and the real
SQLite async session), not direct calls into route functions. The SSRF helper
checks and startup migration are deliberately focused direct checks. It covers:

* the admin UI's one-time API key / webhook secret lifecycle;
* public and internal comments from both the Web UI and the external API;
* two-way ticket updates, filters, isolation and startup migrations;
* signed outbound webhooks, including loop prevention and SSRF rejection;
* scope checks on every /api/v1 endpoint.

External DNS and HTTP transport are the only seams replaced: partner test hosts
resolve to a public IP and deliveries are captured by the dispatcher's own
``sender`` seam, so URL validation, event filtering, payload building and HMAC
signing are all exercised for real.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import socket
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import external
from app.core import api_webhooks, database as dbmod
from app.core.database import get_session
from app.core.security import get_password_hash
from app.models.api_key import APIKey
from app.models.project import Project
from app.models.task import Task
from app.models.ticket import Ticket, TicketComment, TicketHistory, TicketTimeEntry
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes as web

PASSWORD = "Str0ng!PassForTests"
NOW = datetime.utcnow().replace(microsecond=0)
PARTNER_SCOPES = [
    "tickets:read", "tickets:create", "tickets:update", "tickets:comment",
]
ALL_EVENTS = [
    "ticket.created", "ticket.updated", "ticket.commented", "ticket.closed",
]
HOOK_HOSTS = {"hooks.partner.test", "echo.partner.test", "internal.partner.test"}

KEY_LEGACY = "cem_e2elegacy0123456789abcd"
KEY_DENIED = "cem_e2edenied0123456789abc"
KEY_FULL_INTERNAL = "cem_e2efullinternal01234567"
KEY_SCOPED_INTERNAL = "cem_e2escopedint0123456789"
KEY_LISTENER = "cem_e2elistener0123456789ab"
KEY_INTERNAL_LISTENER = "cem_e2einthook0123456789ab"
KEY_OTHER_COMPANY = "cem_e2eotherco0123456789ab"
KEY_EMAIL = "cem_e2eemail0123456789abcd"
KEY_DOMAIN = "cem_e2edomain0123456789ab"
KEY_OTHER_TIME = "cem_e2eothertime0123456789"
KEY_SOURCE = "cem_e2esource0123456789ab"
KEY_CLONE = "cem_e2eclone0123456789ab"
KEY_NOSCOPE = "cem_e2enosscope0123456789a"
KEY_WRONG_SCOPE = "cem_e2ewrongscope01234567"
KEY_TICKETS_CREATE = "cem_e2ecreate0123456789ab"
KEY_TICKETS_UPDATE = "cem_e2eupdate0123456789ab"
KEY_TICKETS_COMMENT = "cem_e2ecomment0123456789a"
KEY_TASKS_READ = "cem_e2etasksread0123456789"
KEY_TASKS_WRITE = "cem_e2etaskswrite01234567"
KEY_PROJECTS_READ = "cem_e2eprojectsread0123456"
KEY_TIME_READ = "cem_e2etimeread0123456789a"
KEY_OVERVIEW_READ = "cem_e2eoverviewread0123456"


def _headers(key: str) -> dict:
    return {"X-API-Key": key}


def _key(raw: str, workspace_id: int, name: str, **fields) -> APIKey:
    return APIKey(
        workspace_id=workspace_id, name=name, key_prefix=raw[:12],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(), **fields,
    )


def _public_resolver(host: str, port: int):
    """Partner names are public in these tests; literal/blocked hosts stay real."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


@pytest.fixture(autouse=True)
def _template_bridge(monkeypatch):
    def render(name, context, *args, **kwargs):
        return Jinja2Templates.TemplateResponse(
            web.templates, context.get("request"), name, context, *args, **kwargs
        )

    monkeypatch.setattr(web, "_original_template_response", render)


@pytest.fixture(autouse=True)
def _partner_dns(monkeypatch):
    """Replace DNS only; the real validator still checks scheme, credentials and answers."""
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if isinstance(host, str) and host.strip(".").lower() in HOOK_HOSTS:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(api_webhooks.socket, "getaddrinfo", getaddrinfo)


def _capturing_dispatcher(deliveries: list, real_dispatch):
    """Real dispatcher + real signing, with httpx replaced by a capture sink."""

    def sender(url, headers, body):
        deliveries.append({"url": url, "headers": headers, "body": body})

    async def dispatch(db, workspace_id, event, ticket, **kwargs):
        kwargs.pop("sender", None)
        return await real_dispatch(
            db, workspace_id, event, ticket, sender=sender,
            resolver=_public_resolver, **kwargs,
        )

    return dispatch


@pytest_asyncio.fixture
async def e2e(tmp_path, monkeypatch):
    import app.models  # noqa: F401  (register every model)

    monkeypatch.chdir(tmp_path)          # the Web UI comment route writes logs
    db_path = tmp_path / "e2e.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(dbmod, "engine", engine)
    await dbmod.init_models()            # real startup PRAGMA migrations

    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    ws = Workspace(name="Mobilis workspace", timezone="UTC")
    other_ws = Workspace(name="Other workspace", timezone="UTC")
    db.add_all([ws, other_ws])
    await db.flush()
    admin = User(username="admin", hashed_password=get_password_hash(PASSWORD),
                 is_admin=True, full_name="Admin Person", profile_completed=True,
                 workspace_id=ws.id)
    staff = User(username="tech", hashed_password=get_password_hash(PASSWORD),
                 full_name="Tech Person", profile_completed=True, workspace_id=ws.id)
    outsider = User(username="outsider", hashed_password=get_password_hash(PASSWORD),
                    is_admin=True, profile_completed=True, workspace_id=other_ws.id)
    db.add_all([admin, staff, outsider])
    await db.flush()
    project = Project(name="Mobilis project", workspace_id=ws.id, owner_id=admin.id)
    db.add(project)
    await db.flush()
    task = Task(title="Partner task", project_id=project.id, creator_id=admin.id,
                status="todo")
    db.add(task)

    tickets = {
        "mobilis": Ticket(
            ticket_number="TKT-2026-30001", subject="Printer down", workspace_id=ws.id,
            is_guest=True, guest_company="Mobilis", guest_email="leon@mobilis.co.za",
            guest_name="Leon", guest_surname="Mobilis", external_ref="MOB-30001",
            status="open", created_at=NOW - timedelta(hours=3),
            updated_at=NOW - timedelta(hours=2),
        ),
        "acme": Ticket(
            ticket_number="TKT-2026-30002", subject="Acme printer", workspace_id=ws.id,
            is_guest=True, guest_company="Acme", guest_email="jane@acme.test",
            status="open", created_at=NOW - timedelta(hours=3),
            updated_at=NOW - timedelta(hours=2),
        ),
        "foreign": Ticket(
            ticket_number="TKT-2026-30003", subject="Other workspace", workspace_id=other_ws.id,
            is_guest=True, guest_company="Mobilis", guest_email="leon@mobilis.co.za",
            status="open", created_at=NOW - timedelta(hours=3),
            updated_at=NOW - timedelta(hours=2),
        ),
    }
    db.add_all(tickets.values())
    await db.flush()
    db.add(TicketTimeEntry(ticket_id=tickets["mobilis"].id, user_id=staff.id,
                           started_at=NOW - timedelta(minutes=30), ended_at=None))

    keys = {
        "legacy": _key(KEY_LEGACY, ws.id, "Legacy full access"),
        "denied": _key(KEY_DENIED, ws.id, "Mobilis partner", scopes=",".join(PARTNER_SCOPES),
                       allowed_company="Mobilis"),
        "full_internal": _key(KEY_FULL_INTERNAL, ws.id, "Full with internal notes",
                              allow_internal_comments=True),
        "scoped_internal": _key(KEY_SCOPED_INTERNAL, ws.id, "Mobilis with internal notes",
                                scopes=",".join(PARTNER_SCOPES), allowed_company="Mobilis",
                                allow_internal_comments=True),
        "listener": _key(KEY_LISTENER, ws.id, "Signed listener", scopes="tickets:read",
                         allowed_company="Mobilis", webhook_url="https://hooks.partner.test/cem",
                         webhook_secret="listener-signing-secret", webhook_events=",".join(ALL_EVENTS)),
        "internal_listener": _key(KEY_INTERNAL_LISTENER, ws.id, "Internal listener",
                                  scopes="tickets:read", allowed_company="Mobilis",
                                  allow_internal_comments=True,
                                  webhook_url="https://internal.partner.test/cem",
                                  webhook_secret="internal-signing-secret",
                                  webhook_events="ticket.commented"),
        "other_company": _key(KEY_OTHER_COMPANY, ws.id, "Acme listener", scopes="tickets:read",
                              allowed_company="Acme", webhook_url="https://hooks.partner.test/acme",
                              webhook_secret="acme-signing-secret", webhook_events=",".join(ALL_EVENTS)),
        "email": _key(KEY_EMAIL, ws.id, "Leon's address", scopes="tickets:read",
                      allowed_email="leon@mobilis.co.za"),
        "domain": _key(KEY_DOMAIN, ws.id, "Mobilis domain", scopes="tickets:read",
                       allowed_email_domain="mobilis.co.za"),
        "other_time": _key(KEY_OTHER_TIME, ws.id, "Acme reader with time:read",
                           scopes="tickets:read,time:read", allowed_company="Acme"),
        "source": _key(KEY_SOURCE, ws.id, "Calling integration", scopes="tickets:read,tickets:create",
                       allowed_company="Mobilis", webhook_url="https://echo.partner.test/cem",
                       webhook_secret="echo-secret", webhook_events=",".join(ALL_EVENTS)),
        "clone": _key(KEY_CLONE, ws.id, "Rotated echo listener", scopes="tickets:read",
                      allowed_company="Mobilis", webhook_url="https://echo.partner.test/cem",
                      webhook_secret="echo-secret", webhook_events=",".join(ALL_EVENTS)),
        "no_scope": _key(KEY_NOSCOPE, ws.id, "No ticket scope", scopes="users:read"),
        "wrong_scope": _key(KEY_WRONG_SCOPE, ws.id, "Read only", scopes="tickets:read"),
        "tickets_create": _key(KEY_TICKETS_CREATE, ws.id, "Create only",
                               scopes="tickets:create", allowed_company="Mobilis"),
        "tickets_update": _key(KEY_TICKETS_UPDATE, ws.id, "Update only",
                               scopes="tickets:update", allowed_company="Mobilis"),
        "tickets_comment": _key(KEY_TICKETS_COMMENT, ws.id, "Comment only",
                                scopes="tickets:comment", allowed_company="Mobilis"),
        "tasks_read": _key(KEY_TASKS_READ, ws.id, "Task reader", scopes="tasks:read"),
        "tasks_write": _key(KEY_TASKS_WRITE, ws.id, "Task writer", scopes="tasks:write"),
        "projects_read": _key(KEY_PROJECTS_READ, ws.id, "Project reader", scopes="projects:read"),
        "time_read": _key(KEY_TIME_READ, ws.id, "Time reader", scopes="time:read"),
        "overview_read": _key(KEY_OVERVIEW_READ, ws.id, "Overview reader", scopes="overview:read"),
    }
    db.add_all(keys.values())
    await db.commit()

    deliveries: list[dict] = []
    real_dispatch = api_webhooks.dispatch_ticket_webhook
    capture = _capturing_dispatcher(deliveries, real_dispatch)
    monkeypatch.setattr(external, "dispatch_ticket_webhook", capture)
    monkeypatch.setattr(web, "dispatch_ticket_webhook", capture)

    app = FastAPI()

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.include_router(external.router, prefix="/api")
    app.include_router(web.router, prefix="/web")
    app.add_middleware(SessionMiddleware, secret_key="cem-e2e-session", same_site="lax")

    async def client_for(key: str | None = None, *, follow_redirects: bool = True) -> AsyncClient:
        headers = _headers(key) if key is not None else {}
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver",
                           headers=headers, follow_redirects=follow_redirects)

    async def login(username: str, *, password: str = PASSWORD) -> AsyncClient:
        client = await client_for()
        response = await client.post("/web/login", data={
            "username": username, "password": password,
        })
        assert response.status_code == 200, response.text
        assert response.url.path in ("/web/dashboard", "/web/projects"), response.url
        return client

    yield SimpleNamespace(
        db=db, engine=engine, app=app, ws=ws, other_ws=other_ws,
        admin=admin, staff=staff, outsider=outsider, tickets=tickets, keys=keys, task=task,
        deliveries=deliveries, client_for=client_for, login=login, db_path=db_path,
    )

    await db.close()
    await engine.dispose()


def _assert_no_secret_in_logs(e2e, *secrets: str) -> None:
    """Credential values must never be written to the route log files."""
    root = e2e.db_path.parent
    log_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*.log") if path.is_file()
    )
    for secret in secrets:
        assert secret and secret not in log_text


def _input_value(html_text: str, element_id: str) -> str:
    match = re.search(
        r'<input\b[^>]*\bid=["\']' + re.escape(element_id) + r'["\'][^>]*>',
        html_text,
    )
    assert match, f"no input#{element_id} in page"
    tag = match.group(0)
    value = re.search(r'\bvalue="([^"]*)"', tag)
    assert value, f"input#{element_id} has no value"
    return html.unescape(value.group(1))


async def _stored_key_values(db, key_id: int):
    """Read columns fresh, avoiding the fixture session's identity map."""
    return (await db.execute(
        select(APIKey.webhook_secret, APIKey.scopes, APIKey.allowed_email_domain,
               APIKey.allow_internal_comments, APIKey.is_active)
        .where(APIKey.id == key_id)
    )).one()


def _key_form(**overrides):
    form = dict(
        name="Mobilis / Leon", access_mode="scoped", scopes=PARTNER_SCOPES,
        allowed_company="Mobilis", allowed_email="", allowed_email_domain="",
        allow_internal_comments="true",
        webhook_url="https://hooks.partner.test/cem", webhook_secret="",
        webhook_events=ALL_EVENTS,
    )
    form.update(overrides)
    return {k: v for k, v in form.items() if v not in ("", [], None)}


async def _create_key_via_ui(client: AsyncClient, **overrides):
    form = _key_form(**overrides)
    response = await client.post("/web/admin/api-keys/create", data=form)
    # The redirect target renders one-time secrets: never echo its body.
    assert response.status_code == 200, f"status={response.status_code}"
    assert response.url.path == "/web/admin/api-keys", response.url
    return response


@pytest.mark.asyncio
async def test_admin_ui_key_lifecycle_over_real_http(e2e):
    """Admin creates, edits, revokes and deletes a key with real form POSTs."""
    admin = await e2e.login("admin")
    created = await _create_key_via_ui(admin, scopes=PARTNER_SCOPES + ["users:read"])
    assert created.headers.get("Cache-Control") == "no-store, private"

    raw_key = _input_value(created.text, "newKeyDisplay")
    webhook_secret = _input_value(created.text, "newWebhookSecretDisplay")
    assert raw_key.startswith("cem_") and len(raw_key) > 30
    assert webhook_secret and webhook_secret != raw_key

    stored = (await e2e.db.execute(
        select(APIKey).where(APIKey.key_prefix == raw_key[:12])
    )).scalar_one()
    assert stored.webhook_secret == webhook_secret
    assert stored.key_hash == hashlib.sha256(raw_key.encode()).hexdigest()

    second = await admin.get("/web/admin/api-keys")
    assert second.status_code == 200
    assert "newKeyDisplay" not in second.text
    assert "newWebhookSecretDisplay" not in second.text
    assert raw_key not in second.text and webhook_secret not in second.text
    assert second.headers.get("Cache-Control") == "no-store, private"
    assert "Cookie" in (second.headers.get("Vary") or "").split(",")

    api = await e2e.client_for(raw_key)
    me = await api.get("/api/v1/me")
    assert me.status_code == 200, f"status={me.status_code}"
    identity = me.json()
    assert identity["scopes"] == sorted(PARTNER_SCOPES + ["users:read"])
    assert identity["allowed_company"] == "Mobilis"
    assert identity["allow_internal_comments"] is True
    assert identity["can_access_internal_comments"] is True
    assert identity["webhook_configured"] is True
    assert identity["webhook_events"] == ",".join(ALL_EVENTS)
    assert raw_key not in me.text and webhook_secret not in me.text
    assert "key_hash" not in identity and "webhook_secret" not in identity
    _assert_no_secret_in_logs(e2e, raw_key, webhook_secret)
    assert (await api.get("/api/v1/users")).status_code == 200   # before narrowing

    # Toggle the permission off and back on, then rotate the signing secret.
    base_form = _key_form(
        name="Mobilis / Leon (edited)", scopes=["tickets:read", "tickets:comment"],
        allowed_email_domain="mobilis.co.za", allow_internal_comments="true",
        webhook_secret_rotate="true",
    )
    edit_off = dict(base_form, allow_internal_comments="false")
    response = await admin.post(f"/web/admin/api-keys/{stored.id}/update", data=edit_off)
    # Rotation happens here too: never echo the secret-bearing response body.
    assert response.status_code == 200, f"status={response.status_code}"
    assert (await api.get("/api/v1/me")).json()["allow_internal_comments"] is False
    assert (await api.get("/api/v1/users")).status_code == 403   # scopes narrowed
    assert (await api.get("/api/v1/tasks")).status_code == 403   # never granted

    response = await admin.post(f"/web/admin/api-keys/{stored.id}/update", data=base_form)
    assert response.status_code == 200, f"status={response.status_code}"
    assert "newWebhookSecretDisplay" in response.text
    rotated_secret = _input_value(response.text, "newWebhookSecretDisplay")
    assert rotated_secret != webhook_secret
    assert webhook_secret not in response.text
    updated = await _stored_key_values(e2e.db, stored.id)
    assert updated.webhook_secret == rotated_secret
    assert updated.scopes == "tickets:read,tickets:comment"
    assert updated.allowed_email_domain == "mobilis.co.za"
    still_works = await api.get("/api/v1/me")
    assert still_works.status_code == 200
    assert still_works.json()["can_access_internal_comments"] is True
    assert rotated_secret not in still_works.text
    _assert_no_secret_in_logs(e2e, rotated_secret)
    assert (await api.get("/api/v1/users")).status_code == 403   # scopes narrowed

    revoke = await admin.post(f"/web/admin/api-keys/{stored.id}/revoke")
    assert revoke.status_code == 200, revoke.text
    for path in ("/api/v1/me", "/api/v1/tickets"):
        assert (await api.get(path)).status_code == 401, path

    delete = await admin.post(f"/web/admin/api-keys/{stored.id}/delete")
    assert delete.status_code == 200, delete.text
    assert (await e2e.db.execute(
        select(APIKey).where(APIKey.id == stored.id)
    )).scalar_one_or_none() is None
    await api.aclose()
    await admin.aclose()


async def _web_comment(e2e, *, internal: bool, body: str) -> None:
    staff = await e2e.login("tech")
    try:
        form = {"content": body, "is_internal": "true"} if internal else {"content": body}
        response = await staff.post(
            f"/web/tickets/{e2e.tickets['mobilis'].id}/comment", data=form
        )
        assert response.status_code == 200, f"status={response.status_code}"
        assert response.url.path == f"/web/tickets/{e2e.tickets['mobilis'].id}"
    finally:
        await staff.aclose()


async def _api_comment(e2e, key: str, *, internal: bool, body: str):
    api = await e2e.client_for(key)
    try:
        return await api.post(
            "/api/v1/tickets/TKT-2026-30001/comments",
            json={"body": body, "is_internal": internal, "author_name": "Leon"},
        )
    finally:
        await api.aclose()


async def _all_comment_reads(e2e, key: str, flag=None):
    """Bodies and internal flags from the detail, list and comments endpoints."""
    suffix = "" if flag is None else ("true" if flag else "false")
    detail_url = "/api/v1/tickets/TKT-2026-30001"
    list_url = "/api/v1/tickets?include=comments,history,time_entries"
    comments_url = "/api/v1/comments?limit=100"
    if suffix:
        detail_url += f"?include_internal={suffix}"
        list_url += f"&include_internal={suffix}"
        comments_url += f"&include_internal={suffix}"
    api = await e2e.client_for(key)
    try:
        responses = [await api.get(url) for url in (detail_url, list_url, comments_url)]
    finally:
        await api.aclose()
    for response in responses:
        assert response.status_code == 200, response.text
    detail, listing, comments = (r.json() for r in responses)
    listed = next(t for t in listing["tickets"]
                  if t["ticket_number"] == "TKT-2026-30001")
    return (
        [(c["body"], c["is_internal"]) for c in detail["comments"]],
        [(c["body"], c["is_internal"]) for c in listed["comments"]],
        [(c["body"], c["is_internal"]) for c in comments["comments"]],
        detail, listing, comments,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [KEY_SCOPED_INTERNAL, KEY_FULL_INTERNAL],
                         ids=["scoped_allowed", "full_access_allowed"])
async def test_allowed_keys_see_web_and_api_notes_on_every_endpoint(e2e, key):
    """No include_internal needed: both sources and their flags come through."""
    await _web_comment(e2e, internal=True, body="Web private note")
    await _web_comment(e2e, internal=False, body="Web public reply")
    internal_api = await _api_comment(e2e, KEY_SCOPED_INTERNAL, internal=True,
                                      body="API private note")
    public_api = await _api_comment(e2e, KEY_SCOPED_INTERNAL, internal=False,
                                    body="API public reply")
    assert (internal_api.status_code, public_api.status_code) == (201, 201)

    expected = {
        ("Web private note", True), ("Web public reply", False),
        ("[Leon] API private note", True), ("[Leon] API public reply", False),
    }
    details, listed, comments, *_ = await _all_comment_reads(e2e, key)
    for bodies in (details, listed, comments):
        assert set(bodies) == expected

    details, listed, comments, *_ = await _all_comment_reads(e2e, key, flag=False)
    public = {("Web public reply", False), ("[Leon] API public reply", False)}
    for bodies in (details, listed, comments):
        assert set(bodies) == public


@pytest.mark.asyncio
async def test_scoped_key_without_permission_never_sees_internal_notes(e2e):
    """Even an explicit opt-in cannot unlock private notes, on any endpoint."""
    await _web_comment(e2e, internal=True, body="Web secret note")
    await _web_comment(e2e, internal=False, body="Public web reply")
    assert (await _api_comment(e2e, KEY_SCOPED_INTERNAL, internal=True,
                               body="API secret note")).status_code == 201
    assert (await _api_comment(e2e, KEY_SCOPED_INTERNAL, internal=False,
                               body="Public API reply")).status_code == 201

    for flag in (None, True):
        details, listed, comments, *_ = await _all_comment_reads(e2e, KEY_DENIED, flag)
        for bodies in (details, listed, comments):
            assert set(bodies) == {
                ("Public web reply", False), ("[Leon] Public API reply", False),
            }
        assert "Web secret note" not in json.dumps((details, listed, comments))
        assert "API secret note" not in json.dumps((details, listed, comments))

    assert (await _api_comment(e2e, KEY_DENIED, internal=True,
                               body="Denied note")).status_code == 403
    allowed = await _api_comment(e2e, KEY_DENIED, internal=False, body="Allowed reply")
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["is_internal"] is False


@pytest.mark.asyncio
async def test_internal_notes_do_not_leak_through_activity_or_since(e2e):
    """Private comment bodies and their times must not leak through reads or sync."""
    await _web_comment(e2e, internal=False, body="Public first")
    await _web_comment(e2e, internal=True, body="Private last")
    assert (await _api_comment(e2e, KEY_SCOPED_INTERNAL, internal=True,
                               body="API private last")).status_code == 201

    # Use the times the routes actually recorded: both writers create a staff
    # history row, and a private note used to bump ticket.updated_at as well.
    rows = (await e2e.db.execute(
        select(TicketComment.is_internal, TicketComment.created_at).where(
            TicketComment.ticket_id == e2e.tickets["mobilis"].id)
    )).all()
    public_at = max(created for internal, created in rows if not internal)
    internal_at = max(created for internal, created in rows if internal)
    assert internal_at > public_at
    cutoff = (public_at + (internal_at - public_at) / 2).isoformat()
    denied = await e2e.client_for(KEY_DENIED)
    detail = await denied.get("/api/v1/tickets/TKT-2026-30001")
    assert detail.status_code == 200, detail.text
    assert detail.json()["last_activity_at"].startswith(public_at.isoformat()[:19])
    listing = await denied.get(
        "/api/v1/tickets?include=comments,history,time_entries"
        f"&updated_since={cutoff}"
    )
    assert [t["ticket_number"] for t in listing.json()["tickets"]] == []
    assert "Private last" not in listing.text and "API private last" not in listing.text
    assert "Added an internal note" not in listing.text
    assert "api-internal:" not in listing.text
    allowed = await e2e.client_for(KEY_SCOPED_INTERNAL)
    allowed_detail = await allowed.get("/api/v1/tickets/TKT-2026-30001")
    assert allowed_detail.json()["last_activity_at"].startswith(internal_at.isoformat()[:19])
    allowed_listing = await allowed.get(f"/api/v1/tickets?updated_since={cutoff}")
    assert "TKT-2026-30001" in {t["ticket_number"] for t in allowed_listing.json()["tickets"]}
    await denied.aclose()
    await allowed.aclose()


@pytest.mark.asyncio
async def test_legacy_unscoped_key_keeps_its_original_internal_note_behaviour(e2e):
    """Legacy keys keep opt-in reads and may still write private notes."""
    await _web_comment(e2e, internal=True, body="Legacy web private")
    assert (await _api_comment(e2e, KEY_SCOPED_INTERNAL, internal=True,
                               body="Legacy API private")).status_code == 201

    public = await _all_comment_reads(e2e, KEY_LEGACY)
    for bodies in public[:3]:
        assert bodies == []
    opted_in = await _all_comment_reads(e2e, KEY_LEGACY, flag=True)
    for bodies in opted_in[:3]:
        assert {body for body, _ in bodies} == {
            "Legacy web private", "[Leon] Legacy API private",
        }
        assert all(flag is True for _, flag in bodies)

    posted = await _api_comment(e2e, KEY_LEGACY, internal=True, body="Legacy can write")
    assert posted.status_code == 201, posted.text
    assert posted.json()["is_internal"] is True


# ---------------------------------------------------------------------------
# Two-way ticket sync
# ---------------------------------------------------------------------------

async def _history_rows(db, ticket_id: int):
    return (await db.execute(
        select(TicketHistory).where(TicketHistory.ticket_id == ticket_id)
    )).scalars().all()


def _assert_signed(deliveries: list[dict], e2e, *expected_events: str) -> list[dict]:
    secrets = {key.webhook_url: key.webhook_secret for key in e2e.keys.values()
               if key.webhook_url and key.webhook_secret}
    payloads, seen_ids = [], set()
    now = int(datetime.now().timestamp())
    for delivery in deliveries:
        headers, body = delivery["headers"], delivery["body"]
        secret = secrets[delivery["url"]]
        assert headers["Content-Type"] == "application/json"
        assert headers["User-Agent"] == "CEM-Partner-Webhook/1.0"
        delivery_id = headers["X-CEM-Delivery-Id"]
        assert delivery_id and delivery_id not in seen_ids
        seen_ids.add(delivery_id)
        timestamp = headers["X-CEM-Timestamp"]
        assert timestamp.isdigit() and abs(int(timestamp) - now) < 300
        # Compute the expectation independently: SHA-256 over the exact bytes
        # "timestamp.raw_body", never the production signing helper.
        expected = "sha256=" + hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        assert headers["X-CEM-Signature"] == expected
        assert headers["X-CEM-Signature"].startswith("sha256=")
        payload = json.loads(body)
        assert payload["event"] == headers["X-CEM-Event"]
        assert payload["timestamp"] == headers["X-CEM-Timestamp"]
        assert payload["delivery_id"] == headers["X-CEM-Delivery-Id"]
        assert payload["data"]["ticket_number"]
        payloads.append(payload)
    if expected_events:
        assert {p["event"] for p in payloads} == set(expected_events)
    return payloads


@pytest.mark.asyncio
async def test_rich_patch_persists_fields_history_timer_and_reopen(e2e):
    """PATCH is the whole write contract: fields, history, timer and a webhook."""
    api = await e2e.client_for(KEY_DENIED)
    ticket = e2e.tickets["mobilis"]
    number = ticket.ticket_number
    before = ticket.updated_at
    e2e.deliveries.clear()

    closed = await api.patch(f"/api/v1/tickets/{number}", json={
        "status": "closed", "priority": "high", "category": "support",
        "subject": "Pump replaced", "description": "Replaced the broken pump",
        "external_ref": "MOB-E2E-1", "close_type": "billable",
        "closing_notes": "Parts fitted", "billable_remote_labour": "2h",
        "billable_equipment_used": "New pump",
    })
    assert closed.status_code == 200, closed.text
    data = closed.json()
    assert data["status"] == "closed" and data["priority"] == "high"
    assert data["category"] == "support" and data["subject"] == "Pump replaced"
    assert data["external_ref"] == "MOB-E2E-1" and data["coverage"] == "billable"
    assert data["close_reason"] == "Parts fitted"
    assert data["billing"]["billable"]["remote_labour"] == "2h"
    assert data["billing"]["billable"]["equipment"] == "New pump"
    assert data["closed_at"] and data["resolved_at"]

    await e2e.db.refresh(ticket)
    assert ticket.external_ref == "MOB-E2E-1" and ticket.closing_notes == "Parts fitted"
    timer = (await e2e.db.execute(
        select(TicketTimeEntry).where(TicketTimeEntry.ticket_id == ticket.id)
    )).scalar_one()
    assert timer.ended_at is not None
    actions = {h.action: h for h in await _history_rows(e2e.db, ticket.id)}
    assert {"status_changed", "priority_changed", "category_changed", "subject_changed",
            "external_ref_changed", "close_type_changed", "closing_notes_changed",
            "description_changed"} <= set(actions)
    assert (actions["status_changed"].old_value,
            actions["status_changed"].new_value) == ("open", "closed")

    changed = await api.get(
        f"/api/v1/tickets?updated_since={(before - timedelta(seconds=1)).isoformat()}"
    )
    assert number in {t["ticket_number"] for t in changed.json()["tickets"]}
    _assert_signed(e2e.deliveries, e2e, "ticket.closed")
    assert {d["url"] for d in e2e.deliveries} == {
        e2e.keys["listener"].webhook_url, e2e.keys["source"].webhook_url,
    }

    # A no-op patch neither bumps the timestamp nor tells a partner anything.
    settled_at = ticket.updated_at
    e2e.deliveries.clear()
    noop = await api.patch(f"/api/v1/tickets/{number}", json={"status": "closed"})
    assert noop.status_code == 200, noop.text
    await e2e.db.refresh(ticket)
    assert ticket.updated_at == settled_at
    assert e2e.deliveries == []

    # Reopening clears both completion timestamps and records the transition.
    e2e.deliveries.clear()
    reopened = await api.patch(f"/api/v1/tickets/{number}", json={"status": "in_progress"})
    assert reopened.status_code == 200, reopened.text
    await e2e.db.refresh(ticket)
    assert ticket.status == "in_progress"
    assert ticket.closed_at is None and ticket.resolved_at is None
    reopened_rows = [h for h in await _history_rows(e2e.db, ticket.id)
                     if h.action == "status_changed" and h.new_value == "in_progress"]
    assert [(h.old_value, h.new_value) for h in reopened_rows] == [("closed", "in_progress")]
    _assert_signed(e2e.deliveries, e2e, "ticket.updated")
    await api.aclose()


@pytest.mark.asyncio
async def test_create_filters_and_client_email_domain_workspace_isolation(e2e):
    """Scopes, filters and client locks agree on lists, details and writes."""
    api = await e2e.client_for(KEY_DENIED)
    created = await api.post("/api/v1/tickets", json={
        "subject": "Mobilis site visit", "external_ref": "E2E-REF-7",
        "guest_branch": "Durban", "guest_office_number": "031 555 0100",
    })
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["guest_company"] == "Mobilis"      # inferred from the key
    assert data["external_ref"] == "E2E-REF-7"
    number = data["ticket_number"]
    row = (await e2e.db.execute(
        select(Ticket).where(Ticket.ticket_number == number)
    )).scalar_one()
    assert row.guest_branch == "Durban" and row.guest_office_number == "031 555 0100"

    async def numbers(key: str, query: str = "") -> set[str]:
        client = await e2e.client_for(key)
        response = await client.get(f"/api/v1/tickets{query}")
        assert response.status_code == 200, response.text
        await client.aclose()
        return {t["ticket_number"] for t in response.json()["tickets"]}

    mobilis = await numbers(KEY_DENIED)
    assert mobilis == {"TKT-2026-30001", number}
    assert await numbers(KEY_DENIED, "?external_ref=E2E-REF-7") == {number}
    assert await numbers(KEY_DENIED, "?guest_company=acme") == set()
    assert await numbers(KEY_DENIED, "?guest_email=LEON%40MOBILIS.CO.ZA") == {
        "TKT-2026-30001"}
    assert await numbers(KEY_DENIED, f"?created_since={(NOW - timedelta(minutes=1)).isoformat()}") \
        == {number}
    assert await numbers(KEY_EMAIL) == {"TKT-2026-30001"}
    assert await numbers(KEY_DOMAIN) == {"TKT-2026-30001"}
    assert await numbers(KEY_OTHER_COMPANY) == {"TKT-2026-30002"}
    assert await numbers(KEY_FULL_INTERNAL) == {"TKT-2026-30001", "TKT-2026-30002", number}

    assert (await api.post("/api/v1/tickets", json={
        "subject": "Not my client", "guest_company": "Acme",
    })).status_code == 403
    assert (await api.get("/api/v1/tickets/TKT-2026-30002")).status_code == 404
    assert (await api.patch("/api/v1/tickets/TKT-2026-30002",
                            json={"status": "closed"})).status_code == 404
    assert (await api.post("/api/v1/tickets/TKT-2026-30002/comments",
                           json={"body": "nope"})).status_code == 404
    assert (await api.get("/api/v1/tickets/TKT-2026-30003")).status_code == 404
    full = await e2e.client_for(KEY_FULL_INTERNAL)
    assert (await full.get("/api/v1/tickets/TKT-2026-30003")).status_code == 404

    assert (await api.post("/api/v1/tickets/TKT-2026-30001/comments",
                           json={"body": "Mobilis reply"})).status_code == 201
    assert (await full.post("/api/v1/tickets/TKT-2026-30002/comments",
                            json={"body": "Acme reply"})).status_code == 201
    other = await e2e.client_for(KEY_OTHER_COMPANY)
    mobilis_comments = (await api.get("/api/v1/comments?limit=100")).json()["comments"]
    acme_comments = (await other.get("/api/v1/comments?limit=100")).json()["comments"]
    assert [c["body"] for c in mobilis_comments] == ["Mobilis reply"]
    assert all(c["ticket_number"] != "TKT-2026-30002" for c in mobilis_comments)
    assert all(c["ticket_number"] == "TKT-2026-30002" for c in acme_comments)
    assert "Acme reply" not in (await api.get("/api/v1/comments?limit=100")).text

    entries = (await full.get("/api/v1/time-entries")).json()["entries"]
    assert any(e["ticket"]["ticket_number"] == "TKT-2026-30001" for e in entries)
    assert (await other.get("/api/v1/time-entries")).status_code == 403
    acme_time = await e2e.client_for(KEY_OTHER_TIME)
    scoped_entries = (await acme_time.get("/api/v1/time-entries")).json()["entries"]
    assert scoped_entries == []
    await acme_time.aclose()
    await api.aclose()
    await full.aclose()
    await other.aclose()


# ---------------------------------------------------------------------------
# Signed outbound webhooks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhooks_cover_all_events_with_signatures_and_required_headers(e2e):
    """Every documented event is delivered with a verifiable signature."""
    api = await e2e.client_for(KEY_DENIED)
    e2e.deliveries.clear()

    created = await api.post("/api/v1/tickets", json={
        "subject": "Webhook job", "guest_company": "Mobilis",
    })
    assert created.status_code == 201, created.text
    number = created.json()["ticket_number"]
    assert (await api.patch(f"/api/v1/tickets/{number}",
                            json={"status": "in_progress"})).status_code == 200
    assert (await api.post(f"/api/v1/tickets/{number}/comments",
                           json={"body": "Public progress",
                                 "author_name": "Leon"})).status_code == 201
    assert (await api.patch(f"/api/v1/tickets/{number}",
                            json={"status": "closed"})).status_code == 200

    payloads = _assert_signed(e2e.deliveries, e2e)
    assert {p["event"] for p in payloads} == set(ALL_EVENTS)
    assert {d["url"] for d in e2e.deliveries} == {
        e2e.keys["listener"].webhook_url, e2e.keys["internal_listener"].webhook_url,
        e2e.keys["source"].webhook_url,
    }
    commented = next(p for p in payloads if p["event"] == "ticket.commented")
    assert commented["data"]["comment"]["body"] == "[Leon] Public progress"
    assert commented["data"]["comment"]["is_internal"] is False
    _assert_no_secret_in_logs(e2e, "listener-signing-secret", "internal-signing-secret")
    await api.aclose()


@pytest.mark.asyncio
async def test_internal_notes_only_reach_keys_that_may_read_them(e2e):
    """Private notes are filtered per receiving key, not globally."""
    e2e.deliveries.clear()
    await _web_comment(e2e, internal=True, body="Web private note")
    urls = [d["url"] for d in e2e.deliveries]
    assert urls == [e2e.keys["internal_listener"].webhook_url]
    payloads = _assert_signed(e2e.deliveries, e2e, "ticket.commented")
    assert payloads[0]["data"]["comment"]["is_internal"] is True
    assert payloads[0]["data"]["comment"]["body"] == "Web private note"

    e2e.deliveries.clear()
    await _web_comment(e2e, internal=False, body="Web public note")
    urls = [d["url"] for d in e2e.deliveries]
    assert set(urls) == {e2e.keys["listener"].webhook_url,
                         e2e.keys["internal_listener"].webhook_url,
                         e2e.keys["source"].webhook_url}
    _assert_signed(e2e.deliveries, e2e, "ticket.commented", "ticket.commented")


@pytest.mark.asyncio
async def test_webhook_loop_prevention_skips_source_and_rotated_endpoint(e2e):
    """A caller never receives its own change, even through a rotated key."""
    api = await e2e.client_for(KEY_SOURCE)
    e2e.deliveries.clear()
    created = await api.post("/api/v1/tickets", json={
        "subject": "No echo", "guest_company": "Mobilis",
    })
    assert created.status_code == 201, created.text
    assert all(d["url"] != e2e.keys["source"].webhook_url for d in e2e.deliveries)
    assert all(d["url"] != e2e.keys["clone"].webhook_url for d in e2e.deliveries)
    assert e2e.keys["listener"].webhook_url in {d["url"] for d in e2e.deliveries}
    _assert_signed(e2e.deliveries, e2e)
    await api.aclose()


@pytest.mark.asyncio
async def test_ssrf_validation_rejects_private_destinations_and_admin_form(e2e, monkeypatch):
    """Validation runs on the URL, on DNS answers, on the form and on dispatch."""
    for url in (
        "http://127.0.0.1/hook", "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/hook", "http://localhost/hook", "http://[::1]/hook",
        "http://[fc00::1]/hook", "http://metadata.google.internal/computeMetadata/v1/",
        "https://user:pass@hooks.partner.test/cem", "ftp://partner.test/hook",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            api_webhooks.validate_webhook_url(url)

    def private_resolver(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))]

    with pytest.raises(ValueError):
        api_webhooks.validate_webhook_url("https://partner.test/hook",
                                          resolver=private_resolver)
    assert api_webhooks.validate_webhook_url(
        "https://hooks.partner.test/cem", resolver=_public_resolver
    ) == "https://hooks.partner.test/cem"

    sent = []
    async def sender(url, headers, body):
        sent.append(url)

    await api_webhooks.dispatch_ticket_webhook(
        e2e.db, e2e.ws.id, "ticket.updated", e2e.tickets["mobilis"],
        sender=sender, resolver=private_resolver,
    )
    assert sent == []   # dispatch validates again, so private DNS is never dialled

    admin = await e2e.login("admin")
    form = _key_form(name="Unsafe hook", webhook_url="http://127.0.0.1/hook")
    response = await admin.post("/web/admin/api-keys/create", data=form)
    assert response.status_code == 200, f"status={response.status_code}"
    assert "Webhook URL is invalid" in response.text
    assert "newKeyDisplay" not in response.text
    assert (await e2e.db.execute(
        select(APIKey).where(APIKey.name == "Unsafe hook")
    )).scalar_one_or_none() is None

    # The admin form uses the real validator; only DNS is substituted here.
    wrapped_getaddrinfo = api_webhooks.socket.getaddrinfo

    def private_dns_getaddrinfo(host, port, *args, **kwargs):
        if isinstance(host, str) and host.strip(".").lower() == "hooks.private.test":
            return private_resolver(host, port)
        return wrapped_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(api_webhooks.socket, "getaddrinfo", private_dns_getaddrinfo)
    form = _key_form(name="Private DNS hook", webhook_url="https://hooks.private.test/cem")
    response = await admin.post("/web/admin/api-keys/create", data=form)
    assert response.status_code == 200, f"status={response.status_code}"
    assert "Webhook URL is invalid" in response.text
    assert (await e2e.db.execute(
        select(APIKey).where(APIKey.name == "Private DNS hook")
    )).scalar_one_or_none() is None
    await admin.aclose()


# ---------------------------------------------------------------------------
# Scope enforcement on every endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_api_endpoint_enforces_its_scope(e2e):
    """Each endpoint is reachable only with its own named scope (or a wildcard)."""
    number = e2e.tickets["mobilis"].ticket_number
    cases = [
        ("GET", "/tickets", None, "tickets:read"),
        ("GET", f"/tickets/{number}", None, "tickets:read"),
        ("POST", "/tickets", {"subject": "Scope check", "guest_company": "Mobilis"},
         "tickets:create"),
        ("PATCH", f"/tickets/{number}", {"status": "in_progress"}, "tickets:update"),
        ("POST", f"/tickets/{number}/comments", {"body": "Scope check"}, "tickets:comment"),
        ("GET", "/comments", None, "tickets:read"),
        ("GET", "/users", None, "users:read"),
        ("GET", "/tasks", None, "tasks:read"),
        ("POST", "/tasks/archive-done", {}, "tasks:write"),
        ("POST", f"/tasks/{e2e.task.id}/archive", None, "tasks:write"),
        ("GET", "/projects", None, "projects:read"),
        ("GET", "/time-entries", None, "time:read"),
        ("GET", "/overview", None, "overview:read"),
    ]
    single_scope_keys = {
        "tickets:read": KEY_WRONG_SCOPE,
        "tickets:create": KEY_TICKETS_CREATE,
        "tickets:update": KEY_TICKETS_UPDATE,
        "tickets:comment": KEY_TICKETS_COMMENT,
        "users:read": KEY_NOSCOPE,
        "tasks:read": KEY_TASKS_READ,
        "tasks:write": KEY_TASKS_WRITE,
        "projects:read": KEY_PROJECTS_READ,
        "time:read": KEY_TIME_READ,
        "overview:read": KEY_OVERVIEW_READ,
    }

    def allowed_status(method: str, path: str) -> int:
        if method == "POST" and (path == "/tickets" or path.endswith("/comments")):
            return 201
        return 200

    full = await e2e.client_for(KEY_FULL_INTERNAL)
    for method, path, body, _ in cases:
        response = await full.request(method, f"/api/v1{path}", json=body)
        assert response.status_code == allowed_status(method, path), \
            (method, path, response.text)

    for required, raw in single_scope_keys.items():
        client = await e2e.client_for(raw)
        for method, path, body, scope in cases:
            response = await client.request(method, f"/api/v1{path}", json=body)
            if scope == required:
                assert response.status_code == allowed_status(method, path), \
                    (required, method, path, response.text)
            else:
                assert response.status_code == 403, \
                    (required, method, path, response.text)
        me = await client.get("/api/v1/me")
        assert me.status_code == 200 and me.json()["scopes"] == [required]
        await client.aclose()

    full_me = await full.get("/api/v1/me")
    assert full_me.json()["scopes"] == ["*"]
    await full.aclose()


@pytest.mark.asyncio
async def test_ping_is_public_but_protected_routes_reject_bad_keys(e2e):
    anonymous = await e2e.client_for()
    ping = await anonymous.get("/api/v1/ping")
    assert ping.status_code == 200 and ping.json()["status"] == "ok"
    assert (await anonymous.get("/api/v1/me")).status_code == 401

    for raw in ("bad", "cem_not-a-real-key", ""):
        client = await e2e.client_for(raw)
        for path in ("/api/v1/tickets", "/api/v1/tickets/TKT-2026-30001",
                     "/api/v1/comments", "/api/v1/users", "/api/v1/me"):
            assert (await client.get(path)).status_code == 401, path
        await client.aclose()
    await anonymous.aclose()


# ---------------------------------------------------------------------------
# Startup migration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_pragma_migration_preserves_legacy_key_and_ticket(tmp_path, monkeypatch):
    """Old databases gain the new columns without losing any existing row."""
    import app.models  # noqa: F401  (register every model)

    path = tmp_path / "legacy-startup.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            'CREATE TABLE "apikey" (id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, '
            'name VARCHAR NOT NULL, key_prefix VARCHAR NOT NULL, key_hash VARCHAR NOT NULL, '
            'is_active BOOLEAN NOT NULL DEFAULT 1, created_at TIMESTAMP)'
        )
        conn.execute(
            "INSERT INTO apikey (workspace_id, name, key_prefix, key_hash) "
            "VALUES (7, 'Old key', 'cem_old', 'old-hash')"
        )
        conn.execute(
            'CREATE TABLE "ticket" (id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, '
            'ticket_number VARCHAR NOT NULL, subject VARCHAR NOT NULL, status VARCHAR, '
            'created_at TIMESTAMP, updated_at TIMESTAMP)'
        )
        conn.execute(
            "INSERT INTO ticket (workspace_id, ticket_number, subject, status) "
            "VALUES (7, 'TKT-OLD-1', 'Old ticket', 'closed')"
        )
        conn.commit()
    finally:
        conn.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    monkeypatch.setattr(dbmod, "engine", engine)
    try:
        await dbmod.init_models()
        await dbmod.init_models()
    finally:
        await engine.dispose()

    conn = sqlite3.connect(str(path))
    try:
        api_columns = [row[1] for row in conn.execute('PRAGMA table_info("apikey")')]
        ticket_columns = [row[1] for row in conn.execute('PRAGMA table_info("ticket")')]
        for column in ("scopes", "allowed_company", "allowed_email",
                       "allowed_email_domain", "allow_internal_comments",
                       "webhook_url", "webhook_secret", "webhook_events"):
            assert api_columns.count(column) == 1
        assert ticket_columns.count("external_ref") == 1
        key_row = conn.execute(
            "SELECT name, scopes, allow_internal_comments FROM apikey WHERE key_hash = 'old-hash'"
        ).fetchone()
        ticket_row = conn.execute(
            "SELECT ticket_number, subject, status, external_ref FROM ticket "
            "WHERE ticket_number = 'TKT-OLD-1'"
        ).fetchone()
    finally:
        conn.close()
    assert key_row == ("Old key", None, 0)
    assert ticket_row == ("TKT-OLD-1", "Old ticket", "closed", None)
