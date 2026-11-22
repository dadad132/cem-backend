"""Email-settings-driven weekly workspace report: it goes on Monday from 08:00
(the company's own time), covers Monday to Friday of the week before, attaches
the very same Excel reports the download buttons produce, goes once per
workspace per week, and never comes back in as a ticket."""
from __future__ import annotations

import asyncio
import io
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from email import message_from_string
from types import SimpleNamespace

import pytest
import pytest_asyncio
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import project_weekly_report as report
from app.models.assignment import Assignment
from app.models.email_settings import EmailSettings
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.project_report import ProjectWeeklyReport, WorkspaceWeeklyReport
from app.models.task import Task
from app.models.task_extensions import TaskTimeEntry, TimeLog
from app.models.ticket import Ticket, TicketComment, TicketHistory
from app.models.user import User
from app.models.workspace import Workspace

WEEK = date(2026, 11, 9)                   # Monday; the week runs to Friday 13 Nov
MONDAY_8 = datetime(2026, 11, 16, 8, 5)    # The next Monday, just after 08:00 (UTC)
PROJECT_SHEETS = ['Summary', 'Contributors', 'Tasks', 'Recent activity']
TICKET_SHEETS = ['Summary', 'All Tickets', 'Agent Performance', 'Closed Tickets',
                 'Open Tickets', 'Comments', 'Activity History']


def at(day, hour=10, minute=0):
    return datetime(2026, 11, day, hour, minute)


def request_as(user_id):
    return SimpleNamespace(session={'user_id': user_id})


@pytest_asyncio.fixture
async def db(tmp_path):
    import app.models  # noqa: F401
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session = AsyncSession(engine, expire_on_commit=False)
    yield session
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def acme(db):
    ws = Workspace(name="Acme", timezone="UTC")
    db.add(ws)
    await db.flush()
    bob = User(username="bob", full_name="Bob Builder", hashed_password="x",
               email="bob@acme.test", workspace_id=ws.id, is_admin=True)
    db.add(bob)
    await db.flush()
    db.add(EmailSettings(
        workspace_id=ws.id, smtp_host="smtp.test", smtp_username="u", smtp_password="p",
        smtp_from_email="helpdesk@acme.test",
        weekly_report_enabled=True, weekly_report_recipients="team@acme.test; boss@acme.test",
        weekly_report_include_projects=True, weekly_report_include_tickets=True,
    ))
    linked = Project(name="Office move", workspace_id=ws.id, owner_id=bob.id,
                     support_email="move@acme.test; boss@acme.test")
    no_email = Project(name="No email", workspace_id=ws.id, owner_id=bob.id)
    archived = Project(name="Old", workspace_id=ws.id, owner_id=bob.id,
                       support_email="old@acme.test", is_archived=True)
    db.add_all([linked, no_email, archived])
    await db.flush()
    t = dict(
        wed=Task(title="Pack boxes", project_id=linked.id, creator_id=bob.id, status=TaskStatus.done,
                 is_archived=True, archived_at=at(11), created_at=at(10)),
        before=Task(title="Order van", project_id=linked.id, creator_id=bob.id, status=TaskStatus.done,
                    is_archived=True, archived_at=at(7), created_at=at(2)),
        late=Task(title="Label desks", project_id=linked.id, creator_id=bob.id, due_date=date(2026, 11, 10),
                  created_at=at(2)),
        stuck=Task(title="Order cables", project_id=linked.id, creator_id=bob.id,
                   status=TaskStatus.blocked, created_at=at(2)),
    )
    db.add_all(t.values())
    await db.flush()
    closed = Ticket(ticket_number="TKT-2026-00001", subject="Move printer", workspace_id=ws.id,
                    related_project_id=linked.id, status="closed", created_at=at(10), closed_at=at(12),
                    closed_by_id=bob.id, assigned_to_id=bob.id, billable_traveling="1h",
                    closing_notes="Printer moved")
    open_ticket = Ticket(ticket_number="TKT-2026-00002", subject="Broken lift", workspace_id=ws.id,
                         status="open", created_at=at(11))
    db.add_all([closed, open_ticket])
    await db.flush()
    db.add_all([
        Assignment(task_id=t["wed"].id, assignee_id=bob.id),
        TaskTimeEntry(task_id=t["wed"].id, user_id=bob.id, started_at=at(10, 9), ended_at=at(10, 11)),
        # Friday 23:00 to Saturday 02:00: only the hour before midnight is this week's
        TaskTimeEntry(task_id=t["late"].id, user_id=bob.id, started_at=at(13, 23), ended_at=at(14, 2)),
        TimeLog(task_id=t["stuck"].id, user_id=bob.id, hours=1.5, logged_at=at(12)),
        TicketComment(ticket_id=closed.id, user_id=bob.id, content="All done"),
        TicketHistory(ticket_id=closed.id, user_id=bob.id, action="status_changed", new_value="closed",
                      created_at=at(12)),
    ])
    await db.commit()
    return ws, linked, no_email, archived, bob


