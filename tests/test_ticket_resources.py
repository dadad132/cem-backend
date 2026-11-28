"""Browser resources and long-ticket descriptions stay cheap to load and render."""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from app.models.ticket import Ticket
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes
from app.web.routes import templates

ROOT = Path(__file__).resolve().parents[1]
BASE_HTML = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
DETAIL_HTML = (ROOT / 'app/templates/tickets/detail.html').read_text(encoding='utf-8')
SW_JS = (ROOT / 'app/static/sw.js').read_text(encoding='utf-8')
LONG_DESC = 'HEADER ' + 'x' * 3100 + ' https://example.com/end'


def _thumbnails():
    """Import lazily so the RED state is a test failure, not a collection error."""
    from app.core import thumbnails
    return thumbnails


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


# --- cached thumbnails --------------------------------------------------------

def _png_bytes(width: int, height: int) -> bytes:
    image = Image.effect_noise((width, height), 60).convert('RGB')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def _write_upload(monkeypatch, tmp_path: Path, name: str, data: bytes, folder='tickets'):
    app_dir = tmp_path / 'app'
    monkeypatch.setattr(routes, 'BASE_DIR', app_dir)
    path = app_dir / 'uploads' / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, f'app/uploads/{folder}/{name}'


def _thumb_cache(uploads_root: Path) -> list[Path]:
    if (uploads_root / '.thumbs').is_dir():
        return sorted((uploads_root / '.thumbs').glob('*.jpg'))
    return []


def test_thumbnail_helper_downscales_caches_and_deletes(tmp_path, monkeypatch):
    thumbs = _thumbnails()
    uploads_root = tmp_path / 'app' / 'uploads'
    source = uploads_root / 'tickets' / 'big.png'
    source.parent.mkdir(parents=True, exist_ok=True)
    data = _png_bytes(2400, 1800)
    source.write_bytes(data)

    thumb = thumbs.get_or_create_thumbnail(source, uploads_root=uploads_root)
    assert thumb is not None
    assert thumb.suffix == '.jpg'
    assert thumb.parent == uploads_root / '.thumbs'
    assert thumb.is_file()

    with Image.open(thumb) as produced:
        assert produced.format == 'JPEG'
        assert produced.info.get('progressive'), 'progressive JPEGs render progressively on slow links'
        assert 0 < produced.width <= 480
        assert 0 < produced.height <= 360
    assert thumb.stat().st_size < len(data), 'a 2400x1800 photo must shrink on disk'

    mtime = thumb.stat().st_mtime_ns
    original_open = thumbs.Image.open
    opened = []

    def counted_open(*args, **kwargs):
        opened.append(args[0] if args else kwargs.get('fp'))
        return original_open(*args, **kwargs)

    monkeypatch.setattr(thumbs.Image, 'open', counted_open)
    assert thumbs.get_or_create_thumbnail(source, uploads_root=uploads_root) == thumb
    assert opened == [], 'a repeat request must reuse the cached JPEG without decoding'
    assert thumb.stat().st_mtime_ns == mtime

    source.touch()
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 1_000_000_000))
    refreshed = thumbs.get_or_create_thumbnail(source, uploads_root=uploads_root)
    assert refreshed is not None
    assert refreshed != thumb, 'a changed source must get a new cache key'

    thumbs.delete_thumbnail(source, uploads_root=uploads_root)
    assert _thumb_cache(uploads_root) == [], 'all cached versions of this source are removed'
    assert not list((uploads_root / '.thumbs').glob('*.json'))
    source.unlink()
    thumbs.delete_thumbnail(source, uploads_root=uploads_root)  # best effort, never raises


def test_thumbnail_helper_applies_exif_orientation(tmp_path):
    thumbs = _thumbnails()
    uploads_root = tmp_path / 'app' / 'uploads'
    source = uploads_root / 'tickets' / 'rotated.jpg'
    source.parent.mkdir(parents=True, exist_ok=True)
    original = Image.new('RGB', (400, 200), (12, 120, 210))
    exif = Image.Exif()
    exif[274] = 6  # 90 degrees clockwise: 400x200 is really 200x400
    original.save(source, format='JPEG', exif=exif)

    thumb = thumbs.get_or_create_thumbnail(source, uploads_root=uploads_root)
    assert thumb is not None
    with Image.open(thumb) as produced:
        assert produced.width < produced.height, 'the photo must be upright, not sideways'
        assert produced.width <= 480 and produced.height <= 360


