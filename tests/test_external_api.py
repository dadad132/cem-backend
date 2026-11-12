"""The external API (/api/v1), as a dashboard sync uses it: the ticket detail
no longer fails on a ticket with comments, and list and detail carry the
fields the Team Overview is built from."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel
from sqlmodel import select as sqlmodel_select

from app.api.routes import external
from app.core.database import get_session
from app.core.team_overview import build_overview
from app.models.api_key import APIKey
from app.models.assignment import Assignment
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.task_extensions import TaskTimeEntry, TimeLog
from app.models.ticket import Ticket, TicketComment, TicketHistory, TicketTimeEntry
from app.models.user import User
from app.models.workspace import Workspace

KEY = "cem_test0123456789abcdef"
NOW = datetime.utcnow().replace(microsecond=0)
TODAY = date.today()


def _ago(hours: float = 0, days: float = 0) -> datetime:
    return NOW - timedelta(hours=hours, days=days)


@pytest_asyncio.fixture
async def api(tmp_path):
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name="Acme", timezone="UTC"), Workspace(name="Other Co")
    db.add_all([acme, other])
    await db.flush()
    u = dict(ada=User(username="ada", hashed_password="x", is_admin=True, workspace_id=acme.id),
             bob=User(username="bob", hashed_password="x", full_name="Bob Builder", workspace_id=acme.id),
             dan=User(username="dan", hashed_password="x", is_active=False, workspace_id=acme.id),
             olive=User(username="olive", hashed_password="x", workspace_id=other.id))
    db.add_all(u.values())
    await db.flush()
    move = Project(name="Office move", workspace_id=acme.id, owner_id=u["ada"].id)
    db.add(move)
    await db.flush()
    t = dict(
        busy=Ticket(ticket_number="TKT-2026-05337", subject="Printer", workspace_id=acme.id, is_guest=True,
                    guest_name="Jane", guest_surname="Botha", guest_email="jane@client.test",
                    guest_company="Client Co", assigned_to_id=u["bob"].id, related_project_id=move.id,
                    status="in_progress", created_at=_ago(days=2), updated_at=_ago(days=2),
                    due_date=NOW - timedelta(days=1), close_type="sla",
                    billable_remote_labour="1h"),
        internal=Ticket(ticket_number="TKT-2026-05673", subject="Swap the UPS", workspace_id=acme.id,
                        created_by_id=u["ada"].id, created_at=_ago(days=1), updated_at=_ago(days=1)),
        theirs=Ticket(ticket_number="TKT-2026-09999", subject="Not yours", workspace_id=other.id,
                      created_at=_ago(days=1), updated_at=_ago(days=1)),
    )
    db.add_all(t.values())
    await db.flush()
    busy = t["busy"]
    db.add_all([
        TicketComment(ticket_id=busy.id, user_id=None, content="It is broken again",
                      created_at=busy.created_at + timedelta(minutes=10)),
        TicketComment(ticket_id=busy.id, user_id=u["bob"].id, content="private: check toner",
                      is_internal=True, created_at=busy.created_at + timedelta(minutes=20)),
        TicketComment(ticket_id=busy.id, user_id=u["bob"].id, content="On my way",
                      created_at=busy.created_at + timedelta(hours=2)),
        TicketComment(ticket_id=busy.id, user_id=u["ada"].id, content="Fixed?",
                      created_at=busy.created_at + timedelta(hours=5)),
        TicketHistory(ticket_id=busy.id, user_id=u["ada"].id, action="assigned", old_value="Unassigned",
                      new_value=str(u["bob"].id), created_at=busy.created_at + timedelta(minutes=30)),
        TicketHistory(ticket_id=busy.id, user_id=u["bob"].id, action="status_changed", old_value="open",
                      new_value="closed", created_at=busy.created_at + timedelta(hours=6)),
        TicketHistory(ticket_id=busy.id, user_id=u["ada"].id, action="status_changed", old_value="closed",
                      new_value="in_progress", created_at=busy.created_at + timedelta(hours=7)),
        TicketTimeEntry(ticket_id=busy.id, user_id=u["bob"].id, started_at=busy.created_at + timedelta(hours=2),
                        ended_at=busy.created_at + timedelta(hours=3)),
    ])
    k = dict(
        blocked=Task(title="Order cables", project_id=move.id, creator_id=u["ada"].id,
                     status=TaskStatus.blocked, due_date=TODAY - timedelta(days=2)),
        done=Task(title="Pack boxes", project_id=move.id, creator_id=u["ada"].id, status=TaskStatus.done,
                  is_archived=True, archived_at=_ago(hours=3)),
        nobody=Task(title="Label desks", project_id=move.id, creator_id=u["ada"].id),
    )
    db.add_all(k.values())
    await db.flush()
    db.add_all([
        Assignment(task_id=k["blocked"].id, assignee_id=u["bob"].id),
        Assignment(task_id=k["done"].id, assignee_id=u["dan"].id),
        TaskTimeEntry(task_id=k["blocked"].id, user_id=u["bob"].id, started_at=_ago(hours=1)),
        TimeLog(task_id=k["done"].id, user_id=u["ada"].id, hours=1.5, logged_at=_ago(hours=4)),
        APIKey(workspace_id=acme.id, name="Grafana", key_prefix=KEY[:12],
               key_hash=hashlib.sha256(KEY.encode()).hexdigest()),
    ])
    await db.commit()

    app = FastAPI()
    app.include_router(external.router, prefix="/api")

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                         headers={"X-API-Key": KEY})
    yield client, db, acme, u, t, k
    await client.aclose()
    await db.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_ticket_with_comments_opens(api):
    client, _, _, _, _, _ = api
    r = await client.get("/api/v1/tickets/TKT-2026-05337")
    assert r.status_code == 200, r.text
    comments = r.json()["comments"]
    assert [c["body"] for c in comments] == ["It is broken again", "On my way", "Fixed?"]
    assert comments[0]["author"] == {"type": "customer", "id": None, "name": "Jane Botha"}
    assert comments[1]["author"]["type"] == "staff" and comments[1]["author"]["name"] == "Bob Builder"
    assert (await client.get("/api/v1/tickets/TKT-2026-05673")).status_code == 200


@pytest.mark.asyncio
async def test_private_notes_only_when_asked_for(api):
    client, *_ = api
    data = (await client.get("/api/v1/tickets/TKT-2026-05337?include_internal=true")).json()
    note = [c for c in data["comments"] if c["is_internal"]]
    assert [c["body"] for c in note] == ["private: check toner"]


@pytest.mark.asyncio
async def test_the_fields_a_dashboard_needs(api):
    client, _, _, u, t, _ = api
    d = (await client.get("/api/v1/tickets/TKT-2026-05337")).json()
    busy = t["busy"]
    assert d["created_at"].endswith("+00:00") and d["end_date"].endswith("+00:00")
    assert d["assigned_to"] == {"id": u["bob"].id, "name": "Bob Builder"}
    assert d["created_by"]["type"] == "customer" and d["origin"] == "customer_request"
    # The first reply the customer saw: not the customer's own message, not the private note
    assert d["first_reply_at"] == (busy.created_at + timedelta(hours=2)).isoformat() + "+00:00"
    assert d["first_reply_by"]["id"] == u["bob"].id
    assert d["first_assigned_at"] == (busy.created_at + timedelta(minutes=30)).isoformat() + "+00:00"
    assert d["last_activity_at"] == (busy.created_at + timedelta(hours=7)).isoformat() + "+00:00"
    assert d["reopened_count"] == 1
    assert d["coverage"] == "sla" and d["is_past_end_date"] is True
    assert d["project"]["name"] == "Office move"
    assert d["time_worked_seconds"] == 3600
    assert d["billing"]["billable"]["remote_labour"] == "1h"
    statuses = [(h["from"], h["to"]) for h in d["history"] if h["action"] == "status_changed"]
    assert statuses == [("open", "closed"), ("closed", "in_progress")]
    assigned = [h for h in d["history"] if h["action"] == "assigned"][0]
    assert assigned["to"] == {"id": u["bob"].id, "name": "Bob Builder"} and assigned["from"] is None
    assert d["time_entries"][0]["seconds"] == 3600

    internal = (await client.get("/api/v1/tickets/TKT-2026-05673")).json()
    assert internal["origin"] == "internal"
    assert internal["created_by"] == {"type": "staff", "id": u["ada"].id, "name": "ada"}
    assert internal["first_reply_at"] is None and internal["assigned_to"] is None


@pytest.mark.asyncio
async def test_list_matches_detail_and_pages(api):
    client, *_ = api
    detail = (await client.get("/api/v1/tickets/TKT-2026-05337")).json()
    page = (await client.get("/api/v1/tickets?include=comments,history,time_entries&limit=1&sort=id")).json()
    assert page["total"] == 2 and page["count"] == 1 and page["next_offset"] == 1
    assert page["tickets"][0] == detail
    rest = (await client.get("/api/v1/tickets?limit=1&offset=1&sort=id")).json()
    assert rest["next_offset"] is None and rest["tickets"][0]["ticket_number"] == "TKT-2026-05673"
    numbers = [x["ticket_number"] for x in (await client.get("/api/v1/tickets")).json()["tickets"]]
    assert "TKT-2026-09999" not in numbers          # another company's ticket


@pytest.mark.asyncio
async def test_filters(api):
    client, db, _, u, t, _ = api

    async def numbers(query):
        r = await client.get(f"/api/v1/tickets?{query}")
        assert r.status_code == 200, r.text
        return [x["ticket_number"] for x in r.json()["tickets"]]

    assert await numbers("status=open,in_progress&sort=id") == ["TKT-2026-05337", "TKT-2026-05673"]
    assert await numbers("assigned_to=none") == ["TKT-2026-05673"]
    assert await numbers(f"assigned_to={u['bob'].id}") == ["TKT-2026-05337"]
    since = (NOW - timedelta(hours=1)).isoformat() + "Z"
    assert await numbers(f"updated_since={since}") == []
    # A comment is a change, even though the ticket row itself did not move
    db.add(TicketComment(ticket_id=t["internal"].id, user_id=u["bob"].id, content="Ordered", created_at=NOW))
    await db.commit()
    assert await numbers(f"updated_since={since}") == ["TKT-2026-05673"]
    bad = await client.get("/api/v1/tickets?updated_since=yesterday")
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_other_company_and_bad_key(api):
    client, *_ = api
    assert (await client.get("/api/v1/tickets/TKT-2026-09999")).status_code == 404
    r = await client.get("/api/v1/tickets", headers={"X-API-Key": "cem_wrong"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_changes_through_the_api_are_in_the_history(api):
    client, *_ = api
    r = await client.patch("/api/v1/tickets/TKT-2026-05673", json={"status": "resolved", "priority": "high"})
    assert r.status_code == 200
    d = (await client.get("/api/v1/tickets/TKT-2026-05673")).json()
    actions = {(h["action"], h["from"], h["to"]) for h in d["history"]}
    assert ("status_changed", "open", "resolved") in actions and ("priority_changed", "medium", "high") in actions
    assert d["resolved_at"]

    created = await client.post("/api/v1/tickets", json={"subject": "From monitoring"})
    assert created.status_code == 201
    made = (await client.get(f"/api/v1/tickets/{created.json()['ticket_number']}")).json()
    assert made["origin"] == "system" and made["created_by"]["type"] == "system"


@pytest.mark.asyncio
async def test_people_tasks_projects_and_time(api):
    client, _, _, u, _, k = api
    users = {x["username"]: x for x in (await client.get("/api/v1/users")).json()["users"]}
    assert users["dan"]["is_active"] is False and users["ada"]["role"] == "admin"
    assert "olive" not in users

    tasks = {x["title"]: x for x in (await client.get("/api/v1/tasks")).json()["tasks"]}
    assert tasks["Order cables"]["is_blocked"] and tasks["Order cables"]["is_overdue"]
    assert tasks["Order cables"]["assignees"] == [{"id": u["bob"].id, "name": "Bob Builder"}]
    assert tasks["Pack boxes"]["completed_at"].endswith("+00:00")
    assert tasks["Label desks"]["assignees"] == []
    nobody = (await client.get("/api/v1/tasks?assignee_id=none")).json()["tasks"]
    assert [x["title"] for x in nobody] == ["Label desks"]

    project = (await client.get("/api/v1/projects")).json()["projects"][0]
    assert (project["tasks"], project["tasks_done"], project["tasks_overdue"], project["tasks_blocked"]) == (3, 1, 1, 1)
    assert project["time_logged_hours"] == 1.5

    entries = (await client.get("/api/v1/time-entries")).json()["entries"]
    kinds = sorted(e["kind"] for e in entries)
    assert kinds == ["manual", "task", "ticket"]
    running = [e for e in entries if e["running"]]
    assert len(running) == 1 and running[0]["task"]["title"] == "Order cables"


@pytest.mark.asyncio
async def test_overview_matches_the_page(api):
    client, db, acme, *_ = api
    data = (await client.get("/api/v1/overview")).json()
    page = await build_overview(db, acme)
    assert data["open_tickets"] == page["open_tickets"] == 2
    assert data["waiting_for_first_reply"] == page["tickets"]["no_reply"]
    assert data["tickets_unassigned"] == page["tickets"]["unassigned"] == 1
    assert data["tickets_past_end_date"] == page["tickets"]["overdue"] == 1
    assert data["tasks_overdue"] == page["tasks"]["overdue"]
    assert data["tasks_blocked"] == page["tasks"]["blocked"] == 1
    assert data["people_on_a_timer"] == page["timers_running"] == 1
    assert data["still_assigned_to_deactivated"] == {"tickets": 0, "tasks": 0}
    assert set(data["last_7_days"]) == {"tickets_in", "tickets_done", "replies", "tasks_done", "backlog_change"}
    on_timer = [p for p in data["team"] if p["working_on"]][0]
    assert on_timer["working_on"]["since"].endswith("+00:00")


@pytest.mark.asyncio
async def test_new_tickets_get_a_free_number_whatever_the_company(api):
    """Numbers are unique across companies. Counting this company's tickets
    gave a number another company (or a deleted ticket) already had."""
    client, db, *_ = api
    prefix = f"TKT-{datetime.utcnow().year}-"
    taken = (await db.execute(sqlmodel_select(Ticket.ticket_number))).scalars().all()
    highest = max((int(n[len(prefix):]) for n in taken if n.startswith(prefix)), default=0)
    first = await client.post("/api/v1/tickets", json={"subject": "From the monitor"})
    second = await client.post("/api/v1/tickets", json={"subject": "And another"})
    assert (first.status_code, second.status_code) == (201, 201)
    assert [first.json()["ticket_number"], second.json()["ticket_number"]] == \
        [f"{prefix}{highest + 1:05d}", f"{prefix}{highest + 2:05d}"]


@pytest.mark.asyncio
async def test_only_values_the_helpdesk_knows(api):
    """Any text was saved as a status, and then counted as open for good."""
    client, *_ = api
    for body in ({"status": "banana"}, {"priority": "whenever"}):
        assert (await client.patch("/api/v1/tickets/TKT-2026-05337", json=body)).status_code == 422, body
    assert (await client.post("/api/v1/tickets", json={"subject": ""})).status_code == 422
    assert (await client.post("/api/v1/tickets", json={"subject": "x", "priority": "asap"})).status_code == 422
    body = (await client.get("/api/v1/tickets/TKT-2026-05337")).json()
    assert body.get("ticket", body)["status"] != "banana"
    edge = await client.get("/api/v1/tickets", params={"updated_since": "0001-01-01T00:00:00+05:00"})
    assert edge.status_code == 400, "a time at the edge of the calendar is a bad request, not a crash"
