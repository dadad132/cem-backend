"""Slice 1: dashboard admin assigned-projects toggle and per-user landing page.

Tests follow the repository's usual pattern: a private SQLite database plus a
fake request, with the route called directly. The two user-facing guarantees
are that an admin only widens *their own* company's project list, and that a
saved landing page is always one of our own internal routes.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.datastructures import QueryParams
from sqlmodel import SQLModel
from jinja2 import Environment, FileSystemLoader

from fastapi.templating import Jinja2Templates

from app.core.security import get_password_hash
from app.models.assignment import Assignment
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.task_history import TaskHistory
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import (
    LANDING_PAGE_OPTIONS,
    safe_landing_page,
    web_dashboard,
    web_login_post,
    web_profile_post,
)


DASHBOARD = '/web/dashboard'
BOARD = '/web/projects'
PASSWORD = 'Passw0rd!'


@pytest.fixture(autouse=True)
def _legacy_template_call(monkeypatch):
    """Bridge the repo's legacy TemplateResponse call to this venv's Starlette.

    app.web.routes patches TemplateResponse with the old
    ``(name, context)`` shape; Starlette 1.10 expects
    ``(request, name, context)``. Rendering through the class method keeps the
    real templates in the test without changing production code for this slice.
    """
    import app.web.routes as routes

    def render(name, context, *args, **kwargs):
        return Jinja2Templates.TemplateResponse(
            routes.templates, context.get('request'), name, context, *args, **kwargs
        )

    monkeypatch.setattr(routes, '_original_template_response', render)


def _request(user, path='/web/dashboard', **query):
    """Mimic the bits of a Request that the web routes and templates touch."""
    return SimpleNamespace(
        session={'user_id': user.id, 'workspace_id': user.workspace_id},
        state=SimpleNamespace(),
        headers={},
        url=SimpleNamespace(path=path, query=''),
        query_params=QueryParams(query),
    )


async def _seed(path):
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name='Acme'), Workspace(name='Other Co')
    db.add_all([acme, other])
    await db.flush()
    hashed = get_password_hash(PASSWORD)
    ada = User(username='ada', hashed_password=hashed, is_admin=True,
               workspace_id=acme.id, profile_completed=True)
    bob = User(username='bob', hashed_password=hashed,
               workspace_id=acme.id, profile_completed=True)
    olive = User(username='olive', hashed_password=hashed, is_admin=True,
                 workspace_id=other.id, profile_completed=True)
    db.add_all([ada, bob, olive])
    await db.flush()
    mine = Project(name='Mine', owner_id=ada.id, workspace_id=acme.id)
    team = Project(name='Team project', owner_id=ada.id, workspace_id=acme.id)
    archived = Project(name='Archived', owner_id=ada.id, workspace_id=acme.id,
                       is_archived=True)
    foreign = Project(name='Foreign', owner_id=olive.id, workspace_id=other.id)
    foreign_assigned = Project(name='Foreign assigned', owner_id=olive.id,
                               workspace_id=other.id)
    db.add_all([mine, team, archived, foreign, foreign_assigned])
    await db.flush()
    db.add_all([
        ProjectMember(project_id=mine.id, user_id=ada.id),
        ProjectMember(project_id=mine.id, user_id=bob.id),
        ProjectMember(project_id=foreign_assigned.id, user_id=bob.id),
    ])
    await db.commit()
    return engine, db, dict(ada=ada, bob=bob, olive=olive), dict(
        mine=mine, team=team, archived=archived, foreign=foreign,
        foreign_assigned=foreign_assigned,
    )


async def _dashboard(user, db, **query):
    page = await web_dashboard(_request(user, **query), view=None, user_id=None, db=db)
    return page, {p.name for p in page.context['projects']}, page.body.decode()


# ---------- Feature 1: admin "Only my assigned projects" toggle ----------

@pytest.mark.asyncio
async def test_admin_dashboard_toggle_switches_assigned_only_and_all(tmp_path):
    engine, db, people, projects = await _seed(tmp_path / 'toggle.db')
    try:
        all_page, all_names, all_html = await _dashboard(people['ada'], db, assigned_only='0')
        assert all_names == {'Mine', 'Team project'}
        assert all_page.context['stats']['active_projects'] == 2
        assert all_page.context['assigned_only'] is False
        assert 'Only my assigned projects' in all_html
        assert 'checked' not in all_html.split('id="assigned-only-toggle"')[1][:200]

        mine_page, mine_names, mine_html = await _dashboard(people['ada'], db, assigned_only='1')
        assert mine_names == {'Mine'}
        assert mine_page.context['assigned_only'] is True
        toggle = mine_html.split('id="assigned-only-toggle"')[1][:200]
        assert 'checked' in toggle
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_toggle_choice_survives_a_page_reload(tmp_path):
    engine, db, people, _ = await _seed(tmp_path / 'persist.db')
    try:
        await _dashboard(people['ada'], db, assigned_only='1')
        await db.refresh(people['ada'])
        assert people['ada'].dashboard_assigned_only is True

        _, names, _ = await _dashboard(people['ada'], db)
        assert names == {'Mine'}
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_non_admin_cannot_use_assigned_only_zero_to_widen_visibility(tmp_path):
    engine, db, people, _ = await _seed(tmp_path / 'rbac.db')
    try:
        page, names, html = await _dashboard(people['bob'], db, assigned_only='0')
        assert names == {'Mine'}, 'no unassigned or cross-company projects for a non-admin'
        assert page.context['assigned_only'] is False
        assert 'Only my assigned projects' not in html, 'the toggle is admin-only'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_stray_cross_tenant_rows_cannot_leak_dashboard_cards(tmp_path):
    """A membership/assignment row pointing at another company is not permission."""
    engine, db, people, projects = await _seed(tmp_path / 'cross_tenant.db')
    try:
        foreign_task = Task(title='Foreign secret', project_id=projects['foreign_assigned'].id,
                            creator_id=people['olive'].id,
                            due_date=date.today() + timedelta(days=1))
        db.add(foreign_task)
        await db.flush()
        db.add_all([
            Assignment(task_id=foreign_task.id, assignee_id=people['bob'].id),
            TaskHistory(task_id=foreign_task.id, editor_id=people['bob'].id, field='created'),
        ])
        await db.commit()

        page = await web_dashboard(
            _request(people['bob'], assigned_only='0'), view=None, user_id=None, db=db
        )
        assert page.context['stats']['my_tasks'] == 0
        assert page.context['tasks_due_soon'] == []
        assert page.context['recent_activities'] == []

        user_view = await web_dashboard(
            _request(people['ada']), view='user', user_id=people['bob'].id, db=db
        )
        assert 'Foreign assigned' not in {p.name for p in user_view.context['selected_user_projects']}
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_htmx_timer_refresh_keeps_the_dashboard_project_scope(tmp_path):
    """The self-refreshing timers panel must not widen scope after 30 seconds."""
    from app.models.task_extensions import TaskTimeEntry
    from app.web.routes import web_dashboard_timers

    engine, db, people, projects = await _seed(tmp_path / 'timers.db')
    try:
        now = datetime.utcnow()
        mine = Task(title='Mine task', project_id=projects['mine'].id,
                    creator_id=people['ada'].id)
        team = Task(title='Team task', project_id=projects['team'].id,
                    creator_id=people['ada'].id)
        db.add_all([mine, team])
        await db.flush()
        db.add_all([
            TaskTimeEntry(task_id=mine.id, user_id=people['ada'].id,
                          started_at=now - timedelta(hours=1), ended_at=now),
            TaskTimeEntry(task_id=team.id, user_id=people['ada'].id,
                          started_at=now - timedelta(hours=1), ended_at=now),
        ])
        await db.commit()

        await _dashboard(people['ada'], db, assigned_only='1')
        page = await web_dashboard_timers(
            _request(people['ada'], '/web/dashboard/timers'), db=db
        )
        timers = page.context['timers']
        refs = {item['ref'] for item in timers['running'] + timers['paused']}
        assert 'Mine' in refs
        assert 'Team project' not in refs
    finally:
        await db.close()
        await engine.dispose()


# ---------- Feature 2: default landing page ----------

def test_landing_page_allowlist_rejects_external_and_unknown_values():
    for value in ('https://evil.com', '//evil.com', 'javascript:alert(1)',
                  '/web/dashboard?next=https://evil.com', '/web/../admin', '', None):
        assert safe_landing_page(value) == DASHBOARD
    assert safe_landing_page(BOARD) == BOARD
    assert BOARD in dict(LANDING_PAGE_OPTIONS)


@pytest.mark.asyncio
async def test_profile_saves_landing_page_and_login_uses_it(tmp_path):
    engine, db, people, _ = await _seed(tmp_path / 'landing.db')
    try:
        user = people['ada']
        saved = await web_profile_post(
            _request(user, '/web/profile'),
            full_name='Ada', email='ada@example.com',
            preferred_meeting_platform=None, calendar_color=None,
            mute_ticket_notifications=None, default_landing_page=BOARD, db=db,
        )
        assert saved.status_code == 200
        await db.refresh(user)
        assert user.default_landing_page == BOARD

        response = await web_login_post(
            _request(user, '/web/login'), username='ada', password=PASSWORD, db=db,
        )
        assert response.status_code == 303
        assert response.headers['location'] == BOARD
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_profile_rejects_external_landing_page_and_falls_back(tmp_path):
    engine, db, people, _ = await _seed(tmp_path / 'evil.db')
    try:
        user = people['ada']
        await web_profile_post(
            _request(user, '/web/profile'),
            full_name='Ada', email='ada@example.com',
            preferred_meeting_platform=None, calendar_color=None,
            mute_ticket_notifications=None,
            default_landing_page='https://evil.com', db=db,
        )
        await db.refresh(user)
        assert user.default_landing_page == DASHBOARD

        response = await web_login_post(
            _request(user, '/web/login'), username='ada', password=PASSWORD, db=db,
        )
        assert response.headers['location'] == DASHBOARD
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_logged_in_root_redirects_to_saved_landing_page(tmp_path):
    engine, db, people, _ = await _seed(tmp_path / 'root.db')
    try:
        from app.main import index
        user = people['bob']
        user.default_landing_page = BOARD
        await db.commit()

        response = await index(_request(user, '/'), db=db)
        assert response.status_code == 303
        assert response.headers['location'] == BOARD
    finally:
        await db.close()
        await engine.dispose()


# ---------- Migration and template gates ----------

async def _columns(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute('PRAGMA table_info("user")')}
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_startup_migration_adds_columns_to_a_fresh_db(tmp_path, monkeypatch):
    import app.core.database as dbmod

    path = tmp_path / 'fresh.db'
    fresh_engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    monkeypatch.setattr(dbmod, 'engine', fresh_engine)
    try:
        await dbmod.init_models()
        cols = await _columns(path)
        assert 'default_landing_page' in cols
        assert 'dashboard_assigned_only' in cols
    finally:
        await fresh_engine.dispose()


@pytest.mark.asyncio
async def test_startup_migration_adds_columns_to_an_existing_db(tmp_path, monkeypatch):
    import app.core.database as dbmod

    path = tmp_path / 'legacy.db'
    conn = sqlite3.connect(str(path))
    conn.execute(
        'CREATE TABLE "user" ('
        'id INTEGER PRIMARY KEY, username VARCHAR NOT NULL, '
        'hashed_password VARCHAR NOT NULL, is_active BOOLEAN DEFAULT 1, '
        'is_admin BOOLEAN DEFAULT 0, workspace_id INTEGER, '
        'preferred_meeting_platform VARCHAR, email_verified BOOLEAN DEFAULT 0, '
        'verification_code VARCHAR, verification_expires_at TIMESTAMP, '
        'mute_ticket_notifications BOOLEAN DEFAULT 0, '
        'can_see_all_tickets BOOLEAN DEFAULT 0)'
    )
    conn.execute("INSERT INTO user (username, hashed_password) VALUES ('old', 'hash')")
    conn.commit()
    conn.close()

    legacy_engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    monkeypatch.setattr(dbmod, 'engine', legacy_engine)
    try:
        await dbmod.init_models()
        cols = await _columns(path)
        assert 'default_landing_page' in cols
        assert 'dashboard_assigned_only' in cols
        # The row and its data survive the upgrade; defaults are safe.
        check = sqlite3.connect(str(path))
        try:
            row = check.execute(
                'SELECT username, default_landing_page, dashboard_assigned_only '
                'FROM "user" WHERE username = ?', ('old',),
            ).fetchone()
        finally:
            check.close()
        assert row[0] == 'old'
        assert row[1] in (None, DASHBOARD)
        assert row[2] in (0, None)
    finally:
        await legacy_engine.dispose()


def test_startup_preference_helper_is_idempotent(tmp_path, monkeypatch):
    import app.core.database as dbmod

    conn = sqlite3.connect(str(tmp_path / 'data.db'))
    conn.execute('CREATE TABLE "user" (id INTEGER PRIMARY KEY, username VARCHAR)')
    conn.commit()
    conn.close()
    monkeypatch.chdir(tmp_path)
    dbmod.ensure_user_preference_columns()
    dbmod.ensure_user_preference_columns()
    cols = sqlite3.connect(str(tmp_path / 'data.db')).execute(
        'PRAGMA table_info("user")'
    ).fetchall()
    names = {row[1] for row in cols}
    assert 'dashboard_assigned_only' in names
    assert 'default_landing_page' in names
    assert sum(row[1] == 'default_landing_page' for row in cols) == 1


def test_modified_templates_parse_cleanly():
    env = Environment(loader=FileSystemLoader('app/templates'))
    for name in ('base.html', 'dashboard/index.html', 'auth/profile.html'):
        env.parse(Path('app/templates', name).read_text(encoding='utf-8'))
