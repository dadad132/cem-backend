"""The Team Overview shows admins what needs someone, and the numbers across the
admin pages agree with it: finished means resolved or closed, archived is not open."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.core.clock import local_now, local_today
from app.core.database import apply_data_changes
from app.core.team_overview import age_text, average_first_reply, build_overview, hours_worked, project_counts
from app.models.assignment import Assignment
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.task_extensions import TaskTimeEntry, TimeLog
from app.models.ticket import Ticket, TicketComment, TicketHistory, TicketTimeEntry
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import (
    web_admin_overview,
    web_admin_user_activity_view,
    web_dashboard,
    web_project_report,
    web_projects,
    web_tasks_list,
    web_tickets_list,
    web_tickets_report,
    web_workload,
)

NOW = datetime.utcnow().replace(microsecond=0)
TODAY = local_today()


def _ago(hours: float = 0, days: float = 0) -> datetime:
    return NOW - timedelta(hours=hours, days=days)


async def _seed(path):
    """Acme: admin Ada, technicians Bob and Cara, Dan who has left. Other Co has its own work."""
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name='Acme'), Workspace(name='Other Co')
    db.add_all([acme, other])
    await db.flush()
    u = dict(ada=User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id),
             bob=User(username='bob', hashed_password='x', full_name='Bob Builder', workspace_id=acme.id),
             cara=User(username='cara', hashed_password='x', workspace_id=acme.id),
             dan=User(username='dan', hashed_password='x', is_active=False, workspace_id=acme.id),
             olive=User(username='olive', hashed_password='x', is_admin=True, workspace_id=other.id))
    db.add_all(u.values())
    await db.flush()

    def ticket(number, ws=acme, **fields):
        fields.setdefault('assigned_to_id', u['bob'].id)
        fields.setdefault('created_at', _ago(days=1))
        fields.setdefault('updated_at', _ago(hours=1))
        return Ticket(ticket_number=number, subject=f'Subject {number}', workspace_id=ws.id, **fields)

    t = dict(
        unanswered=ticket('T1', assigned_to_id=None, created_at=_ago(days=2)),
        note_only=ticket('T2'),
        customer_only=ticket('T3'),
        replied=ticket('T4'),
        late=ticket('T5', status='in_progress', due_date=local_now() - timedelta(days=2)),
        quiet=ticket('T6', status='waiting', updated_at=_ago(days=5)),
        archived=ticket('T7', is_archived=True, assigned_to_id=None),
        resolved=ticket('T8', status='resolved', resolved_at=_ago(hours=2)),
        closed=ticket('T9', status='closed', closed_at=_ago(days=3), created_at=_ago(days=20)),
        left=ticket('T10', status='in_progress', assigned_to_id=u['dan'].id),
        elsewhere=ticket('T11', ws=other, assigned_to_id=None),
    )
    move = Project(name='Office move', workspace_id=acme.id, owner_id=u['ada'].id, due_date=TODAY - timedelta(days=1))
    calm = Project(name='New site', workspace_id=acme.id, owner_id=u['ada'].id)
    foreign = Project(name='Their project', workspace_id=other.id, owner_id=u['olive'].id)
    db.add_all([*t.values(), move, calm, foreign])
    await db.flush()
    db.add_all([
        TicketComment(ticket_id=t['note_only'].id, user_id=u['bob'].id, content='note', is_internal=True),
        TicketComment(ticket_id=t['customer_only'].id, user_id=None, content='Any news?'),
        TicketComment(ticket_id=t['replied'].id, user_id=u['bob'].id, content='On it',
                      created_at=t['replied'].created_at + timedelta(hours=1)),
    ])
    k = dict(
        overdue=Task(title='Patch switch', project_id=move.id, creator_id=u['ada'].id, due_date=TODAY - timedelta(days=3)),
        blocked=Task(title='Order cables', project_id=move.id, creator_id=u['ada'].id, status=TaskStatus.blocked),
        nobody=Task(title='Label desks', project_id=calm.id, creator_id=u['ada'].id),
        due_today=Task(title='Book van', project_id=calm.id, creator_id=u['ada'].id, due_date=TODAY),
        finished=Task(title='Pack boxes', project_id=move.id, creator_id=u['ada'].id, status=TaskStatus.done,
                      is_archived=True, archived_at=_ago(hours=3), updated_at=_ago(hours=3)),
        shelved=Task(title='Shelved', project_id=calm.id, creator_id=u['ada'].id, is_archived=True,
                     due_date=TODAY - timedelta(days=9)),
        theirs=Task(title='Their task', project_id=foreign.id, creator_id=u['olive'].id,
                    status=TaskStatus.done, is_archived=True, archived_at=_ago(hours=1)),
    )
    db.add_all(k.values())
    await db.flush()
    db.add_all([
        Assignment(task_id=k['overdue'].id, assignee_id=u['bob'].id),
        Assignment(task_id=k['blocked'].id, assignee_id=u['cara'].id),
        Assignment(task_id=k['due_today'].id, assignee_id=u['dan'].id),
        Assignment(task_id=k['finished'].id, assignee_id=u['bob'].id),
        TicketTimeEntry(ticket_id=t['late'].id, user_id=u['bob'].id, started_at=_ago(hours=0.5)),
        TaskTimeEntry(task_id=k['blocked'].id, user_id=u['cara'].id, started_at=_ago(hours=3), ended_at=_ago(hours=2)),
        TaskTimeEntry(task_id=k['overdue'].id, user_id=u['bob'].id, started_at=_ago(days=30), ended_at=_ago(days=30, hours=-2)),
        TimeLog(task_id=k['overdue'].id, user_id=u['cara'].id, hours=0.5, logged_at=_ago(hours=1)),
    ])
    await db.commit()
    return engine, db, acme, other, u, t, k, dict(move=move, calm=calm)


def _request(user, path='/web/admin/overview', **query):
    return SimpleNamespace(session={'user_id': user.id}, state=SimpleNamespace(), headers={},
                           url=SimpleNamespace(path=path, query=''), query_params=query)


def _keys(section):
    return [item['ref'] for item in section['items']]


def test_ages_read_plainly():
    assert [age_text(s) for s in (30, 300, 7200, 200000)] == ['0m', '5m', '2h', '2d']


@pytest.mark.asyncio
async def test_what_needs_someone(tmp_path):
    engine, db, acme, _, u, t, k, _ = await _seed(tmp_path / 'o.db')
    try:
        o = await build_overview(db, acme, NOW)
        assert o['open_tickets'] == 7, 'not archived, finished, or another company'
        assert o['tickets'] == dict(no_reply=3, unassigned=1, overdue=1, quiet=1)
        assert o['tasks'] == dict(overdue=1, blocked=1, unassigned=1)
        assert o['needs_someone'] == 3 + 1 + 1 + 1 + 1

        lists = {section['key']: section for section in o['ticket_lists']}
        assert _keys(lists['no_reply'])[0] == '#T1', 'oldest first'
        assert set(_keys(lists['no_reply'])) == {'#T1', '#T2', '#T3'}, 'a staff reply answers it (T4); a private note (T2) or a customer message (T3) does not'
        assert lists['overdue']['items'][0]['when'] == '2d late'
        assert 'last touched 5d ago' in lists['quiet']['items'][0]['when']
        assert lists['unassigned']['url'] == '/web/tickets?attention=unassigned'
        tasks = {section['key']: section for section in o['task_lists']}
        assert tasks['overdue']['items'][0]['title'] == 'Patch switch' and tasks['overdue']['items'][0]['when'] == '3 days late'
        assert tasks['unassigned']['items'][0]['title'] == 'Label desks'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_who_is_carrying_what(tmp_path):
    engine, db, acme, _, u, *_ = await _seed(tmp_path / 'o.db')
    try:
        o = await build_overview(db, acme, NOW)
        people = {p['name']: p for p in o['team']}
        assert set(people) == {'ada', 'Bob Builder', 'cara'}, 'Dan has left, and Olive is elsewhere'
        assert o['team'][0]['name'] == 'Bob Builder', 'most overdue first'
        bob, cara = people['Bob Builder'], people['cara']
        assert (bob['tickets'], bob['tasks'], bob['overdue']) == (5, 1, 2)
        assert bob['working_on']['title'] == 'Subject T5' and bob['today_seconds'] >= 1800 - 5
        assert (cara['tasks'], cara['blocked']) == (1, 1) and cara['working_on'] is None
        assert 4300 < cara['week_seconds'] < 5500, 'an hour on the timer and half an hour typed in'
        assert bob['week_seconds'] < 3600, 'last month does not count this week'
        assert o['timers_running'] == 1
        assert o['left_behind'] == {'tickets': 1, 'tasks': 1}, 'work still held by someone who has left'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_progress_and_what_changed(tmp_path):
    engine, db, acme, _, u, t, k, p = await _seed(tmp_path / 'o.db')
    try:
        o = await build_overview(db, acme, NOW)
        move = o['projects'][0]
        assert move['name'] == 'Office move', 'the one in trouble comes first'
        assert (move['done'], move['total'], move['percent']) == (1, 3, 33)
        assert (move['overdue'], move['blocked'], move['late'], move['at_risk']) == (1, 1, True, True)
        assert move['worked_seconds'] >= 3600
        calm = o['projects'][1]
        assert (calm['total'], calm['done'], calm['at_risk']) == (2, 0, False), 'a shelved task is not work left'

        counts = await project_counts(db, [p['calm'].id], TODAY)
        assert counts[p['calm'].id]['percent'] == 0

        assert o['last_day']['tickets_done'] == 1 and o['last_week']['tickets_done'] == 2, 'resolved counts as finished'
        assert o['last_day']['tasks_done'] == 1, "from its completion, and only this company's"
        assert o['last_week']['tickets_in'] == 9, 'in the last week'
        assert o['last_week']['backlog_change'] == 7
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_time_worked_is_clipped_to_the_period(tmp_path):
    engine, db, acme, _, u, *_ = await _seed(tmp_path / 'o.db')
    try:
        assert await hours_worked(db, u['cara'].id, _ago(days=1), NOW + timedelta(seconds=5), NOW) == 1.5
        assert await hours_worked(db, u['cara'].id, _ago(hours=2.6), _ago(hours=2.1), NOW) == 0.5, 'only the part of the timer inside the period, without the hours typed in later'
        assert await hours_worked(db, u['bob'].id, _ago(days=1), NOW + timedelta(hours=1), NOW) == 0.5, 'running, up to now'
        assert await hours_worked(db, u['ada'].id, _ago(days=1), NOW, NOW) == 0
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_first_reply_ignores_private_notes(tmp_path):
    engine, db, acme, *_ = await _seed(tmp_path / 'o.db')
    try:
        hours = await average_first_reply(db, acme, 30, NOW)
        assert hours is not None and hours >= 0
        empty = Workspace(id=acme.id + 100, name='Nothing yet')
        assert await average_first_reply(db, empty, 30, NOW) is None
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_page_is_for_admins_of_this_company(tmp_path):
    engine, db, acme, _, u, *_ = await _seed(tmp_path / 'o.db')
    try:
        page = await web_admin_overview(_request(u['ada']), db=db)
        html = page.body.decode()
        assert 'Team Overview' in html and 'Subject T1' in html and 'Subject T11' not in html, 'no other company'
        assert 'hx-trigger="every 60s' in html

        panels = await web_admin_overview(_request(u['ada'], panels='1'), db=db)
        assert 'Subject T1' in panels.body.decode() and '<html' not in panels.body.decode()

        with pytest.raises(HTTPException) as not_admin:
            await web_admin_overview(_request(u['bob']), db=db)
        assert not_admin.value.status_code == 403
        signed_out = SimpleNamespace(session={}, state=SimpleNamespace(), headers={}, query_params={'panels': '1'})
        assert (await web_admin_overview(signed_out, db=db)).headers['HX-Redirect'] == '/web/login'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_see_all_lists_match_the_counts(tmp_path):
    engine, db, acme, _, u, t, k, p = await _seed(tmp_path / 'o.db')
    try:
        o = await build_overview(db, acme, NOW)
        for section in o['ticket_lists']:
            page = await web_tickets_list(_request(u['ada'], '/web/tickets', attention=section['key']), db=db)
            assert len(page.context['tickets']) == section['count'], section['key']
        everything = await web_tickets_list(_request(u['ada'], '/web/tickets'), db=db)
        numbers = [ticket.ticket_number for ticket in everything.context['tickets']]
        assert 'T7' not in numbers and len(numbers) == o['open_tickets'], 'an archived ticket belongs to the archive'
        unknown = await web_tickets_list(_request(u['ada'], '/web/tickets', attention='nonsense'), db=db)
        assert len(unknown.context['tickets']) == len(numbers) and unknown.context['attention_filter'] == ''

        # A technician's list keeps its own limits
        mine = await web_tickets_list(_request(u['cara'], '/web/tickets', attention='no_reply'), db=db)
        assert len(mine.context['tickets']) == 0

        for tab, title in (('overdue', 'Patch switch'), ('blocked', 'Order cables'), ('unassigned', 'Label desks')):
            page = await web_tasks_list(_request(u['ada'], '/web/tasks/list'), tab=tab, status=None, priority=None,
                                        assignee_id=None, project_id=None, db=db)
            assert [task.title for task in page.context['tasks']] == [title], tab

        # Work in an archived project is on neither side
        p['move'].is_archived = True
        await db.commit()
        o = await build_overview(db, acme, NOW)
        for section in o['task_lists']:
            tab = section['key']
            page = await web_tasks_list(_request(u['ada'], '/web/tasks/list'), tab=tab, status=None, priority=None,
                                        assignee_id=None, project_id=None, db=db)
            assert len(page.context['tasks']) == section['count'], tab
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_board_view_shows_progress(tmp_path):
    engine, db, acme, _, u, t, k, p = await _seed(tmp_path / 'o.db')
    try:
        page = await web_projects(_request(u['ada'], '/web/projects'), db=db)
        assert page.context['project_progress'][p['move'].id]['percent'] == 33
        assert '1 of 3 done' in page.body.decode() and '1 overdue' in page.body.decode()
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_counts_stay_in_the_company(tmp_path):
    """An admin whose company has no projects saw every company's finished tasks."""
    engine, db, acme, other, u, *_ = await _seed(tmp_path / 'o.db')
    try:
        new = Workspace(name='Brand new')
        db.add(new)
        await db.flush()
        boss = User(username='boss', hashed_password='x', is_admin=True, workspace_id=new.id)
        db.add(boss)
        await db.commit()
        page = await web_dashboard(_request(boss, '/web/dashboard'), view=None, user_id=None, db=db)
        assert page.context['stats']['team_tasks_completed'] == 0

        ada = await web_dashboard(_request(u['ada'], '/web/dashboard'), view=None, user_id=None, db=db)
        assert ada.context['stats']['team_tasks_completed'] == 1
        assert ada.context['attention']['no_reply'] == 3 and 'Open Team Overview' in ada.body.decode()
        assert ada.context['stats']['avg_response_time'] is not None, 'used to be blank for good'
        bob = await web_dashboard(_request(u['bob'], '/web/dashboard'), view=None, user_id=None, db=db)
        assert bob.context['attention'] is None, 'for admins'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_later_edit_does_not_make_old_work_finished_this_week(tmp_path):
    engine, db, acme, _, u, t, k, _ = await _seed(tmp_path / 'o.db')
    try:
        k['finished'].archived_at = _ago(days=30)
        k['finished'].updated_at = _ago(hours=1)
        await db.commit()
        page = await web_dashboard(_request(u['ada'], '/web/dashboard'), view=None, user_id=None, db=db)
        assert page.context['stats']['team_tasks_completed'] == 0
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_tasks_finished_long_ago_keep_a_finish_date(tmp_path):
    engine, db, acme, _, u, t, k, _ = await _seed(tmp_path / 'o.db')
    try:
        k['finished'].archived_at = None
        k['finished'].updated_at = _ago(days=2)
        k['overdue'].archived_at = None
        await db.commit()
        async with engine.begin() as conn:
            await conn.exec_driver_sql('PRAGMA user_version = 3')
            await apply_data_changes(conn)
        await db.refresh(k['finished'])
        await db.refresh(k['overdue'])
        assert k['finished'].archived_at == _ago(days=2), 'the date the old counts used'
        assert k['overdue'].archived_at is None, 'only finished tasks'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_ticket_report_counts_resolved_tickets_as_finished(tmp_path):
    engine, db, acme, _, u, t, *_ = await _seed(tmp_path / 'o.db')
    try:
        db.add(TicketHistory(ticket_id=t['resolved'].id, user_id=u['bob'].id, action='status_changed',
                             old_value='open', new_value='resolved'))
        await db.commit()
        dates = dict(start_date=(TODAY - timedelta(days=5)).isoformat(), end_date=TODAY.isoformat())
        page = await web_tickets_report(_request(u['ada'], '/web/tickets/report'), user_id=None, project_id=None, db=db, **dates)
        summary = page.context['summary']
        bob = next(agent for agent in page.context['agents'] if agent['name'] == 'Bob Builder')
        assert bob['tickets_closed'] == 1, 'resolving a ticket counts for the person who did it'
        assert summary['closed_tickets'] == 1, 'the resolved one; the closed one came in before the period'
        assert summary['finished_in_period'] == 2, 'both finished in the period'
        assert summary['open_tickets'] == 7, 'an archived ticket is not open'
        assert 'Subject T7' not in [ticket['subject'] for ticket in page.context['open_tickets_list']]
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_workload_period_and_leavers(tmp_path):
    engine, db, acme, _, u, t, k, _ = await _seed(tmp_path / 'o.db')
    try:
        far = Task(title='Next year', project_id=k['nobody'].project_id, creator_id=u['ada'].id,
                   due_date=TODAY + timedelta(days=300))
        db.add(far)
        await db.commit()
        week = await web_workload(_request(u['ada'], '/web/workload'), project_id=None, time_range='week', db=db)
        quarter = await web_workload(_request(u['ada'], '/web/workload'), project_id=None, time_range='quarter', db=db)
        assert week.context['total_tasks'] == 4 and quarter.context['total_tasks'] == 4, 'next year waits'
        assert week.context['unassigned_tasks'] == 2, 'nobody on it, or only someone who has left'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_reports_use_the_real_timers(tmp_path):
    """The user report read a table that does not exist, and the project report
    only saw time from timers that had been stopped."""
    engine, db, acme, _, u, t, k, p = await _seed(tmp_path / 'o.db')
    try:
        dates = dict(start_date=(TODAY - timedelta(days=2)).isoformat(), end_date=TODAY.isoformat())
        view = await web_admin_user_activity_view(_request(u['ada'], '/web/admin/reports'), u['cara'].id, db=db, **dates)
        assert view.context['metrics']['total_hours'] == 1.5, 'an hour on the timer and half an hour typed in'

        k['overdue'].time_spent_hours = None
        await db.commit()
        report = await web_project_report(_request(u['ada'], f"/web/projects/{p['move'].id}/report"), p['move'].id, db=db, **dates)
        assert report.context['summary']['total_hours'] >= 3.0, 'two hours on one task, one on another'
    finally:
        await db.close()
        await engine.dispose()
