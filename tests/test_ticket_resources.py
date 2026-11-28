"""Browser resources and long-ticket descriptions stay cheap to load and render."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.models.ticket import Ticket
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes
from app.web.routes import templates

ROOT = Path(__file__).resolve().parents[1]
BASE_HTML = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
SW_JS = (ROOT / 'app/static/sw.js').read_text(encoding='utf-8')
LONG_DESC = 'HEADER ' + 'x' * 3100 + ' https://example.com/end'


# --- base.html --------------------------------------------------------------

def _request(user=None, path='/web/tickets/1'):
    return SimpleNamespace(
        session={'user_id': user.id} if user else {},
        state=SimpleNamespace(),
        headers={},
        url=SimpleNamespace(path=path, scheme='https', netloc='crm.test'),
        query_params={},
    )


class _Form(dict):
    def getlist(self, name):
        return self.get(name) if isinstance(self.get(name), list) else []


def _form_request(user, values):
    request = _request(user, '/web/tickets/1/edit')
    async def form():
        return _Form(values)
    request.form = form
    return request


def test_content_cards_have_no_huge_backdrop_filters():
    rule = re.search(
        r'#page-content \.bg-white\.rounded-xl,\s*'
        r'#page-content \.bg-white\.rounded-lg,\s*'
        r'#page-content \.bg-white\.rounded-2xl\s*\{(.*?)\}', BASE_HTML, re.S
    )
    assert rule, 'the content-card rule is still present'
    css = rule.group(1)
    assert 'backdrop-filter' not in css, 'tall cards must not allocate compositor textures'
    assert 'rgba(255,255,255,0.92)' in css
    assert 'border:' in css and 'box-shadow:' in css

    assert 'blur(40px)' not in BASE_HTML and 'blur(24px)' not in BASE_HTML
    assert 'saturate(' not in BASE_HTML
    for selector in ('.mobile-header', '#mobile-sidebar', '#mobile-menu-button', '.bg-white.shadow-xl'):
        blocks = re.findall(re.escape(selector) + r'\s*\{([^}]*)\}', BASE_HTML)
        assert any('backdrop-filter: blur(12px)' in block for block in blocks), selector


def test_body_background_is_not_fixed():
    bodies = re.findall(r'\bbody\s*\{(.*?)\}', BASE_HTML, re.S)
    background = [css for css in bodies if 'linear-gradient(145deg' in css]
    assert len(background) == 1
    assert 'fixed' not in background[0]
    assert 'linear-gradient(145deg, var(--bg-a) 0%, var(--bg-b) 40%, var(--bg-a) 100%) !important' in background[0]


@pytest.mark.parametrize('theme,family', [
    ('ocean', 'Poppins'),
    ('forest', 'DM+Sans'),
    ('midnight', 'Nunito'),
    ('ember', 'Outfit'),
    ('crimson', 'Inter'),
    (None, 'Inter'),
])
def test_base_imports_one_google_font_for_the_active_theme(theme, family):
    workspace = SimpleNamespace(
        gui_theme=theme, site_title='CEM', name='Acme', primary_color='#dc2626'
    )
    html = templates.env.get_template('base.html').render(request=_request(), workspace=workspace)
    imports = re.findall(r'fonts\.googleapis\.com/css2\?([^\'"\s]+)', html)
    assert len(imports) == 1, 'only the active theme font should be downloaded'
    assert re.findall(r'family=([^&:]+)', imports[0]) == [family]
    assert 'wght@300;400;500;600;700' in imports[0]


def test_notification_polling_skips_hidden_tabs_and_refreshes_on_return():
    function = re.search(r'async function checkNotifications\(\) \{(.*?)\n\s*\}', BASE_HTML, re.S)
    assert function and re.search(r'if\s*\(document\.hidden\)\s*return;', function.group(1))
    assert re.search(r'setInterval\(checkNotifications, 10000\)', BASE_HTML)
    visible = re.search(
        r"addEventListener\('visibilitychange',\s*(?:\(\)\s*=>|function\s*\([^)]*\))\s*\{(.*?)\}\s*\)",
        BASE_HTML, re.S,
    )
    assert visible, 'a visibilitychange listener is required'
    assert 'document.hidden' in visible.group(1)
    assert visible.group(1).count('checkNotifications()') == 1, 'one refresh, not a second polling loop'


# --- sw.js ------------------------------------------------------------------

def test_service_worker_bumps_cache_name():
    assert "const CACHE_NAME = 'cem-cache-v2';" in SW_JS
    assert 'cem-cache-v1' not in SW_JS


_NODE_PROBE = r"""
const fs = require('fs');
const vm = require('vm');
const listeners = {};
const cached = [];
const waits = [];
class Request {
  constructor(url, options) { this.url = url; this.method = (options || {}).method || 'GET'; }
}
const sandbox = {
  console: { log() {}, error() {} },
  URL, Promise, Request,
  fetch: () => Promise.resolve({ status: 200, clone: () => ({}) }),
  caches: {
    open: () => Promise.resolve({ put: (request) => { cached.push(request.url || request); } }),
    match: () => Promise.resolve(undefined),
    keys: () => Promise.resolve([]),
    delete: () => Promise.resolve(true),
  },
};
sandbox.self = {
  location: { origin: 'https://crm.test' },
  addEventListener: (name, fn) => { listeners[name] = fn; },
  skipWaiting() {}, clients: { claim() {} },
  registration: { showNotification: () => Promise.resolve() },
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);

