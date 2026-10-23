"""The mobile app download code: checked on the server, changed by the super admin."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.core import app_download
from app.models.user import User
from app.web.routes import (
    download_app,
    download_app_android,
    download_app_unlock,
    web_superadmin_app_download_code,
)


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    """Each test gets its own settings file and a clean wrong-code count."""
    path = tmp_path / 'server_settings.json'
    monkeypatch.setattr(app_download, 'SETTINGS_FILE', path)
    monkeypatch.setattr(app_download, '_failures', {})
    return path


def _request(session=None, ip='10.0.0.5'):
    return SimpleNamespace(session={} if session is None else session, client=SimpleNamespace(host=ip),
                           state=SimpleNamespace(), url=SimpleNamespace(path='/web/download'))


async def _enter(request, code):
    response = await download_app_unlock(request, code=code)
    assert (response.status_code, response.headers['location']) == (303, '/web/download')
    return request.session.pop('download_error', None)


# --- the code ------------------------------------------------------------------

def test_original_code_works_until_a_new_one_is_set(settings_file):
    assert app_download.check_code(app_download.DEFAULT_CODE)
    assert not app_download.check_code('000000')
    assert not app_download.code_status()['custom']

    app_download.set_code('kyotech-2026', changed_by='root')
    assert app_download.check_code('kyotech-2026')
    assert not app_download.check_code(app_download.DEFAULT_CODE)
    assert 'kyotech-2026' not in settings_file.read_text(), 'only a hash is stored'
    status = app_download.code_status()
    assert status['custom'] and status['changed_by'] == 'root' and status['changed_at']


def test_page_no_longer_carries_the_code():
    from pathlib import Path
    page = (Path(app_download.__file__).resolve().parents[1] / 'templates' / 'download.html').read_text(encoding='utf-8')
    assert app_download.DEFAULT_CODE not in page and '0x32,0x30' not in page
    assert '/static/downloads/' not in page and not (
        Path(app_download.__file__).resolve().parents[1] / 'static' / 'downloads' / 'cem-mobile.apk').exists(), \
        'the APK is not a public file any more'


# --- unlocking the page ------------------------------------------------------------

@pytest.mark.asyncio
async def test_right_code_unlocks_the_page_and_the_apk():
    request = _request()
    locked = await download_app_android(request)
    assert (locked.status_code, locked.headers['location']) == (303, '/web/download')

    assert await _enter(request, f'  {app_download.DEFAULT_CODE} ') is None
    page = await download_app(request)
    assert b'id="download-buttons"' in page.body and b'name="code"' not in page.body

    apk = await download_app_android(request)
    assert isinstance(apk, FileResponse)
    assert apk.media_type == 'application/vnd.android.package-archive'
    assert 'cem-mobile.apk' in apk.headers['content-disposition']


@pytest.mark.asyncio
async def test_wrong_code_keeps_it_locked():
    request = _request()
    assert await _enter(request, '123456') == 'Invalid access code'
    page = await download_app(_request(request.session))
    assert b'name="code"' in page.body and b'id="download-buttons"' not in page.body
    assert not app_download.is_unlocked(request.session)


@pytest.mark.asyncio
async def test_new_code_locks_pages_opened_with_the_old_one():
    request = _request()
    await _enter(request, app_download.DEFAULT_CODE)
    assert app_download.is_unlocked(request.session)
    app_download.set_code('fresh-code', changed_by='root')
    assert not app_download.is_unlocked(request.session)
    assert await _enter(request, app_download.DEFAULT_CODE) == 'Invalid access code'
    assert await _enter(request, 'fresh-code') is None and app_download.is_unlocked(request.session)


def test_unlock_expires(monkeypatch):
    session = {}
    app_download.unlock(session)
    assert app_download.is_unlocked(session)
    monkeypatch.setattr(time, 'time', lambda: session['app_download']['until'] + 1)
    assert not app_download.is_unlocked(session)


@pytest.mark.asyncio
async def test_guessing_is_stopped():
    request = _request(ip='203.0.113.9')
    for _ in range(app_download.MAX_FAILURES):
        assert await _enter(request, 'guess') == 'Invalid access code'
    error = await _enter(request, app_download.DEFAULT_CODE)
    assert error.startswith('Too many wrong codes'), 'even the right code waits'
    assert not app_download.is_unlocked(request.session)
    assert await _enter(_request(ip='198.51.100.7'), app_download.DEFAULT_CODE) is None, 'other addresses unaffected'


# --- changing it on the super admin console -----------------------------------------

async def _seed(path):
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    root = User(username='root', hashed_password='x', is_superadmin=True, is_admin=True)
    company_admin = User(username='ada', hashed_password='x', is_admin=True)
    db.add_all([root, company_admin])
    await db.commit()
    return engine, db, root, company_admin


@pytest.mark.asyncio
async def test_super_admin_changes_the_code(tmp_path):
    engine, db, root, _ = await _seed(tmp_path / 'sa.db')
    try:
        async def change(code, confirm):
            response = await web_superadmin_app_download_code(_request({'user_id': root.id}), code=code,
                                                             confirm_code=confirm, db=db)
            return response.headers['location']

        assert await change('abcd', 'abce') == '/web/superadmin?error=download_code_mismatch'
        assert await change('abc', 'abc') == '/web/superadmin?error=download_code_length'
        assert app_download.check_code(app_download.DEFAULT_CODE), 'unchanged after a refused change'

        assert await change(' 778899 ', '778899') == '/web/superadmin?success=download_code'
        assert app_download.check_code('778899') and app_download.code_status()['changed_by'] == 'root'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_only_the_super_admin_changes_the_code(tmp_path):
    engine, db, _, company_admin = await _seed(tmp_path / 'sa.db')
    try:
        with pytest.raises(HTTPException) as refused:
            await web_superadmin_app_download_code(_request({'user_id': company_admin.id}), code='778899',
                                                   confirm_code='778899', db=db)
        assert refused.value.status_code == 404
        signed_out = await web_superadmin_app_download_code(_request({}), code='778899', confirm_code='778899', db=db)
        assert signed_out.headers['location'] == '/web/login/superadmin'
        assert app_download.check_code(app_download.DEFAULT_CODE) and not app_download.code_status()['custom']
    finally:
        await db.close()
        await engine.dispose()
