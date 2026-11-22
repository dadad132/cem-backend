"""Slice 3: project member search by name/username/email, and safe drag-and-drop uploads.

The two guarantees here are: a project admin can find any of the company's
users by typing part of their name, username or email (and never sees members
already on the project or users of another company), and dropped photos/files
are saved through the existing web upload paths with safe names, allowed types
and workspace checks. The reusable dropzone is a Jinja partial so Slice 5 can
use the same helper for Project Quotation uploads.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select
from starlette.datastructures import Headers, QueryParams

from app.models.assignment import Assignment
from app.models.comment_attachment import CommentAttachment
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.task_extensions import TaskAttachment
from app.models.ticket import Ticket, TicketAttachment
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes as web

PNG = b'\x89PNG\r\n\x1a\n' + b'not really a picture but the magic bytes are right'
EXE = b'MZ\x90\x00' + b'\x00' * 40
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / 'app' / 'templates'


def _request(user, *, headers=None, path='/web/projects'):
    return SimpleNamespace(
        session={'user_id': user.id} if user else {},
        headers=headers or {},
        state=SimpleNamespace(),
        url=SimpleNamespace(path=path, query=''),
        query_params=QueryParams(),
    )


def _upload(filename, data, content_type):
    return UploadFile(
        file=BytesIO(data),
        filename=filename,
        headers=Headers({'content-type': content_type}),
    )


def test_upload_display_names_cannot_break_out_of_html_or_headers():
    from app.core.attachment_upload import clean_upload_name

    name = clean_upload_name('../../meter " onload="alert(1) <script>.PNG')
    assert name.endswith('.PNG') and '..' not in name
    assert not any(ch in name for ch in '"\'<>'), name


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    import app.models  # noqa: F401  (register every model)

    # Keep uploaded bytes in the test's sandbox, never in app/uploads/.
    monkeypatch.setattr(web, 'BASE_DIR', tmp_path)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'slice3.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)

    acme, other = Workspace(name='Acme', timezone='UTC'), Workspace(name='Other Co', timezone='UTC')
    db.add_all([acme, other])
    await db.flush()
    people = dict(
        ada=User(username='ada', full_name='Ada Lovelace', display_name='Countess',
                 email='ada@acme.test', hashed_password='x', is_admin=True,
                 workspace_id=acme.id),
        bob=User(username='bob', full_name='Bob Builder', email='bob@acme.test',
                 hashed_password='x', workspace_id=acme.id),
        cara=User(username='cara', full_name='Cara Case', email='cara@acme.test',
                  hashed_password='x', workspace_id=acme.id),
        dan=User(username='dan', full_name='Dan Dare', email='dan@acme.test',
                 hashed_password='x', workspace_id=acme.id, is_active=False),
        frank=User(username='frank', full_name='Frank Field', email='frank@acme.test',
                   hashed_password='x', workspace_id=acme.id),
        olive=User(username='olive', full_name='Olive Oil', email='olive@other.test',
                   hashed_password='x', is_admin=True, workspace_id=other.id),
    )
    db.add_all(people.values())
    await db.flush()
    move = Project(name='Office move', workspace_id=acme.id, owner_id=people['ada'].id)
    foreign = Project(name='Foreign', workspace_id=other.id, owner_id=people['olive'].id)
    db.add_all([move, foreign])
    await db.flush()
    db.add(ProjectMember(project_id=move.id, user_id=people['bob'].id, assigned_by=people['ada'].id))
    task = Task(title='Pack boxes', project_id=move.id, creator_id=people['ada'].id,
                status=TaskStatus.in_progress)
    foreign_task = Task(title='Not ours', project_id=foreign.id, creator_id=people['olive'].id)
    db.add_all([task, foreign_task])
    await db.flush()
    db.add_all([
        Assignment(task_id=task.id, assignee_id=people['ada'].id),
        Assignment(task_id=foreign_task.id, assignee_id=people['olive'].id),
    ])
    ticket = Ticket(ticket_number='TKT-2026-00001', subject='Printer', workspace_id=acme.id)
    foreign_ticket = Ticket(ticket_number='TKT-2026-00002', subject='Foreign', workspace_id=other.id)
    db.add_all([ticket, foreign_ticket])
    await db.commit()

    yield SimpleNamespace(engine=engine, db=db, tmp_path=tmp_path, people=people,
                          project=move, foreign_project=foreign, task=task,
                          foreign_task=foreign_task, ticket=ticket,
                          foreign_ticket=foreign_ticket, acme=acme, other=other)
    await db.close()
    await engine.dispose()


async def _search(env, q, user=None):
    response = await web.web_project_available_users(
        _request(user or env.people['ada']), env.project.id, q=q, db=env.db
    )
    return response, json.loads(response.body)['users']


# --- Feature 1: available-user search ----------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize('query,expected', [
    ('ADA', 'ada'),          # username, case-insensitive
    ('CARA@ACME.TEST', 'cara'),  # full email, case-insensitive
    ('lovel', 'ada'),        # partial full name, even with a display name set
    ('count', 'ada'),        # partial display name
    ('@OTHER.TEST', None),   # another company's user is never ours
    ('builder', None),       # already a project member
])
async def test_available_user_search_matches_name_username_email_and_scopes(env, query, expected):
    response, users = await _search(env, query)
    assert response.status_code == 200
    assert [u['username'] for u in users] == ([expected] if expected else [])


@pytest.mark.asyncio
async def test_empty_search_lists_every_available_user_but_no_others(env):
    _, users = await _search(env, '')
    assert {u['username'] for u in users} == {'ada', 'cara', 'frank'}
    assert all('@' in u['label'] and '(' in u['label'] and u['email'] for u in users)


@pytest.mark.asyncio
async def test_available_user_search_rejects_wrong_admin_and_foreign_project(env):
    denied = await web.web_project_available_users(
        _request(env.people['frank']), env.project.id, q='', db=env.db
    )
    assert denied.status_code == 403

    with pytest.raises(HTTPException) as foreign:
        await web.web_project_available_users(
            _request(env.people['olive']), env.project.id, q='', db=env.db
        )
    assert foreign.value.status_code == 404

    signed_out = await web.web_project_available_users(
        _request(None), env.project.id, q='', db=env.db
    )
    assert signed_out.status_code == 303 and signed_out.headers['location'] == '/web/login'


@pytest.mark.asyncio
async def test_members_page_has_live_search_and_filterable_user_details(env):
    page = await web.web_project_members(_request(env.people['ada']), env.project.id, db=env.db)
    html = page.body.decode()
    assert html.count('Search by username, name, or email...') == 1, 'single unified top search bar only'
    assert html.count('data-member-search') == 1
    assert 'id="memberSearch"' not in html
    assert 'data-search' in html
    assert 'cara@acme.test' in html, 'the full address is available to the live filter'
    assert 'data-username="cara"' in html
    assert 'data-username="bob"' not in html, 'an existing member is not offered for adding'


def test_profile_dropdown_uses_hover_bridge_and_click_toggle():
    base_source = (TEMPLATES_DIR / 'base.html').read_text(encoding='utf-8')
    assert '.profile-dropdown:hover .profile-dropdown-menu' in base_source
    assert 'profile-dropdown-menu absolute right-0 pt-2 w-56 z-50' in base_source
    assert 'pinned = !pinned' in base_source, 'clicking pins the profile menu open'
    assert '@keydown.escape.window' in base_source


@pytest.mark.asyncio
async def test_selecting_a_searched_user_adds_them_to_the_project(env):
    added = await web.web_project_members_add(
        _request(env.people['ada']), env.project.id,
        user_identifier='cara@acme.test', db=env.db
    )
    assert added.status_code == 303
    member = (await env.db.execute(select(ProjectMember).where(
        ProjectMember.project_id == env.project.id,
        ProjectMember.user_id == env.people['cara'].id,
    ))).scalar_one_or_none()
    assert member is not None
    _, users = await _search(env, 'cara')
    assert users == []


# --- Feature 2: drag-and-drop attachment uploads -----------------------------

@pytest.mark.asyncio
async def test_task_attachment_endpoint_saves_a_photo_with_a_safe_name(env):
    response = await web.web_task_add_attachment(
        _request(env.people['ada'], headers={'X-Requested-With': 'XMLHttpRequest'}),
        env.task.id, files=[_upload('../../holiday photo.PNG', PNG, 'image/png')], db=env.db
    )
    assert response.status_code == 200
    assert json.loads(response.body)['saved'] == 1

    saved = (await env.db.execute(select(TaskAttachment).where(
        TaskAttachment.task_id == env.task.id
    ))).scalar_one()
    assert saved.filename == 'holiday photo.PNG', 'only the basename is kept as the display name'
    assert '..' not in saved.file_path and saved.file_path.endswith('.png')
    stored = env.tmp_path / saved.file_path.replace('app/', '', 1)
    if not stored.exists():
        stored = env.tmp_path / Path(saved.file_path).name
    assert stored.read_bytes() == PNG or (env.tmp_path / 'uploads' / 'tasks' / Path(saved.file_path).name).read_bytes() == PNG


@pytest.mark.asyncio
async def test_task_attachment_endpoint_rejects_disallowed_files(env):
    response = await web.web_task_add_attachment(
        _request(env.people['ada'], headers={'X-Requested-With': 'XMLHttpRequest'}),
        env.task.id, files=[_upload('unsafe.exe', EXE, 'application/octet-stream')], db=env.db
    )
    assert response.status_code == 400
    assert json.loads(response.body)['saved'] == 0
    assert (await env.db.execute(select(TaskAttachment))).scalars().all() == []


@pytest.mark.asyncio
async def test_task_attachment_endpoint_is_workspace_and_permission_scoped(env):
    with pytest.raises(HTTPException) as foreign:
        await web.web_task_add_attachment(
            _request(env.people['ada']), env.foreign_task.id,
            files=[_upload('x.png', PNG, 'image/png')], db=env.db
        )
    assert foreign.value.status_code == 404

    # An outsider cannot use a guessed task id from another company either.
    with pytest.raises(HTTPException) as guessed:
        await web.web_task_add_attachment(
            _request(env.people['olive']), env.task.id,
            files=[_upload('x.png', PNG, 'image/png')], db=env.db
        )
    assert guessed.value.status_code == 404

    with pytest.raises(HTTPException) as unassigned:
        await web.web_task_add_attachment(
            _request(env.people['frank']), env.task.id,
            files=[_upload('x.png', PNG, 'image/png')], db=env.db
        )
    assert unassigned.value.status_code == 403


@pytest.mark.asyncio
async def test_task_comment_attachment_upload_validates_type_and_filename(env):
    response = await web.web_task_add_comment(
        _request(env.people['ada']), env.task.id, content='On site photo',
        files=[
            _upload('..\\..\\shot.PNG', PNG, 'image/png'),
            _upload('payload.exe', EXE, 'application/octet-stream'),
        ],
        db=env.db,
    )
    assert response.status_code == 303
    saved = (await env.db.execute(select(CommentAttachment))).scalars().all()
    assert len(saved) == 1
    assert saved[0].filename == 'shot.PNG'
    assert '..' not in saved[0].file_path and saved[0].file_path.endswith('.png')


@pytest.mark.asyncio
async def test_ticket_photo_upload_succeeds_and_blocks_cross_tenant_access(env):
    response = await web.web_tickets_add_photos(
        _request(env.people['ada'], headers={'X-Requested-With': 'XMLHttpRequest'}),
        env.ticket.id, photos=[_upload('../../meter.png', PNG, 'image/png')], db=env.db
    )
    assert response.status_code == 200
    assert json.loads(response.body)['saved'] == 1
    saved = (await env.db.execute(select(TicketAttachment).where(
        TicketAttachment.ticket_id == env.ticket.id
    ))).scalar_one()
    assert saved.filename == 'meter.png' and '..' not in saved.file_path

    denied = await web.web_tickets_add_photos(
        _request(env.people['olive'], headers={'X-Requested-With': 'XMLHttpRequest'}),
        env.ticket.id, photos=[_upload('x.png', PNG, 'image/png')], db=env.db
    )
    assert denied.status_code == 404
    assert len((await env.db.execute(select(TicketAttachment))).scalars().all()) == 1


# --- Templates: reusable dropzone --------------------------------------------

def test_dropzone_assets_are_reusable_and_wired_into_attachment_pages():
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    html = env.get_template('_dropzone_assets.html').render()
    for marker in ('data-dropzone', 'dragenter', 'dragover', 'dragleave', 'drop',
                   'preventDefault', 'DataTransfer', 'createObjectURL', 'remove',
                   'data-dropzone-submit'):
        assert marker in html, marker

    base_source = (TEMPLATES_DIR / 'base.html').read_text(encoding='utf-8')
    assert '_dropzone_assets.html' in base_source, 'load the helper once for every page'

    rendered_macro = env.from_string(
        "{% from '_dropzone_assets.html' import dropzone %}"
        "{{ dropzone('files', 'test-files', upload_url='/web/tasks/1/attachments') }}"
    ).render()
    assert 'data-dropzone' in rendered_macro
    assert 'name="files"' in rendered_macro
    assert 'data-dropzone-input' in rendered_macro

    for name, marker in (
        ('tasks/detail.html', '/web/tasks/{{ task.id }}/attachments'),
        ('tickets/detail.html', '/web/tickets/{{ ticket.id }}/photos'),
    ):
        source = (TEMPLATES_DIR / name).read_text(encoding='utf-8')
        assert "from '_dropzone_assets.html' import dropzone" in source, name
        assert 'dropzone(' in source, name
        assert marker in source, name

    task_source = (TEMPLATES_DIR / 'tasks/detail.html').read_text(encoding='utf-8')
    assert 'upload_url=' in task_source, 'task drops post immediately'
    assert 'data-dropzone-submit' in task_source, 'the no-JS button is a safe fallback'


def test_every_template_parses_cleanly():
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    for template in sorted(TEMPLATES_DIR.rglob('*.html')):
        env.parse(template.read_text(encoding='utf-8'))
