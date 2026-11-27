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
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
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
from app.models.ticket import Ticket, TicketComment
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


def _element_by_id(html: str, element_id: str) -> str:
    """Return the opening tag of the element carrying ``element_id``."""
    match = re.search(
        r"<[a-zA-Z][^>]*\bid=[\"']" + re.escape(element_id) + r"[\"'][^>]*>", html
    )
    assert match, f"No element with id={element_id!r} in rendered HTML"
    return match.group(0)


def _script_containing(html: str, needle: str) -> str:
    """Return the inline <script> block that contains ``needle``."""
    for block in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
        if needle in block:
            return block
    pytest.fail(f"No <script> block containing {needle!r}")


# Minimal DOM stub so the real inline copy script can be executed under Node.
# It exercises observable outcomes (clipboard content, button label/classes,
# selection, timers) rather than merely matching source text.
_COPY_JS_PRELUDE = r"""
const assert = require('assert');

function makeNode(tag, id) {
  const classes = new Set();
  return {
    tagName: String(tag).toUpperCase(), id: id || '', value: '', textContent: '',
    dataset: {}, style: {}, attributes: {}, parentNode: null, children: [],
    scrollTop: 0, focused: false, selected: false, selection: null,
    scrollArgs: undefined,
    classList: {
      add: function () { for (const c of arguments) classes.add(c); },
      remove: function () { for (const c of arguments) classes.delete(c); },
      contains: function (c) { return classes.has(c); },
    },
    focus: function () { this.focused = true; },
    select: function () { this.selected = true; },
    setSelectionRange: function (a, b) { this.selection = [a, b]; },
    scrollIntoView: function (arg) { this.scrollArgs = arg; },
    setAttribute: function (k, v) { this.attributes[k] = v; },
    removeAttribute: function (k) { delete this.attributes[k]; },
    appendChild: function (n) { n.parentNode = this; this.children.push(n); },
    removeChild: function (n) {
      n.parentNode = null;
      this.children = this.children.filter(x => x !== n);
    },
  };
}

const input = makeNode('input', 'newKeyDisplay');
input.value = 'cem_secret_value';
const status = makeNode('span', 'newKeyDisplayCopyStatus');
const banner = makeNode('div', 'newKeyBanner');
const main = makeNode('div', 'main-content');
main.scrollTop = 250;
const nodes = {
  newKeyDisplay: input,
  newKeyDisplayCopyStatus: status,
  newKeyBanner: banner,
  'main-content': main,
};
const created = [];
let execResult = true;
let copiedText = null;
let timerId = 0;
const timers = new Map();

global.document = {
  getElementById: function (id) { return nodes[id] || null; },
  querySelectorAll: function () { return []; },
  createElement: function (tag) {
    const node = makeNode(tag);
    created.push(node);
    return node;
  },
  body: makeNode('body'),
  execCommand: function (cmd) { assert.strictEqual(cmd, 'copy'); return execResult; },
};
global.window = {
  isSecureContext: true,
  location: { pathname: '/web/admin/api-keys' },
  requestAnimationFrame: function (cb) { cb(); },
};
Object.defineProperty(global, 'navigator', {
  configurable: true,
  writable: true,
  value: {
    clipboard: {
      writeText: function (text) { copiedText = text; return Promise.resolve(); },
    },
  },
});
const storage = { 'scrollPos_/web/admin/api-keys': '250' };
global.sessionStorage = {
  removeItem: function (key) { delete storage[key]; },
  getItem: function (key) { return storage[key] === undefined ? null : storage[key]; },
};
global.setTimeout = function (cb, ms) {
  timerId += 1;
  timers.set(timerId, { cb: cb, ms: ms });
  return timerId;
};
global.clearTimeout = function (id) { timers.delete(id); };

function freshButton() {
  const b = makeNode('button');
  b.textContent = 'Copy';
  return b;
}

function onlyTimer(expectedMs) {
  const pending = Array.from(timers.values());
  assert.strictEqual(pending.length, 1, 'expected exactly one pending reset timer');
  assert.strictEqual(pending[0].ms, expectedMs);
  timers.clear();
  return pending[0].cb;
}
"""