def test_thumbnail_helper_skips_animated_oversized_broken_and_pillow_free(tmp_path, monkeypatch):
    thumbs = _thumbnails()
    uploads_root = tmp_path / 'app' / 'uploads'
    folder = uploads_root / 'tickets'
    folder.mkdir(parents=True, exist_ok=True)

    frames = [Image.new('RGB', (100, 100), color) for color in ((255, 0, 0), (0, 255, 0))]
    animated = folder / 'animated.gif'
    frames[0].save(animated, save_all=True, append_images=frames[1:], duration=100, loop=0)
    assert thumbs.get_or_create_thumbnail(animated, uploads_root=uploads_root) is None

    broken = folder / 'broken.png'
    broken.write_text('not an image', encoding='utf-8')
    assert thumbs.get_or_create_thumbnail(broken, uploads_root=uploads_root) is None

    small = folder / 'small.png'
    small.write_bytes(_png_bytes(100, 100))
    monkeypatch.setattr(thumbs, 'MAX_THUMBNAIL_PIXELS', 1000)
    assert thumbs.get_or_create_thumbnail(small, uploads_root=uploads_root) is None

    monkeypatch.setattr(thumbs, 'Image', None)
    assert thumbs.get_or_create_thumbnail(small, uploads_root=uploads_root) is None
    assert _thumb_cache(uploads_root) == []


def test_thumbnail_delete_works_without_a_sidecar_while_source_exists(tmp_path):
    thumbs = _thumbnails()
    uploads_root = tmp_path / 'app' / 'uploads'
    source = uploads_root / 'tickets' / 'photo.png'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(_png_bytes(120, 90))

    thumb = thumbs.get_or_create_thumbnail(source, uploads_root=uploads_root)
    assert thumb is not None
    thumb.with_suffix('.json').unlink()
    thumbs.delete_thumbnail(source, uploads_root=uploads_root)
    assert not thumb.exists(), 'the live source key alone must be enough to clean up'


def test_thumbnail_cache_is_bounded(tmp_path, monkeypatch):
    thumbs = _thumbnails()
    monkeypatch.setattr(thumbs, '_CACHE_MAX_ENTRIES', 2)
    uploads_root = tmp_path / 'app' / 'uploads'
    source = uploads_root / 'tickets' / 'many-versions.png'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(_png_bytes(80, 60))

    for version in range(5):
        stamp = 1_700_000_000_000_000_000 + version * 1_000_000_000
        os.utime(source, ns=(stamp, stamp))
        assert thumbs.get_or_create_thumbnail(source, uploads_root=uploads_root) is not None

    assert len(_thumb_cache(uploads_root)) <= 2
    assert len(list((uploads_root / '.thumbs').glob('*.json'))) <= 2


@pytest.mark.parametrize('path', [
    '/uploads/.thumbs/anything.jpg',
    '/uploads//.thumbs/anything.jpg',
    '/uploads/./.thumbs/anything.jpg',
    '/uploads/%2e/.thumbs/anything.jpg',
    '/uploads/%2E/.thumbs/anything.jpg',
    '/uploads/../uploads/.thumbs/anything.jpg',
    '/uploads/%2e%2e/uploads/.thumbs/anything.jpg',
    '/uploads/tickets/../.thumbs/anything.jpg',
])
def test_private_thumbnail_cache_is_not_served_by_uploads_mount(path):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    assert client.get(path).status_code == 404


def test_attachment_cards_escape_filenames():
    attachment = SimpleNamespace(
        id=99,
        mime_type='text/csv',
        filename='<img src=x onerror=alert(1)>.csv',
        file_size=12,
        uploaded_at=datetime(2026, 1, 1),
        uploaded_by_id=None,
    )
    html = templates.env.get_template('tickets/_attachments_page.html').render(
        attachments=[attachment],
        older_attachments=0,
        user=SimpleNamespace(id=1, is_admin=True),
        ticket=SimpleNamespace(id=7),
    )
    assert '<img src=x' not in html
    assert '&lt;img src=x' in html


