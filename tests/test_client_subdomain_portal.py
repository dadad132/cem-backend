"""Client subdomain ticket portal: host detection, company autofill, issue templates."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from app.core.database import get_session
from app.core.subdomain_portal import (
    extract_client_subdomain,
    humanize_subdomain,
    resolve_subdomain_company,
    slugify_company,
)
from app.models.company import Company
from app.models.ticket import Ticket
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes as web


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("companya.hubconnect.co.za", "companya"),
        ("company-a.hubconnect.co.za", "company-a"),
        ("mobilis.hubconnect.co.za", "mobilis"),
        ("COMPANYA.HUBCONNECT.CO.ZA", "companya"),
        ("companya.hubconnect.co.za:443", "companya"),
        ("tickets.hubconnect.co.za", None),
        ("hubconnect.co.za", None),
        ("www.hubconnect.co.za", None),
        ("app.hubconnect.co.za", None),
        ("localhost:8000", None),
        ("127.0.0.1", None),
        ("::1", None),
        ("[::1]", None),
        ("", None),
        ("   ", None),
        ("evil..hubconnect.co.za", None),
        ("bad_host!.hubconnect.co.za", None),
        ("a.b.hubconnect.co.za", None),  # multi-label subdomain
        ("tickets.hubconnect.co.za:8000", None),
        ("otherdomain.com", None),
    ],
)
def test_extract_client_subdomain(host, expected):
    assert extract_client_subdomain(host, "tickets.hubconnect.co.za") == expected


def test_extract_client_subdomain_respects_app_domain_root():
    # hubconnect.co.za itself as app_domain still yields root hubconnect.co.za
    assert extract_client_subdomain("companya.hubconnect.co.za", "hubconnect.co.za") == "companya"
    assert extract_client_subdomain("hubconnect.co.za", "hubconnect.co.za") is None
    assert extract_client_subdomain("tickets.hubconnect.co.za", "hubconnect.co.za") is None


@pytest.mark.parametrize(
    "label,expected",
    [
        ("company-a", "Company A"),
        ("company_a", "Company A"),
        ("mobilis", "Mobilis"),
        ("acme-group", "Acme Group"),
        ("phiri-associates", "Phiri Associates"),
    ],
)
def test_humanize_subdomain(label, expected):
    assert humanize_subdomain(label) == expected


def test_slugify_company_matches_hyphenated_and_compact():
    assert "company-a" in slugify_company("Company A")
    assert "companya" in slugify_company("Company A")
    assert "phiri-associates" in slugify_company("Phiri & Associates")
    assert "phiriassociates" in slugify_company("Phiri & Associates")


# ---------------------------------------------------------------------------
# DB-backed resolve + integration
# ---------------------------------------------------------------------------


class _DummyRequest:
    def __init__(self, host="tickets.hubconnect.co.za", forwarded_host=None, client_host="203.0.113.5"):
        headers = {"host": host}
        if forwarded_host is not None:
            headers["x-forwarded-host"] = forwarded_host
        self.headers = headers
        self.client = type("C", (), {"host": client_host})()
        self.url = type("U", (), {"hostname": host.split(":")[0]})()
        self.scope = {}


@pytest_asyncio.fixture
async def portal_env(tmp_path, monkeypatch):
    import app.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'portal.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)

    ws = Workspace(id=1, name="Acme WS", timezone="UTC")
    db.add(ws)
    await db.flush()
    db.add_all([
        Company(name="Company A", workspace_id=1, created_by=1),
        Company(name="Mobilis", workspace_id=1, created_by=1),
    ])
    db.add(User(id=1, username="admin", hashed_password="x", is_admin=True, workspace_id=1))
    await db.commit()

    monkeypatch.setattr(web, "BASE_DIR", tmp_path / "app")

    from app.main import app
    from app.core.config import get_settings

    # Ensure default app_domain for tests
    settings = get_settings()
    monkeypatch.setattr(settings, "app_domain", "tickets.hubconnect.co.za")
    monkeypatch.setattr(settings, "forwarded_allow_ips", "127.0.0.1,::1")

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    # Isolate from leaked overrides in full-suite runs.
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = session
    import app.main as app_main
    app.dependency_overrides[app_main.get_session] = session
    # WorkspaceMiddleware imports get_session from app.core.database at call time.
    import app.core.database as core_db
    monkeypatch.setattr(core_db, "get_session", session)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://tickets.hubconnect.co.za")
    try:
        yield SimpleNamespace(db=db, engine=engine, client=client, app=app, settings=settings)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        await client.aclose()
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_subdomain_company_matches_db_and_humanizes(portal_env):
    db = portal_env.db
    req = _DummyRequest(host="companya.hubconnect.co.za")
    result = await resolve_subdomain_company(req, db, workspace_id=1)
    assert result["is_client_subdomain"] is True
    assert result["subdomain"] == "companya"
    assert result["company_name"] == "Company A"

    req2 = _DummyRequest(host="company-a.hubconnect.co.za")
    result2 = await resolve_subdomain_company(req2, db, workspace_id=1)
    assert result2["company_name"] == "Company A"

    req3 = _DummyRequest(host="acme-corp.hubconnect.co.za")
    result3 = await resolve_subdomain_company(req3, db, workspace_id=1)
    assert result3["is_client_subdomain"] is True
    assert result3["company_name"] == "Acme Corp"


@pytest.mark.asyncio
async def test_resolve_subdomain_company_ignores_untrusted_forwarded_host(portal_env):
    db = portal_env.db
    # Untrusted peer - forwarded host must be ignored
    req = _DummyRequest(host="tickets.hubconnect.co.za", forwarded_host="companya.hubconnect.co.za", client_host="203.0.113.5")
    result = await resolve_subdomain_company(req, db, workspace_id=1)
    assert result["is_client_subdomain"] is False

    # Trusted proxy peer - forwarded host honored
    req2 = _DummyRequest(host="tickets.hubconnect.co.za", forwarded_host="companya.hubconnect.co.za", client_host="127.0.0.1")
    result2 = await resolve_subdomain_company(req2, db, workspace_id=1)
    assert result2["is_client_subdomain"] is True
    assert result2["company_name"] == "Company A"


@pytest.mark.asyncio
async def test_get_root_renders_client_portal_on_subdomain(portal_env):
    client = portal_env.client
    resp = await client.get("/", headers={"Host": "companya.hubconnect.co.za"})
    assert resp.status_code == 200
    body = resp.text
    assert 'name="guest_company"' in body
    assert 'value="Company A"' in body
    assert 'name="issue_type"' in body
    assert 'IT Issue' in body
    assert 'Printer Issue' in body
    assert "I'm a Team Member" not in body
    assert "Log a Support Ticket" in body

    resp_main = await client.get("/", headers={"Host": "tickets.hubconnect.co.za"})
    assert resp_main.status_code == 200
    assert "I'm a Team Member" in resp_main.text


@pytest.mark.asyncio
async def test_guest_submit_it_issue_structured_description_and_autofill(portal_env):
    client = portal_env.client
    resp = await client.post(
        "/web/tickets/guest",
        headers={"Host": "companya.hubconnect.co.za"},
        data={
            "guest_name": "Jane",
            "guest_surname": "Doe",
            "guest_email": "jane@example.com",
            "guest_phone": "555",
            "guest_company": "",  # should autofill from subdomain
            "subject": "Outlook broken",
            "description": "Cannot open Outlook",
            "issue_type": "it",
            "it_system": "Email / Outlook",
            "it_device": "LAPTOP-1",
            "it_error": "0x800CCC0E",
            "it_started": "This morning",
        },
    )
    assert resp.status_code == 200
    assert "Ticket Created!" in resp.text

    async with AsyncSession(portal_env.engine, expire_on_commit=False) as s:
        ticket = (await s.execute(select(Ticket).where(Ticket.subject == "Outlook broken"))).scalar_one()
    assert ticket.guest_company == "Company A"
    assert ticket.category == "support"
    assert "Issue Type: IT Issue" in ticket.description
    assert "Affected Area: Email / Outlook" in ticket.description
    assert "Device / AnyDesk ID: LAPTOP-1" in ticket.description
    assert "Error Message: 0x800CCC0E" in ticket.description
    assert "When Started / Users Affected: This morning" in ticket.description
    assert "Portal Subdomain: companya.hubconnect.co.za" in ticket.description
    assert "--- Details ---" in ticket.description
    assert "Cannot open Outlook" in ticket.description


@pytest.mark.asyncio
async def test_guest_submit_printer_issue_structured_description(portal_env):
    client = portal_env.client
    resp = await client.post(
        "/web/tickets/guest",
        headers={"Host": "mobilis.hubconnect.co.za"},
        data={
            "guest_name": "Sam",
            "guest_surname": "P",
            "guest_email": "sam@mobilis.test",
            "guest_phone": "555",
            "guest_company": "Mobilis",
            "subject": "Printer jam",
            "description": "Paper stuck",
            "issue_type": "printer",
            "printer_problem": "Paper Jam",
            "printer_model": "HP LaserJet",
            "printer_serial": "SN123",
            "printer_location": "2nd floor",
            "printer_error_code": "13.B9",
        },
    )
    assert resp.status_code == 200
    async with AsyncSession(portal_env.engine, expire_on_commit=False) as s:
        ticket = (await s.execute(select(Ticket).where(Ticket.subject == "Printer jam"))).scalar_one()
    assert "Issue Type: Printer Issue" in ticket.description
    assert "Printer Problem: Paper Jam" in ticket.description
    assert "Printer Make & Model: HP LaserJet" in ticket.description
    assert "Serial Number / Asset Tag: SN123" in ticket.description
    assert "Printer Location / Floor: 2nd floor" in ticket.description
    assert "Error Code on Screen: 13.B9" in ticket.description
    assert "Paper stuck" in ticket.description


@pytest.mark.asyncio
async def test_guest_submit_rejects_invalid_issue_type(portal_env):
    client = portal_env.client
    resp = await client.post(
        "/web/tickets/guest",
        headers={"Host": "tickets.hubconnect.co.za"},
        data={
            "guest_name": "Jane",
            "guest_surname": "Doe",
            "guest_email": "jane@example.com",
            "guest_phone": "555",
            "guest_company": "Acme",
            "subject": "Bad type",
            "description": "x",
            "issue_type": "bogus",
        },
    )
    assert resp.status_code == 400
    assert "Invalid issue type" in resp.text
    async with AsyncSession(portal_env.engine, expire_on_commit=False) as s:
        count = (await s.execute(select(Ticket).where(Ticket.subject == "Bad type"))).scalars().all()
    assert count == []


@pytest.mark.asyncio
async def test_guest_submit_requires_company_on_main_host(portal_env):
    client = portal_env.client
    resp = await client.post(
        "/web/tickets/guest",
        headers={"Host": "tickets.hubconnect.co.za"},
        data={
            "guest_name": "Jane",
            "guest_surname": "Doe",
            "guest_email": "jane@example.com",
            "guest_phone": "555",
            "guest_company": "",
            "subject": "Missing company",
            "description": "x",
            "issue_type": "it",
        },
    )
    assert resp.status_code == 400
    assert "Company is required" in resp.text


@pytest.mark.asyncio
async def test_guest_submit_user_company_overrides_subdomain_autofill(portal_env):
    client = portal_env.client
    resp = await client.post(
        "/web/tickets/guest",
        headers={"Host": "companya.hubconnect.co.za"},
        data={
            "guest_name": "Jane",
            "guest_surname": "Doe",
            "guest_email": "jane@example.com",
            "guest_phone": "555",
            "guest_company": "Custom Co",
            "subject": "Override company",
            "description": "desc",
            "issue_type": "it",
        },
    )
    assert resp.status_code == 200
    async with AsyncSession(portal_env.engine, expire_on_commit=False) as s:
        ticket = (await s.execute(select(Ticket).where(Ticket.subject == "Override company"))).scalar_one()
    assert ticket.guest_company == "Custom Co"


@pytest.mark.asyncio
async def test_trusted_forwarded_host_honored_through_middleware(portal_env):
    """Real app middleware: trusted proxy may set X-Forwarded-Host even with XFF."""
    client = portal_env.client
    # ASGITransport client host defaults to testclient; force trusted peer via scope.
    transport = ASGITransport(app=portal_env.app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://tickets.hubconnect.co.za") as proxied:
        resp = await proxied.get(
            "/",
            headers={
                "Host": "tickets.hubconnect.co.za",
                "X-Forwarded-Host": "companya.hubconnect.co.za",
                "X-Forwarded-For": "203.0.113.9",
            },
        )
    assert resp.status_code == 200
    assert 'value="Company A"' in resp.text
    assert "I'm a Team Member" not in resp.text


@pytest.mark.asyncio
async def test_company_name_is_html_escaped_in_portal(portal_env):
    db = portal_env.db
    db.add(Company(name="<script>x</script>", workspace_id=1, created_by=1))
    await db.commit()
    # Slug for "<script>x</script>" is "script-x-script"; use matching subdomain label.
    client = portal_env.client
    resp = await client.get("/", headers={"Host": "script-x-script.hubconnect.co.za"})
    assert resp.status_code == 200
    body = resp.text
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;x&lt;/script&gt;" in body
