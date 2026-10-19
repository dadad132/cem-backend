"""Admins edit a user from Team Members, and a display name replaces the full name on screen."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.core.security import get_password_hash, verify_password
from app.models.user import USER_SHOWN_NAME, User
from app.models.workspace import Workspace
from app.web.routes import templates, web_admin_edit_user


async def _seed(path):
    """Two companies: Acme (an admin and a technician) and Other Co (its own admin)."""
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    # Same session settings as the app's session factory
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name='Acme'), Workspace(name='Other Co')
    db.add_all([acme, other])
    await db.flush()
    admin = User(username='ada', hashed_password=get_password_hash('Admin1'), full_name='Ada Admin',
                 email='ada@acme.test', is_admin=True, workspace_id=acme.id)
    tech = User(username='jsmith', hashed_password=get_password_hash('Old123'), full_name='Johnathan Smith',
                email='john@acme.test', workspace_id=acme.id)
    outsider = User(username='olive', hashed_password='x', full_name='Olive Other',
                    email='olive@other.test', is_admin=True, workspace_id=other.id)
    db.add_all([admin, tech, outsider])
    await db.commit()
    return engine, db, admin, tech, outsider


async def _edit(db, actor, target, **changes):
    """Submit the Edit User dialog as `actor`, starting from `target`'s current values."""
    form = {
        'username': target.username,
        'display_name': target.display_name or '',
        'full_name': target.full_name or '',
        'email': target.email or '',
        'new_password': '',
        **changes,
    }
    request = SimpleNamespace(session={'user_id': actor.id})
    response = await web_admin_edit_user(request, target.id, db=db, **form)
    return response.status_code, json.loads(response.body), request.session


async def _stored(engine, user_id) -> User:
    """The user as saved in the database, read in a fresh session."""
    async with AsyncSession(engine) as fresh:
        return (await fresh.execute(select(User).where(User.id == user_id))).scalar_one()


def test_shown_name_prefers_display_name():
    user = User(username='jsmith', hashed_password='x', full_name='Johnathan Smith')
    assert user.shown_name == 'Johnathan Smith'
    user.display_name = 'John'
    assert user.shown_name == 'John'
    user.display_name, user.full_name = None, None
    assert user.shown_name is None, 'callers add their own fallback (username, email)'