@pytest.mark.asyncio
async def test_thumbnail_generation_is_concurrency_bounded(tmp_path, monkeypatch):
    source = tmp_path / 'photo.png'
    source.write_bytes(_png_bytes(80, 60))
    active = {'count': 0, 'max': 0}
    lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()

    def fake_thumbnail(file_path, *args, **kwargs):
        with lock:
            active['count'] += 1
            active['max'] = max(active['max'], active['count'])
            if active['count'] == 2:
                started.set()
        release.wait(timeout=5)
        with lock:
            active['count'] -= 1
        return None

    monkeypatch.setattr(routes, 'get_or_create_thumbnail', fake_thumbnail)
    tasks = [asyncio.create_task(routes._generate_thumbnail(source)) for _ in range(6)]
    assert await asyncio.to_thread(started.wait, 5), 'two decodes should be allowed to overlap'
    assert active['max'] <= 2, 'a burst must not decode more than two photos at once'
    release.set()
    await asyncio.gather(*tasks)


async def _add_ticket_attachments(db, ticket, user, count: int, basename='file'):
    from app.models.ticket import TicketAttachment

    rows = []
    for index in range(count):
        pdf = index % 5 == 0
        extension = 'pdf' if pdf else 'png'
        rows.append(TicketAttachment(
            ticket_id=ticket.id,
            filename=f'{basename}-{index:02d}.{extension}',
            file_path=f'app/uploads/tickets/{basename}-{index:02d}.{extension}',
            file_size=1024 + index,
            mime_type='application/pdf' if pdf else 'image/png',
            uploaded_by_id=user.id,
            uploaded_at=datetime(2026, 1, 1) + timedelta(minutes=index),
        ))
    db.add_all(rows)
    await db.commit()
    return rows


