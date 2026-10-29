"""Ticket pop-ups are muted by default for everyone who cannot see all tickets."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.core.database import apply_data_changes
from app.models.notification import muted_by_default
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import (
    web_admin_create_user_post,
    web_admin_toggle_admin,
    web_admin_toggle_ticket_visibility,
    web_profile_post,
)


@pytest.mark.parametrize('is_admin, sees_all, muted', [
    (False, False, True), (False, True, False), (True, False, False), (True, True, False),
])
def test_muted_unless_they_see_every_ticket(is_admin, sees_all, muted):
    assert muted_by_default(SimpleNamespace(is_admin=is_admin, can_see_all_tickets=sees_all)) is muted


async def _seed(path):
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme = Workspace(name='Acme')
    db.add(acme)
    await db.flush()
    admin = User(username='ada', hashed_password='x', is_admin=True, can_see_all_tickets=True, workspace_id=acme.id)
    db.add(admin)
    await db.commit()
    return engine, db, admin


def _request(user):
    return SimpleNamespace(session={'user_id': user.id}, state=SimpleNamespace(),
                           url=SimpleNamespace(path='/web/admin/users/create'), query_params={})


async def _user(db, username):
    return (await db.execute(select(User).where(User.username == username))).scalar_one()


@pytest.mark.asyncio
async def test_new_users_start_muted_unless_they_see_every_ticket(tmp_path):
    engine, db, admin = await _seed(tmp_path / 'm.db')
    try:
        for username, access, is_admin in (('proj', 'projects', False), ('all', 'all', False), ('boss', None, True)):
            await web_admin_create_user_post(
                _request(admin), username=username, password='Secret1', full_name=username.title(),
                email=f'{username}@acme.test', display_name='', is_admin=is_admin, ticket_access=access,
                project_ids=[], db=db)
        assert (await _user(db, 'proj')).mute_ticket_notifications is True
        assert (await _user(db, 'all')).mute_ticket_notifications is False
        assert (await _user(db, 'boss')).mute_ticket_notifications is False
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_mute_follows_access_changes(tmp_path):
    engine, db, admin = await _seed(tmp_path / 'm.db')
    try:
        tech = User(username='tech', hashed_password='x', workspace_id=admin.workspace_id, mute_ticket_notifications=True)
        db.add(tech)
        await db.commit()

        await web_admin_toggle_ticket_visibility(_request(admin), tech.id, db=db)
        await db.refresh(tech)
        assert tech.can_see_all_tickets and not tech.mute_ticket_notifications, 'all tickets: pop-ups on'
        await web_admin_toggle_ticket_visibility(_request(admin), tech.id, db=db)
        await db.refresh(tech)
        assert not tech.can_see_all_tickets and tech.mute_ticket_notifications, 'projects only: muted again'
        await web_admin_toggle_admin(_request(admin), tech.id, db=db)
        await db.refresh(tech)
        assert tech.is_admin and not tech.mute_ticket_notifications
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_saving_a_profile_keeps_a_non_admin_muted(tmp_path):
    engine, db, admin = await _seed(tmp_path / 'm.db')
    try:
        tech = User(username='tech', hashed_password='x', workspace_id=admin.workspace_id, mute_ticket_notifications=True)
        db.add(tech)
        await db.commit()
        # Their profile page has no mute checkbox, so the form never sends it
        await web_profile_post(_request(tech), full_name='Tech', email='t@acme.test', preferred_meeting_platform=None,
                               calendar_color=None, mute_ticket_notifications=None, db=db)
        await db.refresh(tech)
        assert tech.mute_ticket_notifications is True

        # Admins have the checkbox and still choose
        await web_profile_post(_request(admin), full_name='Ada', email='a@acme.test', preferred_meeting_platform=None,
                               calendar_color=None, mute_ticket_notifications='true', db=db)
        await db.refresh(admin)
        assert admin.mute_ticket_notifications is True
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_users_are_muted_once(tmp_path):
    engine, db, admin = await _seed(tmp_path / 'm.db')
    try:
        db.add_all([User(username='restricted', hashed_password='x', workspace_id=admin.workspace_id),
                    User(username='sees_all', hashed_password='x', can_see_all_tickets=True, workspace_id=admin.workspace_id)])
        await db.commit()

        async with engine.begin() as conn:
            await apply_data_changes(conn)
        muted = {u.username: u.mute_ticket_notifications for u in (await db.execute(
            select(User).execution_options(populate_existing=True))).scalars()}
        assert muted == {'ada': False, 'restricted': True, 'sees_all': False}

        # Unmuted later (e.g. given more access): the change does not run again
        await db.execute(text('UPDATE "user" SET mute_ticket_notifications = 0 WHERE username = \'restricted\''))
        await db.commit()
        async with engine.begin() as conn:
            await apply_data_changes(conn)
            assert (await conn.exec_driver_sql('PRAGMA user_version')).scalar() >= 1
        assert (await db.execute(text('SELECT mute_ticket_notifications FROM "user" WHERE username = \'restricted\''))).scalar() == 0
    finally:
        await db.close()
        await engine.dispose()
