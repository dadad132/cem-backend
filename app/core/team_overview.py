"""The admin's Team Overview: is the work healthy, where is it stuck, what
changed, and what needs someone now.

Stored times are UTC. Dates people pick (ticket end dates, task due dates) are
stored as picked, so those compare with the company's local time (clock.py).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Dict, List, Optional

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_hours import calculate_business_hours
from app.core.clock import day_start_utc, local_now, local_today, to_local, workspace_zone
from app.core.work_timer import worked_seconds
from app.models.assignment import Assignment
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.task_extensions import TaskTimeEntry, TimeLog
from app.models.ticket import Ticket, TicketComment, TicketTimeEntry
from app.models.user import USER_SHOWN_NAME, User

DONE_TICKET_STATUSES = ('resolved', 'closed')
QUIET_DAYS = 3
LIST_SIZE = 6

# (key, heading, what it means): the lists on the page, in order
TICKET_ATTENTION = (
    ('no_reply', 'Waiting for a first reply', 'Still open, and nobody has written to the customer yet'),
    ('unassigned', 'Nobody assigned', 'No one owns these yet'),
    ('overdue', 'Past their end date', 'The end date has gone by and they are still open'),
    ('quiet', f'Quiet for {QUIET_DAYS}+ days', 'Open, but nothing has happened on them'),
)
TASK_ATTENTION = (
    ('overdue', 'Overdue tasks', 'The due date has gone by'),
    ('blocked', 'Blocked tasks', 'Someone is stuck and waiting on something'),
    ('unassigned', 'Tasks nobody owns', 'No one is assigned'),
)


def age_text(seconds: float) -> str:
    """A rough age: 12m, 5h, 3d."""
    minutes = max(int(seconds // 60), 0)
    if minutes < 60:
        return f'{minutes}m'
    if minutes < 24 * 60:
        return f'{minutes // 60}h'
    return f'{minutes // (24 * 60)}d'


def ticket_attention_filter(kind: str, zone=None, now: Optional[datetime] = None):
    """One definition per list, shared by the Team Overview and the Tickets page
    filter so a count and its "see all" always agree. None for an unknown kind."""
    now = now or datetime.utcnow()
    if kind == 'no_reply':
        replied = exists().where(TicketComment.ticket_id == Ticket.id, TicketComment.is_internal == False,
                                 TicketComment.user_id.is_not(None))
        return and_(Ticket.status == 'open', ~replied)
    if kind == 'unassigned':
        return Ticket.assigned_to_id.is_(None)
    if kind == 'overdue':
        return Ticket.due_date < local_now(zone)
    if kind == 'quiet':
        return Ticket.updated_at < now - timedelta(days=QUIET_DAYS)
    return None


def task_attention_filter(kind: str, today: date):
    """The same for tasks. None for an unknown kind."""
    if kind == 'overdue':
        return Task.due_date < today
    if kind == 'blocked':
        return Task.status == TaskStatus.blocked
    if kind == 'unassigned':
        return ~exists().where(Assignment.task_id == Task.id)
    return None


def _open_tickets(workspace_id: int):
    return and_(Ticket.workspace_id == workspace_id, Ticket.status.notin_(DONE_TICKET_STATUSES),
                Ticket.is_archived == False)


def _open_tasks(workspace_id: int):
    return and_(Project.workspace_id == workspace_id, Project.is_archived == False,
                Task.status != TaskStatus.done, Task.is_archived == False)


async def attention_counts(db: AsyncSession, workspace_id: int, zone=None, now=None) -> Dict[str, int]:
    """How many tickets are on each list, for the dashboard's summary line."""
    now = now or datetime.utcnow()
    counts = {}
    for key, _, _ in TICKET_ATTENTION:
        counts[key] = await db.scalar(select(func.count()).select_from(Ticket).where(
            _open_tickets(workspace_id), ticket_attention_filter(key, zone, now))) or 0
    return counts


