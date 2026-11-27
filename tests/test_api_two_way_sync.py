"""Two-way ticket sync: partner comments, job-card/close fields, external_ref
and signed outbound webhooks.

These tests are the executable contract for Slice 02. They cover the external
REST API, the SQLite startup migration, the SSRF-safe webhook helpers and the
staff-side routes that must notify a second integration without echoing back to
the key that caused the change.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from app.api.routes import external
from app.core import api_webhooks
from app.core.database import get_session
from app.models.api_key import APIKey
from app.models.ticket import Ticket, TicketComment, TicketHistory
from app.models.user import User
from app.models.workspace import Workspace

KEY_PARTNER = "cem_syncpartner0123456789a"
KEY_READONLY = "cem_syncreadonly0123456789"
KEY_INTERNAL = "cem_syncinternal0123456789"
KEY_FULL_INTERNAL = "cem_syncfullinternal01234567"
KEY_HOOK = "cem_synchook0123456789ab"
KEY_MUTE = "cem_syncmute0123456789ab"
KEY_OTHER = "cem_syncother0123456789ab"
KEY_ECHO = "cem_syncecho0123456789ab"
KEY_CLONE = "cem_synccclone0123456789ab"
KEY_UNSIGNED = "cem_syncunsigned0123456789ab"
KEY_UNSUB = "cem_syncunsub0123456789ab"
NOW = datetime.utcnow().replace(microsecond=0)


def _headers(key: str) -> dict:
    return {"X-API-Key": key}


def _key(raw: str, workspace_id: int, name: str, **fields) -> APIKey:
    return APIKey(
        workspace_id=workspace_id, name=name, key_prefix=raw[:12],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(), **fields,
    )


def _iso(value: datetime) -> str:
    return value.isoformat() + "Z"


def _public_resolver(host: str, port: int):
    """DNS stands in for the real resolver; every public name maps to a public IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


async def _read_bodies(client, key: str, number: str, *, include_internal=None):
    """Comment bodies from all three read endpoints for one key and ticket."""
    flag = "" if include_internal is None else (
        "true" if include_internal else "false")
    detail_url = f"/api/v1/tickets/{number}"
    if flag:
        detail_url += f"?include_internal={flag}"
    list_url = f"/api/v1/tickets?include=comments"
    comments_url = f"/api/v1/comments?limit=100"
    if flag:
        suffix = f"&include_internal={flag}"
        list_url += suffix
        comments_url += suffix
    headers = _headers(key)
    detail = await client.get(detail_url, headers=headers)
    listing = await client.get(list_url, headers=headers)
    comments = await client.get(comments_url, headers=headers)
    for response in (detail, listing, comments):
        assert response.status_code == 200, response.text
    return (
        [c["body"] for c in detail.json()["comments"]],
        [c["body"] for t in listing.json()["tickets"] for c in t["comments"]],
        [c["body"] for c in comments.json()["comments"]],
    )


