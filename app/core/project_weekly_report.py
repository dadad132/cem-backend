"""
Weekly project report.

Every Monday from 08:00 (the company's own time), each Board View project
with an e-mail linked to it (Project.support_email) is sent a summary of
the week just past, Monday 00:00 to Friday 23:59: tasks finished and added,
time worked per person, tickets opened and closed, and what is still
overdue or blocked.

Each report sent is recorded, so a restart never sends one twice. If the
server is down at 08:00 the report goes out when it is back, as long as it
is still Monday.

The linked address is usually also a mailbox the helpdesk turns into
tickets. The report carries the X-CEM-Report header, and the e-mail importer
skips any message with it, so the report never comes back as a ticket.
"""
from __future__ import annotations

import asyncio
import logging
import re
import smtplib
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from types import SimpleNamespace
from typing import Callable, Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import day_start_utc, to_local, workspace_zone
from app.core.work_timer import format_duration, worked_seconds
from app.models.assignment import Assignment
from app.models.email_settings import EmailSettings
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.project_report import ProjectWeeklyReport
from app.models.task import Task
from app.models.task_extensions import TaskTimeEntry, TimeLog
from app.models.ticket import Ticket
from app.models.user import User
from app.models.workspace import Workspace

logger = logging.getLogger(__name__)

REPORT_HEADER = 'X-CEM-Report'
SEND_WEEKDAY = 0          # Monday
SEND_HOUR = 8             # 08:00, the company's own time
CHECK_EVERY_SECONDS = 300
SMTP_TIMEOUT = 30
STATUS_LABELS = {'todo': 'To do', 'in_progress': 'In progress', 'blocked': 'Blocked', 'done': 'Done'}


def week_to_report(local_now: datetime) -> Optional[date]:
    """The Monday of the week to report on, if it is time to send: Monday
    from 08:00 sends the week before. Any other time, None."""
    if local_now.weekday() != SEND_WEEKDAY or local_now.hour < SEND_HOUR:
        return None
    return local_now.date() - timedelta(days=7)


def recipients(linked: Optional[str]) -> list:
    """The addresses in a project's linked e-mail (one, or several separated
    by commas, semicolons or spaces)."""
    return [a for a in re.split(r'[\s,;]+', linked or '') if '@' in a]


def _status(task) -> str:
    return getattr(task.status, 'value', task.status)


def _clipped(entries, start: datetime, end: datetime, now: datetime) -> int:
    """Seconds of the entries that fall inside [start, end)."""
    spans = []
    for e in entries:
        s, f = max(e.started_at, start), min(e.ended_at or now, end)
        if f > s:
            spans.append(SimpleNamespace(started_at=s, ended_at=f))
    return worked_seconds(spans, now)


async def build_report(db: AsyncSession, project: Project, workspace: Workspace,
                       week_start: date, now: Optional[datetime] = None) -> dict:
    """The week's figures for one project. Times are stored as UTC."""
    now = now or datetime.utcnow()
    zone = workspace_zone(workspace)
    start = day_start_utc(week_start, zone)
    end = day_start_utc(week_start + timedelta(days=5), zone)        # Saturday 00:00
    today = to_local(now, zone).date()

    tasks = (await db.execute(select(Task).where(Task.project_id == project.id))).scalars().all()
    ids = [t.id for t in tasks]
    holders, entries, logs = {}, {}, []
    if ids:
        for task_id, user_id in (await db.execute(
            select(Assignment.task_id, Assignment.assignee_id).where(Assignment.task_id.in_(ids))
        )).all():
            holders.setdefault(task_id, []).append(user_id)
        for e in (await db.execute(
            select(TaskTimeEntry).where(TaskTimeEntry.task_id.in_(ids), TaskTimeEntry.started_at < end,
                                        or_(TaskTimeEntry.ended_at.is_(None), TaskTimeEntry.ended_at >= start))
        )).scalars().all():
            entries.setdefault(e.user_id, []).append(e)
        logs = (await db.execute(
            select(TimeLog).where(TimeLog.task_id.in_(ids), TimeLog.logged_at >= start, TimeLog.logged_at < end)
        )).scalars().all()

    people = {u for us in holders.values() for u in us} | set(entries) | {log.user_id for log in logs}
    people.discard(None)
    names = {u.id: (u.shown_name or u.username) for u in (await db.execute(
        select(User).where(User.id.in_(people)))).scalars().all()} if people else {}

    def who(task) -> str:
        return ', '.join(names.get(u, '') for u in dict.fromkeys(holders.get(task.id, []))) or 'Nobody'

    worked = {u: _clipped(es, start, end, now) for u, es in entries.items()}
    for log in logs:
        worked[log.user_id] = worked.get(log.user_id, 0) + int(log.hours * 3600)
    worked = {u: s for u, s in worked.items() if s > 0}

    # Finished tasks are archived as they finish (as the Team Overview counts them)
    finished = sorted((t for t in tasks if _status(t) == 'done' and t.archived_at and start <= t.archived_at < end),
                      key=lambda t: t.archived_at)
    added = [t for t in tasks if t.created_at and start <= t.created_at < end]
    still_open = [t for t in tasks if _status(t) != 'done' and not t.is_archived]
    overdue = sorted((t for t in still_open if t.due_date and t.due_date < today), key=lambda t: t.due_date)
    blocked = [t for t in still_open if _status(t) == TaskStatus.blocked.value]

    tickets = (await db.execute(select(Ticket).where(
        Ticket.related_project_id == project.id, Ticket.workspace_id == workspace.id))).scalars().all()

    def done_at(t):
        return t.closed_at or t.resolved_at

    return {
        'project': project.name,
        'company': workspace.name,
        'week_start': week_start,
        'week_end': week_start + timedelta(days=4),
        'finished': [(t.title, who(t), to_local(t.archived_at, zone)) for t in finished],
        'added': len(added),
        'open': len(still_open),
        'open_by_status': {s: sum(1 for t in still_open if _status(t) == s) for s in ('todo', 'in_progress', 'blocked')},
        'overdue': [(t.title, who(t), t.due_date, STATUS_LABELS.get(_status(t), _status(t))) for t in overdue],
        'blocked': [(t.title, who(t)) for t in blocked],
        'time': sorted(((names.get(u, 'Unknown'), s) for u, s in worked.items()), key=lambda p: -p[1]),
        'time_total': sum(worked.values()),
        'tickets_opened': sum(1 for t in tickets if t.created_at and start <= t.created_at < end),
        'tickets_closed': sum(1 for t in tickets if done_at(t) and start <= done_at(t) < end),
        'tickets_open': sum(1 for t in tickets if t.status not in ('resolved', 'closed') and not t.is_archived),
    }


