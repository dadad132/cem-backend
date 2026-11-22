"""
Workspace weekly report.

Every Monday from 08:00 (the company's own time), a workspace with weekly
reports enabled in its Email Settings gets one e-mail covering Monday to Friday
of the week before. It carries the exact Excel reports the download buttons
produce — one per active project and one for the workspace's tickets — and goes
only to the addresses configured in EmailSettings (never Project.support_email).

Each report sent is recorded once per workspace per week, so a restart never
sends it twice. If the server is down at 08:00 the report goes out when it is
back, as long as it is still Monday.

The linked address is usually also a mailbox the helpdesk turns into tickets.
The report carries the X-CEM-Report header, and the e-mail importer skips any
message with it, so the report never comes back as a ticket.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import smtplib
from datetime import date, datetime, timedelta
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from types import SimpleNamespace
from typing import Callable, Optional

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import day_start_utc, to_local, workspace_zone
from app.core.report_excel import build_project_report_excel, build_ticket_report_excel
from app.core.work_timer import format_duration, worked_seconds
from app.models.assignment import Assignment
from app.models.email_settings import EmailSettings
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.project_report import ProjectWeeklyReport, WorkspaceWeeklyReport
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
# An unconfirmed claim is only stale if it predates this process AND has
# outlived the lease (comfortably longer than building workbooks plus SMTP).
# This keeps a live older worker's claim safe, and never retries a mail this
# process already sent but failed to confirm in the database.
_PROCESS_START = datetime.utcnow()
STALE_CLAIM_AFTER = timedelta(minutes=30)
SMTP_TIMEOUT = 30
STATUS_LABELS = {'todo': 'To do', 'in_progress': 'In progress', 'blocked': 'Blocked', 'done': 'Done'}
_EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$')
_HEADER_LINE_RE = re.compile(r'^\s*(to|cc|bcc|from|subject|reply-to)\s*:', re.IGNORECASE)


def week_to_report(local_now: datetime) -> Optional[date]:
    """The Monday of the week to report on, if it is time to send: Monday
    from 08:00 sends the week before. Any other time, None."""
    if local_now.weekday() != SEND_WEEKDAY or local_now.hour < SEND_HOUR:
        return None
    return local_now.date() - timedelta(days=7)


def _last_completed_week(local_now: datetime) -> date:
    """Most recent finished Mon-Fri: the previous week on a workday, and the
    week just gone on Saturday or Sunday."""
    today = local_now.date()
    if today.weekday() <= 4:                 # Monday to Friday
        return today - timedelta(days=7 + today.weekday())
    return today - timedelta(days=today.weekday())


def recipients(raw: Optional[str]) -> list[str]:
    """Addresses from EmailSettings, safely.

    Commas, semicolons, spaces and newlines all separate addresses; duplicates
    are dropped in order. A line that looks like a mail header (Bcc: ...) is
    ignored in full so a stray "Bcc: someone@..." cannot become a recipient,
    and no address containing a CR/LF or failing local@domain.tld is kept.
    """
    if not raw or not isinstance(raw, str):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for line in re.split(r'[\r\n]+', raw):
        if _HEADER_LINE_RE.match(line):
            continue
        for token in re.split(r'[,;\s]+', line):
            address = token.strip()
            key = address.lower()
            if (not address or '\r' in address or '\n' in address
                    or not _EMAIL_RE.fullmatch(address) or key in seen):
                continue
            seen.add(key)
            result.append(address)
    return result


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

    # Finishing is not archiving any more. Older rows still have their finish
    # time in archived_at, so take the first date we actually recorded.
    def finished_at(t):
        return getattr(t, 'completed_at', None) or t.archived_at

    finished = sorted((t for t in tasks if _status(t) == 'done' and finished_at(t)
                       and start <= finished_at(t) < end),
                      key=finished_at)
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
        'finished': [(t.title, who(t), to_local(finished_at(t), zone)) for t in finished],
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
# The old per-project HTML report (kept for backward compatibility)
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
# The workspace-wide HTML summary
# ---------------------------------------------------------------------------

def render_workspace_weekly_report(workspace, week_start: date, week_end: date,
                                   project_summaries: list[dict], ticket_summary: dict,
                                   attachment_names: list[str]) -> tuple[str, str]:
    """(subject, HTML body) for one workspace's weekly e-mail."""
    period = f'{week_start:%d %b} – {week_end:%d %b %Y}'
    subject = f'Weekly report: {workspace.name} ({period})'
    finished = sum(len(p['finished']) for p in project_summaries)
    added = sum(p['added'] for p in project_summaries)
    hours = sum(p['time_total'] for p in project_summaries)
    tiles = [('Active projects', len(project_summaries)), ('Tasks finished', finished),
             ('Tasks added', added), ('Time worked', format_duration(hours)),
             ('Tickets opened', ticket_summary['opened']), ('Tickets closed', ticket_summary['closed']),
             ('Tickets open', ticket_summary['open'])]
    tile_html = ''.join(
        f'<td style="padding:10px 12px;background:#f1f5f9;border-radius:6px;text-align:center">'
        f'<div style="font-size:20px;font-weight:700;color:#0f172a">{escape(str(v))}</div>'
        f'<div style="font-size:12px;color:#475569">{escape(k)}</div></td>' for k, v in tiles)
    project_rows = [
        (p['project'], len(p['finished']), p['added'], format_duration(p['time_total']),
         len(p['overdue']), len(p['blocked']))
        for p in project_summaries
    ]
    projects = _table(['Project', 'Finished', 'Added', 'Time', 'Overdue', 'Blocked'], project_rows,
                      'No active projects this week.')
    files = ''.join(f'<li style="margin:4px 0">{escape(name)}</li>' for name in attachment_names)
    attached = (f'<ul style="margin:4px 0 16px;padding-left:20px">{files}</ul>' if files
                else '<p style="color:#64748b;margin:4px 0 16px">No report files were attached.</p>')
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;color:#0f172a;max-width:760px">
<h1 style="font-size:20px;margin:0 0 4px">{escape(workspace.name)}: weekly report</h1>
<p style="color:#475569;margin:0 0 16px">Monday to Friday, {escape(period)}</p>
<table style="border-spacing:6px;margin:0 -6px 8px"><tr>{tile_html}</tr></table>
<h2 style="font-size:16px;color:#1e3a8a;margin:20px 0 4px">Projects</h2>{projects}
<h2 style="font-size:16px;color:#1e3a8a;margin:20px 0 4px">Attached Excel reports</h2>{attached}
<p style="color:#94a3b8;font-size:12px;margin-top:24px">Sent automatically every Monday at 08:00 to the recipients
configured in Email Settings. This report is never imported as a ticket.</p>
</div>"""
    return subject, html


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _safe_attachment_name(filename: str) -> str:
    """A single safe token for Content-Disposition (no CRLF, quotes or paths)."""
    return re.sub(r'[\r\n"\\/;]+', '_', str(filename)).strip() or 'report.xlsx'


def _smtp_send(smtp: dict, to: list, subject: str, html: str,
               attachments: Optional[list[tuple[str, bytes]]] = None) -> None:
    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['From'] = f"{smtp['from_name']} <{smtp['from_email']}>" if smtp['from_name'] else smtp['from_email']
    msg['To'] = ', '.join(to)
    msg[REPORT_HEADER] = 'weekly-project'     # The importer skips this: never a ticket
    msg['Auto-Submitted'] = 'auto-generated'
    msg.attach(MIMEText(html, 'html'))
    for filename, content in attachments or []:
        part = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        part.set_payload(content)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{_safe_attachment_name(filename)}"')
        msg.attach(part)
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


async def _workspace_ticket_counts(db: AsyncSession, workspace_id: int,
                                   start: datetime, end: datetime) -> dict:
    """Tickets opened/closed in the week, and those still open now."""
    async def count(*conditions) -> int:
        return (await db.execute(select(func.count(Ticket.id)).where(
            Ticket.workspace_id == workspace_id, *conditions))).scalar() or 0

    # A ticket may be resolved and later closed; count it once if either event
    # was in the week, matching the "closed_at or resolved_at" report rule.
    closed_in_week = ((Ticket.closed_at >= start) & (Ticket.closed_at < end)) | \
        ((Ticket.resolved_at >= start) & (Ticket.resolved_at < end))
    return {
        'opened': await count(Ticket.created_at >= start, Ticket.created_at < end),
        'closed': await count(closed_in_week),
        'open': await count(Ticket.status.notin_(('resolved', 'closed')),
                            Ticket.is_archived == False),  # noqa: E712
    }


async def _weekly_mail(db: AsyncSession, workspace: Workspace, settings: EmailSettings,
                       week: date, now: datetime) -> tuple[str, str, list[tuple[str, bytes]]]:
    """The e-mail subject/body and the Excel attachments for one workspace."""
    zone = workspace_zone(workspace)
    week_end = week + timedelta(days=4)
    start_str, end_str = f'{week:%Y-%m-%d}', f'{week_end:%Y-%m-%d}'
    projects = (await db.execute(select(Project).where(
        Project.workspace_id == workspace.id,
        Project.is_archived == False,  # noqa: E712
    ).order_by(Project.name, Project.id))).scalars().all()

    summaries = []
    for project in projects:
        try:
            summaries.append(await build_report(db, project, workspace, week, now))
        except Exception as exc:      # one project's figures must not stop the others
            logger.warning('Weekly report figures for project %s failed: %s', project.id, exc)

    tickets = await _workspace_ticket_counts(
        db, workspace.id, day_start_utc(week, zone), day_start_utc(week + timedelta(days=5), zone))

    attachments: list[tuple[str, bytes]] = []
    if settings.weekly_report_include_tickets:
        try:
            name, content = await build_ticket_report_excel(db, workspace, start_str, end_str)
            attachments.append((name, content))
        except Exception as exc:      # a broken workbook must not lose the e-mail
            logger.warning('Ticket Excel for weekly report of %s failed: %s', workspace.name, exc)
    if settings.weekly_report_include_projects:
        used = {name for name, _ in attachments}
        for project in projects:
            try:
                name, content = await build_project_report_excel(
                    db, project, start_str, end_str, f'Weekly report for {workspace.name}', to_local(now, zone))
            except Exception as exc:  # one bad project must not lose the other files
                logger.warning('Project Excel for project %s failed: %s', project.id, exc)
                continue
            if name in used:            # two projects can have the same name
                stem, ext = os.path.splitext(name)
                candidate = f'{stem}_{project.id}{ext}'
                suffix = 2
                while candidate in used:
                    candidate = f'{stem}_{project.id}_{suffix}{ext}'
                    suffix += 1
                name = candidate
            used.add(name)
            attachments.append((name, content))

    # An e-mail promising Excel reports with none attached must be retried,
    # not recorded as a successful send.
    if not attachments:
        raise RuntimeError('No weekly report workbooks could be built')
    subject, html = render_workspace_weekly_report(workspace, week, week_end, summaries, tickets,
                                                   [name for name, _ in attachments])
    return subject, html, attachments


async def run_weekly_reports(db: AsyncSession, now: Optional[datetime] = None,
                             send: Optional[Callable] = None) -> int:
    """Send every workspace report that is due and not sent yet. Returns how many went."""
    now = now or datetime.utcnow()
    send = send or _smtp_send
    sent = 0
    for workspace in (await db.execute(select(Workspace))).scalars().all():
        week = week_to_report(to_local(now, workspace_zone(workspace)))
        if week is None:
            continue
        settings = (await db.execute(select(EmailSettings).where(
            EmailSettings.workspace_id == workspace.id))).scalars().first()
        if not settings or not settings.weekly_report_enabled:
            continue
        to = recipients(settings.weekly_report_recipients)
        if not to:
            continue
        if not (settings.weekly_report_include_projects or settings.weekly_report_include_tickets):
            continue
        existing = (await db.execute(select(WorkspaceWeeklyReport).where(
            WorkspaceWeeklyReport.workspace_id == workspace.id,
            WorkspaceWeeklyReport.week_start == week))).scalars().first()
        if existing is not None:
            # A row with recipients is a completed send. An unconfirmed row is
            # a claim; retry only one left by an older process AND old enough
            # that process must be gone.
            if (existing.sent_to or existing.sent_at >= _PROCESS_START
                    or (now - existing.sent_at) < STALE_CLAIM_AFTER):
                continue
            logger.warning('Retrying stale weekly report claim for workspace %s', workspace.id)
            try:
                await db.delete(existing)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.error('Could not clear stale claim for workspace %s: %s', workspace.id, exc)
                continue
        smtp = await _smtp_for(db, workspace.id)
        if smtp is None:
            logger.warning('Weekly report for %s not sent: no outgoing mail is set up', workspace.name)
            continue
        # Claim the week *before* sending: the unique constraint arbitrates
        # two workers/schedulers racing, so only the winner can send the mail.
        claim = WorkspaceWeeklyReport(workspace_id=workspace.id, week_start=week,
                                      sent_to='', sent_at=now)
        db.add(claim)
        try:
            await db.commit()
        except IntegrityError:        # another worker owns this week
            await db.rollback()
            logger.info('Weekly report for workspace %s was already recorded', workspace.id)
            continue
        except Exception as exc:
            await db.rollback()
            logger.error('Could not claim weekly report for workspace %s: %s', workspace.id, exc)
            continue
        claim_id = claim.id
        try:
            subject, html, attachments = await _weekly_mail(db, workspace, settings, week, now)
            await asyncio.to_thread(send, smtp, to, subject, html, attachments)
        except BaseException as exc:  # includes cancellation during shutdown
            logger.error('Weekly report for workspace %s failed: %s', workspace.id, exc)
            try:
                await db.rollback()   # clear a failed transaction before deleting
                row = await db.get(WorkspaceWeeklyReport, claim_id)
                if row is not None:
                    await db.delete(row)
                await db.commit()
            except Exception as cleanup_exc:
                await db.rollback()
                logger.error('Could not release weekly report claim for workspace %s: %s',
                             workspace.id, cleanup_exc)
            if isinstance(exc, asyncio.CancelledError):
                raise                 # let the scheduler shut down cleanly
            continue
        # Confirm the claim only now that the mail has really gone out. If this
        # commit fails, the row keeps this process's claim and is not retried,
        # so the recipients never get a duplicate.
        claim.sent_to = ', '.join(to)[:500]
        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.error('Weekly report for workspace %s was sent but not recorded: %s', workspace.id, exc)
        sent += 1
        logger.info('Weekly report for %s sent to %d recipient(s)', workspace.name, len(to))
    return sent


async def send_weekly_report_now(db: AsyncSession, workspace: Workspace, settings: EmailSettings,
                                 now: Optional[datetime] = None, send: Optional[Callable] = None) -> dict:
    """Send one report on demand, covering the most recent completed Mon-Fri.

    No dedup row is written, so a manual test never blocks Monday's scheduled
    send. The recipients and files are exactly the same as the scheduled one.
    """
    now = now or datetime.utcnow()
    send = send or _smtp_send
    if settings.workspace_id != workspace.id:
        raise ValueError('Email Settings do not belong to this workspace')
    week = _last_completed_week(to_local(now, workspace_zone(workspace)))
    to = recipients(settings.weekly_report_recipients)
    if not to:
        raise ValueError('No weekly report recipients are configured in Email Settings')
    if not (settings.weekly_report_include_projects or settings.weekly_report_include_tickets):
        raise ValueError('Enable at least one weekly report attachment (projects or tickets)')
    smtp = await _smtp_for(db, workspace.id)
    if smtp is None:
        raise ValueError('Outgoing mail is not configured for this workspace')

    subject, html, attachments = await _weekly_mail(db, workspace, settings, week, now)
    await asyncio.to_thread(send, smtp, to, subject, html, attachments)
    return {
        'recipients': to,
        'attachments': [name for name, _ in attachments],
        'period': f'{week:%Y-%m-%d} to {week + timedelta(days=4):%Y-%m-%d}',
    }


async def start_weekly_report_scheduler():
    """Checks every few minutes whether a Monday-morning report is due."""
    from app.core.database import engine
    logger.info('Weekly workspace report scheduler started (Mondays from %02d:00)', SEND_HOUR)
    while True:
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                await run_weekly_reports(db)
            await asyncio.sleep(CHECK_EVERY_SECONDS)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error('Weekly report scheduler error: %s', exc)
            await asyncio.sleep(CHECK_EVERY_SECONDS)
