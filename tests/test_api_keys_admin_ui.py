"""Slice 03: admin UI for scoped API keys, client locks and two-way sync.

Executable user journeys:
- As a workspace admin, I create a scoped key for a partner from the API Keys
  page and the raw key immediately works with only that client's tickets.
- As a workspace admin, I edit an existing key's scopes, client lock and webhook
  without regenerating the key, and unsafe webhook URLs are rejected cleanly.
- As a non-admin or another workspace's admin, I cannot view or change keys
  that are not mine.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select
from starlette.datastructures import QueryParams
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import urlsplit

from app.api.routes import external
from app.core.api_webhooks import validate_webhook_url as _real_validate_webhook_url
from app.core.database import get_session
from app.models.api_key import APIKey
from app.models.company import Company
from app.models.ticket import Ticket
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes as web

KEY_EDIT = "cem_ui_edit0123456789abcd"
KEY_OTHER_WS = "cem_ui_other0123456789abc"
KEY_CROSS = "cem_ui_cross0123456789ab"
NOW = datetime.utcnow().replace(microsecond=0)
PARTNER_SCOPES = [
    "tickets:read", "tickets:create", "tickets:update", "tickets:comment",
]


@pytest.fixture(autouse=True)
def _legacy_template_call(monkeypatch):
    """Bridge the repo's legacy TemplateResponse call to the installed Starlette."""
    def render(name, context, *args, **kwargs):
        return Jinja2Templates.TemplateResponse(
            web.templates, context.get("request"), name, context, *args, **kwargs
        )

    monkeypatch.setattr(web, "_original_template_response", render)


@pytest.fixture(autouse=True)
def _public_webhook_hosts(monkeypatch):
    """Avoid real DNS for partner test hosts while keeping SSRF checks intact."""
    allowed_hosts = {"hooks.partner.test", "new.partner.test"}

    def validate(url):
        parts = urlsplit(url) if isinstance(url, str) else None
        if parts and parts.scheme == "https" and parts.hostname in allowed_hosts:
            return url
        return _real_validate_webhook_url(url)

    monkeypatch.setattr(web, "validate_webhook_url", validate, raising=False)


def _key(raw: str, workspace_id: int, name: str, **fields) -> APIKey:
    return APIKey(
        workspace_id=workspace_id, name=name, key_prefix=raw[:12],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(), **fields,
    )


def _request(user, session=None):
    return SimpleNamespace(
        session=session if session is not None else {"user_id": user.id},
        headers={}, state=SimpleNamespace(),
        url=SimpleNamespace(path="/web/admin/api-keys", query=""),
        query_params=QueryParams(),
    )


