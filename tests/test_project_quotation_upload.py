"""Slice 5: a new project needs a quotation/paperwork file before it exists.

The browser offers one drag-and-drop/click-to-browse dropzone on the New Project
form, but the server is the authority: no file (or an empty/disallowed one) means
no Project row. Accepted files are written under a generated UUID name and the
download route is workspace/member scoped.
"""
from __future__ import annotations

import asyncio
import sqlite3
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select
from starlette.datastructures import Headers, QueryParams

from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes as web

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / 'app' / 'templates'
MODIFIED_TEMPLATES = (
    '_dropzone_assets.html',
    'projects/index.html',
    'projects/detail.html',
)
QUOTATION_COLUMNS = {
    'quotation_filename',
    'quotation_original_name',
    'quotation_content_type',
    'quotation_size',
    'quotation_uploaded_at',
}
WORKSPACE_QUOTATION_COLUMNS = {'quotation_bytes'}
QUOTATION_MESSAGE = 'Please upload the project quotation/paperwork before creating the project'

PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'a small quotation image'
EXE = b'MZ\x90\x00' + b'\x00' * 48
HTML = b'<html><script>alert(1)</script></html>'


@pytest.fixture(autouse=True)
def _legacy_template_call(monkeypatch):
    """Keep the repo's legacy TemplateResponse call shape working in this venv."""
    def render(name, context, *args, **kwargs):
        return Jinja2Templates.TemplateResponse(
            web.templates, context.get('request'), name, context, *args, **kwargs
        )

    monkeypatch.setattr(web, '_original_template_response', render)


class _Form(dict):
    def getlist(self, key):
        return self.get(key, [])


def _request(user, *, headers=None, path='/web/projects', form_data=None):
    data = form_data or {}

    async def form():
        return _Form(dict(data))

    return SimpleNamespace(
        session={'user_id': user.id} if user else {},
        headers=headers or {},
        state=SimpleNamespace(),
        url=SimpleNamespace(path=path, query=''),
        query_params=QueryParams(),
        form=form,
    )


def _upload(filename, data, content_type):
    return UploadFile(
        file=BytesIO(data),
        filename=filename,
        headers=Headers({'content-type': content_type}),
    )


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    import app.models  # noqa: F401  (register every model/table)

    # Uploaded bytes stay in the test's sandbox, never in app/uploads/.
    monkeypatch.setattr(web, 'BASE_DIR', tmp_path)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'slice5.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)

    acme, other = Workspace(name='Acme', timezone='UTC'), Workspace(name='Other Co', timezone='UTC')
    db.add_all([acme, other])
    await db.flush()
    # Snapshot IDs: a failed create rolls the session back and expires ORM
    # attributes, but the test still needs stable workspace identifiers.
    acme_id, other_id = acme.id, other.id
    people = dict(
        ada=User(username='ada', full_name='Ada Lovelace', hashed_password='x',
                 is_admin=True, workspace_id=acme.id),
        bob=User(username='bob', full_name='Bob Builder', hashed_password='x',
                 workspace_id=acme.id),
        frank=User(username='frank', full_name='Frank Field', hashed_password='x',
                   workspace_id=acme.id),
        olive=User(username='olive', full_name='Olive Oil', hashed_password='x',
                   is_admin=True, workspace_id=other.id),
    )
    db.add_all(people.values())
    await db.flush()
    foreign = Project(name='Foreign project', workspace_id=other.id, owner_id=people['olive'].id)
    db.add(foreign)
    await db.commit()

    yield SimpleNamespace(engine=engine, db=db, tmp_path=tmp_path, people=people,
                          acme=acme, other=other, acme_id=acme_id, other_id=other_id,
                          foreign=foreign)
    await db.close()
    await engine.dispose()


async def _create(env, user, quotation, *, name='New project', description=None):
    request = _request(user)
    response = await web.web_projects_create(
        request, name=name, description=description, quotation_file=quotation, db=env.db
    )
    return response, request


async def _projects(env):
    return (await env.db.execute(
        select(Project).where(Project.workspace_id == env.acme_id)
    )).scalars().all()