# ---------------------------------------------------------------------------
# API fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def api(tmp_path):
    import app.models  # noqa: F401  (register every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sync.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    ws = Workspace(name="Mobilis", timezone="UTC")
    other_ws = Workspace(name="Other Co", timezone="UTC")
    db.add_all([ws, other_ws])
    await db.flush()
    ada = User(username="ada", hashed_password="x", is_admin=True, workspace_id=ws.id)
    db.add(ada)
    await db.flush()

    tickets = {
        "mobilis": Ticket(
            ticket_number="TKT-2026-20001", subject="Printer down", workspace_id=ws.id,
            is_guest=True, guest_company="Mobilis", guest_email="leon@mobilis.co.za",
            guest_branch="Cape Town", external_ref="MOB-55", status="open",
            created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=2),
        ),
        "acme": Ticket(
            ticket_number="TKT-2026-20002", subject="Someone else's printer", workspace_id=ws.id,
            is_guest=True, guest_company="Acme", guest_email="jane@acme.test",
            created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=2),
        ),
        "foreign": Ticket(
            ticket_number="TKT-2026-20003", subject="Another workspace", workspace_id=other_ws.id,
            is_guest=True, guest_company="Mobilis", guest_email="leon@mobilis.co.za",
            created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=2),
        ),
    }
    db.add_all(tickets.values())
    await db.flush()

    keys = {
        "partner": _key(KEY_PARTNER, ws.id, "Mobilis partner",
                        scopes="tickets:read,tickets:create,tickets:update,tickets:comment",
                        allowed_company="Mobilis"),
        "readonly": _key(KEY_READONLY, ws.id, "Read only", scopes="tickets:read",
                         allowed_company="Mobilis"),
        "internal": _key(KEY_INTERNAL, ws.id, "Internal allowed",
                         scopes="tickets:read,tickets:comment", allowed_company="Mobilis",
                         allow_internal_comments=True),
        "full_internal": _key(KEY_FULL_INTERNAL, ws.id, "Full access with notes",
                              allow_internal_comments=True),
        "hook": _key(KEY_HOOK, ws.id, "Leon's listener", scopes="tickets:read",
                     allowed_company="Mobilis", webhook_url="https://hooks.partner.test/cem",
                     webhook_secret="hook-secret", webhook_events="ticket.created,ticket.updated,"
                     "ticket.commented,ticket.closed"),
        "mute": _key(KEY_MUTE, ws.id, "Internal listener", scopes="tickets:read",
                     allowed_company="Mobilis", webhook_url="https://internal.partner.test/cem",
                     webhook_secret="mute-secret", allow_internal_comments=True,
                     webhook_events="ticket.commented"),
        "other": _key(KEY_OTHER, ws.id, "Acme listener", scopes="tickets:read",
                      allowed_company="Acme", webhook_url="https://acme.partner.test/cem",
                      webhook_secret="other-secret", webhook_events=""),
        "echo": _key(KEY_ECHO, ws.id, "Calling integration", scopes="tickets:read",
                     allowed_company="Mobilis", webhook_url="https://echo.partner.test/cem",
                     webhook_secret="echo-secret", webhook_events=""),
        "clone": _key(KEY_CLONE, ws.id, "Rotated echo listener", scopes="tickets:read",
                      allowed_company="Mobilis", webhook_url="https://echo.partner.test/cem",
                      webhook_secret="echo-secret", webhook_events=""),
        "unsigned": _key(KEY_UNSIGNED, ws.id, "No signing secret", scopes="tickets:read",
                         allowed_company="Mobilis",
                         webhook_url="https://unsigned.partner.test/cem", webhook_events=""),
        "unsub": _key(KEY_UNSUB, ws.id, "Only creations", scopes="tickets:read",
                      allowed_company="Mobilis", webhook_url="https://unsub.partner.test/cem",
                      webhook_secret="unsub-secret", webhook_events="ticket.created"),
    }
    db.add_all(keys.values())
    await db.commit()

    app = FastAPI()
    app.include_router(external.router, prefix="/api")

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                         headers=_headers(KEY_PARTNER))
    yield client, db, ws, tickets, keys, ada
    await client.aclose()
    await db.close()
    await engine.dispose()