@pytest_asyncio.fixture
async def ui(tmp_path):
    import app.models  # noqa: F401  (register every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'admin_ui.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)

    mobilis_ws = Workspace(name="Mobilis workspace", timezone="UTC")
    other_ws = Workspace(name="Other workspace", timezone="UTC")
    db.add_all([mobilis_ws, other_ws])
    await db.flush()

    admin = User(username="jp", hashed_password="x", is_admin=True,
                 full_name="JP", workspace_id=mobilis_ws.id)
    tech = User(username="tech", hashed_password="x", workspace_id=mobilis_ws.id)
    outsider = User(username="olive", hashed_password="x", is_admin=True,
                    workspace_id=other_ws.id)
    db.add_all([admin, tech, outsider])
    await db.flush()

    db.add_all([
        Company(name="Mobilis", workspace_id=mobilis_ws.id, created_by=admin.id),
        Company(name="Acme", workspace_id=mobilis_ws.id, created_by=admin.id),
        Company(name="Foreign Client", workspace_id=other_ws.id, created_by=outsider.id),
    ])

    tickets = {
        "mobilis": Ticket(
            ticket_number="TKT-2026-90001", subject="Mobilis printer", workspace_id=mobilis_ws.id,
            is_guest=True, guest_company="Mobilis", guest_email="leon@mobilis.co.za",
            created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=1),
        ),
        "acme": Ticket(
            ticket_number="TKT-2026-90002", subject="Acme printer", workspace_id=mobilis_ws.id,
            is_guest=True, guest_company="Acme", guest_email="jane@acme.test",
            created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=1),
        ),
        "guest_only": Ticket(
            ticket_number="TKT-2026-90003", subject="Walk-in client", workspace_id=mobilis_ws.id,
            is_guest=True, guest_company="Guest Only Co", guest_email="walkin@example.test",
            created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=1),
        ),
        "foreign": Ticket(
            ticket_number="TKT-2026-90004", subject="Other workspace", workspace_id=other_ws.id,
            is_guest=True, guest_company="Mobilis", guest_email="leon@mobilis.co.za",
            created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=1),
        ),
    }
    db.add_all(tickets.values())

    keys = {
        "edit": _key(KEY_EDIT, mobilis_ws.id, "Leon's key", scopes="tickets:read",
                     allowed_company="Mobilis", webhook_url="https://hooks.partner.test/old",
                     webhook_secret="existing-signing-secret",
                     webhook_events="ticket.created"),
        "cross": _key(KEY_CROSS, mobilis_ws.id, "Do not touch"),
        "other": _key(KEY_OTHER_WS, other_ws.id, "Other workspace key"),
    }
    db.add_all(keys.values())
    await db.commit()

    app = FastAPI()

    @app.get("/_test_login")
    async def _test_login(request: Request):
        request.session["user_id"] = admin.id
        return {"ok": True}

    app.include_router(external.router, prefix="/api")
    app.include_router(web.router, prefix="/web")
    app.add_middleware(SessionMiddleware, secret_key="cem-ui-test-session")

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client.get("/_test_login")

    yield SimpleNamespace(
        db=db, engine=engine, client=client, admin=admin, tech=tech, outsider=outsider,
        ws=mobilis_ws, other_ws=other_ws, tickets=tickets, keys=keys,
    )

    await client.aclose()
    await db.close()
    await engine.dispose()


async def _page(ui, user=None, session=None) -> str:
    response = await web.web_admin_api_keys(_request(user or ui.admin, session), db=ui.db)
    assert response.status_code == 200
    return response.body.decode()


async def _stored_key(ui, key_id: int) -> APIKey:
    return (await ui.db.execute(select(APIKey).where(APIKey.id == key_id))).scalar_one()


async def _create(ui, **overrides):
    form = dict(
        name="Leon / Mobilis", access_mode="scoped", scopes=PARTNER_SCOPES,
        allowed_company="  Mobilis ", allowed_email="", allowed_email_domain="",
        allow_internal_comments=False, webhook_url="https://hooks.partner.test/cem",
        webhook_secret="", webhook_events=["ticket.created", "ticket.updated"],
    )
    form.update(overrides)
    request = _request(ui.admin)
    response = await web.web_admin_api_keys_create(request, db=ui.db, **form)
    return response, request.session


# ---------------------------------------------------------------------------
# Page: company selector, badges, docs and one-time secret display
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_page_shows_workspace_companies_and_guest_companies(ui):
    html = await _page(ui)

    assert '<datalist id="api-key-companies">' in html
    for company in ("Mobilis", "Acme", "Guest Only Co"):
        assert f'<option value="{company}">' in html
    assert "Foreign Client" not in html


@pytest.mark.asyncio
async def test_page_documents_api_endpoints_and_webhook_headers(ui):
    html = await _page(ui)

    for text in (
        "GET /api/v1/me",
        "updated_since",
        "include=comments,history",
        "POST /api/v1/tickets",
        "external_ref",
        "PATCH /api/v1/tickets/{ticket_number}",
        "closing_notes",
        "close_type",
        "/comments",
        "X-CEM-Event",
        "X-CEM-Timestamp",
        "X-CEM-Signature",
    ):
        assert text in html


