"""
Work timer for tickets and tasks.

Start/Resume opens a time entry and Pause ends it, as often as the user needs,
so time spent away from the work (lunch, waiting for a reply) is not counted.
Closing a ticket or completing a task stops its timer.

Each function takes the entry's owner column, e.g. ``TicketTimeEntry.ticket_id``
or ``TaskTimeEntry.task_id``, and the owner's id.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select


def worked_seconds(entries: Iterable, now: Optional[datetime] = None) -> int:
    """Seconds covered by the entries, counting a running entry up to now.

    Overlapping entries (Start pressed twice at the same moment) are merged so
    no stretch of time is counted twice.
    """
    now = now or datetime.utcnow()
    spans = sorted((e.started_at, e.ended_at or now) for e in entries)
    total = 0.0
    span_start = span_end = None
    for start, end in spans:
        if end <= start:
            continue
        if span_end is None or start > span_end:
            if span_end is not None:
                total += (span_end - span_start).total_seconds()
            span_start, span_end = start, end
        else:
            span_end = max(span_end, end)
    if span_end is not None:
        total += (span_end - span_start).total_seconds()
    return int(total)


def format_duration(seconds) -> str:
    """Compact duration for job cards and emails, e.g. "3h 05m"."""
    seconds = int(seconds or 0)
    if seconds < 60:
        return "<1m" if seconds > 0 else "0m"
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def format_clock(seconds) -> str:
    """Stopwatch display, e.g. "03:05:09"; the page ticks it on from here."""
    seconds = max(int(seconds or 0), 0)
    hours, rest = divmod(seconds, 3600)
    return f"{hours:02d}:{rest // 60:02d}:{rest % 60:02d}"


async def get_time_entries(db: AsyncSession, owner_col, owner_id: int) -> List:
    """All of an owner's time entries, oldest first."""
    model = owner_col.class_
    return list((await db.execute(
        select(model).where(owner_col == owner_id).order_by(model.started_at.asc())
    )).scalars().all())


async def start_timer(db: AsyncSession, owner_col, owner_id: int, user_id: Optional[int],
                      now: Optional[datetime] = None) -> bool:
    """Open a time entry unless one is already running. Returns True if started."""
    model = owner_col.class_
    running = (await db.execute(
        select(model.id).where(owner_col == owner_id, model.ended_at.is_(None))
    )).first()
    if running:
        return False
    db.add(model(**{owner_col.key: owner_id, 'user_id': user_id,
                    'started_at': now or datetime.utcnow()}))
    return True


async def stop_timer(db: AsyncSession, owner_col, owner_id: int,
                     now: Optional[datetime] = None) -> bool:
    """End the running time entry, if any. Returns True if one was running."""
    model = owner_col.class_
    running = (await db.execute(
        select(model).where(owner_col == owner_id, model.ended_at.is_(None))
    )).scalars().all()
    now = now or datetime.utcnow()
    for entry in running:
        entry.ended_at = now
    return bool(running)


async def timer_state(db: AsyncSession, owner_col, owner_id: int,
                      now: Optional[datetime] = None) -> dict:
    """Everything a page needs to show the timer."""
    now = now or datetime.utcnow()
    entries = await get_time_entries(db, owner_col, owner_id)
    running = next((e for e in entries if e.ended_at is None), None)
    return {
        'entries': entries,
        'rows': [{'entry': e, 'seconds': worked_seconds([e], now)} for e in entries],
        'running': running is not None,
        'running_since': running.started_at if running else None,
        'total_seconds': worked_seconds(entries, now),
    }