async def _ticket_lists(db: AsyncSession, workspace_id: int, zone, now: datetime) -> List[dict]:
    lists = []
    for key, title, hint in TICKET_ATTENTION:
        condition = and_(_open_tickets(workspace_id), ticket_attention_filter(key, zone, now))
        count = await db.scalar(select(func.count()).select_from(Ticket).where(condition)) or 0
        rows = (await db.execute(
            select(Ticket.id, Ticket.ticket_number, Ticket.subject, Ticket.priority, Ticket.created_at,
                   Ticket.updated_at, Ticket.due_date, USER_SHOWN_NAME.label('shown'), User.username)
            .outerjoin(User, User.id == Ticket.assigned_to_id)
            .where(condition).order_by(Ticket.created_at.asc()).limit(LIST_SIZE)
        )).all()
        items = []
        for row in rows:
            if key == 'overdue':
                when = f'{age_text((local_now(zone) - row.due_date).total_seconds())} late'
            elif key == 'quiet':
                when = f'last touched {age_text((now - row.updated_at).total_seconds())} ago'
            else:
                when = f'came in {age_text((now - row.created_at).total_seconds())} ago'
            items.append({'url': f'/web/tickets/{row.id}', 'ref': f'#{row.ticket_number}', 'title': row.subject,
                          'priority': row.priority, 'who': row.shown or row.username, 'when': when})
        lists.append({'key': key, 'title': title, 'hint': hint, 'count': count, 'items': items,
                      'url': f'/web/tickets?attention={key}'})
    return lists


async def _task_lists(db: AsyncSession, workspace_id: int, today: date) -> List[dict]:
    see_all = {'overdue': '/web/tasks/list?tab=overdue', 'blocked': '/web/tasks/list?tab=blocked',
               'unassigned': '/web/tasks/list?tab=unassigned'}
    lists = []
    for key, title, hint in TASK_ATTENTION:
        condition = and_(_open_tasks(workspace_id), task_attention_filter(key, today))
        count = await db.scalar(
            select(func.count()).select_from(Task).join(Project, Project.id == Task.project_id).where(condition)
        ) or 0
        rows = (await db.execute(
            select(Task.id, Task.title, Task.due_date, Task.created_at, Project.name.label('project'))
            .join(Project, Project.id == Task.project_id)
            .where(condition).order_by(Task.due_date.asc().nullslast(), Task.created_at.asc()).limit(LIST_SIZE)
        )).all()
        people: Dict[int, List[str]] = {}
        if rows:
            for task_id, shown, username in (await db.execute(
                select(Assignment.task_id, USER_SHOWN_NAME, User.username)
                .join(User, User.id == Assignment.assignee_id)
                .where(Assignment.task_id.in_([r.id for r in rows]))
            )).all():
                people.setdefault(task_id, []).append(shown or username)
        items = []
        for row in rows:
            late = (today - row.due_date).days if row.due_date and row.due_date < today else 0
            items.append({'url': f'/web/tasks/{row.id}', 'ref': row.project, 'title': row.title, 'priority': None,
                          'who': ', '.join(people.get(row.id, [])) or None,
                          'when': f"{late} day{'' if late == 1 else 's'} late" if late else None})
        lists.append({'key': key, 'title': title, 'hint': hint, 'count': count, 'items': items, 'url': see_all[key]})
    return lists


def _clipped(entries, start: datetime, now: datetime) -> list:
    """The parts of timer entries after start, so a long stretch counts only for its share."""
    return [SimpleNamespace(started_at=max(e.started_at, start), ended_at=min(e.ended_at or now, now))
            for e in entries if (e.ended_at or now) > start]


