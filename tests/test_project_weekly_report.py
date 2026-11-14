"""The weekly report to Board View projects with a linked e-mail: it goes on
Monday from 08:00 (the company's own time), covers Monday to Friday of the
week before, goes once, and never comes back in as a ticket."""
from __future__ import annotations

from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import project_weekly_report as report
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

WEEK = date(2026, 11, 9)                   # Monday; the week runs to Friday 13 Nov
MONDAY_8 = datetime(2026, 11, 16, 8, 5)    # The next Monday, just after 08:00 (UTC)


def at(day, hour=10, minute=0):
    return datetime(2026, 11, day, hour, minute)


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
    bob = User(username="bob", full_name="Bob Builder", hashed_password="x", workspace_id=ws.id)
    db.add(bob)
    await db.flush()
    db.add(EmailSettings(workspace_id=ws.id, smtp_host="smtp.test", smtp_username="u", smtp_password="p",
                         smtp_from_email="helpdesk@acme.test"))
    linked = Project(name="Office move", workspace_id=ws.id, owner_id=bob.id,
                     support_email="move@acme.test; boss@acme.test")
    no_email = Project(name="No email", workspace_id=ws.id, owner_id=bob.id)
    archived = Project(name="Old", workspace_id=ws.id, owner_id=bob.id, support_email="old@acme.test",
                       is_archived=True)
    db.add_all([linked, no_email, archived])
    await db.flush()
    t = dict(
        wed=Task(title="Pack boxes", project_id=linked.id, creator_id=bob.id, status=TaskStatus.done,
                 is_archived=True, archived_at=at(11), created_at=at(10)),
        before=Task(title="Order van", project_id=linked.id, creator_id=bob.id, status=TaskStatus.done,
                    is_archived=True, archived_at=at(7), created_at=at(2)),
        late=Task(title="Label desks", project_id=linked.id, creator_id=bob.id, due_date=date(2026, 11, 10),
                  created_at=at(2)),
        stuck=Task(title="Order cables", project_id=linked.id, creator_id=bob.id, status=TaskStatus.blocked,
                   created_at=at(2)),
    )
    db.add_all(t.values())
    await db.flush()
    db.add_all([
        Assignment(task_id=t["wed"].id, assignee_id=bob.id),
        TaskTimeEntry(task_id=t["wed"].id, user_id=bob.id, started_at=at(10, 9), ended_at=at(10, 11)),
        # Friday 23:00 to Saturday 02:00: only the hour before midnight is this week's
        TaskTimeEntry(task_id=t["late"].id, user_id=bob.id, started_at=at(13, 23), ended_at=at(14, 2)),
        TimeLog(task_id=t["stuck"].id, user_id=bob.id, hours=1.5, logged_at=at(12)),
        Ticket(ticket_number="TKT-2026-00001", subject="Move printer", workspace_id=ws.id,
               related_project_id=linked.id, status="closed", created_at=at(10), closed_at=at(12)),
    ])
    await db.commit()
    return ws, linked


@pytest.mark.asyncio
async def test_the_weeks_figures(db, acme):
    ws, linked = acme
    r = await report.build_report(db, linked, ws, WEEK, MONDAY_8)
    assert [f[0] for f in r["finished"]] == ["Pack boxes"]          # not last Saturday's
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
async def test_sent_monday_from_eight_once_to_linked_projects_only(db, acme):
    _, linked = acme
    mails = []

    def send(smtp, to, subject, html):
        mails.append((smtp["from_email"], to, subject))

    assert await report.run_weekly_reports(db, datetime(2026, 11, 16, 7, 59), send) == 0
    assert await report.run_weekly_reports(db, MONDAY_8, send) == 1
    assert mails == [("helpdesk@acme.test", ["move@acme.test", "boss@acme.test"],
                      "Weekly report: Office move (09 Nov – 13 Nov 2026)")]
    # Later checks that Monday, and the Tuesday, send nothing more
    assert await report.run_weekly_reports(db, datetime(2026, 11, 16, 11), send) == 0
    assert await report.run_weekly_reports(db, datetime(2026, 11, 17, 8, 5), send) == 0
    sent = (await db.execute(select(ProjectWeeklyReport))).scalars().all()
    assert [(s.project_id, s.week_start) for s in sent] == [(linked.id, WEEK)]


@pytest.mark.asyncio
async def test_eight_oclock_in_the_companys_own_time(db, acme):
    ws, _ = acme
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
    assert await report.run_weekly_reports(db, datetime(2026, 11, 16, 8, 10), lambda *a: None) == 1


def test_the_report_never_becomes_a_ticket():
    """The linked address is usually a mailbox the helpdesk reads; both
    import paths skip the report's header."""
    from pathlib import Path
    source = Path("app/core/email_to_ticket_v2.py").read_text()
    assert source.count("msg.get('X-CEM-Report')") == 2
    assert report.REPORT_HEADER == "X-CEM-Report"
