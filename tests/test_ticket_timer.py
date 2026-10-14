"""Ticket work timer: only time between Start/Resume and Pause is counted."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.core.ticket_timer import (
    format_clock,
    format_duration,
    start_timer,
    stop_timer,
    timer_state,
    worked_seconds,
)
from app.models.ticket import Ticket, TicketTimeEntry
from app.models.user import User
from app.models.workspace import Workspace

T0 = datetime(2026, 10, 14, 8, 0, 0)


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _entry(start_min: int, end_min=None) -> TicketTimeEntry:
    return TicketTimeEntry(
        ticket_id=1,
        started_at=_at(start_min),
        ended_at=None if end_min is None else _at(end_min),
    )


def test_pauses_are_not_counted():
    # 08:00-10:00, lunch, then 11:00-12:30
    assert worked_seconds([_entry(0, 120), _entry(180, 270)]) == (120 + 90) * 60


def test_running_entry_counts_up_to_now():
    assert worked_seconds([_entry(0)], now=_at(45)) == 45 * 60


def test_overlapping_entries_are_counted_once():
    # e.g. Start pressed twice at about the same moment
    assert worked_seconds([_entry(0, 60), _entry(0, 60), _entry(30, 90)]) == 90 * 60


def test_no_entries_is_zero():
    assert worked_seconds([]) == 0


@pytest.mark.parametrize('seconds, expected', [
    (0, '0m'),
    (30, '<1m'),
    (60, '1m'),
    (59 * 60, '59m'),
    (3600, '1h 00m'),
    (3 * 3600 + 5 * 60 + 9, '3h 05m'),
])
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_format_clock():
    assert format_clock(0) == '00:00:00'
    assert format_clock(3 * 3600 + 5 * 60 + 9) == '03:05:09'
    assert format_clock(100 * 3600) == '100:00:00'


async def _seed(path):
    """A database with one workspace, technician and ticket."""
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    # Same session settings as the app's session factory
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    ws = Workspace(name='Acme')
    db.add(ws)
    await db.flush()
    tech = User(username='tech', hashed_password='x', workspace_id=ws.id)
    db.add(tech)
    await db.flush()
    ticket = Ticket(ticket_number='TKT-TEST-00001', subject='Printer offline', workspace_id=ws.id)
    db.add(ticket)
    await db.commit()
    return engine, db, ticket, tech


@pytest.mark.asyncio
async def test_start_pause_resume(tmp_path):
    engine, db, ticket, tech = await _seed(tmp_path / 'timer.db')
    try:
        state = await timer_state(db, ticket.id)
        assert not state['running'] and state['total_seconds'] == 0

        # Each step is its own request, so commit in between
        assert await start_timer(db, ticket.id, tech.id, now=_at(0))
        await db.commit()
        assert not await start_timer(db, ticket.id, tech.id, now=_at(5)), 'already running'
        await db.commit()
        assert await stop_timer(db, ticket.id, now=_at(120))  # off to lunch
        await db.commit()
        assert not await stop_timer(db, ticket.id, now=_at(150)), 'already paused'
        await db.commit()

        state = await timer_state(db, ticket.id, now=_at(170))
        assert not state['running']
        assert state['total_seconds'] == 120 * 60, 'lunch is not counted'

        assert await start_timer(db, ticket.id, tech.id, now=_at(180))  # resume
        await db.commit()
        state = await timer_state(db, ticket.id, now=_at(210))
        assert state['running']
        assert state['running_since'] == _at(180)
        assert state['total_seconds'] == (120 + 30) * 60
        assert [row['seconds'] for row in state['rows']] == [120 * 60, 30 * 60]
        assert all(e.user_id == tech.id for e in state['entries'])
    finally:
        await db.close()
        await engine.dispose()