def _stored_files(env):
    folder = env.tmp_path / 'private_uploads' / 'quotations'
    return list(folder.glob('*')) if folder.exists() else []


# --- Feature 1: creation is impossible without a quotation --------------------

@pytest.mark.asyncio
async def test_create_without_quotation_is_rejected_and_no_project_row(env):
    response, request = await _create(env, env.people['ada'], None)

    assert response.status_code == 303
    assert response.headers['location'] == '/web/projects'
    assert 'quotation' in request.session['error_message'].lower()
    assert await _projects(env) == []
    assert _stored_files(env) == []

    # The no-JavaScript path sees a visible HTML error, not a silent no-op.
    board = await web.web_projects(request, db=env.db)
    assert 'quotation' in board.body.decode().lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('filename,data,content_type', [
    ('', PDF, 'application/pdf'),                       # browser sent no filename
    ('quotation.pdf', b'', 'application/pdf'),          # 0-byte file
    ('malware.exe', EXE, 'application/octet-stream'),   # disallowed extension
    ('page.html', HTML, 'text/html'),                   # script-capable type
])
async def test_create_with_missing_empty_or_disallowed_file_is_rejected(
    env, filename, data, content_type
):
    response, request = await _create(
        env, env.people['ada'], _upload(filename, data, content_type)
    )

    assert response.status_code == 303
    assert response.headers['location'] == '/web/projects'
    assert request.session['error_message']
    assert await _projects(env) == []
    assert _stored_files(env) == []


@pytest.mark.asyncio
async def test_oversized_quotation_is_rejected_without_writing_a_project(env, monkeypatch):
    import app.core.project_quotation as quotations

    monkeypatch.setattr(quotations, 'MAX_QUOTATION_BYTES', 32)
    assert quotations.MAX_QUOTATION_BYTES == 32

    response, request = await _create(
        env, env.people['ada'], _upload('big.pdf', PDF, 'application/pdf')
    )

    assert response.status_code == 303
    assert 'quotation' in request.session['error_message'].lower()
    assert await _projects(env) == []
    assert _stored_files(env) == []
    # The production limit stays generous; only the test lowered it.
    assert len(PDF) < 25 * 1024 * 1024


# --- Feature 2: an accepted file is stored safely and linked to the project ---

@pytest.mark.asyncio
async def test_valid_pdf_creates_project_saves_file_and_links_metadata(env):
    response, _ = await _create(
        env, env.people['ada'], _upload('../../Quotation 2026.pdf', PDF, 'application/pdf')
    )

    assert response.status_code == 303 and response.headers['location'] == '/web/projects'
    projects = await _projects(env)
    assert len(projects) == 1
    project = projects[0]
    assert project.name == 'New project'
    assert project.quotation_filename.endswith('.pdf')
    assert project.quotation_original_name == 'Quotation 2026.pdf'
    assert project.quotation_content_type == 'application/pdf'
    assert project.quotation_size == len(PDF)
    assert project.quotation_uploaded_at is not None

    stored = env.tmp_path / 'private_uploads' / 'quotations' / project.quotation_filename
    assert stored.is_file() and stored.read_bytes() == PDF
    assert '/' not in project.quotation_filename and '\\' not in project.quotation_filename
    assert '..' not in project.quotation_filename

    # The creator is still auto-assigned in the same transaction.
    members = (await env.db.execute(select(ProjectMember))).scalars().all()
    assert [(m.project_id, m.user_id) for m in members] == [
        (project.id, env.people['ada'].id)
    ]


@pytest.mark.asyncio
async def test_valid_image_quotation_is_accepted_and_served_as_an_image(env):
    await _create(env, env.people['bob'], _upload('quote.png', PNG, 'image/png'))

    project = (await _projects(env))[0]
    assert project.quotation_content_type == 'image/png'
    assert project.quotation_filename.endswith('.png')
    assert (env.tmp_path / 'private_uploads' / 'quotations' / project.quotation_filename).read_bytes() == PNG