async function probe(method, url) {
  let responded = false;
  let pending;
  listeners.fetch({
    request: { method, url },
    respondWith: (promise) => { responded = true; pending = promise; },
    waitUntil: (promise) => { waits.push(promise); },
  });
  const before = cached.length;
  if (pending) await pending;
  while (waits.length) await waits.shift();
  return { responded, cached: cached.slice(before) };
}

(async () => {
  const cases = [
    ['GET', 'https://crm.test/static/app.js'],
    ['GET', 'https://crm.test/static/app.js?v=3'],
    ['GET', 'https://crm.test/web/tickets/1?include=comments'],
    ['GET', 'https://crm.test/api/v1/tickets'],
    ['GET', 'https://crm.test/web/tickets/1/attachments/9/preview'],
    ['GET', 'https://crm.test/web/notifications/unread'],
    ['POST', 'https://crm.test/static/app.js'],
    ['GET', 'https://cdn.test/static/app.js'],
  ];
  const results = {};
  for (const [method, url] of cases) results[method + ' ' + url] = await probe(method, url);
  console.log(JSON.stringify(results));
})().catch((error) => { console.error(error); process.exit(1); });
"""


def test_service_worker_only_caches_same_origin_static_gets():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is needed to execute sw.js')
    proc = subprocess.run(
        [node, '-e', _NODE_PROBE, str(ROOT / 'app/static/sw.js')],
        capture_output=True, text=True, timeout=20, check=True,
    )
    results = json.loads(proc.stdout)
    static_key = 'https://crm.test/static/app.js'
    assert results['GET ' + static_key] == {'responded': True, 'cached': [static_key]}
    # Version queries are normalized to one cache entry instead of piling up.
    assert results['GET ' + static_key + '?v=3'] == {'responded': True, 'cached': [static_key]}
    for key, value in results.items():
        if key.startswith('GET ' + static_key):
            continue
        assert value == {'responded': False, 'cached': []}, key


def test_description_routes_are_mounted_under_web_prefix():
    from app.main import app

    paths = {getattr(route, 'path', '') for route in app.routes}
    for included in app.routes:
        router = getattr(included, 'original_router', None)
        if router:
            paths.update('/web' + getattr(route, 'path', '') for route in router.routes)
    for ticket_id in ('full', 'summary', 'raw'):
        assert '/web/tickets/{ticket_id}/description/' + ticket_id in paths


# --- description partial -----------------------------------------------------

_EDIT_PROBE = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');
const state = { field: null, save: { disabled: true }, fetchCalls: [] };

function makeField() {
  const field = {
    value: '', placeholder: '', dataset: {}, listeners: {},
    getAttribute: (name) => name === 'data-lazy-desc-url' ? '/web/tickets/7/description/raw' : null,
    addEventListener: (name, listener) => { field.listeners[name] = listener; },
    removeEventListener: (name) => { delete field.listeners[name]; },
  };
  return field;
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
function flush() { return new Promise((resolve) => process.nextTick(resolve)); }

const sandbox = {
  Promise, console: { error() {}, log() {} },
  document: {
    getElementById: (id) => id === 'etDescription' ? state.field : {
      classList: { remove() {}, add() {} },
      querySelector: (selector) => selector === 'button[type="submit"]' ? state.save : null,
    },
  },
  fetch: () => Promise.reject(new Error('unset')),
};
vm.runInNewContext(src, sandbox);

(async () => {
  const results = {};
  const reset = () => {
    state.field = makeField();
    state.save.disabled = true;
    state.fetchCalls = [];
  };

  reset();
  sandbox.fetch = (url, options) => {
    state.fetchCalls.push({ url, options });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ description: 'FULL TEXT' }) });
  };
  sandbox.openEditTicket();
  results.successLoading = { save: state.save.disabled, value: state.field.value };
  await flush(); await flush();
  results.success = {
    save: state.save.disabled, value: state.field.value,
    cache: state.fetchCalls[0].options.cache, loaded: state.field.dataset.descLoaded,
  };

  reset();
  sandbox.fetch = () => Promise.reject(new Error('offline'));
  sandbox.openEditTicket();
  await flush(); await flush();
  results.failure = {
    save: state.save.disabled, value: state.field.value,
    placeholder: state.field.placeholder, loaded: state.field.dataset.descLoaded || null,
  };

  reset();
  const pending = deferred();
  sandbox.fetch = () => pending.promise;
  sandbox.openEditTicket();
  state.field.value = 'ADMIN TYPED';
  state.field.listeners.input();
  pending.resolve({ ok: true, json: () => Promise.resolve({ description: 'FULL TEXT' }) });
  await flush(); await flush();
  results.typedWhileLoading = { save: state.save.disabled, value: state.field.value };

  reset();
  const failing = deferred();
  sandbox.fetch = () => failing.promise;
  sandbox.openEditTicket();
  state.field.value = 'ADMIN TYPED';
  state.field.listeners.input();
  failing.reject(new Error('offline'));
  await flush(); await flush();
  results.typedThenFailure = {
    save: state.save.disabled, value: state.field.value, placeholder: state.field.placeholder,
  };

  const failedRetry = deferred();
  sandbox.fetch = () => failedRetry.promise;
  sandbox.openEditTicket();
  failedRetry.reject(new Error('offline'));
  await flush(); await flush();
  state.field.value = 'TYPED AFTER FAILURE';
  sandbox.fetch = () => Promise.resolve({
    ok: true, json: () => Promise.resolve({ description: 'FULL TEXT' }),
  });
  sandbox.openEditTicket();
  await flush(); await flush();
  results.typeAfterFailureThenRetry = { save: state.save.disabled, value: state.field.value };

  console.log(JSON.stringify(results));
})().catch((error) => { console.error(error); process.exit(1); });
"""