async def _team_load(db: AsyncSession, workspace_id: int, zone, now: datetime) -> dict:
    today = local_today(zone)
    day_start = day_start_utc(today, zone)
    week_start = day_start_utc(today - timedelta(days=today.weekday()), zone)
    local = local_now(zone)

    members = (await db.execute(
        select(User).where(User.workspace_id == workspace_id, User.is_active == True).order_by(USER_SHOWN_NAME)
    )).scalars().all()
    rows = {m.id: {'id': m.id, 'name': m.shown_name or m.username, 'tickets': 0, 'tasks': 0, 'overdue': 0,
                   'due_today': 0, 'blocked': 0, 'today_seconds': 0, 'week_seconds': 0, 'working_on': None}
            for m in members}
    left_behind = {'tickets': 0, 'tasks': 0}  # still assigned to someone who has left

    for assignee_id, due in (await db.execute(
        select(Ticket.assigned_to_id, Ticket.due_date)
        .where(_open_tickets(workspace_id), Ticket.assigned_to_id.is_not(None))
    )).all():
        row = rows.get(assignee_id)
        if row is None:
            left_behind['tickets'] += 1
            continue
        row['tickets'] += 1
        row['overdue'] += 1 if due and due < local else 0
        row['due_today'] += 1 if due and due.date() == today else 0

    for assignee_id, due, status in (await db.execute(
        select(Assignment.assignee_id, Task.due_date, Task.status)
        .join(Task, Task.id == Assignment.task_id).join(Project, Project.id == Task.project_id)
        .where(_open_tasks(workspace_id))
    )).all():
        row = rows.get(assignee_id)
        if row is None:
            left_behind['tasks'] += 1
            continue
        row['tasks'] += 1
        row['overdue'] += 1 if due and due < today else 0
        row['due_today'] += 1 if due == today else 0
        row['blocked'] += 1 if status == TaskStatus.blocked else 0

    # Time worked this week, by whoever ran the timer
    spans: Dict[int, list] = {}
    since_week = or_(TicketTimeEntry.ended_at.is_(None), TicketTimeEntry.ended_at >= week_start)
    for entry, ticket_id, number, subject in (await db.execute(
        select(TicketTimeEntry, Ticket.id, Ticket.ticket_number, Ticket.subject)
        .join(Ticket, Ticket.id == TicketTimeEntry.ticket_id)
        .where(Ticket.workspace_id == workspace_id, since_week)
    )).all():
        spans.setdefault(entry.user_id, []).append(entry)
        if entry.ended_at is None and entry.user_id in rows:
            rows[entry.user_id]['working_on'] = {'url': f'/web/tickets/{ticket_id}', 'title': subject,
                                                 'ref': f'#{number}', 'since': entry.started_at}
    since_week = or_(TaskTimeEntry.ended_at.is_(None), TaskTimeEntry.ended_at >= week_start)
    for entry, task_id, title, project in (await db.execute(
        select(TaskTimeEntry, Task.id, Task.title, Project.name)
        .join(Task, Task.id == TaskTimeEntry.task_id).join(Project, Project.id == Task.project_id)
        .where(Project.workspace_id == workspace_id, since_week)
    )).all():
        spans.setdefault(entry.user_id, []).append(entry)
        if entry.ended_at is None and entry.user_id in rows:
            rows[entry.user_id]['working_on'] = {'url': f'/web/tasks/{task_id}', 'title': title,
                                                 'ref': project, 'since': entry.started_at}
    for user_id, entries in spans.items():
        if user_id in rows:
            rows[user_id]['today_seconds'] = worked_seconds(_clipped(entries, day_start, now), now)
            rows[user_id]['week_seconds'] = worked_seconds(_clipped(entries, week_start, now), now)

    # Hours typed in by hand, which no timer knows about
    for user_id, hours, logged_at in (await db.execute(
        select(TimeLog.user_id, TimeLog.hours, TimeLog.logged_at)
        .join(Task, Task.id == TimeLog.task_id).join(Project, Project.id == Task.project_id)
        .where(Project.workspace_id == workspace_id, TimeLog.logged_at >= week_start)
    )).all():
        if user_id in rows:
            rows[user_id]['week_seconds'] += int(hours * 3600)
            rows[user_id]['today_seconds'] += int(hours * 3600) if logged_at >= day_start else 0

    people = sorted(rows.values(), key=lambda r: (-r['overdue'], -(r['tickets'] + r['tasks']), r['name'].lower()))
    return {'people': people, 'left_behind': left_behind}


