"""The dashboard shows which work timers are running and which are not."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.models.assignment import Assignment
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.task_extensions import TaskTimeEntry
from app.models.ticket import Ticket, TicketTimeEntry
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import _dashboard_timers, web_dashboard_timers

# The panel endpoint uses the real clock, so the seeded times are relative to it
NOW = datetime.utcnow().replace(microsecond=0)


def _ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


async def _seed(path):
    """Acme: admin Ada; technicians Bob, Cara, Dan. Other Co has its own running timer."""
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name='Acme'), Workspace(name='Other Co')
    db.add_all([acme, other])
    await db.flush()
    p = dict(
        ada=User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id),
        bob=User(username='bob', hashed_password='x', full_name='Bob Builder', workspace_id=acme.id),
        cara=User(username='cara', hashed_password='x', display_name='Cara', workspace_id=acme.id),
        dan=User(username='dan', hashed_password='x', workspace_id=acme.id),
        olive=User(username='olive', hashed_password='x', workspace_id=other.id),
    )
    db.add_all(p.values())
    await db.flush()

    def ticket(number, ws, assignee, status='open'):
        return Ticket(ticket_number=number, subject=f'Subject {number}', workspace_id=ws.id,
                      assigned_to_id=assignee.id if assignee else None, status=status)

    t = dict(
        running=ticket('T1', acme, p['bob']),
        started_by_admin=ticket('T2', acme, p['cara']),
        paused=ticket('T3', acme, p['bob']),
        closed=ticket('T4', acme, p['bob'], status='closed'),
        untimed=ticket('T5', acme, p['dan']),
        elsewhere=ticket('T6', other, p['olive']),
    )
    project = Project(name='Office move', workspace_id=acme.id, owner_id=p['ada'].id)
    db.add_all([*t.values(), project])
    await db.flush()
    forgotten = Task(title='Patch switch', project_id=project.id, creator_id=p['ada'].id)
    finished = Task(title='Old job', project_id=project.id, creator_id=p['ada'].id, status=TaskStatus.done)
    db.add_all([forgotten, finished])
    await db.flush()
    db.add_all([Assignment(task_id=forgotten.id, assignee_id=p['cara'].id),
                Assignment(task_id=finished.id, assignee_id=p['dan'].id)])

    def entry(model, owner, start_h, end_h, who):
        key = 'ticket_id' if model is TicketTimeEntry else 'task_id'
        return model(**{key: owner.id}, user_id=who.id, started_at=_ago(start_h),
                     ended_at=None if end_h is None else _ago(end_h))

    db.add_all([
        entry(TicketTimeEntry, t['running'], 5, 4, p['bob']),          # 1h earlier today
        entry(TicketTimeEntry, t['running'], 2, None, p['bob']),       # running for 2h
        entry(TicketTimeEntry, t['started_by_admin'], 0.5, None, p['ada']),
        entry(TicketTimeEntry, t['paused'], 3, 2.5, p['bob']),
        entry(TicketTimeEntry, t['closed'], 6, 5, p['bob']),
        entry(TicketTimeEntry, t['elsewhere'], 1, None, p['olive']),
        entry(TaskTimeEntry, forgotten, 9, None, p['cara']),           # left running overnight
        entry(TaskTimeEntry, finished, 8, 7, p['dan']),
    ])
    await db.commit()
    return engine, db, p


@pytest.mark.asyncio
async def test_admin_sees_the_whole_team(tmp_path):
    engine, db, p = await _seed(tmp_path / 'd.db')
    try:
        timers = await _dashboard_timers(db, p['ada'], now=NOW)
        assert timers['team']
        running = [(r['ref'], r['title']) for r in timers['running']]
        assert running == [('Office move', 'Patch switch'), ('#T1', 'Subject T1'), ('#T2', 'Subject T2')], \
            'longest running first; closed work and other companies left out'
        by_title = {r['title']: r for r in timers['running']}
        assert by_title['Subject T1']['total_seconds'] == 3 * 3600, 'earlier stretch + running stretch'
        assert by_title['Subject T1']['assigned_names'] == ['Bob Builder']
        assert by_title['Subject T2']['assigned_names'] == ['Cara'] and by_title['Subject T2']['started_by'] == 'ada'
        assert by_title['Patch switch']['forgotten'] and not by_title['Subject T1']['forgotten']

        assert [r['ref'] for r in timers['paused']] == ['#T3'], 'open work with time on it, not running'
        assert timers['paused'][0]['since'] == _ago(2.5) and timers['paused'][0]['total_seconds'] == 1800

        idle = [(i['user'].username, i['open_items']) for i in timers['not_running']]
        assert idle == [('dan', 1)], 'Bob and Cara have timers running; Dan has open work and none'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_others_see_only_their_own_work(tmp_path):
    engine, db, p = await _seed(tmp_path / 'd.db')
    try:
        bob = await _dashboard_timers(db, p['bob'], now=NOW)
        assert not bob['team'] and not bob['not_running']
        assert [r['ref'] for r in bob['running']] == ['#T1'] and [r['ref'] for r in bob['paused']] == ['#T3']

        cara = await _dashboard_timers(db, p['cara'], now=NOW)
        assert sorted(r['title'] for r in cara['running']) == ['Patch switch', 'Subject T2']

        dan = await _dashboard_timers(db, p['dan'], now=NOW)
        assert dan['running'] == [] and dan['paused'] == []
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_panel_refreshes_on_its_own(tmp_path):
    engine, db, p = await _seed(tmp_path / 'd.db')
    try:
        request = SimpleNamespace(session={'user_id': p['ada'].id}, state=SimpleNamespace())
        html = (await web_dashboard_timers(request, db=db)).body.decode()
        assert 'id="dashboardTimers"' in html and 'hx-get="/web/dashboard/timers"' in html
        assert 'Team Work Timers' in html and '3 running' in html and '1 paused' in html
        assert 'forgotten?' in html and 'started by ada' in html and 'dan' in html
        assert 'Subject T6' not in html, 'never another company'

        signed_out = await web_dashboard_timers(SimpleNamespace(session={}, state=SimpleNamespace()), db=db)
        assert signed_out.headers['HX-Redirect'] == '/web/login'
    finally:
        await db.close()
        await engine.dispose()