def _probe_edit_js(html: str, tmp_path: Path) -> dict:
    function = re.search(r'function openEditTicket\(\) \{.*?\n\}', html, re.S)
    assert function, 'openEditTicket is missing'
    script = tmp_path / 'open_edit_ticket.js'
    script.write_text(function.group(0), encoding='utf-8')
    node = shutil.which('node')
    if not node:
        pytest.skip('node is needed to execute openEditTicket()')
    proc = subprocess.run(
        [node, '-e', _EDIT_PROBE, str(script)],
        capture_output=True, text=True, timeout=20, check=True,
    )
    return json.loads(proc.stdout)


def test_description_limit_is_available_in_templates():
    assert routes.TICKET_DESCRIPTION_CLIP_CHARS == 3000
    assert templates.env.globals['description_clip_chars'] == 3000


def test_description_partial_clips_and_linkifies_only_visible_text():
    ticket = SimpleNamespace(id=7, description=LONG_DESC)
    template = templates.env.get_template('tickets/_description.html')

    clipped = template.render(ticket=ticket, show_full=False)
    assert 'id="ticket-description"' in clipped
    assert 'Show full description' in clipped
    assert '/web/tickets/7/description/full' in clipped
    assert 'https://example.com/end' not in clipped
    assert clipped.count('x') < 3200

    full = template.render(ticket=ticket, show_full=True)
    assert 'Show less' in full
    assert '/web/tickets/7/description/summary' in full
    assert 'Show full description' not in full
    assert '<a href="https://example.com/end"' in full