@pytest.mark.asyncio
async def test_preview_thumb_returns_small_jpeg_with_hardened_headers_and_caches(tmp_path, monkeypatch):
    from app.models.ticket import TicketAttachment

    engine, db, admin, _tech, _outsider, ticket, _other = await _seed(tmp_path / 'preview-thumb.db')
    try:
        data = _png_bytes(1200, 900)
        _path, stored = _write_upload(monkeypatch, tmp_path, 'big.png', data)
        attachment = TicketAttachment(
            ticket_id=ticket.id, filename='big.png', file_path=stored,
            file_size=len(data), mime_type='image/png', uploaded_by_id=admin.id,
        )
        db.add(attachment)
        await db.commit()

        response = await routes.web_ticket_attachment_preview(
            _request(admin), attachment.id, db=db, thumb=1
        )
        assert response.status_code == 200
        assert response.media_type == 'image/jpeg'
        body = Path(response.path).read_bytes()
        assert body[:3] == b'\xff\xd8\xff'
        assert len(body) < len(data), 'the browser must not receive the full bitmap'
        assert response.headers['cache-control'] == 'private, max-age=86400'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['content-security-policy'] == "default-src 'none'"
        assert response.headers['content-disposition'].startswith('inline;')

        cache = _thumb_cache(routes.BASE_DIR / 'uploads')
        assert len(cache) == 1
        again = await routes.web_ticket_attachment_preview(
            _request(admin), attachment.id, db=db, thumb=1
        )
        assert Path(again.path).read_bytes() == body
        assert _thumb_cache(routes.BASE_DIR / 'uploads') == cache

        original = await routes.web_ticket_attachment_preview(
            _request(admin), attachment.id, db=db, thumb=0
        )
        assert original.media_type == 'image/png'
        assert Path(original.path).read_bytes() == data
        assert original.headers['cache-control'] == 'private, max-age=86400'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_thumb_rejects_non_images_and_cross_workspace_users(tmp_path, monkeypatch):
    from app.models.ticket import TicketAttachment

    engine, db, admin, _tech, outsider, ticket, _other = await _seed(tmp_path / 'preview-deny.db')
    try:
        _path, stored = _write_upload(
            monkeypatch, tmp_path, 'notes.csv', b'sensor,value\n1,42\n'
        )
        non_image = TicketAttachment(
            ticket_id=ticket.id, filename='notes.csv', file_path=stored,
            file_size=15, mime_type='text/csv', uploaded_by_id=admin.id,
        )
        db.add(non_image)
        await db.flush()
        with pytest.raises(HTTPException) as denied:
            await routes.web_ticket_attachment_preview(
                _request(admin), non_image.id, db=db, thumb=1
            )
        assert denied.value.status_code == 400

        data = _png_bytes(400, 300)
        photo_path, photo_stored = _write_upload(monkeypatch, tmp_path, 'photo.png', data)
        photo = TicketAttachment(
            ticket_id=ticket.id, filename='photo.png', file_path=photo_stored,
            file_size=photo_path.stat().st_size, mime_type='image/png', uploaded_by_id=admin.id,
        )
        db.add(photo)
        await db.commit()
        with pytest.raises(HTTPException) as denied:
            await routes.web_ticket_attachment_preview(
                _request(outsider), photo.id, db=db, thumb=1
            )
        assert denied.value.status_code == 403
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_thumbnail_generation_runs_off_the_event_loop(tmp_path, monkeypatch):
    from app.models.ticket import TicketAttachment

    engine, db, admin, _tech, _outsider, ticket, _other = await _seed(tmp_path / 'preview-thread.db')
    try:
        data = _png_bytes(300, 200)
        _path, stored = _write_upload(monkeypatch, tmp_path, 'photo.png', data)
        attachment = TicketAttachment(
            ticket_id=ticket.id, filename='photo.png', file_path=stored,
            file_size=len(data), mime_type='image/png', uploaded_by_id=admin.id,
        )
        db.add(attachment)
        await db.commit()

        called_on = []
        event_loop_thread = threading.get_ident()

        def fake_thumbnail(file_path, *args, **kwargs):
            called_on.append(threading.get_ident())
            return None

        monkeypatch.setattr(routes, 'get_or_create_thumbnail', fake_thumbnail)
        response = await routes.web_ticket_attachment_preview(
            _request(admin), attachment.id, db=db, thumb=1
        )
        assert response.status_code == 200
        assert called_on and called_on[0] != event_loop_thread
        assert response.media_type == 'image/png', 'a failed thumbnail falls back to the original'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_attachment_removes_its_cached_thumbnail(tmp_path, monkeypatch):
    from app.models.ticket import TicketAttachment

    engine, db, admin, _tech, _outsider, ticket, _other = await _seed(tmp_path / 'delete-thumb.db')
    try:
        data = _png_bytes(800, 600)
        path, stored = _write_upload(monkeypatch, tmp_path, 'photo.png', data)
        attachment = TicketAttachment(
            ticket_id=ticket.id, filename='photo.png', file_path=stored,
            file_size=len(data), mime_type='image/png', uploaded_by_id=admin.id,
        )
        db.add(attachment)
        await db.commit()
        await routes.web_ticket_attachment_preview(
            _request(admin), attachment.id, db=db, thumb=1
        )
        cache = _thumb_cache(routes.BASE_DIR / 'uploads')
        assert cache

        deleted = await routes.web_ticket_attachment_delete(
            _request(admin), attachment.id, db=db
        )
        assert deleted.status_code == 200
        assert all(not item.exists() for item in cache)
        assert not path.exists()
        assert _thumb_cache(routes.BASE_DIR / 'uploads') == []
    finally:
        await db.close()
        await engine.dispose()


# --- paginated attachment cards ----------------------------------------------