# ---------------------------------------------------------------------------
# The e-mail
# ---------------------------------------------------------------------------

def _table(headings, rows, empty: str) -> str:
    if not rows:
        return f'<p style="color:#64748b;margin:4px 0 16px">{escape(empty)}</p>'
    th = ''.join(f'<th style="text-align:left;padding:6px 8px;border-bottom:2px solid #cbd5e1">{escape(h)}</th>'
                 for h in headings)
    body = ''.join('<tr>' + ''.join(f'<td style="padding:6px 8px;border-bottom:1px solid #e2e8f0">{escape(str(c))}</td>'
                                    for c in row) + '</tr>' for row in rows)
    return (f'<table style="border-collapse:collapse;width:100%;font-size:14px;margin:4px 0 16px">'
            f'<tr>{th}</tr>{body}</table>')


def render_report(r: dict) -> tuple:
    """(subject, HTML body)."""
    period = f"{r['week_start']:%d %b} – {r['week_end']:%d %b %Y}"
    subject = f"Weekly report: {r['project']} ({period})"
    tiles = [('Tasks finished', len(r['finished'])), ('Tasks added', r['added']),
             ('Time worked', format_duration(r['time_total'])), ('Open tasks', r['open']),
             ('Overdue', len(r['overdue'])), ('Blocked', len(r['blocked']))]
    tile_html = ''.join(
        f'<td style="padding:10px 12px;background:#f1f5f9;border-radius:6px;text-align:center">'
        f'<div style="font-size:20px;font-weight:700;color:#0f172a">{escape(str(v))}</div>'
        f'<div style="font-size:12px;color:#475569">{escape(k)}</div></td>' for k, v in tiles)
    h2 = '<h2 style="font-size:16px;color:#1e3a8a;margin:20px 0 4px">{}</h2>'
    open_line = ', '.join(f"{STATUS_LABELS[s]} {n}" for s, n in r['open_by_status'].items())
    finished = _table(['Task', 'Done by', 'Finished'], [(t, w, f"{d:%a %d %b %H:%M}") for t, w, d in r['finished']],
                      'No tasks were finished this week.')
    time_rows = _table(['Person', 'Time'], [(n, format_duration(s)) for n, s in r['time']],
                       'No time was recorded this week.')
    overdue = _table(['Task', 'Assigned to', 'Due', 'Status'],
                     [(t, w, f"{d:%d %b %Y}", s) for t, w, d, s in r['overdue']], 'Nothing is overdue.')
    blocked = _table(['Task', 'Assigned to'], r['blocked'], 'Nothing is blocked.')
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;color:#0f172a;max-width:760px">
<h1 style="font-size:20px;margin:0 0 4px">{escape(r['project'])}: weekly report</h1>
<p style="color:#475569;margin:0 0 16px">{escape(r['company'] or '')} · work from Monday to Friday, {escape(period)}</p>
<table style="border-spacing:6px;margin:0 -6px 8px"><tr>{tile_html}</tr></table>
{h2.format('Finished this week')}{finished}
{h2.format('Time worked')}{time_rows}
{h2.format('Overdue')}{overdue}
{h2.format('Blocked')}{blocked}
{h2.format('Tickets')}
<p style="margin:4px 0 16px">{r['tickets_opened']} opened and {r['tickets_closed']} closed this week;
{r['tickets_open']} still open.</p>
<p style="color:#475569;font-size:13px">Open tasks now: {escape(open_line)}.</p>
<p style="color:#94a3b8;font-size:12px;margin-top:24px">Sent automatically every Monday at 08:00 to the e-mail
linked to this project on the Board View.</p>
</div>"""
    return subject, html


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _smtp_send(smtp: dict, to: list, subject: str, html: str) -> None:
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"{smtp['from_name']} <{smtp['from_email']}>" if smtp['from_name'] else smtp['from_email']
    msg['To'] = ', '.join(to)
    msg[REPORT_HEADER] = 'weekly-project'     # The importer skips this: never a ticket
    msg['Auto-Submitted'] = 'auto-generated'
    msg.attach(MIMEText(html, 'html'))
    if smtp['use_tls']:
        server = smtplib.SMTP(smtp['host'], smtp['port'], timeout=SMTP_TIMEOUT)
        server.starttls()
    else:
        server = smtplib.SMTP_SSL(smtp['host'], smtp['port'], timeout=SMTP_TIMEOUT)
    try:
        server.login(smtp['username'], smtp['password'])
        server.send_message(msg)
    finally:
        server.quit()


async def _smtp_for(db: AsyncSession, workspace_id: int) -> Optional[dict]:
    """The company's own outgoing mail settings (as ticket replies use), or
    the server's from the environment. None if neither is set up."""
    s = (await db.execute(select(EmailSettings).where(EmailSettings.workspace_id == workspace_id))).scalars().first()
    if s and s.smtp_host and s.smtp_username and s.smtp_password and s.smtp_from_email:
        return {'host': s.smtp_host, 'port': s.smtp_port, 'username': s.smtp_username, 'password': s.smtp_password,
                'from_email': s.smtp_from_email, 'from_name': s.smtp_from_name, 'use_tls': s.smtp_use_tls}
    from app.core.email import _get_smtp_settings
    host, port, username, password, from_email, use_tls = _get_smtp_settings()
    if host and username and password:
        return {'host': host, 'port': port, 'username': username, 'password': password,
                'from_email': from_email, 'from_name': None, 'use_tls': use_tls}
    return None