async def _seed(path, description=LONG_DESC):
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name='Acme'), Workspace(name='Other Co')
    db.add_all([acme, other])
    await db.flush()
    admin = User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id)
    tech = User(username='tech', hashed_password='x', is_admin=False, workspace_id=acme.id)
    outsider = User(username='olive', hashed_password='x', is_admin=True, workspace_id=other.id)
    ticket = Ticket(ticket_number='TKT-2026-00001', subject='Printer', description=description,
                    workspace_id=acme.id)
    other_ticket = Ticket(ticket_number='TKT-2026-00002', subject='Other', description='other',
                          workspace_id=other.id)
    db.add_all([admin, tech, outsider, ticket, other_ticket])
    await db.commit()
    return engine, db, admin, tech, outsider, ticket, other_ticket


@pytest.mark.asyncio
async def test_description_endpoints_toggle_cleanly_and_stay_workspace_scoped(tmp_path):
    engine, db, admin, _, outsider, ticket, other_ticket = await _seed(tmp_path / 'desc.db')
    try:
        full = await routes.web_ticket_description_full(_request(admin), ticket.id, db=db)
        html = full.body.decode()
        assert full.status_code == 200
        assert full.headers['Cache-Control'] == 'no-store, private'
        assert '<a href="https://example.com/end"' in html
        assert 'Show less' in html
        assert 'Show full description' not in html

        summary = await routes.web_ticket_description_summary(_request(admin), ticket.id, db=db)
        html = summary.body.decode()
        assert summary.status_code == 200
        assert summary.headers['Cache-Control'] == 'no-store, private'
        assert 'Show full description' in html
        assert 'https://example.com/end' not in html

        for user, ticket_id in ((outsider, ticket.id), (admin, other_ticket.id)):
            for endpoint in (routes.web_ticket_description_full, routes.web_ticket_description_summary):
                response = await endpoint(_request(user), ticket_id, db=db)
                assert response.status_code == 404
        signed_out = await routes.web_ticket_description_full(_request(), ticket.id, db=db)
        assert signed_out.headers['HX-Redirect'] == '/web/login'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_description_raw_is_admin_only_and_workspace_scoped(tmp_path):
    engine, db, admin, tech, outsider, ticket, other_ticket = await _seed(tmp_path / 'raw.db')
    try:
        signed_out = await routes.web_ticket_description_raw(_request(), ticket.id, db=db)
        assert signed_out.status_code == 401

        non_admin = await routes.web_ticket_description_raw(_request(tech), ticket.id, db=db)
        assert non_admin.status_code == 403

        for user, ticket_id in ((outsider, ticket.id), (admin, other_ticket.id)):
            response = await routes.web_ticket_description_raw(_request(user), ticket_id, db=db)
            assert response.status_code == 404

        raw = await routes.web_ticket_description_raw(_request(admin), ticket.id, db=db)
        assert raw.status_code == 200
        assert raw.headers['Cache-Control'] == 'no-store, private'
        assert json.loads(raw.body) == {'description': LONG_DESC}
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_lazy_description_cannot_clear_stored_text(tmp_path):
    engine, db, admin, _, _, ticket, _ = await _seed(tmp_path / 'edit-guard.db')
    try:
        request = _form_request(admin, {'subject': 'Printer', 'description': ''})
        response = await routes.web_tickets_edit(request, ticket.id, db=db)
        assert response.status_code == 303
        assert 'did not load' in request.session['error_message']
        await db.refresh(ticket)
        assert ticket.description == LONG_DESC
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_detail_page_lazy_loads_long_edit_description(tmp_path):
    engine, db, admin, _, _, ticket, _ = await _seed(tmp_path / 'detail-long.db')
    try:
        response = await routes.web_tickets_detail(_request(admin), ticket.id, db=db)
        html = response.body.decode()
        assert response.status_code == 200
        assert 'id="ticket-description"' in html
        assert 'https://example.com/end' not in html, '20k chars must not ship in the page twice'

        field = re.search(r'<textarea[^>]*id="etDescription"[^>]*>(.*?)</textarea>', html, re.S)
        assert field
        assert field.group(1).strip() == ''
        opening_tag = html[field.start():field.start() + field.group(0).index('>') + 1]
        assert 'data-lazy-desc-url="/web/tickets/%d/description/raw"' % ticket.id in opening_tag
        assert 'Loading full description...' in opening_tag

        function = re.search(r'function openEditTicket\(\) \{(.*?)\n\}', html, re.S)
        assert function and 'getAttribute(\'data-lazy-desc-url\')' in function.group(1)
        assert 'fetch(url' in function.group(1)
        assert 'save.disabled = false' in function.group(1)
        assert 'save.disabled = true' in function.group(1)

        modal_html = html[html.index('id="editTicketModal"'):html.index('<!-- Forward Ticket Modal')]
        save = re.search(r'<button type="submit"[^>]*>', modal_html, re.S)
        assert save and 'disabled' in save.group(0), 'safe even if the script never runs'

        results = _probe_edit_js(html, tmp_path)
        assert results['successLoading'] == {'save': True, 'value': ''}
        assert results['success'] == {
            'save': False, 'value': 'FULL TEXT', 'cache': 'no-store', 'loaded': 'true',
        }
        assert results['failure']['save'] is True
        assert results['failure']['value'] == ''
        assert 'Could not load' in results['failure']['placeholder']
        assert results['failure']['loaded'] is None
        assert results['typedWhileLoading'] == {'save': False, 'value': 'ADMIN TYPED'}
        assert results['typedThenFailure']['save'] is True, 'a partial field must not be submittable'
        assert results['typedThenFailure']['value'] == 'ADMIN TYPED'
        assert 'Could not load' in results['typedThenFailure']['placeholder']
        assert results['typeAfterFailureThenRetry'] == {
            'save': False, 'value': 'TYPED AFTER FAILURE',
        }, 'a successful retry must not overwrite text typed after a failure'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_detail_page_keeps_short_edit_description_inline(tmp_path):
    short = 'Small ticket description'
    engine, db, admin, _, _, ticket, _ = await _seed(tmp_path / 'detail-short.db', description=short)
    try:
        html = (await routes.web_tickets_detail(_request(admin), ticket.id, db=db)).body.decode()
        field = re.search(r'<textarea[^>]*id="etDescription"[^>]*>(.*?)</textarea>', html, re.S)
        assert field and field.group(1).strip() == short
        opening_tag = html[field.start():field.start() + field.group(0).index('>') + 1]
        assert 'data-lazy-desc-url' not in opening_tag
        modal_html = html[html.index('id="editTicketModal"'):html.index('<!-- Forward Ticket Modal')]
        save = re.search(r'<button type="submit"[^>]*>', modal_html, re.S)
        assert save and 'disabled' not in save.group(0)
    finally:
        await db.close()
        await engine.dispose()
