"""Slice 2: a done task stays on the board until a bot archives it.

Two guarantees are tested here.  Marking a task Done through the SLA/billable
dialog records when it finished but does not archive it (the job card and the
billing fields are still saved), so the Board View keeps the card in Done and
the done-this-week figures still count it.  An API-key bot may then archive
tasks explicitly, one at a time or in bulk, only inside its own workspace.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
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

from app.api.routes import external
from app.core import project_weekly_report as report
from app.core.database import get_session
from app.core.team_overview import build_overview
from app.models.api_key import APIKey
from app.models.assignment import Assignment
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes as web

KEY = 'cem_slice2test0123456789'
# A fixed Wednesday keeps the report week deterministic; the repo's date is
# otherwise taken from the machine clock and a Sunday falls outside Sat end.
NOW = datetime(2026, 11, 18, 12, 0)


@pytest.fixture(autouse=True)
def _legacy_template_call(monkeypatch):
    """Bridge the repo's legacy TemplateResponse call to this venv's Starlette.

    app.web.routes patches TemplateResponse with the old ``(name, context)``
    shape; Starlette 1.10 expects ``(request, name, context)``. This keeps the
    real Board View template in the test without changing production code.
    """
    def render(name, context, *args, **kwargs):
        return Jinja2Templates.TemplateResponse(
            web.templates, context.get('request'), name, context, *args, **kwargs
        )

    monkeypatch.setattr(web, '_original_template_response', render)


def _request(user, *, headers=None, path='/web/tasks'):
    return SimpleNamespace(
        session={'user_id': user.id},
        headers=headers or {},
        state=SimpleNamespace(),
        url=SimpleNamespace(path=path, query=''),
        query_params=QueryParams(),
    )


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    import app.models  # noqa: F401  (register every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'slice2.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)

    acme, other = Workspace(name='Acme', timezone='UTC'), Workspace(name='Other Co', timezone='UTC')
    db.add_all([acme, other])
    await db.flush()
    people = dict(
        ada=User(username='ada', full_name='Ada Lovelace', email='ada@example.com',
                 hashed_password='x', is_admin=True, workspace_id=acme.id,
                 profile_completed=True),
        bob=User(username='bob', full_name='Bob Builder', email='bob@example.com',
                 hashed_password='x', workspace_id=acme.id, profile_completed=True),
        olive=User(username='olive', full_name='Olive Oil', email='olive@example.com',
                   hashed_password='x', is_admin=True, workspace_id=other.id,
                   profile_completed=True),
    )
    db.add_all(people.values())
    await db.flush()
    projects = dict(
        move=Project(name='Office move', workspace_id=acme.id, owner_id=people['ada'].id),
        yard=Project(name='Yard', workspace_id=acme.id, owner_id=people['ada'].id),
        foreign=Project(name='Foreign', workspace_id=other.id, owner_id=people['olive'].id),
    )
    db.add_all(projects.values())
    await db.flush()

    tasks = dict(
        active=Task(title='Pack boxes', project_id=projects['move'].id, creator_id=people['ada'].id),
        fresh=Task(title='Fresh done', project_id=projects['move'].id, creator_id=people['ada'].id,
                   status=TaskStatus.done, completed_at=NOW - timedelta(hours=2)),
        legacy=Task(title='Legacy archived', project_id=projects['move'].id, creator_id=people['ada'].id,
                    status=TaskStatus.done, is_archived=True, archived_at=NOW - timedelta(hours=3),
                    completed_at=NOW - timedelta(hours=3)),
        old=Task(title='Old done', project_id=projects['move'].id, creator_id=people['ada'].id,
                 status=TaskStatus.done, completed_at=NOW - timedelta(days=10)),
        yard=Task(title='Done in yard', project_id=projects['yard'].id, creator_id=people['ada'].id,
                  status=TaskStatus.done, completed_at=NOW - timedelta(hours=1)),
        foreign=Task(title='Not yours', project_id=projects['foreign'].id, creator_id=people['olive'].id,
                     status=TaskStatus.done, completed_at=NOW - timedelta(hours=4)),
    )
    db.add_all(tasks.values())
    await db.flush()
    db.add_all([
        Assignment(task_id=tasks['fresh'].id, assignee_id=people['ada'].id),
        Assignment(task_id=tasks['active'].id, assignee_id=people['ada'].id),
        APIKey(workspace_id=acme.id, name='Bot', key_prefix=KEY[:12],
               key_hash=hashlib.sha256(KEY.encode()).hexdigest()),
    ])
    await db.commit()

    async def _no_email(*args, **kwargs):
        return None

    monkeypatch.setattr(web, 'send_completion_notification_email', _no_email)
    monkeypatch.setattr('app.core.job_card_pdf.generate_job_card_pdf', lambda **_: b'PDF')

    app = FastAPI()
    app.include_router(external.router, prefix='/api')

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session
    client = AsyncClient(transport=ASGITransport(app=app), base_url='http://test',
                         headers={'X-API-Key': KEY})
    yield SimpleNamespace(engine=engine, db=db, app=app, client=client, key=KEY,
                          people=people, projects=projects, tasks=tasks, ws=acme, other=other)
    await client.aclose()
    await db.close()
    await engine.dispose()


async def _complete(env, task_id, *, close_type='sla', **fields):
    """Post the SLA/billable dialog the same way the Board View does."""
    values = dict(close_type=close_type, customer_name=None, customer_surname=None,
                  customer_phone=None, customer_office_number=None, customer_branch=None,
                  billable_traveling=None, billable_labour_onsite=None,
                  billable_remote_labour=None, billable_equipment_used=None,
                  completion_notes=None)
    values.update(fields)
    return await web.web_task_complete_with_details(
        _request(env.people['ada'], headers={'accept': 'application/json'}), task_id,
        **values, db=env.db
    )


# ---------------------------------------------------------------------------
# Marking done: completion time, not an archive
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sla_completion_keeps_done_task_unarchived_and_on_board(env):
    task, project = env.tasks['active'], env.projects['move']

    response = await _complete(env, task.id, close_type='sla')
    assert response.status_code == 200
    await env.db.refresh(task)

    assert task.status == TaskStatus.done
    assert task.is_archived is False, 'done is not archived; a bot archives later'
    assert task.archived_at is None
    assert task.completed_at is not None, 'the finish time moves to completed_at'
    assert task.close_type == 'sla'
    assert task.billable_labour_onsite is None

    page = await web.web_project_detail(_request(env.people['ada']), project.id, db=env.db)
    done_ids = [t.id for t in page.context['columns'][TaskStatus.done]]
    assert task.id in done_ids, 'the card stays in the Board View Done column'
    assert 'Pack boxes' in page.body.decode()


@pytest.mark.asyncio
async def test_repeated_done_status_keeps_original_completion_time(env):
    task = env.tasks['fresh']
    finished_at = task.completed_at

    response = await web.web_task_update_status(
        _request(env.people['ada'], headers={'accept': 'application/json'}),
        task.id, status_value='done', db=env.db,
    )
    assert response.status_code == 200, response.body
    await env.db.refresh(task)

    assert task.status == TaskStatus.done
    assert task.completed_at == finished_at
    assert task.is_archived is False and task.archived_at is None


@pytest.mark.asyncio
async def test_non_admin_cannot_change_a_done_task_status(env):
    task = env.tasks['fresh']
    finished_at = task.completed_at

    with pytest.raises(HTTPException) as denied:
        await web.web_task_update_status(
            _request(env.people['bob'], headers={'accept': 'application/json'}),
            task.id, status_value='in_progress', db=env.db,
        )
    assert denied.value.status_code == 403
    await env.db.refresh(task)
    assert task.status == TaskStatus.done
    assert task.completed_at == finished_at
    assert task.is_archived is False and task.archived_at is None


@pytest.mark.asyncio
async def test_moving_legacy_archived_task_out_of_done_unarchives_it(env):
    task = env.tasks['legacy']

    response = await web.web_task_update_status(
        _request(env.people['ada'], headers={'accept': 'application/json'}),
        task.id, status_value='in_progress', db=env.db,
    )
    assert response.status_code == 200, response.body
    await env.db.refresh(task)
    assert task.status == TaskStatus.in_progress
    assert task.completed_at is None
    assert task.is_archived is False and task.archived_at is None


@pytest.mark.asyncio
async def test_billable_completion_still_saves_the_job_card_without_archiving(env):
    task = env.tasks['active']

    response = await _complete(
        env, task.id, close_type='billable', customer_name='Jane', customer_surname='Botha',
        customer_phone='0123456789', customer_branch='Sandton',
        customer_office_number='0111234567', billable_traveling='40km',
        billable_labour_onsite='2h', billable_remote_labour='1h',
        billable_equipment_used='Cable', completion_notes='Replaced switch',
    )
    assert response.status_code == 200
    await env.db.refresh(task)

    assert (task.status, task.is_archived, task.archived_at) == (TaskStatus.done, False, None)
    assert task.completed_at is not None
    assert task.close_type == 'billable'
    assert (task.customer_name, task.customer_surname, task.customer_phone,
            task.customer_branch, task.customer_office_number) == (
        'Jane', 'Botha', '0123456789', 'Sandton', '0111234567')
    assert (task.billable_traveling, task.billable_labour_onsite,
            task.billable_remote_labour, task.billable_equipment_used,
            task.completion_notes) == ('40km', '2h', '1h', 'Cable', 'Replaced switch')


@pytest.mark.asyncio
async def test_billable_completion_still_refuses_an_incomplete_job_card(env):
    task = env.tasks['active']

    response = await _complete(env, task.id, close_type='billable', customer_name='Jane')
    assert response.status_code == 400
    await env.db.refresh(task)
    assert task.status != TaskStatus.done and task.completed_at is None


# ---------------------------------------------------------------------------
# Done-this-week figures
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_weekly_report_and_overview_count_unarchived_done_tasks(env):
    ws, project = env.ws, env.projects['move']
    week_start = (NOW - timedelta(days=NOW.weekday())).date()

    data = await report.build_report(env.db, project, ws, week_start, NOW)
    titles = [title for title, _, _ in data['finished']]
    assert 'Fresh done' in titles, 'an unarchived done task is finished work'
    assert 'Old done' not in titles

    overview = await build_overview(env.db, ws, NOW)
    # Fresh (2h), the legacy archived task (3h) and the other project's task
    # (1h) all finished in the last week; the one from ten days ago did not.
    assert overview['last_week']['tasks_done'] == 3


@pytest.mark.asyncio
async def test_dashboard_done_counts_include_unarchived_done_tasks(env):
    page = await web.web_dashboard(_request(env.people['ada']), view=None, user_id=None, db=env.db)
    assert page.context['stats']['my_tasks_done'] >= 1
    assert page.context['stats']['team_tasks_completed'] >= 1


# ---------------------------------------------------------------------------
# Explicit archive API
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_task_archive_is_scoped_and_authenticated(env):
    task = env.tasks['fresh']
    response = await env.client.post(f'/api/v1/tasks/{task.id}/archive')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['id'] == task.id and body['status'] == 'done'
    assert body['is_archived'] is True and body['archived_at'].endswith('+00:00')
    assert body['completed_at'].endswith('+00:00')
    await env.db.refresh(task)
    assert task.is_archived is True and task.archived_at is not None

    # Archiving twice is harmless and keeps the original archive time
    archived_at = task.archived_at
    again = await env.client.post(f'/api/v1/tasks/{task.id}/archive')
    assert again.status_code == 200
    await env.db.refresh(task)
    assert task.archived_at == archived_at

    foreign = env.tasks['foreign']
    response = await env.client.post(f'/api/v1/tasks/{foreign.id}/archive')
    assert response.status_code == 404
    await env.db.refresh(foreign)
    assert foreign.is_archived is False and foreign.archived_at is None

    assert (await env.client.post('/api/v1/tasks/999999/archive')).status_code == 404

    other = AsyncClient(transport=ASGITransport(app=env.app), base_url='http://test')
    try:
        assert (await other.post(f'/api/v1/tasks/{task.id}/archive')).status_code == 401
        bad = AsyncClient(transport=ASGITransport(app=env.app), base_url='http://test',
                          headers={'X-API-Key': 'cem_wrong'})
        assert (await bad.post(f'/api/v1/tasks/{task.id}/archive')).status_code == 401
        await bad.aclose()
    finally:
        await other.aclose()


@pytest.mark.asyncio
async def test_bulk_archive_done_filters_by_project_and_age(env):
    response = await env.client.post('/api/v1/tasks/archive-done', json={'older_than_days': 5})
    assert response.status_code == 200, response.text
    assert response.json() == {'archived_count': 1, 'task_ids': [env.tasks['old'].id]}

    response = await env.client.post('/api/v1/tasks/archive-done',
                                     json={'project_id': env.projects['yard'].id})
    assert response.status_code == 200, response.text
    assert response.json() == {'archived_count': 1, 'task_ids': [env.tasks['yard'].id]}

    # Another company's project is never in scope, and neither is active work
    response = await env.client.post('/api/v1/tasks/archive-done',
                                     json={'project_id': env.projects['foreign'].id})
    assert response.status_code == 200 and response.json()['archived_count'] == 0
    await env.db.refresh(env.tasks['foreign'])
    assert env.tasks['foreign'].is_archived is False
    await env.db.refresh(env.tasks['active'])
    assert env.tasks['active'].is_archived is False

    # An unfiltered run still picks up the recent unarchived done task.
    again = await env.client.post('/api/v1/tasks/archive-done', json={})
    assert again.status_code == 200
    assert again.json() == {'archived_count': 1, 'task_ids': [env.tasks['fresh'].id]}
    assert (await env.client.post('/api/v1/tasks/archive-done', json={})).json() == {
        'archived_count': 0, 'task_ids': []}

    other = AsyncClient(transport=ASGITransport(app=env.app), base_url='http://test')
    try:
        assert (await other.post('/api/v1/tasks/archive-done')).status_code == 401
    finally:
        await other.aclose()
    assert (await env.client.post('/api/v1/tasks/archive-done',
                                  json={'older_than_days': -1})).status_code == 422


def test_modified_templates_parse_cleanly():
    env = Environment(loader=FileSystemLoader('app/templates'))
    for name in ('projects/detail.html', 'projects/report.html', 'tasks/detail.html'):
        env.parse(Path('app/templates', name).read_text(encoding='utf-8'))