async def project_counts(db: AsyncSession, project_ids, today: date) -> Dict[int, dict]:
    """Progress per project: {project id: total, done, percent, blocked, overdue}.
    Completed tasks are archived, so they count here though the board hides them."""
    project_ids = list(project_ids)
    counts = {pid: {'total': 0, 'done': 0, 'blocked': 0, 'overdue': 0, 'percent': 0} for pid in project_ids}
    if not project_ids:
        return counts
    for project_id, status, due, archived in (await db.execute(
        select(Task.project_id, Task.status, Task.due_date, Task.is_archived).where(Task.project_id.in_(project_ids))
    )).all():
        row = counts[project_id]
        if status == TaskStatus.done:
            row['total'] += 1
            row['done'] += 1
        elif not archived:
            row['total'] += 1
            row['blocked'] += 1 if status == TaskStatus.blocked else 0
            row['overdue'] += 1 if due and due < today else 0
    for row in counts.values():
        row['percent'] = round(row['done'] / row['total'] * 100) if row['total'] else 0
    return counts


async def _project_progress(db: AsyncSession, workspace_id: int, today: date, now: datetime) -> List[dict]:
    projects = (await db.execute(
        select(Project).where(Project.workspace_id == workspace_id, Project.is_archived == False)
    )).scalars().all()
    counts = await project_counts(db, [p.id for p in projects], today)

    per_task: Dict[tuple, list] = {}
    for project_id, task_id, started_at, ended_at in (await db.execute(
        select(Task.project_id, TaskTimeEntry.task_id, TaskTimeEntry.started_at, TaskTimeEntry.ended_at)
        .join(Task, Task.id == TaskTimeEntry.task_id).join(Project, Project.id == Task.project_id)
        .where(Project.workspace_id == workspace_id, Project.is_archived == False)
    )).all():
        per_task.setdefault((project_id, task_id), []).append(
            SimpleNamespace(started_at=started_at, ended_at=ended_at))
    worked: Dict[int, int] = {}
    for (project_id, _), entries in per_task.items():
        worked[project_id] = worked.get(project_id, 0) + worked_seconds(entries, now)

    rows = []
    for project in projects:
        row = counts[project.id]
        open_tasks = row['total'] - row['done']
        days_left = (project.due_date - today).days if project.due_date else None
        late = days_left is not None and days_left < 0 and open_tasks > 0
        rows.append({**row, 'id': project.id, 'name': project.name, 'open': open_tasks, 'days_left': days_left,
                     'late': late, 'at_risk': late or row['overdue'] > 0 or row['blocked'] > 0,
                     'worked_seconds': worked.get(project.id, 0)})
    rows.sort(key=lambda r: (not r['at_risk'], not r['late'], -r['overdue'], r['percent'], r['name'].lower()))
    return rows


async def _changes(db: AsyncSession, workspace_id: int, start: datetime, end: datetime) -> dict:
    """What happened between two stored times."""
    done_at = func.coalesce(Ticket.closed_at, Ticket.resolved_at)
    tickets_in = await db.scalar(select(func.count()).select_from(Ticket).where(
        Ticket.workspace_id == workspace_id, Ticket.created_at >= start, Ticket.created_at < end)) or 0
    tickets_done = await db.scalar(select(func.count()).select_from(Ticket).where(
        Ticket.workspace_id == workspace_id, Ticket.status.in_(DONE_TICKET_STATUSES),
        done_at >= start, done_at < end)) or 0
    replies = await db.scalar(
        select(func.count()).select_from(TicketComment).join(Ticket, Ticket.id == TicketComment.ticket_id).where(
            Ticket.workspace_id == workspace_id, TicketComment.is_internal == False,
            TicketComment.user_id.is_not(None), TicketComment.created_at >= start, TicketComment.created_at < end)
    ) or 0
    # archived_at: done tasks are archived as they finish, and later edits leave it alone
    tasks_done = await db.scalar(
        select(func.count()).select_from(Task).join(Project, Project.id == Task.project_id).where(
            Project.workspace_id == workspace_id, Task.status == TaskStatus.done,
            Task.archived_at >= start, Task.archived_at < end)
    ) or 0
    return {'tickets_in': tickets_in, 'tickets_done': tickets_done, 'replies': replies, 'tasks_done': tasks_done,
            'backlog_change': tickets_in - tickets_done}