# The old per-project figures stay available for the Board View page.
@pytest.mark.asyncio
async def test_the_weeks_figures(db, acme):
    ws, linked, _, _, _ = acme
    r = await report.build_report(db, linked, ws, WEEK, MONDAY_8)
    assert [f[0] for f in r["finished"]] == ["Pack boxes"]
    assert r["finished"][0][1] == "Bob Builder"
    assert r["added"] == 1
    assert r["time"] == [("Bob Builder", 2 * 3600 + 3600 + 90 * 60)]
    assert [o[0] for o in r["overdue"]] == ["Label desks"]
    assert [b[0] for b in r["blocked"]] == ["Order cables"]
    assert (r["tickets_opened"], r["tickets_closed"], r["tickets_open"]) == (1, 1, 0)
    subject, html = report.render_report(r)
    assert subject == "Weekly report: Office move (09 Nov – 13 Nov 2026)"
    assert "Pack boxes" in html and "4h 30m" in html


@pytest.mark.asyncio
async def test_only_settings_recipients_when_enabled(db, acme):
    """No setting, no mail. support_email is never used again."""
    ws, _, _, _, _ = acme
    settings = (await db.execute(select(EmailSettings))).scalar_one()
    mails = []
    settings.weekly_report_enabled = False
    await db.commit()
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 0
    assert mails == []

    settings.weekly_report_enabled = True
    settings.weekly_report_recipients = None
    await db.commit()
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 0
    assert mails == []

    settings.weekly_report_recipients = "team@acme.test, boss@acme.test"
    await db.commit()
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 1
    assert mails[0][1] == ["team@acme.test", "boss@acme.test"]
    assert "move@acme.test" not in mails[0][1]


@pytest.mark.asyncio
async def test_all_active_projects_and_tickets_are_attached(db, acme):
    ws, linked, no_email, archived, _ = acme
    mails = []
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 1
    smtp, to, subject, html, attachments = mails[0]
    names = [name for name, _ in attachments]
    assert to == ["team@acme.test", "boss@acme.test"]
    assert any(name.startswith('ticket_report_20261109_20261113') for name in names)
    assert sum(1 for name in names if name.startswith('project_report_')) == 2   # not the archived one
    assert not any('Old' in name for name in names)
    assert any('Office_move' in name for name in names)
    assert any('No_email' in name for name in names)
    assert 'Weekly report' in subject and '09 Nov' in subject
    assert 'Office move' in html and 'No email' in html and 'Old' not in html
    for name, content in attachments:
        assert name.endswith('.xlsx')
        workbook = load_workbook(io.BytesIO(content))
        if name.startswith('ticket_report_'):
            assert workbook.sheetnames == TICKET_SHEETS
        else:
            assert workbook.sheetnames == PROJECT_SHEETS


@pytest.mark.asyncio
async def test_attachments_are_identical_to_download_endpoints(db, acme, monkeypatch):
    """The button and the e-mail share one builder, byte for byte."""
    from app.core import report_excel
    from app.web import routes as web

    ws, linked, _, _, bob = acme
    fixed = datetime(2026, 11, 16, 8, 5)
    monkeypatch.setattr(report_excel, 'local_now', lambda zone=None: fixed)
    monkeypatch.setattr(web, 'local_now', lambda zone=None: fixed)

    label = "Exported by Bob Builder on 2026-11-16 08:05"
    name, content = await report_excel.build_project_report_excel(db, linked, None, None, label, fixed)
    response = await web.web_project_report_excel(request_as(bob.id), linked.id, None, None, db)
    assert response.body == content
    assert name in response.headers['content-disposition']
    assert load_workbook(io.BytesIO(content)).sheetnames == PROJECT_SHEETS

    name, content = await report_excel.build_ticket_report_excel(db, ws, None, None)
    response = await web.web_tickets_report_excel(request_as(bob.id), None, None, None, None, db)
    streamed = b''.join([chunk async for chunk in response.body_iterator])
    assert streamed == content
    assert name in response.headers['content-disposition']
    assert load_workbook(io.BytesIO(content)).sheetnames == TICKET_SHEETS