@pytest.mark.asyncio
async def test_created_key_shows_one_time_key_and_generated_webhook_secret(ui):
    response, session = await _create(ui)
    assert response.status_code == 303
    assert response.headers["location"] == "/web/admin/api-keys"

    raw = session["new_api_key"]
    secret = session["new_webhook_secret"]
    assert raw.startswith("cem_") and len(raw) > 20
    assert secret and len(secret) >= 32

    stored = (await ui.db.execute(select(APIKey).where(APIKey.key_prefix == raw[:12]))).scalar_one()
    assert stored.webhook_secret == secret

    html = await _page(ui, session=session)
    assert raw in html
    assert secret in html
    assert "Webhook Active" in html


@pytest.mark.asyncio
async def test_key_cards_show_full_access_and_scope_lock_badges(ui):
    await _create(ui)
    await _create(ui, name="Full key", access_mode="full", scopes=[],
                  webhook_url="", webhook_events=[])

    html = await _page(ui)
    assert "Full Access" in html
    assert "tickets:read" in html
    assert "Locked to: Mobilis" in html
    assert "Webhook Active" in html
    assert f'/web/admin/api-keys/{ui.keys["edit"].id}/update' in html


# ---------------------------------------------------------------------------
# Create: validation, normalization and immediate API usability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_scoped_key_locked_to_mobilis_works_immediately(ui):
    response, session = await _create(ui)
    assert response.status_code == 303

    stored = (await ui.db.execute(
        select(APIKey).where(APIKey.key_prefix == session["new_api_key"][:12])
    )).scalar_one()
    assert stored.scopes == ",".join(PARTNER_SCOPES)
    assert stored.allowed_company == "Mobilis"
    assert stored.allow_internal_comments is False
    assert stored.webhook_events == "ticket.created,ticket.updated"

    headers = {"X-API-Key": session["new_api_key"]}
    tickets = await ui.client.get("/api/v1/tickets", headers=headers)
    assert tickets.status_code == 200, tickets.text
    assert [t["ticket_number"] for t in tickets.json()["tickets"]] == ["TKT-2026-90001"]

    users = await ui.client.get("/api/v1/users", headers=headers)
    assert users.status_code == 403

    foreign = await ui.client.get("/api/v1/tickets/TKT-2026-90002", headers=headers)
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_create_full_access_mode_clears_scopes(ui):
    _, session = await _create(ui, name="Full key", access_mode="full", scopes=[],
                                  allowed_company="", webhook_url="", webhook_events=[])
    stored = (await ui.db.execute(
        select(APIKey).where(APIKey.key_prefix == session["new_api_key"][:12])
    )).scalar_one()
    assert stored.scopes is None
    assert stored.allowed_company is None


@pytest.mark.asyncio
async def test_create_normalizes_email_and_domain(ui):
    _, session = await _create(ui, allowed_company="", allowed_email="  Leon@Mobilis.CO.ZA ",
                                  allowed_email_domain=" @Mobilis.CO.ZA ", webhook_url="")
    stored = (await ui.db.execute(
        select(APIKey).where(APIKey.key_prefix == session["new_api_key"][:12])
    )).scalar_one()
    assert stored.allowed_company is None
    assert stored.allowed_email == "leon@mobilis.co.za"
    assert stored.allowed_email_domain == "mobilis.co.za"


