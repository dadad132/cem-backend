"""Pop-ups: muted users only hear about their own tickets, new users only see
notifications from after they were created, and the super admin can clear them."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.models.notification import Notification, wants_ticket_notification
from app.models.ticket import Ticket
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import (
    web_admin_delete_user,
    web_notifications_count,
    web_notifications_unread,
    web_superadmin_clear_notifications,
    web_tickets_assign,
)


def test_muted_users_only_hear_about_their_own_tickets():
    muted, listening = SimpleNamespace(id=1, mute_ticket_notifications=True), SimpleNamespace(id=2, mute_ticket_notifications=False)
    assert wants_ticket_notification(listening, None) and wants_ticket_notification(listening, 9)
    assert not wants_ticket_notification(muted, None) and not wants_ticket_notification(muted, 9)
    assert wants_ticket_notification(muted, 1), 'their own ticket'


def test_new_users_record_when_they_were_made():
    before = datetime.utcnow()
    assert User(username='new', hashed_password='x').created_at >= before


async def _seed(path):
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme = Workspace(name='Acme')
    db.add(acme)
    await db.flush()
    p = dict(
        root=User(username='root', hashed_password='x', is_superadmin=True, is_admin=True),
        ada=User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id),
        old_hand=User(username='old_hand', hashed_password='x', workspace_id=acme.id),
        newbie=User(username='newbie', hashed_password='x', workspace_id=acme.id),
        quiet=User(username='quiet', hashed_password='x', workspace_id=acme.id, mute_ticket_notifications=True),
    )
    db.add_all(p.values())
    await db.flush()
    ticket = Ticket(ticket_number='TKT-1', subject='Printer', workspace_id=acme.id)
    db.add(ticket)
    await db.commit()
    # An account from before creation times were recorded: the column is empty
    from sqlalchemy import update
    await db.execute(update(User).where(User.id == p['old_hand'].id).values(created_at=None))
    await db.commit()
    p['old_hand'].created_at = None
    return engine, db, p, ticket


def _request(user):
    return SimpleNamespace(session={'user_id': user.id} if user else {}, state=SimpleNamespace())


async def _popups(db, user):
    response = await web_notifications_unread(_request(user), db=db)
    return [n['message'] for n in json.loads(response.body)]


@pytest.mark.asyncio
async def test_new_users_see_no_notifications_from_before_they_existed(tmp_path):
    engine, db, p, _ = await _seed(tmp_path / 'n.db')
    try:
        made = p['newbie'].created_at
        for who in ('newbie', 'old_hand'):
            db.add_all([
                # e.g. left behind by a deleted user whose id this account was given
                Notification(user_id=p[who].id, message='from before', created_at=made - timedelta(days=30)),
                Notification(user_id=p[who].id, message='after', created_at=made + timedelta(seconds=1)),
            ])
        await db.commit()

        assert await _popups(db, p['newbie']) == ['after']
        assert (await web_notifications_count(_request(p['newbie']), db=db)).body == b'1'
        # Accounts from before creation times were recorded keep seeing everything
        assert await _popups(db, p['old_hand']) == ['after', 'from before']
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_muted_assignee_still_hears_about_their_ticket(tmp_path):
    engine, db, p, ticket = await _seed(tmp_path / 'n.db')
    try:
        await web_tickets_assign(_request(p['ada']), ticket.id, assigned_to_id=str(p['quiet'].id), db=db)
        assert any('assigned you' in m for m in await _popups(db, p['quiet'])), 'muting does not hide your own tickets'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_email_replies_reach_muted_users_only_on_their_own_tickets(tmp_path):
    from app.core.email_to_ticket_v2 import add_comment_from_email_for_account

    engine, db, p, ticket = await _seed(tmp_path / 'n.db')
    try:
        await add_comment_from_email_for_account(db, ticket, 'Customer', 'c@client.test', 'Any news?')
        await db.commit()
        assert await _popups(db, p['quiet']) == [], 'someone else’s ticket'
        assert len(await _popups(db, p['newbie'])) == 1, 'not muted: told as before'

        ticket.assigned_to_id = p['quiet'].id
        await db.commit()
        await add_comment_from_email_for_account(db, ticket, 'Customer', 'c@client.test', 'Hello again')
        await db.commit()
        assert len(await _popups(db, p['quiet'])) == 1, 'their own ticket now'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_deleting_a_user_deletes_their_notifications(tmp_path):
    engine, db, p, _ = await _seed(tmp_path / 'n.db')
    try:
        db.add(Notification(user_id=p['newbie'].id, message='hi'))
        await db.commit()
        await web_admin_delete_user(_request(p['ada']), p['newbie'].id, db=db)
        left = (await db.execute(select(Notification).where(Notification.user_id == p['newbie'].id))).all()
        assert left == [], 'nothing left for a future account given the same id'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_super_admin_clears_all_notifications(tmp_path):
    engine, db, p, _ = await _seed(tmp_path / 'n.db')
    try:
        db.add_all([Notification(user_id=p[who].id, message='old') for who in ('ada', 'newbie', 'old_hand')])
        await db.commit()

        with pytest.raises(HTTPException) as refused:
            await web_superadmin_clear_notifications(_request(p['ada']), db=db)
        assert refused.value.status_code == 404
        assert len((await db.execute(select(Notification))).all()) == 3

        response = await web_superadmin_clear_notifications(_request(p['root']), db=db)
        assert response.headers['location'] == '/web/superadmin?success=notifications_cleared&count=3'
        assert (await db.execute(select(Notification))).all() == []
        assert await _popups(db, p['old_hand']) == []
    finally:
        await db.close()
        await engine.dispose()