@pytest.mark.asyncio
async def test_quotation_display_name_is_sanitized(env):
    hostile = '../../meter " onload="alert(1) <script>.pdf'
    await _create(env, env.people['ada'], _upload(hostile, PDF, 'application/pdf'))

    project = (await _projects(env))[0]
    assert not any(ch in project.quotation_original_name for ch in '"\'<>')
    assert '/' not in project.quotation_original_name
    assert project.quotation_filename.endswith('.pdf')


@pytest.mark.asyncio
async def test_detail_page_shows_quotation_card_and_download_link(env):
    await _create(env, env.people['ada'], _upload('Quotation.pdf', PDF, 'application/pdf'))
    project = (await _projects(env))[0]

    page = await web.web_project_detail(
        _request(env.people['ada']), project.id, db=env.db
    )
    html = page.body.decode()
    assert 'Quotation / Paperwork' in html
    assert f'/web/projects/{project.id}/quotation' in html
    assert 'Quotation.pdf' in html


# --- Feature 3: tenant-scoped, path-safe download -----------------------------

@pytest.mark.asyncio
async def test_download_is_authenticated_and_workspace_member_scoped(env):
    await _create(env, env.people['ada'], _upload('quote.pdf', PDF, 'application/pdf'))
    project = (await _projects(env))[0]
    # Bob is on the project; Frank is in the same company but not a member.
    env.db.add(ProjectMember(project_id=project.id, user_id=env.people['bob'].id,
                             assigned_by=env.people['ada'].id))
    await env.db.commit()
    url_user = lambda user: web.web_project_quotation_download(
        _request(user), project.id, db=env.db
    )

    signed_out = await url_user(None)
    assert signed_out.status_code == 303
    assert signed_out.headers['location'] == '/web/login'

    for allowed in (env.people['ada'], env.people['bob']):
        response = await url_user(allowed)
        assert response.status_code == 200
        assert Path(response.path).read_bytes() == PDF
        assert response.headers['content-type'].startswith('application/pdf')
        assert 'nosniff' in response.headers.get('x-content-type-options', '')
        assert 'no-store' in response.headers.get('cache-control', '')

    with pytest.raises(HTTPException) as denied:
        await url_user(env.people['frank'])
    assert denied.value.status_code == 403

    with pytest.raises(HTTPException) as foreign:
        await url_user(env.people['olive'])
    assert foreign.value.status_code == 404


@pytest.mark.asyncio
async def test_download_of_another_companys_project_is_not_found(env):
    with pytest.raises(HTTPException) as foreign:
        await web.web_project_quotation_download(
            _request(env.people['ada']), env.foreign.id, db=env.db
        )
    assert foreign.value.status_code == 404