@pytest.mark.asyncio
async def test_real_http_form_posts_bind_repeated_fields(ui):
    """Exercise the browser path: Form parsing, repeated checkboxes and routing."""
    create = await ui.client.post(
        "/web/admin/api-keys/create",
        data={
            "name": "HTTP Form Key",
            "access_mode": "scoped",
            "scopes": ["tickets:read", "tickets:comment"],
            "allowed_company": "Mobilis",
            "webhook_url": "https://hooks.partner.test/http",
            "webhook_events": ["ticket.created"],
        },
        follow_redirects=False,
    )
    assert create.status_code == 303

    created = (await ui.db.execute(
        select(APIKey).where(APIKey.name == "HTTP Form Key")
    )).scalar_one()
    assert created.scopes == "tickets:read,tickets:comment"
    assert created.webhook_events == "ticket.created"
    old_secret = created.webhook_secret
    assert old_secret

    page = await ui.client.get("/web/admin/api-keys")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store, private"
    assert "HTTP Form Key" in page.text

    update = await ui.client.post(
        f"/web/admin/api-keys/{created.id}/update",
        data={
            "name": "HTTP Form Key Updated",
            "access_mode": "full",
            "webhook_url": "https://hooks.partner.test/http",
            "webhook_secret_rotate": "on",
        },
        follow_redirects=False,
    )
    assert update.status_code == 303
    await ui.db.refresh(created)
    assert created.name == "HTTP Form Key Updated"
    assert created.scopes is None
    assert created.webhook_secret != old_secret


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"name": "  "}, "Key name is required"),
        ({"access_mode": "scoped", "scopes": []}, "Select at least one permission"),
        ({"scopes": ["tickets:read", "bogus:scope"]}, "Unknown permission"),
        ({"webhook_url": "http://127.0.0.1/hook"}, "Webhook URL host is not public"),
        ({"webhook_url": "ftp://partner.test/hook"}, "Webhook URL must be http or https"),
    ],
)
@pytest.mark.asyncio
async def test_create_rejects_invalid_input_without_storing_a_key(ui, overrides, message):
    before = len((await ui.db.execute(select(APIKey))).scalars().all())
    request = _request(ui.admin)
    form = dict(
        name="Partner", access_mode="scoped", scopes=PARTNER_SCOPES,
        allowed_company="Mobilis", allowed_email="", allowed_email_domain="",
        allow_internal_comments=False, webhook_url="", webhook_secret="",
        webhook_events=["ticket.created"],
    )
    form.update(overrides)

    response = await web.web_admin_api_keys_create(request, db=ui.db, **form)

    assert response.status_code == 303
    assert message in request.session["error_message"]
    assert "new_api_key" not in request.session
    assert len((await ui.db.execute(select(APIKey))).scalars().all()) == before


# ---------------------------------------------------------------------------
# Update: scopes, locks, webhooks and secret lifecycle
# ---------------------------------------------------------------------------

async def _update(ui, key=None, **overrides):
    key = key or ui.keys["edit"]
    form = dict(
        name="Leon's key (updated)", access_mode="scoped",
        scopes=["tickets:read", "tickets:comment"],
        allowed_company="", allowed_email=" Leon@Mobilis.CO.ZA ",
        allowed_email_domain=" @Mobilis.co.za ", allow_internal_comments=True,
        webhook_url="https://new.partner.test/hook", webhook_secret="",
        webhook_events=["ticket.updated"], webhook_secret_rotate=False,
        webhook_secret_clear=False,
    )
    form.update(overrides)
    request = _request(ui.admin)
    response = await web.web_admin_api_keys_update(request, key.id, db=ui.db, **form)
    return response, request.session


@pytest.mark.asyncio
async def test_admin_can_edit_key_without_regenerating_it(ui):
    key = ui.keys["edit"]
    original_prefix, original_hash = key.key_prefix, key.key_hash
    response, session = await _update(ui)

    assert response.status_code == 303
    await ui.db.refresh(key)
    assert key.key_prefix == original_prefix
    assert key.key_hash == original_hash
    assert key.name == "Leon's key (updated)"
    assert key.scopes == "tickets:read,tickets:comment"
    assert key.allowed_company is None
    assert key.allowed_email == "leon@mobilis.co.za"
    assert key.allowed_email_domain == "mobilis.co.za"
    assert key.allow_internal_comments is True
    assert key.webhook_url == "https://new.partner.test/hook"
    assert key.webhook_events == "ticket.updated"
    assert key.webhook_secret == "existing-signing-secret"
    assert "new_webhook_secret" not in session