_COPY_JS_EPILOGUE = r"""
(async function () {
  // The banner IIFE runs on load, before base.html can restore the scroll key.
  assert.strictEqual(storage['scrollPos_/web/admin/api-keys'], undefined);
  assert.strictEqual(main.scrollTop, 0);
  assert.deepStrictEqual(banner.scrollArgs, { block: 'start' });

  // Secure context, clipboard resolves: copies exact value + green feedback.
  let button = freshButton();
  window.isSecureContext = true;
  navigator.clipboard.writeText = function (text) {
    copiedText = text;
    return Promise.resolve();
  };
  execResult = false;
  cemCopy('newKeyDisplay', button);
  await new Promise(function (resolve) { setImmediate(resolve); });
  assert.strictEqual(copiedText, 'cem_secret_value');
  assert.strictEqual(button.textContent, 'Copied!');
  assert.ok(button.classList.contains('bg-green-600'));
  assert.ok(status.textContent.indexOf('Copied') !== -1);
  onlyTimer(2000)();
  assert.strictEqual(button.textContent, 'Copy');
  assert.ok(button.classList.contains('bg-amber-500'));

  // Clipboard rejects: fall back to execCommand, still report success.
  button = freshButton();
  navigator.clipboard.writeText = function () {
    return Promise.reject(new Error('permission denied'));
  };
  execResult = true;
  input.selected = false;
  cemCopy('newKeyDisplay', button);
  await new Promise(function (resolve) { setImmediate(resolve); });
  assert.strictEqual(button.textContent, 'Copied!');
  assert.ok(input.selected);
  onlyTimer(2000)();

  // Plain HTTP: no clipboard API, synchronous execCommand fallback succeeds.
  button = freshButton();
  window.isSecureContext = false;
  cemCopy('newKeyDisplay', button);
  assert.strictEqual(button.textContent, 'Copied!');
  onlyTimer(2000)();

  // A writeText implementation that throws synchronously must not fail silently.
  button = freshButton();
  window.isSecureContext = true;
  navigator.clipboard.writeText = function () { throw new Error('security error'); };
  cemCopy('newKeyDisplay', button);
  assert.strictEqual(button.textContent, 'Copied!');
  onlyTimer(2000)();

  // Both methods fail: select the field, show persistent guidance for 3s.
  button = freshButton();
  window.isSecureContext = false;
  execResult = false;
  input.focused = false;
  input.selected = false;
  cemCopy('newKeyDisplay', button);
  assert.strictEqual(button.textContent, 'Press Ctrl+C');
  assert.ok(input.focused && input.selected);
  assert.deepStrictEqual(input.selection, [0, 'cem_secret_value'.length]);
  assert.ok(status.textContent.indexOf('Ctrl+C') !== -1);
  onlyTimer(3000)();
  assert.strictEqual(button.textContent, 'Copy');

  // Repeated clicks replace the reset timer instead of racing it.
  button = freshButton();
  window.isSecureContext = false;
  execResult = true;
  cemCopy('newKeyDisplay', button);
  cemCopy('newKeyDisplay', button);
  onlyTimer(2000)();

  // Non-input nodes use a temporary, in-viewport textarea that is cleaned up.
  button = freshButton();
  const code = makeNode('code');
  delete code.value;
  code.textContent = '  spaced  ';
  nodes.newKeyDisplay = code;
  cemCopy('newKeyDisplay', button);
  const temporary = created[created.length - 1];
  assert.strictEqual(temporary.value, 'spaced');
  assert.strictEqual(temporary.style.opacity, '0');
  assert.strictEqual(temporary.parentNode, null);
  assert.strictEqual(document.body.children.length, 0);
  onlyTimer(2000)();

  console.log('OK');
})().catch(function (error) {
  console.error(error);
  process.exitCode = 1;
});
"""


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
        "GET /api/v1/comments",
        "include_internal=true|false",
        "is_internal: true",
        "X-CEM-Event",
        "X-CEM-Timestamp",
        "X-CEM-Signature",
    ):
        assert text in html


@pytest.mark.asyncio
async def test_internal_permission_checkboxes_submit_true_and_explain_effects(ui):
    html = await _page(ui)
    boxes = re.findall(
        r'<input[^>]*name="allow_internal_comments"[^>]*>', html)
    assert len(boxes) >= 2, boxes          # create form + every editable key
    assert all('value="true"' in box for box in boxes)
    for text in (
        "automatically included in API responses &amp; webhooks",
        "post <code class=\"font-mono\">is_internal: true</code>",
    ):
        assert text in html