@pytest.mark.asyncio
async def test_download_rejects_a_traversal_name_stored_in_the_database(env):
    await _create(env, env.people['ada'], _upload('quote.pdf', PDF, 'application/pdf'))
    project = (await _projects(env))[0]
    project.quotation_filename = '../../../data.db'
    await env.db.commit()

    with pytest.raises(HTTPException) as error:
        await web.web_project_quotation_download(
            _request(env.people['ada']), project.id, db=env.db
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('filename,data,content_type', [
    ('notes.txt', HTML, 'text/plain'),
    ('quote.csv', HTML, 'text/csv'),
    ('order.docx', EXE, 'application/octet-stream'),
])
async def test_active_or_executable_content_is_refused_with_a_safe_extension(
    env, filename, data, content_type
):
    response, request = await _create(
        env, env.people['ada'], _upload(filename, data, content_type)
    )

    assert response.status_code == 303
    assert 'quotation' in request.session['error_message'].lower()
    assert await _projects(env) == []
    assert _stored_files(env) == []


@pytest.mark.asyncio
async def test_quota_stops_repeated_uploads_from_filling_the_disk(env, monkeypatch):
    import app.core.project_quotation as quotations

    monkeypatch.setattr(quotations, 'MAX_QUOTATION_BYTES_PER_WORKSPACE', len(PDF))
    await _create(env, env.people['ada'], _upload('first.pdf', PDF, 'application/pdf'))

    response, request = await _create(
        env, env.people['ada'], _upload('second.png', PNG, 'image/png')
    )

    assert response.status_code == 303
    assert 'storage limit' in request.session['error_message'].lower()
    assert len(await _projects(env)) == 1
    assert len(_stored_files(env)) == 1


@pytest.mark.asyncio
async def test_concurrent_creates_cannot_both_pass_the_quota(env, monkeypatch):
    import app.core.project_quotation as quotations

    monkeypatch.setattr(quotations, 'MAX_QUOTATION_BYTES_PER_WORKSPACE', len(PDF))
    sessions = [
        AsyncSession(env.engine, expire_on_commit=False, autoflush=False)
        for _ in range(2)
    ]
    requests = [
        _request(env.people['ada'],
                 form_data={'quotation_file': f'quote{i}.pdf'})
        for i in range(2)
    ]
    try:
        results = await asyncio.gather(*[
            web.web_projects_create(
                requests[i], name=f'Race {i}', description=None,
                quotation_file=_upload(f'quote{i}.pdf', PDF, 'application/pdf'),
                db=session,
            )
            for i, session in enumerate(sessions)
        ])
    finally:
        for session in sessions:
            await session.close()

    assert all(response.status_code == 303 for response in results)
    assert len([r for r in requests if 'success_message' in r.session]) == 1
    assert len([r for r in requests if 'error_message' in r.session]) == 1
    assert len(await _projects(env)) == 1
    assert len(_stored_files(env)) == 1
    await env.db.refresh(env.acme)
    assert env.acme.quotation_bytes == len(PDF)


@pytest.mark.asyncio
async def test_quotations_are_not_written_to_the_public_uploads_mount(env):
    await _create(env, env.people['ada'], _upload('quote.pdf', PDF, 'application/pdf'))
    project = (await _projects(env))[0]

    public_root = env.tmp_path / 'uploads'
    assert not public_root.exists() or not list(public_root.rglob('*'))
    assert (env.tmp_path / 'private_uploads' / 'quotations' / project.quotation_filename).is_file()


@pytest.mark.asyncio
async def test_json_api_creation_is_an_explicit_documented_exemption(env):
    """Integrations may still create projects without a web upload.

    The invariant is pinned deliberately: API-created projects have no
    quotation metadata, so the detail card and download route stay inactive.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.api.deps import get_current_user_id, get_db
    from app.api.routes import projects as api_projects

    app = FastAPI()
    app.include_router(api_projects.router, prefix='/api')
    app.dependency_overrides[get_current_user_id] = lambda: env.people['ada'].id
    app.dependency_overrides[get_db] = lambda: env.db

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/projects/', json={'name': 'API project'})

    assert response.status_code == 201
    project = next(p for p in await _projects(env) if p.name == 'API project')
    assert project.quotation_filename is None


@pytest.mark.asyncio
async def test_web_project_deletion_removes_quotation_file_and_frees_quota(env):
    await _create(env, env.people['ada'], _upload('quote.pdf', PDF, 'application/pdf'))
    project = (await _projects(env))[0]
    stored = env.tmp_path / 'private_uploads' / 'quotations' / project.quotation_filename
    assert stored.is_file()

    request = _request(env.people['ada'],
                       form_data={'deletion_reason': 'Project cancelled'})
    response = await web.web_projects_delete(request, project.id, db=env.db)

    assert response.status_code == 303
    assert await _projects(env) == []
    assert not stored.exists()
    await env.db.refresh(env.acme)
    assert env.acme.quotation_bytes == 0


@pytest.mark.asyncio
async def test_api_project_deletion_removes_quotation_file_and_frees_quota(env, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.api.deps import get_current_user_id, get_db
    from app.api.routes import projects as api_projects

    # The API delete path must resolve the same app directory the web upload
    # writes to; this guard catches an off-by-one parents[] mistake.
    assert api_projects.APP_DIR == Path(web.__file__).resolve().parents[1]
    monkeypatch.setattr(api_projects, 'APP_DIR', env.tmp_path)
    await _create(env, env.people['ada'], _upload('quote.pdf', PDF, 'application/pdf'))
    project = (await _projects(env))[0]
    stored = env.tmp_path / 'private_uploads' / 'quotations' / project.quotation_filename
    assert stored.is_file()

    app = FastAPI()
    app.include_router(api_projects.router, prefix='/api')
    app.dependency_overrides[get_current_user_id] = lambda: env.people['ada'].id
    app.dependency_overrides[get_db] = lambda: env.db
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.request(
            'DELETE', f'/api/projects/{project.id}',
            json={'deletion_reason': 'Project cancelled'},
        )

    assert response.status_code == 204
    assert await _projects(env) == []
    assert not stored.exists()
    await env.db.refresh(env.acme)
    assert env.acme.quotation_bytes == 0


# --- Feature 4: startup migration and templates -------------------------------

async def _table_columns(db_path, table):
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_clean_db_startup_migration_creates_all_quotation_columns(tmp_path, monkeypatch):
    import app.core.database as dbmod

    path = tmp_path / 'fresh.db'
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    monkeypatch.setattr(dbmod, 'engine', engine)
    try:
        await dbmod.init_models()
        assert QUOTATION_COLUMNS <= await _table_columns(path, 'project')
        assert WORKSPACE_QUOTATION_COLUMNS <= await _table_columns(path, 'workspace')
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_db_startup_migration_keeps_rows_and_is_idempotent(tmp_path, monkeypatch):
    import app.core.database as dbmod

    path = tmp_path / 'legacy.db'
    conn = sqlite3.connect(str(path))
    conn.execute(
        'CREATE TABLE "project" ('
        'id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, owner_id INTEGER NOT NULL, '
        'workspace_id INTEGER, description VARCHAR, support_email VARCHAR, '
        'imap_host VARCHAR, imap_port INTEGER, imap_username VARCHAR, imap_password VARCHAR, '
        'imap_use_ssl BOOLEAN DEFAULT 1, is_archived BOOLEAN DEFAULT 0, '
        'archived_at TIMESTAMP, start_date DATE, due_date DATE, '
        'created_at TIMESTAMP, updated_at TIMESTAMP)'
    )
    conn.execute("INSERT INTO project (id, name, owner_id) VALUES (1, 'Old project', 7)")
    conn.commit()
    conn.close()

    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    monkeypatch.setattr(dbmod, 'engine', engine)
    try:
        await dbmod.init_models()
        await dbmod.init_models()
        conn = sqlite3.connect(str(path))
        try:
            columns = conn.execute('PRAGMA table_info("project")').fetchall()
            names = [row[1] for row in columns]
            assert QUOTATION_COLUMNS <= set(names)
            assert all(names.count(column) == 1 for column in QUOTATION_COLUMNS)
            assert conn.execute(
                'SELECT name FROM project WHERE id = 1'
            ).fetchone()[0] == 'Old project'
        finally:
            conn.close()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_project_form_has_required_multipart_dropzone(env):
    page = await web.web_projects(_request(env.people['ada']), db=env.db)
    html = page.body.decode()

    assert 'enctype="multipart/form-data"' in html
    assert 'name="quotation_file"' in html
    assert 'Project Quotation / Paperwork (Required)' in html
    assert QUOTATION_MESSAGE in html
    assert 'data-dropzone-required' in html and 'data-dropzone-show-size' in html
    # No static disabled attribute: without JavaScript the native required file
    # input still works; with JavaScript syncRequired() disables it on load.
    assert '<button type="submit" data-dropzone-submit' in html
    assert '.pdf' in html and '.docx' in html and '.xlsx' in html
    assert 'CEMDropzone' in html  # reusable drag-and-drop script from Slice 3


def test_modified_templates_parse_cleanly():
    environment = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    environment.filters.update(web.templates.env.filters)
    environment.globals.update(web.templates.env.globals)
    for name in MODIFIED_TEMPLATES:
        environment.get_template(name)
