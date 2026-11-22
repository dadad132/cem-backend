"""Slice 2: Weekly Reports (Excel) settings UI and send-now route.

Covers Email Settings save/validation, template rendering, debug exposure,
admin-only access, and tenant-isolated send-weekly-report behavior.
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
import pytest_asyncio
from jinja2 import Environment, FileSystemLoader
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from app.models.email_settings import EmailSettings
from app.models.project import Project
from app.models.task import Task
from app.models.ticket import Ticket
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import (
    web_admin_email_settings,
    web_admin_debug_settings,
    web_admin_email_settings_save,
    web_admin_email_settings_send_weekly_report,
)


@pytest.fixture(autouse=True)
def _legacy_template_call(monkeypatch):
    """Bridge legacy TemplateResponse shape used by app.web.routes in this venv."""
    import app.web.routes as routes
    from fastapi.templating import Jinja2Templates

    def render(name, context, *args, **kwargs):
        return Jinja2Templates.TemplateResponse(
            routes.templates, context.get("request"), name, context, *args, **kwargs
        )

    monkeypatch.setattr(routes, "_original_template_response", render)


def _request(user_id=None, path="/web/admin/email-settings"):
    return SimpleNamespace(
        session={"user_id": user_id} if user_id is not None else {},
        state=SimpleNamespace(),
        headers={},
        url=SimpleNamespace(path=path, scheme="https", netloc="crm.test"),
        query_params={},
    )


async def _seed(tmp_path):
    import app.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'slice2.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False)

    ws1 = Workspace(name="Acme", timezone="UTC")
    ws2 = Workspace(name="Other", timezone="UTC")
    db.add_all([ws1, ws2])
    await db.flush()

    admin = User(username="admin", full_name="Admin", hashed_password="x", email="admin@acme.test", workspace_id=ws1.id, is_admin=True, is_active=True)
    member = User(username="member", full_name="Member", hashed_password="x", email="member@acme.test", workspace_id=ws1.id, is_admin=False, is_active=True)
    other_admin = User(username="other", full_name="Other", hashed_password="x", email="other@other.test", workspace_id=ws2.id, is_admin=True, is_active=True)
    db.add_all([admin, member, other_admin])
    await db.flush()

    db.add(EmailSettings(
        workspace_id=ws1.id,
        smtp_host="smtp.test",
        smtp_port=587,
        smtp_username="smtp@acme.test",
        smtp_password="secret",
        smtp_from_email="support@acme.test",
        smtp_from_name="Acme Support",
        smtp_use_tls=True,
        company_name="Acme",
        weekly_report_enabled=True,
        weekly_report_recipients="team@acme.test, boss@acme.test",
        weekly_report_include_projects=True,
        weekly_report_include_tickets=True,
    ))
    db.add(EmailSettings(
        workspace_id=ws2.id,
        smtp_host="smtp.other.test",
        smtp_port=587,
        smtp_username="smtp@other.test",
        smtp_password="secret",
        smtp_from_email="support@other.test",
        smtp_from_name="Other Support",
        smtp_use_tls=True,
        company_name="Other",
        weekly_report_enabled=True,
        weekly_report_recipients="other@other.test",
        weekly_report_include_projects=True,
        weekly_report_include_tickets=True,
    ))

    p1 = Project(name="Acme Project", workspace_id=ws1.id, owner_id=admin.id, is_archived=False)
    p2 = Project(name="Other Secret Project", workspace_id=ws2.id, owner_id=other_admin.id, is_archived=False)
    db.add_all([p1, p2])
    await db.flush()
    db.add(Task(title="Acme Task", project_id=p1.id, creator_id=admin.id))
    db.add(Task(title="Other Task", project_id=p2.id, creator_id=other_admin.id))
    db.add(Ticket(ticket_number="TKT-ACME-001", subject="Acme Ticket", workspace_id=ws1.id, status="open"))
    db.add(Ticket(ticket_number="TKT-OTHER-001", subject="Other Secret Ticket", workspace_id=ws2.id, status="open"))
    await db.commit()
    return db, engine, ws1, ws2, admin, member, other_admin


@pytest_asyncio.fixture
async def seeded(tmp_path):
    db, engine, *rest = await _seed(tmp_path)
    try:
        yield (db, *rest)
    finally:
        await db.close()
        await engine.dispose()


def test_weekly_reports_template_parses_and_renders_saved_values():
    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    template = env.get_template("admin/email_settings.html")
    html = template.render(
        request=SimpleNamespace(session={}, url=SimpleNamespace(scheme="https", netloc="crm.test")),
        user=SimpleNamespace(id=1, is_admin=True),
        settings=SimpleNamespace(
            smtp_host="smtp.test", smtp_port=587, smtp_username="u", smtp_password="p",
            smtp_from_email="a@b.com", smtp_from_name="A", smtp_use_tls=True,
            incoming_mail_type="IMAP", incoming_mail_host=None, incoming_mail_port=993,
            incoming_mail_username=None, incoming_mail_password=None, incoming_mail_use_ssl=True,
            webmail_url=None, confirmation_subject="s", confirmation_body="b", company_name="Acme",
            auto_reply_enabled=True, completion_notify_enabled=False, completion_notify_email=None,
            completion_notify_task=True, completion_notify_ticket=True,
            completion_email_subject="{type} Completed - {title}", completion_email_body="body",
            weekly_report_enabled=True, weekly_report_recipients="team@acme.test, ops@acme.test",
            weekly_report_include_projects=True, weekly_report_include_tickets=False,
        ),
        default_body="body",
        default_completion_body="body",
    )
    assert "Weekly Reports (Excel)" in html
    assert "Send Weekly Report Now" in html
    assert 'id="send-weekly-report-btn"' in html
    assert "team@acme.test, ops@acme.test" in html
    assert 'name="weekly_report_enabled"' in html
    assert 'name="weekly_report_recipients"' in html
    assert 'name="weekly_report_include_projects"' in html
    assert 'name="weekly_report_include_tickets"' in html


@pytest.mark.asyncio
async def test_save_weekly_settings_persists_and_reloads(seeded):
    db, ws1, _ws2, admin, _member, _other = seeded
    request = _request(admin.id)
    response = await web_admin_email_settings_save(
        request,
        smtp_host="smtp.test", smtp_port=587, smtp_username="smtp@acme.test", smtp_password="secret",
        smtp_from_email="support@acme.test", smtp_from_name="Acme Support", smtp_use_tls="true",
        incoming_mail_type="IMAP", incoming_mail_host=None, incoming_mail_port=993,
        incoming_mail_username=None, incoming_mail_password=None, incoming_mail_use_ssl="true",
        webmail_url=None, confirmation_subject="Subject", confirmation_body="Body",
        company_name="Acme", auto_reply_enabled="true",
        completion_notify_enabled=None, completion_notify_email=None,
        completion_notify_task=None, completion_notify_ticket=None,
        completion_email_subject="{type} Completed - {title}", completion_email_body="Body",
        weekly_report_enabled="true",
        weekly_report_recipients="one@acme.test;two@acme.test\nthree@acme.test",
        weekly_report_include_projects="true", weekly_report_include_tickets=None,
        db=db,
    )
    assert response.status_code == 303
    settings = (await db.execute(select(EmailSettings).where(EmailSettings.workspace_id == ws1.id))).scalar_one()
    assert settings.weekly_report_enabled is True
    assert settings.weekly_report_recipients == "one@acme.test, two@acme.test, three@acme.test"
    assert settings.weekly_report_include_projects is True
    assert settings.weekly_report_include_tickets is False

    page = await web_admin_email_settings(_request(admin.id), db)
    body = bytes(page.body).decode()
    assert "one@acme.test, two@acme.test, three@acme.test" in body
    assert "Weekly Reports (Excel)" in body


@pytest.mark.asyncio
async def test_save_sanitizes_header_injection(seeded):
    db, ws1, _ws2, admin, *_ = seeded
    request = _request(admin.id)
    response = await web_admin_email_settings_save(
        request,
        smtp_host="smtp.test", smtp_port=587, smtp_username="smtp@acme.test", smtp_password="secret",
        smtp_from_email="support@acme.test", smtp_from_name="Acme Support", smtp_use_tls="true",
        incoming_mail_type="IMAP", incoming_mail_host=None, incoming_mail_port=993,
        incoming_mail_username=None, incoming_mail_password=None, incoming_mail_use_ssl="true",
        webmail_url=None, confirmation_subject="Subject", confirmation_body="Body",
        company_name="Acme", auto_reply_enabled="true",
        completion_notify_enabled=None, completion_notify_email=None,
        completion_notify_task=None, completion_notify_ticket=None,
        completion_email_subject="{type} Completed - {title}", completion_email_body="Body",
        weekly_report_enabled="true",
        weekly_report_recipients="evil@test.com\r\nBcc: victim@test.com, good@test.com",
        weekly_report_include_projects="true", weekly_report_include_tickets="true",
        db=db,
    )
    assert response.status_code == 303
    settings = (await db.execute(select(EmailSettings).where(EmailSettings.workspace_id == ws1.id))).scalar_one()
    # Header-like lines are ignored in full to prevent Bcc/header injection.
    assert settings.weekly_report_recipients == "evil@test.com"
    assert "victim@test.com" not in (settings.weekly_report_recipients or "")
    assert "good@test.com" not in (settings.weekly_report_recipients or "")
    assert "\r" not in (settings.weekly_report_recipients or "")
    assert "\n" not in (settings.weekly_report_recipients or "")


@pytest.mark.asyncio
async def test_enable_weekly_reports_without_valid_recipient_is_rejected(seeded):
    db, ws1, _ws2, admin, *_ = seeded
    request = _request(admin.id)
    response = await web_admin_email_settings_save(
        request,
        smtp_host="smtp.test", smtp_port=587, smtp_username="smtp@acme.test", smtp_password="secret",
        smtp_from_email="support@acme.test", smtp_from_name="Acme Support", smtp_use_tls="true",
        incoming_mail_type="IMAP", incoming_mail_host=None, incoming_mail_port=993,
        incoming_mail_username=None, incoming_mail_password=None, incoming_mail_use_ssl="true",
        webmail_url=None, confirmation_subject="Subject", confirmation_body="Body",
        company_name="Acme", auto_reply_enabled="true",
        completion_notify_enabled=None, completion_notify_email=None,
        completion_notify_task=None, completion_notify_ticket=None,
        completion_email_subject="{type} Completed - {title}", completion_email_body="Body",
        weekly_report_enabled="true",
        weekly_report_recipients=" \n ; ",
        weekly_report_include_projects="true", weekly_report_include_tickets="true",
        db=db,
    )
    assert response.status_code == 303
    settings = (await db.execute(select(EmailSettings).where(EmailSettings.workspace_id == ws1.id))).scalar_one()
    assert settings.weekly_report_enabled is False
    assert request.session["flash_type"] == "error"
    assert "at least one valid email address" in request.session["flash_message"]


@pytest.mark.asyncio
async def test_enable_weekly_reports_without_attachment_type_is_rejected(seeded):
    db, ws1, _ws2, admin, *_ = seeded
    request = _request(admin.id)
    response = await web_admin_email_settings_save(
        request,
        smtp_host="smtp.test", smtp_port=587, smtp_username="smtp@acme.test", smtp_password="secret",
        smtp_from_email="support@acme.test", smtp_from_name="Acme Support", smtp_use_tls="true",
        incoming_mail_type="IMAP", incoming_mail_host=None, incoming_mail_port=993,
        incoming_mail_username=None, incoming_mail_password=None, incoming_mail_use_ssl="true",
        webmail_url=None, confirmation_subject="Subject", confirmation_body="Body",
        company_name="Acme", auto_reply_enabled="true",
        completion_notify_enabled=None, completion_notify_email=None,
        completion_notify_task=None, completion_notify_ticket=None,
        completion_email_subject="{type} Completed - {title}", completion_email_body="Body",
        weekly_report_enabled="true",
        weekly_report_recipients="team@acme.test",
        weekly_report_include_projects=None, weekly_report_include_tickets=None,
        db=db,
    )
    assert response.status_code == 303
    settings = (await db.execute(select(EmailSettings).where(EmailSettings.workspace_id == ws1.id))).scalar_one()
    assert settings.weekly_report_enabled is False
    assert request.session["flash_type"] == "error"
    assert "at least one weekly report attachment" in request.session["flash_message"].lower()


@pytest.mark.asyncio
async def test_non_admin_access_denied(seeded):
    db, _ws1, _ws2, _admin, member, _other = seeded
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await web_admin_email_settings(_request(member.id), db)
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        await web_admin_email_settings_save(
            _request(member.id),
            smtp_host="smtp.test", smtp_port=587, smtp_username="smtp@acme.test", smtp_password="secret",
            smtp_from_email="support@acme.test", smtp_from_name="Acme Support", smtp_use_tls="true",
            incoming_mail_type="IMAP", incoming_mail_host=None, incoming_mail_port=993,
            incoming_mail_username=None, incoming_mail_password=None, incoming_mail_use_ssl="true",
            webmail_url=None, confirmation_subject="Subject", confirmation_body="Body",
            company_name="Acme", auto_reply_enabled="true",
            completion_notify_enabled=None, completion_notify_email=None,
            completion_notify_task=None, completion_notify_ticket=None,
            completion_email_subject="{type} Completed - {title}", completion_email_body="Body",
            weekly_report_enabled="true", weekly_report_recipients="x@y.com",
            weekly_report_include_projects="true", weekly_report_include_tickets="true",
            db=db,
        )
    assert exc.value.status_code == 403

    resp = await web_admin_email_settings_send_weekly_report(_request(member.id), db)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_send_weekly_report_now_tenant_isolated_and_returns_attachments(seeded, monkeypatch):
    db, ws1, _ws2, admin, _member, _other = seeded
    from app.core import project_weekly_report as report

    sent = {}

    def fake_send(smtp, to, subject, html, attachments=None):
        sent["smtp"] = smtp
        sent["to"] = to
        sent["subject"] = subject
        sent["html"] = html
        sent["attachments"] = attachments or []

    monkeypatch.setattr(report, "_smtp_send", fake_send)

    resp = await web_admin_email_settings_send_weekly_report(_request(admin.id), db)
    assert resp.status_code == 200
    data = resp.body
    import json
    payload = json.loads(data.decode())
    assert payload["success"] is True
    assert payload["recipients"] == ["team@acme.test", "boss@acme.test"]
    assert len(payload["attachments"]) >= 2
    assert any(name.startswith("ticket_report_") and name.endswith(".xlsx") for name in payload["attachments"])
    assert any(name.startswith("project_report_") and name.endswith(".xlsx") for name in payload["attachments"])

    names = [name for name, _ in sent["attachments"]]
    assert names == payload["attachments"]
    blob_text = b"".join(content for _, content in sent["attachments"])
    assert b"Other Secret Project" not in blob_text
    assert b"TKT-OTHER-001" not in blob_text
    assert b"Other Secret Ticket" not in blob_text

    # sanity: workbook is readable
    for name, content in sent["attachments"]:
        wb = load_workbook(io.BytesIO(content))
        assert wb.sheetnames


@pytest.mark.asyncio
async def test_send_weekly_report_now_handles_missing_recipients_and_smtp(seeded, monkeypatch):
    db, ws1, _ws2, admin, *_ = seeded
    settings = (await db.execute(select(EmailSettings).where(EmailSettings.workspace_id == ws1.id))).scalar_one()
    settings.weekly_report_recipients = None
    await db.commit()

    resp = await web_admin_email_settings_send_weekly_report(_request(admin.id), db)
    assert resp.status_code == 400
    payload = __import__("json").loads(resp.body.decode())
    assert payload["success"] is False
    assert "recipient" in payload["error"].lower()

    settings.weekly_report_recipients = "team@acme.test"
    await db.commit()
    from app.core import project_weekly_report as report

    async def no_smtp(*args, **kwargs):
        return None

    monkeypatch.setattr(report, "_smtp_for", no_smtp)
    resp = await web_admin_email_settings_send_weekly_report(_request(admin.id), db)
    assert resp.status_code == 400
    payload = __import__("json").loads(resp.body.decode())
    assert payload["success"] is False
    assert "outgoing mail" in payload["error"].lower() or "smtp" in payload["error"].lower()


@pytest.mark.asyncio
async def test_debug_includes_weekly_fields_without_passwords(seeded):
    db, _ws1, _ws2, admin, *_ = seeded
    resp = await web_admin_debug_settings(_request(admin.id), db)
    assert resp.status_code == 200
    payload = __import__("json").loads(resp.body.decode())
    assert payload["success"] is True
    s = payload["settings"]
    assert s["weekly_report_enabled"] is True
    assert s["weekly_report_recipients"] == "team@acme.test, boss@acme.test"
    assert s["weekly_report_include_projects"] is True
    assert s["weekly_report_include_tickets"] is True
    assert "smtp_password" not in s
    assert "incoming_mail_password" not in s