@pytest.mark.asyncio
async def test_edit_preserves_or_rotates_or_clears_webhook_secret(ui):
    key = ui.keys["edit"]

    await _update(ui)
    await ui.db.refresh(key)
    assert key.webhook_secret == "existing-signing-secret"

    _, rotate_session = await _update(ui, webhook_secret_rotate=True)
    await ui.db.refresh(key)
    assert key.webhook_secret == rotate_session["new_webhook_secret"]
    assert key.webhook_secret != "existing-signing-secret"

    # A queued-but-unseen secret must not resurface after a later edit.
    _, next_session = await _update(ui)
    assert "new_webhook_secret" not in next_session

    _, clear_session = await _update(ui, webhook_secret_clear=True)
    await ui.db.refresh(key)
    assert key.webhook_secret is None
    assert "new_webhook_secret" not in clear_session

    # A disabled webhook must stay disabled on later edits that leave secret blank.
    _, later_session = await _update(ui, webhook_secret="")
    await ui.db.refresh(key)
    assert key.webhook_secret is None
    assert "new_webhook_secret" not in later_session


@pytest.mark.asyncio
async def test_page_flags_webhook_without_signing_secret(ui):
    await _update(ui, webhook_secret_clear=True)
    html = await _page(ui)
    assert "Webhook configured (no signing secret)" in html
    assert "Webhook Active" not in html


@pytest.mark.asyncio
async def test_edit_explicit_secret_is_stored_and_shown_once(ui):
    _, session = await _update(ui, webhook_secret="new-manual-secret-0123456789")
    key = await _stored_key(ui, ui.keys["edit"].id)
    assert key.webhook_secret == "new-manual-secret-0123456789"
    assert session["new_webhook_secret"] == "new-manual-secret-0123456789"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"webhook_url": "http://127.0.0.1/hook"}, "Webhook URL host is not public"),
        ({"webhook_url": "https://metadata.google.internal/hook"}, "Webhook URL host is not allowed"),
        ({"scopes": ["tickets:read", "unknown"]}, "Unknown permission"),
        ({"access_mode": "scoped", "scopes": []}, "Select at least one permission"),
    ],
)
@pytest.mark.asyncio
async def test_edit_rejects_unsafe_or_invalid_configuration(ui, overrides, message):
    key = ui.keys["edit"]
    original = (key.name, key.scopes, key.webhook_url, key.webhook_secret)
    request = _request(ui.admin)
    form = dict(
        name="Changed", access_mode="scoped", scopes=["tickets:read"],
        allowed_company="Mobilis", allowed_email="", allowed_email_domain="",
        allow_internal_comments=False, webhook_url="https://new.partner.test/hook",
        webhook_secret="", webhook_events=["ticket.created"],
        webhook_secret_rotate=False, webhook_secret_clear=False,
    )
    form.update(overrides)

    response = await web.web_admin_api_keys_update(request, key.id, db=ui.db, **form)

    assert response.status_code == 303
    assert message in request.session["error_message"]
    assert (key.name, key.scopes, key.webhook_url, key.webhook_secret) == original


@pytest.mark.asyncio
async def test_edit_full_mode_clears_scopes_and_filters(ui):
    await _update(ui, access_mode="full", scopes=[], allowed_email="",
                  allowed_email_domain="", allow_internal_comments=False)
    key = await _stored_key(ui, ui.keys["edit"].id)
    assert key.scopes is None
    assert key.allowed_company is None
    assert key.allowed_email is None
    assert key.allowed_email_domain is None


# ---------------------------------------------------------------------------
# Authorization and workspace isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signed_out_admins_are_redirected_to_login(ui):
    request = _request(ui.admin, session={})
    response = await web.web_admin_api_keys(request, db=ui.db)
    assert response.status_code == 303
    assert response.headers["location"] == "/web/login"