async def _internal_test_comment(ui, content):
    ui.db.add(TicketComment(
        ticket_id=ui.tickets["mobilis"].id, user_id=ui.admin.id, content=content,
        is_internal=True, created_at=NOW,
    ))
    await ui.db.commit()


async def _raw_key_from_new_key_banner(ui) -> str:
    html = (await ui.client.get("/web/admin/api-keys")).text
    element = _element_by_id(html, "newKeyDisplay")
    match = re.search(r'value="([^"]+)"', element)
    assert match, element
    return match.group(1)


async def _api_bodies(ui, raw_key: str):
    headers = {"X-API-Key": raw_key}
    number = ui.tickets["mobilis"].ticket_number
    paths = (
        f"/api/v1/tickets/{number}",
        "/api/v1/tickets?include=comments",
        "/api/v1/comments?limit=100",
    )
    bodies = []
    for path in paths:
        response = await ui.client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        if "tickets" in data:
            bodies.append([c["body"] for t in data["tickets"] for c in t["comments"]])
        else:                                # detail and /comments both use "comments"
            bodies.append([c["body"] for c in data["comments"]])
    return bodies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("checkbox", "expected"),
    [("true", True), ("on", True), (None, False)],
    ids=["value_true", "value_on", "omitted"],
)
async def test_real_http_create_form_persists_internal_permission(ui, checkbox, expected):
    """The browser's checkbox values really reach SQLite and the external API."""
    await _internal_test_comment(ui, "HTTP private note")
    data = {
        "name": "Internal form key", "access_mode": "scoped", "scopes": PARTNER_SCOPES,
        "allowed_company": "Mobilis", "webhook_url": "",
    }
    if checkbox is not None:
        data["allow_internal_comments"] = checkbox
    response = await ui.client.post(
        "/web/admin/api-keys/create", data=data, follow_redirects=False)
    assert response.status_code == 303

    raw = await _raw_key_from_new_key_banner(ui)
    stored = (await ui.db.execute(
        select(APIKey).where(APIKey.key_prefix == raw[:12]))).scalar_one()
    assert bool(stored.allow_internal_comments) is expected

    bodies = await _api_bodies(ui, raw)
    for page_bodies in bodies:
        assert ("HTTP private note" in page_bodies) is expected
    me = (await ui.client.get("/api/v1/me", headers={"X-API-Key": raw})).json()
    assert me["allow_internal_comments"] is expected
    assert me["can_access_internal_comments"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("checkbox", "expected"),
    [("true", True), ("on", True), (None, False)],
    ids=["value_true", "value_on", "omitted"],
)
async def test_real_http_update_form_persists_internal_permission(ui, checkbox, expected):
    """Editing the same key flips both the DB setting and immediate API visibility."""
    await _internal_test_comment(ui, "HTTP private note")
    key = ui.keys["edit"]
    data = {
        "name": "Updated internal key", "access_mode": "scoped",
        "scopes": ["tickets:read"], "allowed_company": "Mobilis", "webhook_url": "",
    }
    if checkbox is not None:
        data["allow_internal_comments"] = checkbox
    response = await ui.client.post(
        f"/web/admin/api-keys/{key.id}/update", data=data, follow_redirects=False)
    assert response.status_code == 303
    await ui.db.refresh(key)
    assert bool(key.allow_internal_comments) is expected

    bodies = await _api_bodies(ui, KEY_EDIT)
    for page_bodies in bodies:
        assert ("HTTP private note" in page_bodies) is expected
    me = (await ui.client.get("/api/v1/me",
                              headers={"X-API-Key": KEY_EDIT})).json()
    assert me["can_access_internal_comments"] is expected


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


# ---------------------------------------------------------------------------
# One-time secrets: selectable fields, universal copy, and visible banner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_created_secrets_render_as_selectable_readonly_inputs(ui):
    _, session = await _create(ui)
    raw, secret = session["new_api_key"], session["new_webhook_secret"]
    html = await _page(ui, session=session)

    for element_id, value in (
        ("newKeyDisplay", raw),
        ("newWebhookSecretDisplay", secret),
    ):
        element = _element_by_id(html, element_id)
        assert element.startswith("<input "), element
        assert 'type="text"' in element
        assert "readonly" in element
        assert f'value="{value}"' in element
        assert 'autocomplete="off"' in element
        assert 'spellcheck="false"' in element
        assert "this.focus(); this.select();" in element
        assert "this.setSelectionRange(0, this.value.length)" in element
        assert "font-mono" in element and "w-full" in element
        assert "bg-white" in element and "text-slate-800" in element


