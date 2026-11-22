"""Slice 4: a Board View project cannot be deleted without a reason, and an
admin can mark an active project as completed (which archives it).

The delete reason is required in the browser and again on the server, both for
the web form and the JSON API, and is kept in a tenant-scoped audit table after
the project row itself is gone. Completion uses the existing is_archived /
archived_at fields -- Project has no status or completed_at column.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select
from starlette.datastructures import QueryParams

from app.api.deps import get_current_user_id, get_db
from app.api.routes import projects as api_projects
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes as web

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / 'app' / 'templates'
MODIFIED_TEMPLATES = (
    'projects/index.html',
    'projects/detail.html',
    'projects/archived.html',
)


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


class _FilePart:
    """Stands in for an UploadFile returned by a file-part form field."""

    def __str__(self):
        return 'UploadFile(filename=reason.txt)'


def _request(user, *, form=None, path='/web/projects'):
    async def form_data():
        return _Form(form or {})

    return SimpleNamespace(
        session={'user_id': user.id} if user else {},
        headers={},
        state=SimpleNamespace(),
        url=SimpleNamespace(path=path, query=''),
        query_params=QueryParams(),
        form=form_data,
    )


@pytest_asyncio.fixture
async def env(tmp_path):
    import app.models  # noqa: F401  (register every model, incl. the audit table)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'slice4.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)

    acme, other = Workspace(name='Acme', timezone='UTC'), Workspace(name='Other Co', timezone='UTC')
    db.add_all([acme, other])
    await db.flush()
    people = dict(
        ada=User(username='ada', full_name='Ada Lovelace', hashed_password='x',
                 is_admin=True, workspace_id=acme.id),
        bob=User(username='bob', full_name='Bob Builder', hashed_password='x',
                 workspace_id=acme.id),
        olive=User(username='olive', full_name='Olive Oil', hashed_password='x',
                   is_admin=True, workspace_id=other.id),
    )
    db.add_all(people.values())
    await db.flush()
    projects = dict(
        move=Project(name='Office move', workspace_id=acme.id, owner_id=people['ada'].id),
        yard=Project(name='Yard cleanup', workspace_id=acme.id, owner_id=people['ada'].id),
        foreign=Project(name='Foreign project', workspace_id=other.id, owner_id=people['olive'].id),
    )
    db.add_all(projects.values())
    await db.flush()
    db.add(ProjectMember(project_id=projects['move'].id, user_id=people['bob'].id,
                         assigned_by=people['ada'].id))
    await db.commit()
    yield SimpleNamespace(engine=engine, db=db, people=people, projects=projects,
                          acme=acme, other=other)
    await db.close()
    await engine.dispose()


async def _audits(db):
    from app.models.project_audit import ProjectDeletionAudit

    return (await db.execute(select(ProjectDeletionAudit))).scalars().all()


async def _delete(env, user, project_id, reason):
    request = _request(user, form={'deletion_reason': reason} if reason is not None else {})
    return await web.web_projects_delete(request, project_id, db=env.db)


async def _complete(env, user, project_id):
    return await web.web_projects_complete(_request(user), project_id, db=env.db)


def _api_client(env, user):
    app = FastAPI()
    app.include_router(api_projects.router, prefix='/api')
    app.dependency_overrides[get_current_user_id] = lambda: user.id
    app.dependency_overrides[get_db] = lambda: env.db
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# --- Feature 1: deletion reason is mandatory ---------------------------------

@pytest.mark.asyncio
async def test_delete_without_reason_is_rejected_and_project_survives(env):
    request = _request(env.people['ada'])
    response = await web.web_projects_delete(request, env.projects['move'].id, db=env.db)

    assert response.status_code == 303 and response.headers['location'] == '/web/projects'
    assert 'deletion reason is required' in request.session['error_message'].lower()
    assert (await env.db.get(Project, env.projects['move'].id)) is not None
    assert await _audits(env.db) == []

    # The no-JavaScript path gets a visible HTML error, not a raw JSON response.
    board = await web.web_projects(request, db=env.db)
    assert 'deletion reason is required' in board.body.decode().lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['', '   ', '\n\t  '])
async def test_delete_with_whitespace_reason_is_rejected(env, reason):
    request = _request(env.people['ada'], form={'deletion_reason': reason})
    response = await web.web_projects_delete(request, env.projects['move'].id, db=env.db)

    assert response.status_code == 303 and response.headers['location'] == '/web/projects'
    assert 'deletion reason is required' in request.session['error_message'].lower()
    assert (await env.db.get(Project, env.projects['move'].id)) is not None
    assert await _audits(env.db) == []


@pytest.mark.asyncio
async def test_delete_rejects_file_part_submitted_as_reason(env):
    request = _request(env.people['ada'], form={'deletion_reason': _FilePart()})
    response = await web.web_projects_delete(request, env.projects['move'].id, db=env.db)

    assert response.status_code == 303
    assert 'deletion reason is required' in request.session['error_message'].lower()
    assert (await env.db.get(Project, env.projects['move'].id)) is not None
    assert await _audits(env.db) == []


@pytest.mark.asyncio
async def test_delete_with_reason_removes_project_and_persists_audit(env):
    name = env.projects['move'].name
    response = await _delete(env, env.people['ada'], env.projects['move'].id,
                             '  Project finished  ')

    assert response.status_code == 303 and response.headers['location'] == '/web/projects'
    assert await env.db.get(Project, env.projects['move'].id) is None
    audits = await _audits(env.db)
    assert len(audits) == 1
    audit = audits[0]
    assert audit.project_id == env.projects['move'].id
    assert audit.project_name == name
    assert audit.deleted_by_user_id == env.people['ada'].id
    assert audit.workspace_id == env.acme.id
    assert audit.deletion_reason == 'Project finished'
    assert audit.deleted_at is not None

    from app.models.task_extensions import ActivityLog

    logs = (await env.db.execute(select(ActivityLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].entity_type == 'project'
    assert logs[0].entity_id == env.projects['move'].id
    assert logs[0].action_type == 'deleted'
    assert 'Project finished' in (logs[0].details or '')


@pytest.mark.asyncio
async def test_api_delete_without_reason_cannot_bypass_the_web_rule(env):
    client = _api_client(env, env.people['ada'])
    async with client:
        blank = await client.request('DELETE', f"/api/projects/{env.projects['yard'].id}",
                                     json={'deletion_reason': '   '})
        missing = await client.request('DELETE', f"/api/projects/{env.projects['yard'].id}")
        assert blank.status_code == 400 and missing.status_code == 400

    assert await env.db.get(Project, env.projects['yard'].id) is not None
    assert await _audits(env.db) == []


@pytest.mark.asyncio
async def test_api_delete_with_reason_is_audited(env):
    client = _api_client(env, env.people['ada'])
    async with client:
        response = await client.request('DELETE', f"/api/projects/{env.projects['yard'].id}",
                                        json={'deletion_reason': 'No longer needed'})

    assert response.status_code == 204
    assert await env.db.get(Project, env.projects['yard'].id) is None
    assert [a.deletion_reason for a in await _audits(env.db)] == ['No longer needed']


@pytest.mark.asyncio
async def test_non_admin_cannot_delete_project(env):
    with pytest.raises(HTTPException) as denied:
        await _delete(env, env.people['bob'], env.projects['move'].id, 'done')
    assert denied.value.status_code == 403
    assert await env.db.get(Project, env.projects['move'].id) is not None


@pytest.mark.asyncio
async def test_other_company_project_cannot_be_deleted(env):
    with pytest.raises(HTTPException) as denied:
        await _delete(env, env.people['olive'], env.projects['move'].id, 'done')
    assert denied.value.status_code == 404
    assert await env.db.get(Project, env.projects['move'].id) is not None
    assert await _audits(env.db) == []


@pytest.mark.asyncio
async def test_overlong_reason_is_bounded_to_2000_characters(env):
    response = await _delete(env, env.people['ada'], env.projects['move'].id, 'x' * 2100)

    assert response.status_code == 303
    assert len((await _audits(env.db))[0].deletion_reason) == 2000


@pytest.mark.asyncio
async def test_unauthenticated_delete_and_complete_redirect_to_login(env):
    request = _request(None, form={'deletion_reason': 'done'})
    response = await web.web_projects_delete(request, env.projects['move'].id, db=env.db)
    assert response.status_code == 303 and response.headers['location'] == '/web/login'

    request = _request(None)
    response = await web.web_projects_complete(request, env.projects['move'].id, db=env.db)
    assert response.status_code == 303 and response.headers['location'] == '/web/login'

    assert await env.db.get(Project, env.projects['move'].id) is not None
    assert await _audits(env.db) == []


# --- Feature 2: Mark as Completed archives the Board View project ------------

@pytest.mark.asyncio
async def test_mark_as_completed_archives_and_moves_project_to_archived_list(env):
    project = env.projects['move']
    response = await _complete(env, env.people['ada'], project.id)

    assert response.status_code == 303
    assert response.headers['location'] == '/web/projects/archived'
    await env.db.refresh(project)
    assert project.is_archived is True
    assert project.archived_at is not None

    board = await web.web_projects(_request(env.people['ada']), db=env.db)
    assert project.id not in [p.id for p in board.context['projects']]
    assert project.name not in board.body.decode()

    archived = await web.web_projects_archived(_request(env.people['ada']), db=env.db)
    assert project.id in [p.id for p in archived.context['projects']]
    assert project.name in archived.body.decode()


@pytest.mark.asyncio
async def test_completion_is_logged_and_reports_success(env):
    request = _request(env.people['ada'])
    await web.web_projects_complete(request, env.projects['move'].id, db=env.db)

    from app.models.task_extensions import ActivityLog

    logs = (await env.db.execute(select(ActivityLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].entity_type == 'project'
    assert logs[0].entity_id == env.projects['move'].id
    assert logs[0].action_type == 'completed'
    assert 'Office move' in (logs[0].details or '')
    assert 'completed' in request.session['success_message'].lower()

    archived = await web.web_projects_archived(request, db=env.db)
    assert 'marked as completed' in archived.body.decode().lower()


@pytest.mark.asyncio
async def test_non_admin_and_other_company_cannot_complete_project(env):
    with pytest.raises(HTTPException) as denied:
        await _complete(env, env.people['bob'], env.projects['move'].id)
    assert denied.value.status_code == 403

    with pytest.raises(HTTPException) as foreign:
        await _complete(env, env.people['olive'], env.projects['move'].id)
    assert foreign.value.status_code == 404

    await env.db.refresh(env.projects['move'])
    assert env.projects['move'].is_archived is False


@pytest.mark.asyncio
async def test_completing_an_already_archived_project_is_a_noop(env):
    project = env.projects['move']
    project.is_archived = True
    project.archived_at = datetime(2020, 1, 1)
    await env.db.commit()

    request = _request(env.people['ada'])
    response = await web.web_projects_complete(request, project.id, db=env.db)

    assert response.status_code == 303
    assert response.headers['location'] == '/web/projects/archived'
    assert 'already archived' in request.session['error_message'].lower()
    await env.db.refresh(project)
    assert project.archived_at == datetime(2020, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('user_key', ['bob', 'ada'])
async def test_api_patch_cannot_archive_project_through_mass_assignment(env, user_key):
    client = _api_client(env, env.people[user_key])
    async with client:
        response = await client.patch(f"/api/projects/{env.projects['move'].id}",
                                      json={'is_archived': True})

    assert response.status_code == 200
    await env.db.refresh(env.projects['move'])
    assert env.projects['move'].is_archived is False


# --- Templates ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_reason_modal_renders_in_project_list(env):
    page = await web.web_projects(_request(env.people['ada']), db=env.db)
    html = page.body.decode()
    assert "Why is this project being deleted?" in html
    assert 'name="deletion_reason"' in html
    assert 'data-delete-submit' in html and 'disabled' in html
    assert 'maxlength="2000"' in html
    assert 'confirm(\'Delete project' not in html


@pytest.mark.asyncio
async def test_mark_as_completed_button_renders_next_to_add_task(env):
    page = await web.web_project_detail(_request(env.people['ada']), env.projects['move'].id,
                                        db=env.db)
    html = page.body.decode()
    add_at = html.index('Add task')
    complete_at = html.index('Mark as Completed')
    assert 0 <= complete_at - add_at < 2000, 'the button sits next to Add task'
    assert f"/web/projects/{env.projects['move'].id}/complete" in html
    assert "Mark this project as completed and move it to Archived Projects?" in html


@pytest.mark.asyncio
async def test_completed_button_is_hidden_on_archived_projects_and_for_non_admins(env):
    env.projects['yard'].is_archived = True
    await env.db.commit()
    page = await web.web_project_detail(_request(env.people['ada']), env.projects['yard'].id,
                                        db=env.db)
    assert 'Mark as Completed' not in page.body.decode()

    page = await web.web_project_detail(_request(env.people['bob']), env.projects['move'].id,
                                        db=env.db)
    assert 'Mark as Completed' not in page.body.decode()


def test_modified_jinja_templates_parse_cleanly():
    environment = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    environment.filters.update(web.templates.env.filters)
    environment.globals.update(web.templates.env.globals)
    for name in MODIFIED_TEMPLATES:
        environment.get_template(name)