@pytest.mark.asyncio
async def test_deactivated_admin_is_logged_out_and_cannot_mutate_keys(ui):
    ui.admin.is_active = False
    await ui.db.commit()
    calls = (
        lambda r: web.web_admin_api_keys(r, db=ui.db),
        lambda r: web.web_admin_api_keys_create(
            r, name="Inactive", access_mode="full", scopes=[], allowed_company="",
            allowed_email="", allowed_email_domain="", allow_internal_comments=False,
            webhook_url="", webhook_secret="", webhook_events=[], db=ui.db,
        ),
        lambda r: web.web_admin_api_keys_update(
            r, ui.keys["edit"].id, name="Inactive", access_mode="full", scopes=[],
            allowed_company="", allowed_email="", allowed_email_domain="",
            allow_internal_comments=False, webhook_url="", webhook_secret="",
            webhook_events=[], webhook_secret_rotate=False,
            webhook_secret_clear=False, db=ui.db,
        ),
        lambda r: web.web_admin_api_keys_revoke(r, ui.keys["edit"].id, db=ui.db),
        lambda r: web.web_admin_api_keys_delete(r, ui.keys["edit"].id, db=ui.db),
    )
    for call in calls:
        request = _request(ui.admin)
        response = await call(request)
        assert response.status_code == 303
        assert response.headers["location"] == "/web/login"
        assert request.session == {}

    key = await _stored_key(ui, ui.keys["edit"].id)
    assert key.name == "Leon's key"
    assert key.is_active is True


@pytest.mark.asyncio
async def test_non_admin_cannot_view_create_or_edit_keys(ui):
    with pytest.raises(HTTPException) as page:
        await web.web_admin_api_keys(_request(ui.tech), db=ui.db)
    assert page.value.status_code == 403

    with pytest.raises(HTTPException) as create:
        await web.web_admin_api_keys_create(
            _request(ui.tech), name="Illegal", access_mode="full", scopes=[],
            allowed_company="", allowed_email="", allowed_email_domain="",
            allow_internal_comments=False, webhook_url="", webhook_secret="",
            webhook_events=[], db=ui.db,
        )
    assert create.value.status_code == 403

    with pytest.raises(HTTPException) as update_non_admin:
        await web.web_admin_api_keys_update(
            _request(ui.tech), ui.keys["edit"].id, name="Illegal", access_mode="full",
            scopes=[], allowed_company="", allowed_email="", allowed_email_domain="",
            allow_internal_comments=False, webhook_url="", webhook_secret="",
            webhook_events=[], webhook_secret_rotate=False, webhook_secret_clear=False,
            db=ui.db,
        )
    assert update_non_admin.value.status_code == 403


@pytest.mark.asyncio
async def test_other_workspace_admin_sees_only_own_keys_and_gets_404_on_edit(ui):
    html = await _page(ui, user=ui.outsider)
    assert "Other workspace key" in html
    assert "Leon's key" not in html
    assert "Do not touch" not in html

    for action in (web.web_admin_api_keys_update, web.web_admin_api_keys_revoke,
                   web.web_admin_api_keys_delete):
        request = _request(ui.outsider)
        if action is web.web_admin_api_keys_update:
            with pytest.raises(HTTPException) as exc:
                await action(
                    request, ui.keys["cross"].id, name="Stolen", access_mode="full",
                    scopes=[], allowed_company="", allowed_email="",
                    allowed_email_domain="", allow_internal_comments=False,
                    webhook_url="", webhook_secret="", webhook_events=[],
                    webhook_secret_rotate=False, webhook_secret_clear=False, db=ui.db,
                )
        else:
            with pytest.raises(HTTPException) as exc:
                await action(request, ui.keys["cross"].id, db=ui.db)
        assert exc.value.status_code == 404

    assert (await _stored_key(ui, ui.keys["cross"].id)).is_active is True