@pytest.mark.asyncio
async def test_monday_eight_local_and_once_per_workspace(db, acme):
    _, _, _, _, _ = acme
    mails = []

    def send(*args):
        mails.append(args)

    assert await report.run_weekly_reports(db, datetime(2026, 11, 16, 7, 59), send) == 0
    assert await report.run_weekly_reports(db, MONDAY_8, send) == 1
    assert len(mails) == 1
    # Later checks that Monday, and the Tuesday, send nothing more
    assert await report.run_weekly_reports(db, datetime(2026, 11, 16, 11), send) == 0
    assert await report.run_weekly_reports(db, datetime(2026, 11, 17, 8, 5), send) == 0
    sent = (await db.execute(select(WorkspaceWeeklyReport))).scalars().all()
    assert [(s.workspace_id, s.week_start, s.sent_to) for s in sent] == [
        (1, WEEK, "team@acme.test, boss@acme.test")]


@pytest.mark.asyncio
async def test_eight_oclock_in_the_companys_own_time(db, acme):
    ws, _, _, _, _ = acme
    ws.timezone = "Africa/Johannesburg"      # 08:00 there is 06:00 UTC
    await db.commit()
    mails = []
    assert await report.run_weekly_reports(db, datetime(2026, 11, 16, 5, 55), lambda *a: mails.append(a)) == 0
    assert await report.run_weekly_reports(db, datetime(2026, 11, 16, 6, 5), lambda *a: mails.append(a)) == 1


@pytest.mark.asyncio
async def test_a_failed_send_is_tried_again(db, acme):
    def broken(*_):
        raise OSError("mail server down")

    assert await report.run_weekly_reports(db, MONDAY_8, broken) == 0
    assert (await db.execute(select(WorkspaceWeeklyReport))).scalars().all() == []
    assert await report.run_weekly_reports(db, datetime(2026, 11, 16, 8, 10), lambda *a: None) == 1


@pytest.mark.asyncio
async def test_both_report_types_can_be_turned_off_independently(db, acme):
    ws, _, _, _, _ = acme
    settings = (await db.execute(select(EmailSettings))).scalar_one()
    mails = []
    settings.weekly_report_include_projects = False
    settings.weekly_report_include_tickets = False
    await db.commit()
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 0

    settings.weekly_report_include_tickets = True
    await db.commit()
    assert await report.run_weekly_reports(db, datetime(2026, 11, 16, 8, 10), _send(mails)) == 1
    assert len(mails[0][4]) == 1
    assert mails[0][4][0][0].startswith('ticket_report_')


def test_recipients_reject_header_injection_and_dupes():
    raw = "a@x.test, b@x.test;\r\nBcc: evil@x.test a@x.test\n c@x.test not-an-email"
    assert report.recipients(raw) == ["a@x.test", "b@x.test", "c@x.test"]
    assert all('\r' not in a and '\n' not in a for a in report.recipients(raw))
    assert report.recipients(None) == []