@pytest.mark.asyncio
async def test_shown_name_in_sql_matches_the_property(tmp_path):
    engine, db, admin, tech, outsider = await _seed(tmp_path / 'users.db')
    try:
        tech.display_name = 'John'
        outsider.display_name = ''  # never stored by the app, but must not blank the name
        await db.commit()
        rows = dict((await db.execute(select(User.id, USER_SHOWN_NAME))).all())
        assert rows == {admin.id: 'Ada Admin', tech.id: 'John', outsider.id: 'Olive Other'}
        assert rows == {u.id: u.shown_name for u in (admin, tech, outsider)}
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_edits_every_field(tmp_path):
    engine, db, admin, tech, _ = await _seed(tmp_path / 'users.db')
    try:
        status, body, session = await _edit(
            db, admin, tech,
            username='  john.smith ', display_name=' John ', full_name='John Smith',
            email='john.smith@acme.test', new_password='Fresh1',
        )
        assert (status, body) == (200, {'ok': True})
        saved = await _stored(engine, tech.id)
        assert saved.username == 'john.smith'
        assert saved.display_name == 'John'
        assert saved.full_name == 'John Smith'
        assert saved.email == 'john.smith@acme.test'
        assert verify_password('Fresh1', saved.hashed_password)
        assert session['users_notice'] == 'Saved changes to John, including a new password.'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_blank_fields_clear_or_keep(tmp_path):
    engine, db, admin, tech, _ = await _seed(tmp_path / 'users.db')
    try:
        await _edit(db, admin, tech, display_name='John')
        status, _, session = await _edit(db, admin, tech, display_name='   ', email='', new_password='')
        assert status == 200
        saved = await _stored(engine, tech.id)
        assert saved.display_name is None, 'blank display name shows the full name again'
        assert saved.email is None
        assert verify_password('Old123', saved.hashed_password), 'blank password keeps the old one'
        assert session['users_notice'] == 'Saved changes to Johnathan Smith.'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes, error', [
    ({'username': 'ada'}, 'Username already taken'),
    ({'username': 'olive'}, 'Username already taken'),  # usernames are unique across companies
    ({'username': '  '}, 'Username is required'),
    ({'email': 'ada@acme.test'}, 'Email already in use'),
    ({'new_password': 'short'}, 'Password must be at least 6 characters long'),
    ({'new_password': 'nouppercase'}, 'Password must contain at least one uppercase letter'),
])
async def test_rejected_edit_changes_nothing(tmp_path, changes, error):
    engine, db, admin, tech, _ = await _seed(tmp_path / 'users.db')
    try:
        status, body, session = await _edit(db, admin, tech, display_name='John', **changes)
        assert (status, body) == (400, {'error': error})
        saved = await _stored(engine, tech.id)
        assert (saved.username, saved.display_name, saved.email) == ('jsmith', None, 'john@acme.test')
        assert verify_password('Old123', saved.hashed_password)
        assert 'users_notice' not in session
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_unchanged_email_shared_with_someone_else_is_not_blocking(tmp_path):
    """Old data can hold duplicate emails; that must not block editing other fields."""
    engine, db, admin, tech, _ = await _seed(tmp_path / 'users.db')
    try:
        tech.email = admin.email
        await db.commit()
        status, _, _ = await _edit(db, admin, tech, display_name='John')
        assert status == 200
        assert (await _stored(engine, tech.id)).display_name == 'John'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_edit_themselves(tmp_path):
    engine, db, admin, _, _ = await _seed(tmp_path / 'users.db')
    try:
        status, _, _ = await _edit(db, admin, admin, display_name='Ada')
        assert status == 200
        assert (await _stored(engine, admin.id)).display_name == 'Ada'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_only_admins_of_the_same_company_can_edit(tmp_path):
    engine, db, admin, tech, outsider = await _seed(tmp_path / 'users.db')
    try:
        with pytest.raises(HTTPException) as not_admin:
            await _edit(db, tech, admin, display_name='Boss')
        assert not_admin.value.status_code == 403

        with pytest.raises(HTTPException) as other_company:
            await _edit(db, outsider, tech, display_name='Hacked')
        assert other_company.value.status_code == 403

        operator = User(username='operator', hashed_password='x', is_superadmin=True,
                        workspace_id=admin.workspace_id)
        db.add(operator)
        await db.commit()
        with pytest.raises(HTTPException) as superadmin:
            await _edit(db, admin, operator, display_name='Op')
        assert superadmin.value.status_code == 404

        assert (await _stored(engine, admin.id)).display_name is None
        assert (await _stored(engine, tech.id)).display_name is None
    finally:
        await db.close()
        await engine.dispose()


def _render_team_members(viewer, users):
    request = SimpleNamespace(url=SimpleNamespace(path='/web/admin/users'), session={'user_id': viewer.id})
    return templates.env.get_template('admin/users_list.html').render(
        request=request, user=viewer, users=users, notice=None, workspace=None,
    )


def test_team_members_page_shows_display_name_and_edit_dialog():
    admin = User(id=1, username='ada', hashed_password='x', full_name='Ada Admin', is_admin=True)
    tech = User(id=2, username="o'brien", hashed_password='x', full_name="Johnathan O'Brien",
                display_name='John', email='john@acme.test')
    name_cell = re.compile(r'font-medium text-gray-900">\s*John\s*(<span[^>]*>\(Johnathan O&#39;Brien\)</span>\s*)?</div>')

    html = _render_team_members(admin, [admin, tech])
    assert 'id="editUserModal"' in html
    assert 'openEditUserModal($el)' in html, 'Edit User in the Actions menu'
    assert 'onclick="openEditUserModal(this)"' in html, 'Edit button on your own row'
    assert 'data-display-name="John"' in html
    assert 'data-full-name="Johnathan O&#39;Brien"' in html
    assert name_cell.search(html).group(1), 'admins see whose display name it is'
    # A quote in a username must not break out of the confirm() prompts
    assert "confirm('PERMANENTLY DELETE o'brien" not in html
    assert 'confirm("PERMANENTLY DELETE o\\u0027brien? This cannot be undone!")' in html

    team_view = _render_team_members(tech, [admin, tech])
    assert 'id="editUserModal"' not in team_view
    assert name_cell.search(team_view).group(1) is None
    assert "O&#39;Brien" not in team_view, 'only admins see the full name behind a display name'