@pytest.mark.asyncio
async def test_secret_fields_have_labels_and_live_copy_status(ui):
    _, session = await _create(ui)
    assert session["new_api_key"]
    html = await _page(ui, session=session)

    for element_id in ("newKeyDisplay", "newWebhookSecretDisplay"):
        assert f'<label for="{element_id}"' in html
        status = _element_by_id(html, f"{element_id}CopyStatus")
        assert 'role="status"' in status
        assert 'aria-live="polite"' in status
        assert "sr-only" in status


@pytest.mark.asyncio
async def test_copy_buttons_target_the_readonly_secret_inputs(ui):
    _, session = await _create(ui)
    html = await _page(ui, session=session)

    for element_id in ("newKeyDisplay", "newWebhookSecretDisplay"):
        onclick = f"cemCopy('{element_id}', this)"
        assert f'onclick="{onclick}"' in html
        button = re.search(
            r"<button[^>]*\bonclick=\"" + re.escape(onclick) + r"\"[^>]*>(.*?)</button>",
            html, re.S,
        )
        assert button, f"No Copy button wired to {element_id}"
        assert button.group(1).strip() == "Copy"


@pytest.mark.asyncio
async def test_copy_script_guards_clipboard_and_falls_back_to_exec_command(ui):
    script = _script_containing(await _page(ui), "function cemCopy")

    assert "function fallbackCopy(text, node)" in script
    assert "window.isSecureContext && navigator.clipboard" in script
    assert "typeof navigator.clipboard.writeText === 'function'" in script
    assert "Promise.resolve(navigator.clipboard.writeText(text))" in script
    assert ".catch(tryFallback)" in script
    assert "if (fallbackCopy(text, node)) copied(); else manualCopy();" in script
    assert "document.execCommand('copy')" in script
    assert "setSelectionRange(0, text.length)" in script
    assert "typeof node.value === 'string' ? node.value : (node.textContent || '').trim()" in script
    assert "'Copied!'" in script and "setTimeout(" in script
    assert "'Press Ctrl+C'" in script
    assert "clearTimeout(button._cemTimer)" in script


def test_copy_javascript_behaves_correctly_across_copy_paths(tmp_path):
    """Execute the real inline script with stubbed browser APIs under Node."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node runtime unavailable")
    template = Path(__file__).resolve().parents[1] / "app/templates/admin/api_keys.html"
    script = _script_containing(template.read_text(), "function cemCopy")
    js_file = tmp_path / "copy_behaviour.js"
    js_file.write_text(_COPY_JS_PRELUDE + script + _COPY_JS_EPILOGUE)

    result = subprocess.run(
        [node, str(js_file)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


@pytest.mark.asyncio
async def test_new_secret_banner_is_scrolled_into_view_on_page_load(ui):
    _, session = await _create(ui)
    assert session["new_api_key"] and session["new_webhook_secret"]
    html = await _page(ui, session=session)

    banner = _element_by_id(html, "newKeyBanner")
    assert "border-amber-300" in banner
    assert "border-amber-400" not in banner

    script = _script_containing(html, "newKeyBanner")
    assert "document.getElementById('newKeyBanner')" in script
    assert "sessionStorage.removeItem('scrollPos_' + window.location.pathname)" in script
    assert "scrollIntoView({ block: 'start' })" in script
    assert "main-content" in script and "scrollTop = 0" in script


@pytest.mark.asyncio
async def test_no_secret_banner_or_code_elements_when_nothing_was_created(ui):
    html = await _page(ui)

    assert 'id="newKeyBanner"' not in html
    assert '<code id="newKeyDisplay"' not in html
    assert '<code id="newWebhookSecretDisplay"' not in html


@pytest.mark.asyncio
async def test_manual_webhook_secret_is_html_escaped_in_readonly_input(ui):
    secret = 'a"b<c>&d'
    _, session = await _update(ui, webhook_secret=secret)
    assert session["new_webhook_secret"] == secret
    html = await _page(ui, session=session)

    assert 'value="a&#34;b&lt;c&gt;&amp;d"' in html