def test_smtp_message_has_report_header_and_safe_attachments(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent['host'] = host

        def starttls(self):
            sent['tls'] = True

        def login(self, username, password):
            sent['login'] = (username, password)

        def send_message(self, msg):
            sent['msg'] = msg

        def quit(self):
            sent['quit'] = True

    monkeypatch.setattr(report.smtplib, 'SMTP', FakeSMTP)
    smtp = dict(host='smtp.test', port=587, username='u', password='p',
                from_email='helpdesk@acme.test', from_name='Helpdesk', use_tls=True)
    report._smtp_send(smtp, ['a@x.test', 'b@x.test'], 'Weekly report', '<p>Hi</p>',
                      [('bad\r\nname.xlsx', b'PK\x03\x04')])
    msg = sent['msg']
    assert msg[report.REPORT_HEADER] == 'weekly-project'
    assert msg['Auto-Submitted'] == 'auto-generated'
    assert msg['To'] == 'a@x.test, b@x.test'
    assert msg.get_content_type() == 'multipart/mixed'
    parts = msg.get_payload()
    assert parts[0].get_content_type() == 'text/html'
    assert parts[1].get_content_type() == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    disposition = parts[1]['Content-Disposition']
    assert 'attachment' in disposition and '\r' not in disposition and '\n' not in disposition
    assert parts[1].get_payload(decode=True) == b'PK\x03\x04'
    # Backward-compatible callers with no attachments still work
    report._smtp_send(smtp, ['a@x.test'], 'Weekly report', '<p>Hi</p>')
    assert message_from_string(sent['msg'].as_string()).get_payload()[0].get_content_type() == 'text/html'


@pytest.mark.asyncio
async def test_send_weekly_report_now(db, acme):
    ws, _, _, _, _ = acme
    mails = []
    result = await report.send_weekly_report_now(
        db, ws, (await db.execute(select(EmailSettings))).scalar_one(), now=MONDAY_8, send=_send(mails))
    assert len(mails) == 1
    assert result['recipients'] == ['team@acme.test', 'boss@acme.test']
    assert result['period'] == '2026-11-09 to 2026-11-13'
    assert len(result['attachments']) == 3
    assert (await db.execute(select(WorkspaceWeeklyReport))).scalars().all() == []  # no dedup row


@pytest.mark.asyncio
async def test_send_weekly_report_now_validates_and_handles_weekends(db, acme, monkeypatch):
    ws, _, _, _, _ = acme
    settings = (await db.execute(select(EmailSettings))).scalar_one()
    settings.weekly_report_recipients = None
    with pytest.raises(ValueError, match='recipient'):
        await report.send_weekly_report_now(db, ws, settings)
    settings.weekly_report_recipients = 'team@acme.test'
    settings.weekly_report_enabled = False   # a manual send ignores the enable switch
    with monkeypatch.context() as m:
        m.setattr(report, '_smtp_for', lambda *a, **k: _noop(None))
        with pytest.raises(ValueError, match='SMTP|[Oo]utgoing'):
            await report.send_weekly_report_now(db, ws, settings)
    settings.weekly_report_include_projects = False
    settings.weekly_report_include_tickets = False
    with pytest.raises(ValueError, match='report'):
        await report.send_weekly_report_now(db, ws, settings)

    # Saturday and Sunday report on Monday-Friday just gone, and do not write a dedup row.
    settings.weekly_report_include_tickets = True
    saturday = datetime(2026, 11, 21, 9, 0)
    mails = []
    assert (await report.send_weekly_report_now(
        db, ws, settings, now=saturday, send=_send(mails)))['period'] == '2026-11-16 to 2026-11-20'
    sunday = datetime(2026, 11, 22, 9, 0)
    assert (await report.send_weekly_report_now(
        db, ws, settings, now=sunday, send=_send(mails)))['period'] == '2026-11-16 to 2026-11-20'
    assert (await db.execute(select(WorkspaceWeeklyReport))).scalars().all() == []


@pytest.mark.asyncio
async def test_no_smtp_configuration_is_skipped(db, acme, monkeypatch):
    ws, _, _, _, _ = acme
    monkeypatch.setattr(report, '_smtp_for', lambda *a, **k: _noop(None))
    mails = []
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 0
    assert mails == []


def test_sqlite_weekly_column_migration_is_idempotent(tmp_path):
    from app.core.database import _ensure_email_settings_weekly_columns

    path = tmp_path / 'old.db'
    conn = sqlite3.connect(str(path))
    conn.execute('CREATE TABLE emailsettings (id INTEGER PRIMARY KEY, workspace_id INTEGER, '
                 'smtp_host VARCHAR, smtp_username VARCHAR, smtp_password VARCHAR, smtp_from_email VARCHAR)')
    conn.execute("INSERT INTO emailsettings (workspace_id, smtp_host) VALUES (7, 'smtp.old')")
    conn.commit()
    conn.close()

    _ensure_email_settings_weekly_columns(path)
    _ensure_email_settings_weekly_columns(path)     # a second run changes nothing
    conn = sqlite3.connect(str(path))
    cols = {row[1]: row for row in conn.execute('PRAGMA table_info("emailsettings")')}
    assert set(('weekly_report_enabled', 'weekly_report_recipients',
                'weekly_report_include_projects', 'weekly_report_include_tickets')) <= set(cols)
    assert cols['weekly_report_enabled'][3] == 1 and cols['weekly_report_enabled'][4] == '0'
    assert cols['weekly_report_recipients'][3] == 0
    assert cols['weekly_report_include_projects'][3] == 1 and cols['weekly_report_include_projects'][4] == '1'
    assert cols['weekly_report_include_tickets'][3] == 1 and cols['weekly_report_include_tickets'][4] == '1'
    assert conn.execute('SELECT smtp_host, weekly_report_enabled, weekly_report_include_projects '
                        'FROM emailsettings').fetchone() == ('smtp.old', 0, 1)
    conn.close()

    # Missing file and an unrelated table are harmless too.
    _ensure_email_settings_weekly_columns(tmp_path / 'absent.db')


def test_the_report_never_becomes_a_ticket():
    """The linked address is usually a mailbox the helpdesk reads; both
    import paths skip the report's header."""
    source = Path("app/core/email_to_ticket_v2.py").read_text()
    assert source.count("msg.get('X-CEM-Report')") == 2
    assert report.REPORT_HEADER == "X-CEM-Report"


@pytest.mark.asyncio
async def test_one_broken_workbook_does_not_lose_the_report(db, acme, monkeypatch):
    """A bad project file cannot stop the ticket report and the other projects."""
    _, _, no_email, _, _ = acme
    original = report.build_project_report_excel

    async def flaky(session, project, *args, **kwargs):
        if project.id == no_email.id:
            raise RuntimeError('bad workbook')
        return await original(session, project, *args, **kwargs)

    monkeypatch.setattr(report, 'build_project_report_excel', flaky)
    mails = []
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 1
    names = [name for name, _ in mails[0][4]]
    assert any(name.startswith('ticket_report_') for name in names)
    assert sum(name.startswith('project_report_') for name in names) == 1


@pytest.mark.asyncio
async def test_no_workbooks_means_no_send_and_no_claim(db, acme, monkeypatch):
    """A mail with no Excel files must be retried, not recorded as sent."""
    async def broken(*args, **kwargs):
        raise RuntimeError('workbook builder unavailable')

    monkeypatch.setattr(report, 'build_ticket_report_excel', broken)
    monkeypatch.setattr(report, 'build_project_report_excel', broken)
    mails = []
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 0
    assert mails == []
    assert (await db.execute(select(WorkspaceWeeklyReport))).scalars().all() == []


@pytest.mark.asyncio
async def test_cancellation_releases_the_claim(db, acme):
    """Shutdown mid-send must not permanently block that week's report."""
    def cancel(*args):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await report.run_weekly_reports(db, MONDAY_8, cancel)
    assert (await db.execute(select(WorkspaceWeeklyReport))).scalars().all() == []
    mails = []
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 1


@pytest.mark.asyncio
async def test_stale_claim_from_an_older_process_is_retried(db, acme):
    """A process killed mid-send must not strand the whole week's report."""
    ws, _, _, _, _ = acme
    db.add(WorkspaceWeeklyReport(workspace_id=ws.id, week_start=WEEK, sent_to='',
                                 sent_at=MONDAY_8 - timedelta(minutes=31)))
    await db.commit()
    mails = []
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 1
    row = (await db.execute(select(WorkspaceWeeklyReport))).scalar_one()
    assert row.sent_to == 'team@acme.test, boss@acme.test'


@pytest.mark.asyncio
async def test_in_flight_claim_from_another_worker_is_not_resent(db, acme):
    ws, _, _, _, _ = acme
    db.add(WorkspaceWeeklyReport(workspace_id=ws.id, week_start=WEEK, sent_to='',
                                 sent_at=MONDAY_8 - timedelta(minutes=1)))
    await db.commit()
    mails = []
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 0
    assert mails == []


@pytest.mark.asyncio
async def test_old_claim_from_this_process_is_not_duplicated(db, acme):
    """A mail sent but not confirmed must never be sent twice."""
    ws, _, _, _, _ = acme
    db.add(WorkspaceWeeklyReport(workspace_id=ws.id, week_start=WEEK, sent_to='',
                                 sent_at=datetime.utcnow() - timedelta(minutes=31)))
    await db.commit()
    mails = []
    assert await report.run_weekly_reports(db, MONDAY_8, _send(mails)) == 0
    assert mails == []


@pytest.mark.asyncio
async def test_admin_form_persists_weekly_settings(db, acme):
    """The settings page is the only way an admin can turn the report on."""
    from app.web.routes import web_admin_email_settings_save

    ws, _, _, _, bob = acme
    request = SimpleNamespace(session={'user_id': bob.id})
    response = await web_admin_email_settings_save(
        request, smtp_host='smtp.test', smtp_port=587, smtp_username='u', smtp_password='p',
        smtp_from_email='helpdesk@acme.test', smtp_from_name='Helpdesk', smtp_use_tls='true',
        incoming_mail_type='IMAP', incoming_mail_host=None, incoming_mail_port=993,
        incoming_mail_username=None, incoming_mail_password=None, incoming_mail_use_ssl='true',
        webmail_url=None, confirmation_subject='Subject', confirmation_body='Body',
        company_name='Acme', auto_reply_enabled='true',
        completion_notify_enabled=None, completion_notify_email=None,
        completion_notify_task=None, completion_notify_ticket=None,
        completion_email_subject='{type} Completed - {title}', completion_email_body='Body',
        weekly_report_enabled='true',
        weekly_report_recipients=' new@acme.test ; boss@acme.test ',
        weekly_report_include_projects='true', weekly_report_include_tickets=None, db=db)
    assert response.status_code == 303
    settings = (await db.execute(select(EmailSettings))).scalar_one()
    assert settings.weekly_report_enabled is True
    assert settings.weekly_report_recipients == 'new@acme.test, boss@acme.test'
    assert settings.weekly_report_include_projects is True
    assert settings.weekly_report_include_tickets is False
    assert request.session['flash_type'] == 'success'


def test_admin_template_exposes_weekly_settings():
    template = Path('app/templates/admin/email_settings.html').read_text()
    for field in ('weekly_report_enabled', 'weekly_report_recipients',
                  'weekly_report_include_projects', 'weekly_report_include_tickets'):
        assert f'name="{field}"' in template


@pytest.mark.asyncio
async def test_ticket_report_survives_a_failed_workspace_lookup(db, acme, monkeypatch):
    """The download must still use the admin's company, its tickets and its time zone."""
    from app.core import report_excel
    from app.web import routes as web

    ws, _, _, _, bob = acme
    fixed = datetime(2026, 11, 16, 8, 5)
    monkeypatch.setattr(report_excel, 'local_now', lambda zone=None: fixed)
    monkeypatch.setattr(web, 'get_workspace_for_user', lambda *a, **k: _noop(None))

    def workbook_rows(blob):
        workbook = load_workbook(io.BytesIO(blob))
        return {sheet.title: [[cell.value for cell in row] for row in sheet.iter_rows()]
                for sheet in workbook}

    name, content = await report_excel.build_ticket_report_excel(db, ws, None, None)
    response = await web.web_tickets_report_excel(request_as(bob.id), None, None, None, None, db)
    streamed = b''.join([chunk async for chunk in response.body_iterator])
    assert name in response.headers['content-disposition']
    assert workbook_rows(streamed) == workbook_rows(content)

    # The builder itself also accepts an explicit id when no workspace row exists.
    _, fallback = await report_excel.build_ticket_report_excel(db, None, None, None, workspace_id=ws.id)
    def ticket_numbers(blob):
        sheet = load_workbook(io.BytesIO(blob))['All Tickets']
        return [row[0] for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert ticket_numbers(fallback) == ticket_numbers(content)


def test_weekly_migration_path_follows_the_database_url(monkeypatch):
    from app.core import database as database_core

    monkeypatch.setattr(database_core._settings, 'database_url', 'sqlite+aiosqlite:///./custom/data.db')
    assert database_core._weekly_report_db_path() == Path('custom/data.db')
    monkeypatch.setattr(database_core._settings, 'database_url', 'postgresql://u:p@host/db')
    assert database_core._weekly_report_db_path() == Path('data.db')


@pytest.mark.asyncio
async def test_send_now_rejects_settings_from_another_workspace(db, acme):
    ws, _, _, _, _ = acme
    settings = (await db.execute(select(EmailSettings))).scalar_one()
    settings.workspace_id = ws.id + 100
    with pytest.raises(ValueError, match='belong'):
        await report.send_weekly_report_now(db, ws, settings)
    settings.workspace_id = ws.id


def _send(mails):
    def send(smtp, to, subject, html, attachments=None):
        mails.append((smtp, to, subject, html, attachments or []))
    return send


async def _noop(value):
    return value
