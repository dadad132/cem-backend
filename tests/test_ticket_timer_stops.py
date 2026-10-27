"""Resolving, closing or archiving a ticket stops its work timer, and timers
that were left running on finished tickets are ended when the ticket was."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.core.work_timer import stop_timers_left_running
from app.models.ticket import Ticket, TicketHistory, TicketTimeEntry
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import web_tickets_archive, web_tickets_timer, web_tickets_update_status

T0 = datetime(2026, 10, 20, 8, 0, 0)


async def _seed(path):
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme = Workspace(name='Acme')
    db.add(acme)
    await db.flush()
    admin = User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id)
    db.add(admin)
    await db.flush()
    ticket = Ticket(ticket_number='TKT-1', subject='Printer', workspace_id=acme.id, assigned_to_id=admin.id)
    db.add(ticket)
    await db.flush()
    db.add(TicketTimeEntry(ticket_id=ticket.id, user_id=admin.id, started_at=datetime.utcnow() - timedelta(hours=1)))
    await db.commit()
    return engine, db, admin, ticket


def _request(user, ajax=False):
    return SimpleNamespace(session={'user_id': user.id}, state=SimpleNamespace(),
                           headers={'X-Requested-With': 'XMLHttpRequest'} if ajax else {})


async def _running(db, ticket):
    return (await db.execute(select(TicketTimeEntry).where(
        TicketTimeEntry.ticket_id == ticket.id, TicketTimeEntry.ended_at.is_(None)))).scalars().all()


@pytest.mark.asyncio
async def test_a_resolved_tickets_timer_stays_stopped(tmp_path):
    engine, db, admin, ticket = await _seed(tmp_path / 't.db')
    try:
        # Resolved before resolving went through "Mark as Resolved & Close Ticket"
        ticket.status = 'resolved'
        await db.commit()
        await stop_timers_left_running(db)
        assert await _running(db, ticket) == []

        card = await web_tickets_timer(_request(admin, ajax=True), ticket.id, 'start', db=db)
        assert await _running(db, ticket) == [], 'Start does nothing on a resolved ticket'
        assert b'Stopped &mdash; ticket resolved' in card.body
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_status_dropdown_still_cannot_resolve(tmp_path):
    """Resolving goes through the close drawer, which stops the timer itself."""
    engine, db, admin, ticket = await _seed(tmp_path / 't.db')
    try:
        await web_tickets_update_status(_request(admin), ticket.id, status='resolved', db=db)
        await db.refresh(ticket)
        assert ticket.status == 'open'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_archiving_stops_the_timer(tmp_path):
    engine, db, admin, ticket = await _seed(tmp_path / 't.db')
    try:
        await web_tickets_archive(_request(admin), ticket.id, db=db)
        assert await _running(db, ticket) == []
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_timers_left_running_are_ended_when_the_ticket_was_finished(tmp_path):
    import app.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    try:
        ws = Workspace(name='Acme')
        db.add(ws)
        await db.flush()

        def at(hours):
            return T0 + timedelta(hours=hours)

        tickets = dict(
            # resolved at 10:00 (history), closed later: ends at the resolve
            resolved_then_closed=Ticket(ticket_number='A', subject='a', workspace_id=ws.id, status='closed',
                                        resolved_at=at(2), closed_at=at(30)),
            # no history row: falls back to the resolved time
            no_history=Ticket(ticket_number='B', subject='b', workspace_id=ws.id, status='resolved', resolved_at=at(3)),
            # started after it was resolved (and never reopened): counts nothing
            started_after=Ticket(ticket_number='C', subject='c', workspace_id=ws.id, status='resolved', resolved_at=at(1)),
            # still open: untouched
            still_open=Ticket(ticket_number='D', subject='d', workspace_id=ws.id, status='open'),
        )
        db.add_all(tickets.values())
        await db.flush()
        db.add_all([
            TicketHistory(ticket_id=tickets['resolved_then_closed'].id, action='status_changed', new_value='resolved', created_at=at(2)),
            TicketHistory(ticket_id=tickets['resolved_then_closed'].id, action='status_changed', new_value='closed', created_at=at(30)),
            TicketTimeEntry(ticket_id=tickets['resolved_then_closed'].id, started_at=at(0)),
            TicketTimeEntry(ticket_id=tickets['no_history'].id, started_at=at(1)),
            TicketTimeEntry(ticket_id=tickets['started_after'].id, started_at=at(5)),
            TicketTimeEntry(ticket_id=tickets['still_open'].id, started_at=at(0)),
        ])
        await db.commit()

        assert await stop_timers_left_running(db) == 3
        ended = {t: (await db.execute(select(TicketTimeEntry.ended_at).where(
            TicketTimeEntry.ticket_id == tickets[t].id))).scalar_one() for t in tickets}
        assert ended == {
            'resolved_then_closed': at(2),
            'no_history': at(3),
            'started_after': at(5),
            'still_open': None,
        }
        assert await stop_timers_left_running(db) == 0, 'safe to run on every start'
    finally:
        await db.close()
        await engine.dispose()
