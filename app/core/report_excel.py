"""The Excel reports shared by the download buttons and the weekly e-mail.

One builder per report means the file a manager downloads from
/web/projects/{id}/report/excel (or /web/tickets/report/excel) and the file
attached to the workspace's weekly report are the same workbook: same sheets,
same columns, same figures and same styling. This module never imports the web
layer, so the weekly scheduler can use it without pulling in HTTP code.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.business_hours import calculate_business_hours
from app.core.clock import days_utc, format_local, local_now, local_today, to_local, workspace_zone
from app.core.exports import Export, ExportTable, to_excel
from app.core.team_overview import DONE_TICKET_STATUSES
from app.core.work_timer import worked_seconds
from app.models.assignment import Assignment
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.task_extensions import TaskTimeEntry
from app.models.task_history import TaskHistory
from app.models.ticket import Ticket, TicketComment, TicketHistory
from app.models.user import User
from app.models.workspace import Workspace

TASK_STATUS_LABELS = {'todo': 'To do', 'in_progress': 'In progress', 'blocked': 'Blocked', 'done': 'Done'}
_MIME_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


# ---------------------------------------------------------------------------
# Reporting periods and shared task figures
# ---------------------------------------------------------------------------

def _report_days(start_date: Optional[str], end_date: Optional[str], zone) -> tuple:
    """A report's days as picked (YYYY-MM-DD; by default the last 30 days up
    to today) in the company's time: (first day, last day, start, end), where
    start <= a stored (UTC) time < end falls on those days."""
    today = local_today(zone)
    first = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else today - timedelta(days=30)
    last = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else today
    return (first, last, *days_utc(first, last, zone))


async def _tasks_time_worked(db: AsyncSession, task_ids, user_id: Optional[int] = None) -> dict:
    """Seconds on each task's work timer: {task id: seconds}. A running timer
    counts up to now. With user_id, only the time that person ran."""
    entries = {}
    if task_ids:
        query = select(TaskTimeEntry).where(TaskTimeEntry.task_id.in_(list(task_ids)))
        if user_id:
            query = query.where(TaskTimeEntry.user_id == user_id)
        for entry in (await db.execute(query)).scalars().all():
            entries.setdefault(entry.task_id, []).append(entry)
    return {task_id: worked_seconds(task_entries) for task_id, task_entries in entries.items()}


def _task_hours(task, worked: dict) -> float:
    """Hours on a task: its timer, or what was entered by hand if that is more.
    worked: seconds from _tasks_time_worked."""
    return round(max(worked.get(task.id, 0) / 3600, task.time_spent_hours or 0), 2)


async def _project_report_data(db: AsyncSession, project, start_date: Optional[str],
                               end_date: Optional[str]) -> dict:
    """Who did what on a project, and the time spent: the project report page
    and its Excel export. start_date/end_date are YYYY-MM-DD (last 30 days if not given)."""
    zone = workspace_zone(await db.get(Workspace, project.workspace_id))
    first_day, last_day, start_dt, end_dt = _report_days(start_date, end_date, zone)
    start_date, end_date = f'{first_day:%Y-%m-%d}', f'{last_day:%Y-%m-%d}'

    all_tasks = (await db.execute(select(Task).where(Task.project_id == project.id))).scalars().all()
    task_ids = [t.id for t in all_tasks]
    worked = await _tasks_time_worked(db, task_ids)

    assignments = []
    if task_ids:
        assignments = (await db.execute(
            select(Assignment, User)
            .join(User, User.id == Assignment.assignee_id)
            .where(Assignment.task_id.in_(task_ids))
        )).all()

    assignees_map = {}  # task_id -> [(name, email), ...]
    for assignment, assignee_user in assignments:
        assignees_map.setdefault(assignment.task_id, []).append(
            (assignee_user.shown_name or assignee_user.username, assignee_user.email))

    task_completions = {}  # task_id -> completion datetime
    if task_ids:
        completions = (await db.execute(
            select(TaskHistory)
            .where(TaskHistory.task_id.in_(task_ids), TaskHistory.field == 'status',
                   TaskHistory.new_value == 'done')
            .order_by(TaskHistory.created_at.desc())
        )).scalars().all()
        for completion in completions:
            if completion.task_id not in task_completions:
                task_completions[completion.task_id] = completion.created_at

    contributor_stats = {}  # user_id -> stats
    for assignment, assignee_user in assignments:
        uid = assignee_user.id
        if uid not in contributor_stats:
            contributor_stats[uid] = {
                'user': assignee_user,
                'name': assignee_user.shown_name or assignee_user.username,
                'email': assignee_user.email,
                'initials': ''.join(
                    [n[0].upper() for n in (assignee_user.shown_name or assignee_user.email
                                           or assignee_user.username).split()[:2]]),
                'tasks_assigned': 0,
                'tasks_completed': 0,
                'hours_logged': 0.0,
            }
        task = next((t for t in all_tasks if t.id == assignment.task_id), None)
        if task:
            contributor_stats[uid]['tasks_assigned'] += 1
            if task.status == TaskStatus.done:
                contributor_stats[uid]['tasks_completed'] += 1
            contributor_stats[uid]['hours_logged'] += _task_hours(task, worked)

    contributors = []
    for uid, stats in contributor_stats.items():
        completion_rate = round((stats['tasks_completed'] / stats['tasks_assigned'] * 100)
                                if stats['tasks_assigned'] > 0 else 0)
        avg_hours = round(stats['hours_logged'] / stats['tasks_completed'], 1) \
            if stats['tasks_completed'] > 0 else 0
        contributors.append({
            'name': stats['name'], 'email': stats['email'], 'initials': stats['initials'],
            'tasks_assigned': stats['tasks_assigned'], 'tasks_completed': stats['tasks_completed'],
            'completion_rate': completion_rate, 'hours_logged': round(stats['hours_logged'], 1),
            'avg_hours_per_task': avg_hours,
        })
    contributors.sort(key=lambda x: x['tasks_completed'], reverse=True)

    tasks_detail = []
    for task in all_tasks:
        # (the last time it was done means nothing if it was reopened since)
        completed_at = ((task.completed_at or task.archived_at or task_completions.get(task.id))
                        if task.status == TaskStatus.done else None)
        duration_days = (completed_at - task.created_at).days if completed_at else None
        tasks_detail.append({
            'id': task.id, 'title': task.title, 'description': task.description,
            'status': task.status.value if hasattr(task.status, 'value') else task.status,
            'priority': task.priority.value if hasattr(task.priority, 'value') else task.priority,
            'assignees': [a[0] for a in assignees_map.get(task.id, [])],
            'created_at': task.created_at, 'completed_at': completed_at,
            'time_spent_hours': _task_hours(task, worked) or None,
            'duration_days': duration_days,
        })
    tasks_detail.sort(key=lambda x: x['created_at'], reverse=True)

    activities = []
    if task_ids:
        history_entries = (await db.execute(
            select(TaskHistory, User, Task)
            .join(User, User.id == TaskHistory.editor_id)
            .join(Task, Task.id == TaskHistory.task_id)
            .where(TaskHistory.task_id.in_(task_ids), TaskHistory.created_at >= start_dt,
                   TaskHistory.created_at < end_dt)
            .order_by(TaskHistory.created_at.desc())
            .limit(50)
        )).all()
        for history, editor, task in history_entries:
            action = 'updated'
            description = f'updated {history.field}'
            if history.field == 'created':
                action, description = 'created', 'created the task'
            elif history.field == 'status':
                action = 'status_changed'
                if history.new_value == 'done':
                    action, description = 'completed', 'completed the task'
                else:
                    description = f'changed status from {history.old_value or "none"} to {history.new_value}'
            elif history.field == 'priority':
                description = f'changed priority from {history.old_value or "none"} to {history.new_value}'
            elif history.field == 'assignee':
                description = 'changed assignee'
            activities.append({
                'action': action, 'description': description,
                'user_name': editor.shown_name or editor.username,
                'task_title': task.title, 'created_at': history.created_at,
            })

    total_tasks = len(all_tasks)
    completed_tasks = sum(1 for t in all_tasks if t.status == TaskStatus.done)
    summary = {
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'completion_rate': round((completed_tasks / total_tasks * 100) if total_tasks > 0 else 0),
        'total_hours': round(sum(_task_hours(t, worked) for t in all_tasks), 1),
        'active_contributors': len(contributor_stats),
    }
    return {
        'start_date': start_date, 'end_date': end_date, 'summary': summary,
        'contributors': contributors, 'tasks_detail': tasks_detail, 'activities': activities,
    }


def _excel_filename(stem: str) -> str:
    """The same filename sanitising the downloads have always used."""
    return re.sub(r'[^A-Za-z0-9_-]+', '_', stem).strip('_') or 'export'


# ---------------------------------------------------------------------------
# Project report workbook (Summary / Contributors / Tasks / Recent activity)
# ---------------------------------------------------------------------------

async def build_project_report_excel(db: AsyncSession, project: Project, start_date: Optional[str],
                                     end_date: Optional[str], exported_by_label: str,
                                     now: Optional[datetime] = None) -> tuple[str, bytes]:
    """Build the project report workbook, identical to the download button.

    exported_by_label is the "Exported by ..." line; the weekly scheduler passes
    a fixed label so its workbook has the same structure as a manual download.
    """
    zone = workspace_zone(await db.get(Workspace, project.workspace_id))
    report = await _project_report_data(db, project, start_date, end_date)
    generated = now or local_now(zone)
    summary = report['summary']
    stem = f"project_report_{project.name}_{report['start_date']}_{report['end_date']}"
    export = Export(
        title=f'Project Report: {project.name}',
        filename=stem,
        lines=[f"Period: {report['start_date']} to {report['end_date']}", exported_by_label],
        generated_at=generated,
        summary=[('Total tasks', summary['total_tasks']), ('Completed tasks', summary['completed_tasks']),
                 ('Completion rate', f"{summary['completion_rate']}%"), ('Hours logged', summary['total_hours']),
                 ('Active contributors', summary['active_contributors'])],
        tables=[
            ExportTable('Contributors', ['Name', 'Email', 'Tasks assigned', 'Tasks completed', 'Completion %',
                                         'Hours logged', 'Avg hours per task'],
                        [[c['name'], c['email'], c['tasks_assigned'], c['tasks_completed'],
                          f"{c['completion_rate']}%", c['hours_logged'], c['avg_hours_per_task']]
                         for c in report['contributors']]),
            ExportTable('Tasks', ['Task', 'Status', 'Priority', 'Assigned to', 'Created', 'Completed',
                                  'Days to complete', 'Hours'],
                        [[t['title'], TASK_STATUS_LABELS.get(t['status'], t['status']),
                          (t['priority'] or '').title(), ', '.join(t['assignees']),
                          to_local(t['created_at'], zone), to_local(t['completed_at'], zone),
                          t['duration_days'], t['time_spent_hours']] for t in report['tasks_detail']],
                        [4, 1.3, 1.1, 2.6, 1.6, 1.6, 1.2, 0.9]),
            ExportTable('Recent activity', ['When', 'Who', 'Task', 'What'],
                        [[to_local(a['created_at'], zone), a['user_name'], a['task_title'], a['description']]
                         for a in report['activities']], [1.5, 1.8, 3, 3.5]),
        ],
    )
    return f'{_excel_filename(stem)}.xlsx', to_excel(export)


# ---------------------------------------------------------------------------
# Ticket report workbook (7 sheets)
# ---------------------------------------------------------------------------

def _ticket_finished(ticket) -> bool:
    return ticket.status in DONE_TICKET_STATUSES


def _ticket_finished_at(ticket):
    """When a finished ticket was finished: some are resolved and never closed."""
    return ticket.closed_at or ticket.resolved_at


async def build_ticket_report_excel(db: AsyncSession, workspace: Optional[Workspace],
                                    start_date: Optional[str], end_date: Optional[str],
                                    user_id: Optional[int] = None, project_id: Optional[int] = None,
                                    workspace_id: Optional[int] = None) -> tuple[str, bytes]:
    """Build the workspace ticket report workbook, identical to the download.

    Filtering by user/project is preserved exactly: the user filter applies to
    the main ticket list and to comments/history, while the current-open list
    uses assigned/created instead of assigned/closed (as the report page does).
    workspace may be None when the workspace lookup fails; pass its id explicitly
    (as the download route does) so the report still covers the right company.
    """
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    zone = workspace_zone(workspace)
    first_day, last_day, start_dt, end_dt = _report_days(start_date, end_date, zone)
    workspace_id = workspace.id if workspace is not None else workspace_id
    if workspace_id is None:
        raise ValueError('A workspace id is required to build the ticket report')

    ticket_query = select(Ticket).where(
        Ticket.workspace_id == workspace_id,
        Ticket.created_at >= start_dt,
        Ticket.created_at < end_dt,
    )
    if user_id:
        ticket_query = ticket_query.where(
            (Ticket.assigned_to_id == user_id) | (Ticket.closed_by_id == user_id))
    if project_id:
        ticket_query = ticket_query.where(Ticket.related_project_id == project_id)
    all_tickets = (await db.execute(ticket_query.order_by(Ticket.created_at.desc()))).scalars().all()
    ticket_ids = [t.id for t in all_tickets]

    # ALL currently open tickets, regardless of date range (as HTML and PDF).
    current_open_query = select(Ticket).where(
        Ticket.workspace_id == workspace_id,
        Ticket.status.in_(['open', 'in_progress', 'waiting']),
        Ticket.is_archived == False,  # noqa: E712
    )
    if user_id:
        current_open_query = current_open_query.where(
            (Ticket.assigned_to_id == user_id) | (Ticket.created_by_id == user_id))
    if project_id:
        current_open_query = current_open_query.where(Ticket.related_project_id == project_id)
    all_current_open_tickets = (
        await db.execute(current_open_query.order_by(Ticket.created_at.desc()))).scalars().all()

    all_comments = []
    if ticket_ids:
        comment_query = (
            select(TicketComment)
            .join(Ticket, TicketComment.ticket_id == Ticket.id)
            .where(Ticket.workspace_id == workspace_id, Ticket.created_at >= start_dt,
                   Ticket.created_at < end_dt)
        )
        if user_id:
            comment_query = comment_query.where(TicketComment.user_id == user_id)
        if project_id:
            comment_query = comment_query.where(Ticket.related_project_id == project_id)
        all_comments = (await db.execute(
            comment_query.order_by(TicketComment.created_at.desc()))).scalars().all()

    all_history = []
    if ticket_ids:
        history_query = (
            select(TicketHistory)
            .join(Ticket, TicketHistory.ticket_id == Ticket.id)
            .where(Ticket.workspace_id == workspace_id, Ticket.created_at >= start_dt,
                   Ticket.created_at < end_dt)
        )
        if user_id:
            history_query = history_query.where(TicketHistory.user_id == user_id)
        if project_id:
            history_query = history_query.where(Ticket.related_project_id == project_id)
        all_history = (await db.execute(
            history_query.order_by(TicketHistory.created_at.desc()))).scalars().all()

    users_result = (await db.execute(select(User).where(User.workspace_id == workspace_id))).scalars().all()
    users_dict = {u.id: u for u in users_result}
    projects_result = (await db.execute(select(Project).where(Project.workspace_id == workspace_id))).scalars().all()
    projects_dict = {p.id: p for p in projects_result}

    biz_start = workspace.business_hours_start if workspace and hasattr(workspace, 'business_hours_start') else "07:30"
    biz_end = workspace.business_hours_end if workspace and hasattr(workspace, 'business_hours_end') else "16:00"
    biz_exclude_weekends = (
        workspace.business_hours_exclude_weekends
        if workspace and hasattr(workspace, 'business_hours_exclude_weekends') else True)

    agent_stats = {
        usr.id: {'name': usr.shown_name or usr.username, 'email': usr.email, 'tickets_assigned': 0,
                 'tickets_closed': 0, 'comments_made': 0, 'resolution_times': []}
        for usr in users_result
    }
    for ticket in all_tickets:
        if ticket.assigned_to_id and ticket.assigned_to_id in agent_stats:
            agent_stats[ticket.assigned_to_id]['tickets_assigned'] += 1
        if ticket.closed_by_id and ticket.closed_by_id in agent_stats:
            agent_stats[ticket.closed_by_id]['tickets_closed'] += 1
            if _ticket_finished_at(ticket):
                agent_stats[ticket.closed_by_id]['resolution_times'].append(calculate_business_hours(
                    to_local(ticket.created_at, zone), to_local(_ticket_finished_at(ticket), zone),
                    biz_start, biz_end, biz_exclude_weekends))
    for comment in all_comments:
        if comment.user_id and comment.user_id in agent_stats:
            agent_stats[comment.user_id]['comments_made'] += 1

    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="6366F1", end_color="6366F1", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'),
                         top=Side(style='thin'), bottom=Side(style='thin'))

    def style_header_row(ws, row_num, col_count):
        for col in range(1, col_count + 1):
            cell = ws.cell(row=row_num, column=col)
            cell.font, cell.fill = header_font, header_fill
            cell.alignment, cell.border = header_alignment, thin_border

    def auto_width(ws):
        for column_cells in ws.columns:
            max_length = 0
            column = column_cells[0].column_letter
            for cell in column_cells:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except Exception:
                    pass
            ws.column_dimensions[column].width = min(max_length + 2, 60)

    def safe_excel_value(value):
        if isinstance(value, str):
            return ILLEGAL_CHARACTERS_RE.sub('', value)
        return value

    def append_row(ws, row):
        ws.append([safe_excel_value(value) for value in row])

    def resolution_hours(ticket):
        if not (_ticket_finished(ticket) and _ticket_finished_at(ticket)):
            return ''
        return calculate_business_hours(
            to_local(ticket.created_at, zone), to_local(_ticket_finished_at(ticket), zone),
            biz_start, biz_end, biz_exclude_weekends)

    def client_name(ticket):
        return ' '.join(filter(None, [ticket.guest_name, ticket.guest_surname])) or ticket.guest_company or ''

    # Sheet 1: Summary
    ws_summary = wb.active
    ws_summary.title = "Summary"
    ws_summary.append(["Ticket Report"])
    ws_summary.append([f"Period: {first_day.strftime('%Y-%m-%d')} to {last_day.strftime('%Y-%m-%d')}"])
    ws_summary.append([f"Generated: {local_now(zone).strftime('%Y-%m-%d %H:%M')}"])
    if user_id:
        filter_user = users_dict.get(user_id)
        append_row(ws_summary, [f"Filtered by User: "
                                f"{filter_user.shown_name or filter_user.username if filter_user else 'Unknown'}"])
    if project_id:
        filter_project = projects_dict.get(project_id)
        append_row(ws_summary, [f"Filtered by Project: {filter_project.name if filter_project else 'Unknown'}"])
    append_row(ws_summary, [])

    total_tickets = len(all_tickets)
    closed_count = sum(1 for t in all_tickets if _ticket_finished(t))
    current_open_count = len(all_current_open_tickets)
    resolution_rate = round((closed_count / total_tickets * 100) if total_tickets > 0 else 0)
    ws_summary.append(["Metric", "Value"])
    style_header_row(ws_summary, ws_summary.max_row, 2)
    ws_summary.append(["Total Tickets (in period)", total_tickets])
    ws_summary.append(["Resolved or Closed", closed_count])
    ws_summary.append(["Currently Open Tickets", current_open_count])
    ws_summary.append(["Resolution Rate", f"{resolution_rate}%"])
    ws_summary.append(["Total Comments", len(all_comments)])
    ws_summary.append(["Business Hours", f"{biz_start} - {biz_end}"])
    auto_width(ws_summary)

    # Sheet 2: All Tickets
    ws_tickets = wb.create_sheet("All Tickets")
    ws_tickets.append(["Ticket #", "Subject", "Client", "Status", "Priority", "Category", "Assigned To",
                       "Created", "Closed", "Resolution (hrs)", "Project"])
    style_header_row(ws_tickets, 1, 11)
    for ticket in all_tickets:
        assigned_to = users_dict.get(ticket.assigned_to_id)
        project = projects_dict.get(ticket.related_project_id)
        append_row(ws_tickets, [
            ticket.ticket_number, ticket.subject, client_name(ticket), ticket.status, ticket.priority,
            ticket.category or '', (assigned_to.shown_name or assigned_to.username) if assigned_to else '',
            format_local(ticket.created_at, zone, '%Y-%m-%d %H:%M'),
            format_local(_ticket_finished_at(ticket), zone, '%Y-%m-%d %H:%M')
            if _ticket_finished_at(ticket) else '',
            resolution_hours(ticket), project.name if project else '',
        ])
    auto_width(ws_tickets)

    # Sheet 3: Agent Performance
    ws_agents = wb.create_sheet("Agent Performance")
    ws_agents.append(["Agent", "Email", "Tickets Assigned", "Tickets Closed", "Comments Made",
                      "Avg Resolution (hrs)"])
    style_header_row(ws_agents, 1, 6)
    for uid, stats in sorted(agent_stats.items(), key=lambda x: x[1]['tickets_closed'], reverse=True):
        if stats['tickets_assigned'] > 0 or stats['tickets_closed'] > 0 or stats['comments_made'] > 0:
            avg_resolution = (round(sum(stats['resolution_times']) / len(stats['resolution_times']), 1)
                              if stats['resolution_times'] else 0)
            append_row(ws_agents, [stats['name'], stats['email'], stats['tickets_assigned'],
                                   stats['tickets_closed'], stats['comments_made'], avg_resolution])
    auto_width(ws_agents)

    # Sheet 4: Closed Tickets
    ws_closed = wb.create_sheet("Closed Tickets")
    ws_closed.append(["Ticket #", "Subject", "Client", "Priority", "Category", "Closed By", "Created", "Closed",
                      "Resolution (hrs)", "Billable Traveling", "Billable Labour On-site",
                      "Billable Remote Labour", "Billable Equipment", "Non-Billable Traveling",
                      "Non-Billable Labour On-site", "Non-Billable Remote Labour", "Non-Billable Equipment",
                      "Closing Notes"])
    style_header_row(ws_closed, 1, 18)
    for ticket in all_tickets:
        if _ticket_finished(ticket) and _ticket_finished_at(ticket):
            closed_by = users_dict.get(ticket.closed_by_id)
            append_row(ws_closed, [
                ticket.ticket_number, ticket.subject, client_name(ticket), ticket.priority,
                ticket.category or '', (closed_by.shown_name or closed_by.username) if closed_by else 'Unknown',
                format_local(ticket.created_at, zone, '%Y-%m-%d %H:%M'),
                format_local(_ticket_finished_at(ticket), zone, '%Y-%m-%d %H:%M'),
                resolution_hours(ticket),
                ticket.billable_traveling or '', ticket.billable_labour_onsite or '',
                ticket.billable_remote_labour or '', ticket.billable_equipment_used or '',
                ticket.non_billable_traveling or '', ticket.non_billable_labour_onsite or '',
                ticket.non_billable_remote_labour or '', ticket.non_billable_equipment_used or '',
                ticket.closing_notes or '',
            ])
    auto_width(ws_closed)

    # Sheet 5: Open Tickets (ALL currently open, regardless of date range)
    ws_open = wb.create_sheet("Open Tickets")
    ws_open.append(["Ticket #", "Subject", "Status", "Priority", "Category", "Assigned To", "Created",
                    "Days Open", "Project"])
    style_header_row(ws_open, 1, 9)
    for ticket in all_current_open_tickets:
        assigned_to = users_dict.get(ticket.assigned_to_id)
        project = projects_dict.get(ticket.related_project_id)
        days_open = (datetime.utcnow() - ticket.created_at).days
        append_row(ws_open, [
            ticket.ticket_number, ticket.subject, ticket.status.replace('_', ' ').title(), ticket.priority,
            ticket.category or '', (assigned_to.shown_name or assigned_to.username) if assigned_to else 'Unassigned',
            format_local(ticket.created_at, zone, '%Y-%m-%d %H:%M'), days_open,
            project.name if project else '',
        ])
    auto_width(ws_open)

    # Sheet 6: Comments
    ws_comments = wb.create_sheet("Comments")
    ws_comments.append(["Date", "Ticket #", "Author", "Comment", "Internal"])
    style_header_row(ws_comments, 1, 5)
    tickets_dict = {t.id: t for t in all_tickets}
    for comment in all_comments:
        author = users_dict.get(comment.user_id)
        ticket = tickets_dict.get(comment.ticket_id)
        append_row(ws_comments, [
            format_local(comment.created_at, zone, '%Y-%m-%d %H:%M'),
            ticket.ticket_number if ticket else '',
            (author.shown_name or author.username) if author else 'Guest',
            comment.content, 'Yes' if comment.is_internal else 'No',
        ])
    auto_width(ws_comments)

    # Sheet 7: Activity History
    ws_history = wb.create_sheet("Activity History")
    ws_history.append(["Date", "Ticket #", "User", "Action", "Details"])
    style_header_row(ws_history, 1, 5)
    for history in all_history:
        usr = users_dict.get(history.user_id)
        ticket = tickets_dict.get(history.ticket_id)
        append_row(ws_history, [
            format_local(history.created_at, zone, '%Y-%m-%d %H:%M'),
            ticket.ticket_number if ticket else '',
            (usr.shown_name or usr.username) if usr else 'System',
            history.action.replace('_', ' ').title(), history.new_value or '',
        ])
    auto_width(ws_history)

    buffer = io.BytesIO()
    wb.save(buffer)
    filename = f"ticket_report_{first_day.strftime('%Y%m%d')}_{last_day.strftime('%Y%m%d')}.xlsx"
    return filename, buffer.getvalue()