# ---------------------------------------------------------------------------
# POST /tickets/{number}/comments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partner_comment_creates_public_comment_history_and_activity(api):
    client, db, _, tickets, keys, _ = api
    ticket = tickets["mobilis"]
    before = ticket.updated_at
    response = await client.post(
        f"/api/v1/tickets/{ticket.ticket_number}/comments",
        json={"body": "Please replace the toner", "author_name": "Leon (Mobilis)"},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["ticket_number"] == ticket.ticket_number
    assert item["id"] and item["is_internal"] is False
    assert item["body"] == "[Leon (Mobilis)] Please replace the toner"
    assert item["author"]["type"] == "partner"
    assert item["author"]["name"] == "Leon (Mobilis)"
    assert item["created_at"].endswith("+00:00")

    comment = (await db.execute(select(TicketComment).where(TicketComment.id == item["id"]))).scalar_one()
    assert comment.user_id is None  # never forges a staff identity
    assert comment.content == item["body"]
    history = (await db.execute(
        select(TicketHistory).where(TicketHistory.ticket_id == ticket.id,
                                    TicketHistory.action == "commented")
    )).scalars().all()
    assert [h.new_value for h in history] == [f"api:{keys['partner'].name}"]
    await db.refresh(ticket)
    assert ticket.updated_at > before

    seen = await client.get(f"/api/v1/comments?since={_iso(before - timedelta(minutes=1))}"
                            f"&ticket_number={ticket.ticket_number}")
    assert [c["body"] for c in seen.json()["comments"]] == [item["body"]]


@pytest.mark.asyncio
async def test_comment_requires_scope_and_hides_out_of_scope_tickets(api):
    client, _, _, tickets, _, _ = api
    response = await client.post(
        f"/api/v1/tickets/{tickets['mobilis'].ticket_number}/comments",
        headers=_headers(KEY_READONLY), json={"body": "Not allowed"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "API key lacks required scope: tickets:comment"

    outside = await client.post(
        f"/api/v1/tickets/{tickets['acme'].ticket_number}/comments", json={"body": "Not yours"}
    )
    assert outside.status_code == 404
    assert outside.json()["detail"] == "Ticket not found"

    other_workspace = await client.post(
        f"/api/v1/tickets/{tickets['foreign'].ticket_number}/comments", json={"body": "Not yours"}
    )
    assert other_workspace.status_code == 404

    empty = await client.post(
        f"/api/v1/tickets/{tickets['mobilis'].ticket_number}/comments", json={"body": ""}
    )
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_internal_comment_needs_explicit_permission(api):
    client, db, _, tickets, _, _ = api
    number = tickets["mobilis"].ticket_number
    denied = await client.post(f"/api/v1/tickets/{number}/comments",
                               json={"body": "Private note", "is_internal": True})
    assert denied.status_code == 403

    allowed = await client.post(
        f"/api/v1/tickets/{number}/comments", headers=_headers(KEY_INTERNAL),
        json={"body": "Private note", "is_internal": True},
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["is_internal"] is True
    comment = (await db.execute(select(TicketComment).where(
        TicketComment.id == allowed.json()["id"]))).scalar_one()
    assert comment.is_internal is True and comment.user_id is None


@pytest.mark.asyncio
async def test_api_created_internal_comment_is_readable_by_allowed_keys(api, monkeypatch):
    client, db, _, tickets, _, _ = api
    number = tickets["mobilis"].ticket_number
    dispatched = []

    async def capture(db_arg, workspace_id, event, ticket_arg, **kwargs):
        dispatched.append((event, kwargs))

    monkeypatch.setattr(external, "dispatch_ticket_webhook", capture)

    created = await client.post(
        f"/api/v1/tickets/{number}/comments", headers=_headers(KEY_INTERNAL),
        json={"body": "API private note", "is_internal": True},
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["is_internal"] is True
    comment = (await db.execute(select(TicketComment).where(
        TicketComment.id == item["id"]))).scalar_one()
    assert comment.is_internal is True and comment.user_id is None
    assert dispatched and dispatched[-1][0] == "ticket.commented"
    assert dispatched[-1][1]["comment"].id == comment.id
    assert dispatched[-1][1]["source_api_key_id"] is not None

    for key in (KEY_INTERNAL, KEY_FULL_INTERNAL):
        for bodies in await _read_bodies(client, key, number):
            assert "API private note" in bodies
        for bodies in await _read_bodies(client, key, number, include_internal=False):
            assert "API private note" not in bodies

    for bodies in await _read_bodies(client, KEY_PARTNER, number, include_internal=True):
        assert "API private note" not in bodies
    denied = await client.post(f"/api/v1/tickets/{number}/comments",
                               headers=_headers(KEY_PARTNER),
                               json={"body": "Denied", "is_internal": True})
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_web_ui_internal_comment_is_visible_to_permitted_api_keys(
        api, tmp_path, monkeypatch):
    from app.web import routes as web

    client, db, _, tickets, _, ada = api
    number = tickets["mobilis"].ticket_number
    monkeypatch.chdir(tmp_path)          # the comment route writes logs in cwd
    dispatched = []

    async def capture(db_arg, workspace_id, event, ticket_arg, **kwargs):
        dispatched.append((event, kwargs))

    monkeypatch.setattr(web, "dispatch_ticket_webhook", capture)
    request = SimpleNamespace(
        session={"user_id": ada.id}, headers={}, state=SimpleNamespace(),
        url=SimpleNamespace(path="/web/tickets/1", query=""), method="POST",
    )
    response = await web.web_tickets_add_comment(
        request, tickets["mobilis"].id, content="Web private note", is_internal=True,
        files=[], background_tasks=BackgroundTasks(), db=db,
    )
    assert response.status_code == 303
    comment = (await db.execute(select(TicketComment).where(
        TicketComment.content == "Web private note"))).scalar_one()
    assert comment.is_internal is True and comment.user_id == ada.id
    assert any(event == "ticket.commented" and kwargs["comment"].id == comment.id
               for event, kwargs in dispatched)

    for key in (KEY_INTERNAL, KEY_FULL_INTERNAL):
        for bodies in await _read_bodies(client, key, number):
            assert "Web private note" in bodies
        for bodies in await _read_bodies(client, key, number, include_internal=False):
            assert "Web private note" not in bodies

    for bodies in await _read_bodies(client, KEY_PARTNER, number, include_internal=True):
        assert "Web private note" not in bodies


# ---------------------------------------------------------------------------
# PATCH /tickets/{number} and POST /tickets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_updates_close_fields_and_reopen_clears_timestamps(api):
    client, db, _, tickets, _, _ = api
    ticket = tickets["mobilis"]
    ticket.resolved_at = NOW - timedelta(minutes=30)
    await db.commit()
    before = ticket.updated_at
    number = ticket.ticket_number

    closed = await client.patch(f"/api/v1/tickets/{number}", json={
        "status": "closed", "closing_notes": "Replaced the pump",
        "close_reason": "Parts fitted", "close_type": "billable",
        "billable_remote_labour": "2h", "billable_equipment_used": "New pump",
        "external_ref": "MOB-101", "subject": "Pump replaced",
    })
    assert closed.status_code == 200, closed.text
    data = closed.json()
    assert data["status"] == "closed" and data["coverage"] == "billable"
    assert data["close_reason"] == "Replaced the pump"
    assert data["billing"]["billable"]["remote_labour"] == "2h"
    assert data["billing"]["billable"]["equipment"] == "New pump"
    assert data["external_ref"] == "MOB-101" and data["subject"] == "Pump replaced"
    assert data["updated_at"] and data["closed_at"]
    await db.refresh(ticket)
    assert ticket.closed_at is not None and ticket.closing_notes == "Replaced the pump"
    actions = [h.action for h in (await db.execute(
        select(TicketHistory).where(TicketHistory.ticket_id == ticket.id)
    )).scalars().all()]
    assert "status_changed" in actions and "close_type_changed" in actions
    assert "external_ref_changed" in actions

    changes = await client.get(f"/api/v1/tickets?updated_since={_iso(before - timedelta(minutes=1))}")
    assert number in [t["ticket_number"] for t in changes.json()["tickets"]]

    reopened = await client.patch(f"/api/v1/tickets/{number}", json={"status": "in_progress"})
    assert reopened.status_code == 200, reopened.text
    await db.refresh(ticket)
    assert ticket.status == "in_progress"
    assert ticket.closed_at is None and ticket.resolved_at is None
    reopened_history = (await db.execute(
        select(TicketHistory).where(TicketHistory.ticket_id == ticket.id,
                                    TicketHistory.action == "status_changed",
                                    TicketHistory.new_value == "in_progress")
    )).scalars().all()
    assert [(h.old_value, h.new_value) for h in reopened_history] == [("closed", "in_progress")]


@pytest.mark.asyncio
async def test_noop_status_patch_does_not_emit_activity(api, monkeypatch):
    client, db, _, tickets, _, _ = api
    ticket = tickets["mobilis"]
    before = ticket.updated_at
    calls = []

    async def capture(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(external, "dispatch_ticket_webhook", capture)
    response = await client.patch(f"/api/v1/tickets/{ticket.ticket_number}",
                                  json={"status": ticket.status})
    assert response.status_code == 200, response.text
    await db.refresh(ticket)
    assert ticket.updated_at == before
    assert calls == []


@pytest.mark.asyncio
async def test_create_persists_external_ref_and_filters(api):
    client, db, _, _, _, _ = api
    created = await client.post("/api/v1/tickets", json={
        "subject": "Mobilis site visit", "external_ref": "MOB-101",
        "guest_branch": "Durban", "guest_office_number": "031 555 0100",
    })
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["external_ref"] == "MOB-101"
    number = data["ticket_number"]

    filtered = await client.get("/api/v1/tickets?external_ref=MOB-101")
    assert [t["ticket_number"] for t in filtered.json()["tickets"]] == [number]
    assert (await client.get("/api/v1/tickets?external_ref=MOB-999")).json()["tickets"] == []
    row = (await db.execute(select(Ticket).where(Ticket.ticket_number == number))).scalar_one()
    assert row.external_ref == "MOB-101"
    assert row.guest_branch == "Durban" and row.guest_office_number == "031 555 0100"


# ---------------------------------------------------------------------------
# SSRF validation and signing helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/hook",
    "http://127.42.1.9/hook",
    "http://localhost/hook",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/hook",
    "http://172.16.0.1/hook",
    "http://192.168.1.10/hook",
    "http://[::1]/hook",
    "http://metadata.google.internal/computeMetadata/v1/",
    "file:///etc/passwd",
    "ftp://partner.test/hook",
])
def test_ssrf_validator_blocks_private_and_non_http_urls(url):
    with pytest.raises(ValueError):
        api_webhooks.validate_webhook_url(url, resolver=_public_resolver)


def test_ssrf_validator_blocks_public_name_that_resolves_privately():
    def private_resolver(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))]

    with pytest.raises(ValueError):
        api_webhooks.validate_webhook_url("https://partner.test/hook", resolver=private_resolver)


@pytest.mark.parametrize("url", [
    "https://hooks.partner.test/cem",
    "http://8.8.8.8/hook",
    "https://[2606:4700:4700::1111]/hook",
])
def test_ssrf_validator_accepts_public_urls(url):
    assert api_webhooks.validate_webhook_url(url, resolver=_public_resolver) == url


def test_signature_covers_timestamp_and_body():
    body = b'{"event":"ticket.updated"}'
    signature = api_webhooks.sign_webhook_payload("hook-secret", "1760000000", body)
    expected = hmac.new(b"hook-secret", b"1760000000." + body, hashlib.sha256).hexdigest()
    assert signature == f"sha256={expected}"
    assert api_webhooks.VALID_WEBHOOK_EVENTS == (
        "ticket.created", "ticket.updated", "ticket.commented", "ticket.closed")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_signs_payload_only_for_matching_subscribed_key(api):
    _, db, ws, tickets, keys, _ = api
    sent = []

    async def sender(url, headers, body):
        sent.append((url, headers, body))

    await api_webhooks.dispatch_ticket_webhook(
        db, ws.id, "ticket.updated", tickets["mobilis"],
        source_api_key_id=keys["echo"].id, sender=sender, resolver=_public_resolver,
    )
    assert [url for url, _, _ in sent] == [keys["hook"].webhook_url]
    assert keys["unsigned"].webhook_url not in sent  # unsigned endpoints are skipped
    url, headers, body = sent[0]
    assert headers["Content-Type"] == "application/json"
    assert headers["X-CEM-Event"] == "ticket.updated"
    assert headers["X-CEM-Delivery-Id"]
    timestamp = headers["X-CEM-Timestamp"]
    assert api_webhooks.sign_webhook_payload("hook-secret", timestamp, body) == \
        headers["X-CEM-Signature"]
    payload = json.loads(body)
    assert payload["event"] == "ticket.updated" and payload["timestamp"] == timestamp
    assert payload["delivery_id"] == headers["X-CEM-Delivery-Id"]
    assert payload["data"]["ticket_number"] == tickets["mobilis"].ticket_number
    assert payload["data"]["external_ref"] == "MOB-55"
    assert "T" in payload["data"]["updated_at"]
    assert payload["data"]["updated_at"].endswith("+00:00")  # same wire format as REST


@pytest.mark.asyncio
async def test_dispatch_never_sends_another_clients_ticket_to_mobilis(api):
    _, db, ws, tickets, _, _ = api
    sent = []

    async def sender(url, headers, body):
        sent.append(url)

    await api_webhooks.dispatch_ticket_webhook(db, ws.id, "ticket.updated", tickets["acme"],
                                               sender=sender, resolver=_public_resolver)
    assert sent == ["https://acme.partner.test/cem"]  # never the Mobilis webhooks
    sent.clear()
    await api_webhooks.dispatch_ticket_webhook(db, ws.id, "ticket.updated", tickets["foreign"],
                                               sender=sender, resolver=_public_resolver)
    assert sent == []  # a ticket from a different workspace is invisible


@pytest.mark.asyncio
async def test_dispatch_does_not_echo_to_source_or_unsubscribed_keys(api):
    _, db, ws, tickets, keys, _ = api
    sent = []

    async def sender(url, headers, body):
        sent.append(url)

    await api_webhooks.dispatch_ticket_webhook(db, ws.id, "ticket.closed", tickets["mobilis"],
                                               source_api_key_id=keys["hook"].id,
                                               sender=sender, resolver=_public_resolver)
    assert keys["hook"].webhook_url not in sent  # no loop back to the caller
    assert keys["unsub"].webhook_url not in sent  # only subscribed to ticket.created
    assert keys["echo"].webhook_url in sent


@pytest.mark.asyncio
async def test_rotated_key_sharing_source_endpoint_is_not_notified(api):
    _, db, ws, tickets, keys, _ = api
    sent = []

    async def sender(url, headers, body):
        sent.append(url)

    await api_webhooks.dispatch_ticket_webhook(db, ws.id, "ticket.updated", tickets["mobilis"],
                                               source_api_key_id=keys["echo"].id,
                                               sender=sender, resolver=_public_resolver)
    assert keys["echo"].webhook_url not in sent
    assert keys["clone"].webhook_url not in sent  # same URL: would ping-pong forever
    assert keys["hook"].webhook_url in sent


@pytest.mark.asyncio
async def test_internal_comments_go_only_to_keys_allowed_to_read_them(api):
    _, db, ws, tickets, keys, _ = api
    sent = []

    async def sender(url, headers, body):
        sent.append(url)

    ticket = tickets["mobilis"]
    await api_webhooks.dispatch_ticket_webhook(
        db, ws.id, "ticket.commented", ticket,
        comment=SimpleNamespace(is_internal=True, content="Private", id=1),
        sender=sender, resolver=_public_resolver,
    )
    assert sent == [keys["mute"].webhook_url]
    sent.clear()
    await api_webhooks.dispatch_ticket_webhook(
        db, ws.id, "ticket.commented", ticket,
        comment=SimpleNamespace(is_internal=False, content="Public", id=2),
        sender=sender, resolver=_public_resolver,
    )
    assert set(sent) == {keys["hook"].webhook_url, keys["mute"].webhook_url,
                         keys["echo"].webhook_url}


@pytest.mark.asyncio
async def test_dispatch_swallows_transport_failures(api):
    _, db, ws, tickets, _, _ = api

    async def down(url, headers, body):
        raise RuntimeError("partner endpoint is down")

    await api_webhooks.dispatch_ticket_webhook(db, ws.id, "ticket.updated", tickets["mobilis"],
                                               sender=down, resolver=_public_resolver)


@pytest.mark.asyncio
async def test_default_sender_dials_the_validated_ip_with_original_host(api, monkeypatch):
    _, db, ws, tickets, _, _ = api
    calls = []

    class FakeRequest:
        def __init__(self, url, content, headers, extensions):
            self.url, self.content = url, content
            self.headers, self.extensions = headers, extensions

    class FakeClient:
        def __init__(self, timeout=None):
            calls.append(("timeout", timeout))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def build_request(self, method, url, content, headers, extensions):
            calls.append(("build", method, url, headers, extensions))
            return FakeRequest(url, content, headers, extensions)

        async def send(self, request):
            calls.append(("send", request.url, request.content, request.headers))
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(api_webhooks.httpx, "AsyncClient", FakeClient)
    await api_webhooks.dispatch_ticket_webhook(db, ws.id, "ticket.updated", tickets["mobilis"],
                                               resolver=_public_resolver)
    await asyncio.sleep(0)  # let the background delivery start
    assert ("timeout", 5.0) in calls
    build = next(call for call in calls if call[0] == "build")
    assert build[1] == "POST"
    assert build[2] == "https://93.184.216.34/cem"  # pinned, not a second DNS lookup
    assert build[3]["Host"] == "hooks.partner.test"
    assert build[4] == {"sni_hostname": "hooks.partner.test"}
    assert any(call[0] == "send" for call in calls)


@pytest.mark.asyncio
async def test_api_update_still_succeeds_when_webhook_boom(api, monkeypatch):
    client, _, _, tickets, _, _ = api

    async def boom(*args, **kwargs):
        raise RuntimeError("webhook system down")

    monkeypatch.setattr(external, "dispatch_ticket_webhook", boom)
    response = await client.patch(
        f"/api/v1/tickets/{tickets['mobilis'].ticket_number}", json={"priority": "high"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["priority"] == "high"


# ---------------------------------------------------------------------------
# Startup migration
# ---------------------------------------------------------------------------

def _legacy_ticket_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute('CREATE TABLE "ticket" (id INTEGER PRIMARY KEY, subject VARCHAR NOT NULL)')
        conn.execute("INSERT INTO ticket (id, subject) VALUES (1, 'Old ticket')")
        conn.commit()
    finally:
        conn.close()


def _ticket_columns(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute('PRAGMA table_info("ticket")')]
    finally:
        conn.close()


def _ticket_indexes(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute('PRAGMA index_list("ticket")')]
    finally:
        conn.close()


def test_startup_helper_adds_external_ref_once(tmp_path):
    from app.core.database import _ensure_ticket_external_ref_column

    path = tmp_path / "legacy-ticket.db"
    _legacy_ticket_db(path)
    _ensure_ticket_external_ref_column(path)
    _ensure_ticket_external_ref_column(path)
    assert _ticket_columns(path).count("external_ref") == 1
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("SELECT subject, external_ref FROM ticket").fetchall() == [
            ("Old ticket", None)]
    finally:
        conn.close()
    assert "ix_ticket_external_ref" in _ticket_indexes(path)


@pytest.mark.asyncio
async def test_init_models_adds_external_ref_to_old_ticket_table(tmp_path, monkeypatch):
    import app.core.database as dbmod

    path = tmp_path / "legacy-init.db"
    _legacy_ticket_db(path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    monkeypatch.setattr(dbmod, "engine", engine)
    try:
        await dbmod.init_models()
        await dbmod.init_models()
    finally:
        await engine.dispose()
    assert _ticket_columns(path).count("external_ref") == 1
    assert "ix_ticket_external_ref" in _ticket_indexes(path)


# ---------------------------------------------------------------------------
# Staff-side webhook wiring (web routes)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_staff_edit_comment_status_and_close_dispatch_webhooks(api, tmp_path, monkeypatch):
    from app.web import routes as web

    client, db, ws, tickets, keys, ada = api  # noqa: F001  (client kept alive for the app)
    ticket = tickets["mobilis"]
    calls = []

    async def capture(db_arg, workspace_id, event, ticket_arg, **kwargs):
        calls.append((event, kwargs))

    monkeypatch.setattr(web, "dispatch_ticket_webhook", capture)
    monkeypatch.chdir(tmp_path)  # the comment route writes logs in cwd

    request = SimpleNamespace(
        session={"user_id": ada.id}, headers={}, state=SimpleNamespace(),
        url=SimpleNamespace(path="/web/tickets/1", query=""), method="POST",
    )

    class _Form(dict):
        def getlist(self, key):
            return self.get(key, [])

    async def form():
        return _Form({"subject": "Printer down (updated)", "description": "Desk 4",
                      "priority": "high", "category": "support", "scheduled_date": "",
                      "due_date": "", "assigned_to_id": "", "working_days": ["0", "1"]})

    edit_request = SimpleNamespace(**vars(request))
    edit_request.form = form
    await web.web_tickets_edit(edit_request, ticket.id, db=db)
    await web.web_tickets_add_comment(
        request, ticket.id, content="Technician on the way", is_internal=False, files=[],
        background_tasks=BackgroundTasks(), db=db,
    )
    await web.web_tickets_add_comment(
        request, ticket.id, content="Private note", is_internal=True, files=[],
        background_tasks=BackgroundTasks(), db=db,
    )
    await web.web_tickets_update_status(request, ticket.id, status="in_progress", db=db)
    await web.web_tickets_close_with_details(request, ticket.id, close_type="sla", db=db)

    events = [event for event, _ in calls]
    assert events.count("ticket.updated") == 2  # edit + status change
    assert events.count("ticket.commented") == 2  # private notes are filtered in dispatcher
    assert "ticket.closed" in events
    assert all(kwargs.get("source_api_key_id") is None for _, kwargs in calls)
    comments = [kwargs["comment"] for event, kwargs in calls if event == "ticket.commented"]
    assert [c.is_internal for c in comments] == [False, True]