async def hours_worked(db: AsyncSession, user_id: int, start: datetime, end: datetime,
                       now: Optional[datetime] = None) -> float:
    """Hours a person worked between two stored times: ticket and task timers
    they ran, plus hours typed in by hand."""
    until = min(end, now or datetime.utcnow())
    entries = []
    for model in (TicketTimeEntry, TaskTimeEntry):
        entries += (await db.execute(select(model).where(
            model.user_id == user_id, model.started_at < until,
            or_(model.ended_at.is_(None), model.ended_at > start),
        ))).scalars().all()
    typed = await db.scalar(select(func.sum(TimeLog.hours)).where(
        TimeLog.user_id == user_id, TimeLog.logged_at >= start, TimeLog.logged_at < end)) or 0
    return round(worked_seconds(_clipped(entries, start, until), until) / 3600 + typed, 1)


async def average_first_reply(db: AsyncSession, workspace, days: int, now: Optional[datetime] = None) -> Optional[float]:
    """Average business hours to the first reply the customer saw, for tickets
    from the last few days. Private notes do not count. None if there are none yet."""
    now = now or datetime.utcnow()
    zone = workspace_zone(workspace)
    first = (
        select(TicketComment.ticket_id, func.min(TicketComment.created_at).label('replied_at'))
        .where(TicketComment.is_internal == False, TicketComment.user_id.is_not(None))
        .group_by(TicketComment.ticket_id).subquery()
    )
    rows = (await db.execute(
        select(Ticket.created_at, first.c.replied_at).join(first, first.c.ticket_id == Ticket.id)
        .where(Ticket.workspace_id == workspace.id, Ticket.created_at >= now - timedelta(days=days))
    )).all()
    if not rows:
        return None
    hours = [calculate_business_hours(to_local(created, zone), to_local(replied, zone),
                                      workspace.business_hours_start or '07:30', workspace.business_hours_end or '16:00',
                                      workspace.business_hours_exclude_weekends)
             for created, replied in rows]
    return round(sum(hours) / len(hours), 1)


async def build_overview(db: AsyncSession, workspace, now: Optional[datetime] = None) -> dict:
    """Everything the Team Overview shows, for one company."""
    now = now or datetime.utcnow()
    zone = workspace_zone(workspace)
    today = local_today(zone)

    ticket_lists = await _ticket_lists(db, workspace.id, zone, now)
    task_lists = await _task_lists(db, workspace.id, today)
    team = await _team_load(db, workspace.id, zone, now)
    tickets = {section['key']: section['count'] for section in ticket_lists}
    tasks = {section['key']: section['count'] for section in task_lists}
    open_tickets = await db.scalar(select(func.count()).select_from(Ticket).where(_open_tickets(workspace.id))) or 0

    return {
        'open_tickets': open_tickets,
        'tickets': tickets,
        'tasks': tasks,
        'needs_someone': tickets['no_reply'] + tickets['unassigned'] + tickets['overdue']
        + tasks['overdue'] + tasks['blocked'],
        'timers_running': sum(1 for p in team['people'] if p['working_on']),
        'first_reply_hours': await average_first_reply(db, workspace, 30, now),
        'ticket_lists': ticket_lists,
        'task_lists': task_lists,
        'team': team['people'],
        'left_behind': team['left_behind'],
        'projects': await _project_progress(db, workspace.id, today, now),
        'last_day': await _changes(db, workspace.id, now - timedelta(days=1), now),
        'last_week': await _changes(db, workspace.id, now - timedelta(days=7), now),
    }