async def run_weekly_reports(db: AsyncSession, now: Optional[datetime] = None,
                             send: Optional[Callable] = None) -> int:
    """Send every report that is due and not sent yet. Returns how many went."""
    now = now or datetime.utcnow()
    send = send or _smtp_send
    sent = 0
    for workspace in (await db.execute(select(Workspace))).scalars().all():
        week = week_to_report(to_local(now, workspace_zone(workspace)))
        if week is None:
            continue
        projects = (await db.execute(select(Project).where(
            Project.workspace_id == workspace.id, Project.is_archived == False,  # noqa: E712
            Project.support_email.is_not(None), Project.support_email != '',
        ).order_by(Project.id))).scalars().all()
        done = set((await db.execute(select(ProjectWeeklyReport.project_id).where(
            ProjectWeeklyReport.week_start == week))).scalars().all())
        projects = [p for p in projects if p.id not in done and recipients(p.support_email)]
        if not projects:
            continue
        smtp = await _smtp_for(db, workspace.id)
        if smtp is None:
            logger.warning('Weekly project reports for %s not sent: no outgoing mail is set up', workspace.name)
            continue
        for project in projects:
            to = recipients(project.support_email)
            try:
                subject, html = render_report(await build_report(db, project, workspace, week, now))
                await asyncio.to_thread(send, smtp, to, subject, html)
            except Exception as exc:  # one project's failure must not stop the others
                logger.error('Weekly report for project %s failed: %s', project.id, exc)
                continue
            db.add(ProjectWeeklyReport(project_id=project.id, week_start=week, sent_to=', '.join(to)[:500],
                                       sent_at=now))
            try:
                await db.commit()
            except IntegrityError:      # (another worker sent it at the same moment)
                await db.rollback()
                continue
            sent += 1
            logger.info('Weekly report for %s sent to %s', project.name, ', '.join(to))
    return sent


async def start_weekly_report_scheduler():
    """Checks every few minutes whether a Monday-morning report is due."""
    from app.core.database import engine
    logger.info('Weekly project report scheduler started (Mondays from %02d:00)', SEND_HOUR)
    while True:
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                await run_weekly_reports(db)
            await asyncio.sleep(CHECK_EVERY_SECONDS)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error('Weekly project report scheduler error: %s', exc)
            await asyncio.sleep(CHECK_EVERY_SECONDS)