@pytest.mark.asyncio
async def test_detail_page_paginates_attachments_and_uses_thumbnails(tmp_path):
    engine, db, admin, _tech, outsider, ticket, other_ticket = await _seed(
        tmp_path / 'detail-attachments.db'
    )
    try:
        rows = await _add_ticket_attachments(db, ticket, admin, 15)
        await _add_ticket_attachments(db, other_ticket, outsider, 2, basename='secret')
        response = await routes.web_tickets_detail(_request(admin), ticket.id, db=db)
        html = response.body.decode()

        assert response.status_code == 200
        assert routes.TICKET_ATTACHMENTS_SHOWN == 12
        assert 'Photos &amp; Files (15)' in html
        assert len(re.findall(r'class="[^"]*tk-attachment-card', html)) == 12
        assert 'Show more files (3)' in html
        assert f'/web/tickets/{ticket.id}/attachments/more?before={rows[3].id}' in html
        for row in rows[:3]:
            assert row.filename not in html, 'only the newest 12 load with the page'
        for row in rows[3:]:
            assert row.filename in html

        photos = [row for row in rows[3:] if row.mime_type == 'image/png']
        assert html.count('?thumb=1') == len(photos)
        for photo in photos:
            assert f'/web/tickets/attachments/{photo.id}/preview?thumb=1' in html
        assert re.search(
            r'<a href="/web/tickets/attachments/\d+/preview" target="_blank"', html
        ), 'the full-resolution image still opens in a new tab'
        assert 'decoding="async"' in html
        assert 'fa-file-pdf' in html, 'non-photo file cards are still rendered'
        assert 'secret-' not in html

        assert 'content-visibility: auto' in DETAIL_HTML
        assert 'contain-intrinsic-size: auto 220px' in DETAIL_HTML
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_attachments_more_pages_without_duplicates_or_cross_workspace_leaks(tmp_path):
    engine, db, admin, _tech, outsider, ticket, other_ticket = await _seed(
        tmp_path / 'more-attachments.db'
    )
    try:
        rows = await _add_ticket_attachments(db, ticket, admin, 15)
        await _add_ticket_attachments(db, other_ticket, outsider, 2, basename='secret')
        more = routes.web_ticket_attachments_more

        response = await more(_request(admin), ticket.id, before=rows[3].id, db=db)
        html = response.body.decode()
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store, private'
        assert len(re.findall(r'class="[^"]*tk-attachment-card', html)) == 3
        for row in rows[:3]:
            assert row.filename in html
        for row in rows[3:]:
            assert row.filename not in html, 'a page must not repeat what is already shown'
        assert 'Show more files' not in html
        assert 'secret-' not in html
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_attachments_more_protects_access_and_requires_a_valid_anchor(tmp_path):
    engine, db, admin, _tech, outsider, ticket, other_ticket = await _seed(
        tmp_path / 'more-access.db'
    )
    try:
        rows = await _add_ticket_attachments(db, ticket, admin, 15)
        more = routes.web_ticket_attachments_more

        signed_out = await more(_request(), ticket.id, before=rows[3].id, db=db)
        assert signed_out.headers['HX-Redirect'] == '/web/login'

        for user, ticket_id, before in (
            (outsider, ticket.id, rows[3].id),
            (admin, other_ticket.id, rows[3].id),
            (admin, ticket.id, 999_999),
        ):
            response = await more(_request(user), ticket_id, before=before, db=db)
            assert response.status_code == 404
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_attachment_pages_break_upload_time_ties_by_id(tmp_path):
    from app.models.ticket import TicketAttachment

    engine, db, admin, _tech, _outsider, ticket, _other = await _seed(
        tmp_path / 'attachment-ties.db'
    )
    try:
        stamp = datetime(2026, 2, 1)
        rows = []
        for index in range(4):
            row = TicketAttachment(
                ticket_id=ticket.id,
                filename=f'tie-{index}.png',
                file_path=f'app/uploads/tickets/tie-{index}.png',
                file_size=100,
                mime_type='image/png',
                uploaded_by_id=admin.id,
                uploaded_at=stamp,
            )
            db.add(row)
            rows.append(row)
        await db.commit()

        page, older = await routes._ticket_attachments_page(
            db, ticket.id, 12, before=rows[2]
        )
        assert [row.id for row in page] == [rows[1].id, rows[0].id]
        assert older == 0
    finally:
        await db.close()
        await engine.dispose()


def test_attachments_more_route_is_mounted_under_web_prefix():
    from app.main import app

    paths = {getattr(route, 'path', '') for route in app.routes}
    for included in app.routes:
        router = getattr(included, 'original_router', None)
        if router:
            paths.update('/web' + getattr(route, 'path', '') for route in router.routes)
    assert '/web/tickets/{ticket_id}/attachments/more' in paths


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
