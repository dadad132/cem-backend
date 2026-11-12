"""
Public REST API — secured by API key.
External programs call these endpoints using the header:
    X-API-Key: cem_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

All responses are JSON.  Base path: /api/v1/

Times carry their offset. Times the system records (created, closed, a
comment written) are UTC, "+00:00". Times a person picks (a ticket's start
and end date) are the company's own wall-clock time, with its offset.

For a sync that only fetches what changed: GET /tickets?updated_since=...
counts any comment, status change, reassignment or timer as a change, and
returns the same fields as GET /tickets/{number}; add include=comments,
history,time_entries for those too, so no call per ticket is needed.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_hours import calculate_business_hours
from app.core.clock import local_now, local_today, to_local, workspace_zone
from app.core.database import get_session
from app.core.team_overview import build_overview, project_counts
from app.core.work_timer import TICKET_DONE_STATUSES, stop_timer, worked_seconds
from app.models.api_key import APIKey
from app.models.assignment import Assignment
from app.models.enums import TaskStatus
from app.models.project import Project
from app.models.task import Task
from app.models.task_extensions import TaskTimeEntry, TimeLog
from app.models.ticket import Ticket, TicketComment, TicketHistory, TicketTimeEntry
from app.models.user import User
from app.models.workspace import Workspace

router = APIRouter(prefix="/v1", tags=["External API"])

TICKET_INCLUDES = ("comments", "history", "time_entries")
BILLING_FIELDS = (("travel", "traveling"), ("labour_onsite", "labour_onsite"),
                  ("remote_labour", "remote_labour"), ("equipment", "equipment_used"))


# ---------------------------------------------------------------------------
# API-key dependency
# ---------------------------------------------------------------------------

async def require_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_session),
) -> tuple[APIKey, Workspace]:
    """Validate the X-API-Key header and return (api_key_row, workspace)."""
    if not x_api_key or not x_api_key.startswith("cem_"):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    api_key = (
        await db.execute(select(APIKey).where(APIKey.key_hash == key_hash))
    ).scalar_one_or_none()

    if not api_key or not api_key.is_active:
        raise HTTPException(status_code=401, detail="API key not found or disabled")

    workspace = (
        await db.execute(select(Workspace).where(Workspace.id == api_key.workspace_id))
    ).scalar_one_or_none()

    if not workspace:
        raise HTTPException(status_code=401, detail="Workspace not found")

    # A dashboard calls every few seconds: note the use at most every few
    # minutes, rather than writing to the database on every call
    now = datetime.utcnow()
    if not api_key.last_used_at or now - api_key.last_used_at > timedelta(minutes=5):
        api_key.last_used_at = now
        await db.commit()

    return api_key, workspace


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

# Only the values the helpdesk knows: anything else would count as open forever
TicketStatusValue = Literal["open", "in_progress", "waiting", "resolved", "closed"]
TicketPriorityValue = Literal["low", "medium", "high", "urgent"]


class TicketCreateRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=50000)
    priority: Optional[TicketPriorityValue] = "medium"
    category: Optional[str] = Field("general", max_length=50)
    guest_name: Optional[str] = Field(None, max_length=200)
    guest_surname: Optional[str] = Field(None, max_length=200)
    guest_email: Optional[str] = Field(None, max_length=254)
    guest_phone: Optional[str] = Field(None, max_length=50)
    guest_company: Optional[str] = Field(None, max_length=200)


class TicketUpdateRequest(BaseModel):
    status: Optional[TicketStatusValue] = None
    priority: Optional[TicketPriorityValue] = None
    category: Optional[str] = Field(None, max_length=50)


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------

def _utc(value) -> Optional[str]:
    """A time the system recorded (stored as UTC), with its offset."""
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.isoformat()
    return value.isoformat()


def _picked(value, zone) -> Optional[str]:
    """A time a person picked (an end date), stored as the company's local
    wall-clock time, with that zone's offset. A plain date stays a date."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        return value.isoformat()
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone) if zone is not None else value.astimezone()
    return value.isoformat()


def _since(value: Optional[str], name: str) -> Optional[datetime]:
    """An ISO 8601 time from a query string, as a stored (UTC) time. One
    without an offset is taken as UTC."""
    if not value:
        return None
    text = value.strip()
    if "T" in text:
        text = text.replace(" ", "+")      # a "+" not URL-encoded arrives as a space
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):  # (OverflowError: a time at the edge of the calendar)
        raise HTTPException(status_code=400,
                            detail=f"{name}: use an ISO 8601 time, like 2026-11-11T04:56:01Z")
    return moment


def _jsonable(value):
    """Datetimes and dates in a nested structure as ISO text (UTC)."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return _utc(value)
    return value


def _page(rows: list, total: int, limit: int, offset: int) -> dict:
    nxt = offset + len(rows)
    return {"total": total, "count": len(rows), "limit": limit, "offset": offset,
            "next_offset": nxt if nxt < total else None}


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

async def _users(db: AsyncSession, workspace_id: int, extra_ids=()) -> dict:
    """{id: User} for the company, plus anyone else referred to by id."""
    users = {u.id: u for u in (await db.execute(
        select(User).where(User.workspace_id == workspace_id))).scalars().all()}
    missing = {i for i in extra_ids if i and i not in users}
    if missing:
        users.update({u.id: u for u in (await db.execute(
            select(User).where(User.id.in_(missing)))).scalars().all()})
    return users


def _person(users: dict, user_id) -> Optional[dict]:
    if not user_id:
        return None
    u = users.get(user_id)
    return {"id": user_id, "name": (u.shown_name or u.username) if u else None}


def _customer_name(t: Ticket) -> Optional[str]:
    return " ".join(p for p in (t.guest_name, t.guest_surname) if p) or t.guest_email or None


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

async def _ticket_context(db: AsyncSession, workspace, tickets: list, include: set,
                          include_internal: bool) -> dict:
    """Everything the ticket fields need, fetched once for a whole page."""
    ids = [t.id for t in tickets]
    ctx = {"first_reply": {}, "last_comment": {}, "history": {}, "worked": {},
           "entries": {}, "comments": {}, "projects": {}}
    if not ids:
        ctx["users"] = {}
        return ctx

    # The first reply the customer saw: a staff comment that is not a private note
    for ticket_id, user_id, created in (await db.execute(
        select(TicketComment.ticket_id, TicketComment.user_id, TicketComment.created_at)
        .where(TicketComment.ticket_id.in_(ids), TicketComment.is_internal == False,
               TicketComment.user_id.is_not(None))
        .order_by(TicketComment.created_at.asc(), TicketComment.id.asc())
    )).all():
        ctx["first_reply"].setdefault(ticket_id, (created, user_id))
    for ticket_id, last in (await db.execute(
        select(TicketComment.ticket_id, func.max(TicketComment.created_at))
        .where(TicketComment.ticket_id.in_(ids)).group_by(TicketComment.ticket_id)
    )).all():
        ctx["last_comment"][ticket_id] = last
    for h in (await db.execute(
        select(TicketHistory).where(TicketHistory.ticket_id.in_(ids))
        .order_by(TicketHistory.created_at.asc(), TicketHistory.id.asc())
    )).scalars().all():
        ctx["history"].setdefault(h.ticket_id, []).append(h)
    for e in (await db.execute(
        select(TicketTimeEntry).where(TicketTimeEntry.ticket_id.in_(ids))
        .order_by(TicketTimeEntry.started_at.asc())
    )).scalars().all():
        ctx["entries"].setdefault(e.ticket_id, []).append(e)
    now = datetime.utcnow()
    for ticket_id, entries in ctx["entries"].items():
        ctx["worked"][ticket_id] = worked_seconds(entries, now)
    if "comments" in include:
        query = select(TicketComment).where(TicketComment.ticket_id.in_(ids))
        if not include_internal:
            query = query.where(TicketComment.is_internal == False)
        for c in (await db.execute(
            query.order_by(TicketComment.created_at.asc(), TicketComment.id.asc())
        )).scalars().all():
            ctx["comments"].setdefault(c.ticket_id, []).append(c)

    project_ids = {t.related_project_id for t in tickets if t.related_project_id}
    if project_ids:
        ctx["projects"] = dict((await db.execute(
            select(Project.id, Project.name).where(Project.id.in_(project_ids))
        )).all())
    referred = set()
    for t in tickets:
        referred |= {t.assigned_to_id, t.created_by_id, t.closed_by_id}
    for rows in ctx["history"].values():
        for h in rows:
            referred.add(h.user_id)
            if h.action == "assigned":
                referred |= {int(v) for v in (h.old_value, h.new_value) if v and str(v).isdigit()}
    for rows in ctx["entries"].values():
        referred |= {e.user_id for e in rows}
    for rows in ctx["comments"].values():
        referred |= {c.user_id for c in rows}
    referred |= {uid for _, uid in ctx["first_reply"].values()}
    ctx["users"] = await _users(db, workspace.id, referred)
    return ctx


def _origin(t: Ticket, history: list) -> tuple:
    """(who created it, where it came from)."""
    if any(h.action == "created" and h.new_value == "api" for h in history):
        return {"type": "system", "id": None, "name": "API"}, "system"
    if t.created_by_id:
        return None, ("customer_request" if (t.is_guest or t.guest_name or t.guest_email
                                             or t.guest_company) else "internal")
    if t.is_guest:
        return {"type": "customer", "id": None, "name": _customer_name(t)}, "customer_request"
    return {"type": "system", "id": None, "name": None}, "system"


def _history_item(h: TicketHistory, users: dict) -> dict:
    def value(v):
        if h.action == "assigned":
            return _person(users, int(v)) if v and str(v).isdigit() else None
        return v
    return {"id": h.id, "at": _utc(h.created_at), "by": _person(users, h.user_id),
            "action": h.action, "from": value(h.old_value), "to": value(h.new_value)}


def _ticket_to_dict(t: Ticket, ctx: Optional[dict] = None, zone=None, workspace=None,
                    include: frozenset = frozenset()) -> dict:
    data = {
        "id": t.id,
        "ticket_number": t.ticket_number,
        "subject": t.subject,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "category": t.category,
        "guest_name": t.guest_name,
        "guest_surname": t.guest_surname,
        "guest_email": t.guest_email,
        "guest_phone": t.guest_phone,
        "guest_company": t.guest_company,
        "created_at": _utc(t.created_at),
        "closed_at": _utc(t.closed_at),
    }
    if ctx is None:
        return data
    users = ctx["users"]
    history = ctx["history"].get(t.id, [])
    creator, origin = _origin(t, history)
    if creator is None:
        creator = {"type": "staff", **_person(users, t.created_by_id)}
    first = ctx["first_reply"].get(t.id)
    first_hours = None
    if first and workspace is not None:
        first_hours = round(calculate_business_hours(
            to_local(t.created_at, zone), to_local(first[0], zone),
            workspace.business_hours_start or "07:30", workspace.business_hours_end or "16:00",
            workspace.business_hours_exclude_weekends), 2)
    assigned = [h.created_at for h in history
                if h.action == "assigned" and h.new_value and str(h.new_value).isdigit()]
    reopened = sum(1 for h in history if h.action == "status_changed"
                   and h.old_value in TICKET_DONE_STATUSES and h.new_value not in TICKET_DONE_STATUSES)
    activity = [t.updated_at, t.created_at, ctx["last_comment"].get(t.id),
                history[-1].created_at if history else None]
    entries = ctx["entries"].get(t.id, [])
    activity += [e.ended_at or e.started_at for e in entries]
    is_open = t.status not in TICKET_DONE_STATUSES and not t.is_archived
    data.update({
        "customer": {"name": t.guest_name, "surname": t.guest_surname, "email": t.guest_email,
                     "phone": t.guest_phone, "company": t.guest_company, "company_id": None,
                     "branch": t.guest_branch, "office_number": t.guest_office_number},
        "assigned_to": _person(users, t.assigned_to_id),
        "created_by": creator,
        "origin": origin,
        "updated_at": _utc(t.updated_at),
        "last_activity_at": _utc(max(a for a in activity if a)),
        "first_reply_at": _utc(first[0]) if first else None,
        "first_reply_by": _person(users, first[1]) if first else None,
        "first_reply_business_hours": first_hours,
        "first_assigned_at": _utc(assigned[0]) if assigned else None,
        "resolved_at": _utc(t.resolved_at),
        "closed_by": _person(users, t.closed_by_id),
        "close_reason": t.closing_notes or None,
        "coverage": t.close_type,                       # "sla", "billable", or null
        "start_date": _picked(t.scheduled_date, zone),
        "end_date": _picked(t.due_date, zone),
        "is_past_end_date": bool(is_open and t.due_date and t.due_date < local_now(zone)),
        "project": ({"id": t.related_project_id, "name": ctx["projects"].get(t.related_project_id)}
                    if t.related_project_id else None),
        "is_archived": t.is_archived,
        "reopened_count": reopened,
        "time_worked_seconds": ctx["worked"].get(t.id, 0),
        "billing": {
            "billable": {k: getattr(t, f"billable_{f}") for k, f in BILLING_FIELDS},
            "non_billable": {k: getattr(t, f"non_billable_{f}") for k, f in BILLING_FIELDS},
        },
    })
    if "comments" in include:
        customer = _customer_name(t)
        data["comments"] = [{
            "id": c.id,
            "author": ({"type": "staff", **_person(users, c.user_id)} if c.user_id
                       else {"type": "customer", "id": None, "name": customer}),
            "created_at": _utc(c.created_at),
            "is_internal": c.is_internal,
            "body": c.content,
        } for c in ctx["comments"].get(t.id, [])]
    if "history" in include:
        data["history"] = [_history_item(h, users) for h in history]
    if "time_entries" in include:
        now = datetime.utcnow()
        data["time_entries"] = [{
            "id": e.id, "user": _person(users, e.user_id), "started_at": _utc(e.started_at),
            "ended_at": _utc(e.ended_at), "running": e.ended_at is None,
            "seconds": int(((e.ended_at or now) - e.started_at).total_seconds()),
        } for e in entries]
    return data


def _includes(value: Optional[str]) -> frozenset:
    wanted = {p.strip() for p in (value or "").split(",") if p.strip()}
    unknown = wanted - set(TICKET_INCLUDES)
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"include: unknown {', '.join(sorted(unknown))}; "
                                   f"choose from {', '.join(TICKET_INCLUDES)}")
    return frozenset(wanted)


def _changed_since(since: datetime):
    """A ticket with any activity since: an edit, a comment, a history entry
    (status, reassignment, priority), or a timer started or stopped."""
    return or_(
        Ticket.updated_at >= since, Ticket.created_at >= since,
        Ticket.closed_at >= since, Ticket.resolved_at >= since,
        exists().where(TicketComment.ticket_id == Ticket.id, TicketComment.created_at >= since),
        exists().where(TicketHistory.ticket_id == Ticket.id, TicketHistory.created_at >= since),
        exists().where(TicketTimeEntry.ticket_id == Ticket.id,
                       or_(TicketTimeEntry.started_at >= since, TicketTimeEntry.ended_at >= since)),
    )


# ---------------------------------------------------------------------------
# GET /api/v1/tickets
# ---------------------------------------------------------------------------

@router.get("/tickets")
async def list_tickets(
    status: Optional[str] = Query(None, description="Filter by status; several separated by commas"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    assigned_to: Optional[str] = Query(None, description="A user id, or 'none' for unassigned"),
    project_id: Optional[int] = Query(None, description="Filter by project"),
    updated_since: Optional[str] = Query(None, description="Only tickets with any activity since this time"),
    created_since: Optional[str] = Query(None, description="Only tickets created since this time"),
    include: Optional[str] = Query(None, description="comments,history,time_entries"),
    include_internal: bool = Query(False, description="With include=comments: also private notes"),
    sort: str = Query("-created_at", description="-created_at, created_at, id or -id"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    offset: int = Query(0, ge=0, description="Skip N results"),
    auth=Depends(require_api_key),
    db: AsyncSession = Depends(get_session),
):
    """List tickets for your workspace, with the same fields as a single ticket."""
    _, workspace = auth
    wanted = _includes(include)
    conditions = [Ticket.workspace_id == workspace.id]
    if status:
        conditions.append(Ticket.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if priority:
        conditions.append(Ticket.priority == priority)
    if assigned_to:
        if assigned_to.strip().lower() == "none":
            conditions.append(Ticket.assigned_to_id.is_(None))
        elif assigned_to.strip().isdecimal():
            conditions.append(Ticket.assigned_to_id == int(assigned_to))
        else:
            raise HTTPException(status_code=400, detail="assigned_to: a user id, or 'none'")
    if project_id is not None:
        conditions.append(Ticket.related_project_id == project_id)
    changed = _since(updated_since, "updated_since")
    if changed:
        conditions.append(_changed_since(changed))
    created = _since(created_since, "created_since")
    if created:
        conditions.append(Ticket.created_at >= created)
    order = {"-created_at": (Ticket.created_at.desc(), Ticket.id.desc()),
             "created_at": (Ticket.created_at.asc(), Ticket.id.asc()),
             "id": (Ticket.id.asc(),), "-id": (Ticket.id.desc(),)}.get(sort)
    if order is None:
        raise HTTPException(status_code=400, detail="sort: -created_at, created_at, id or -id")

    where = and_(*conditions)
    total = await db.scalar(select(func.count()).select_from(Ticket).where(where)) or 0
    tickets = (await db.execute(
        select(Ticket).where(where).order_by(*order).offset(offset).limit(limit)
    )).scalars().all()
    zone = workspace_zone(workspace)
    ctx = await _ticket_context(db, workspace, tickets, wanted, include_internal)
    return JSONResponse({
        **_page(tickets, total, limit, offset),
        "timezone": workspace.timezone or None,
        "tickets": [_ticket_to_dict(t, ctx, zone, workspace, wanted) for t in tickets],
    })


# ---------------------------------------------------------------------------
# GET /api/v1/tickets/{ticket_number}
# ---------------------------------------------------------------------------

@router.get("/tickets/{ticket_number}")
async def get_ticket(
    ticket_number: str,
    include_internal: bool = Query(False, description="Also private notes"),
    auth=Depends(require_api_key),
    db: AsyncSession = Depends(get_session),
):
    """Get a single ticket by ticket number (e.g. TKT-2024-00001), with its
    comments, history and timer entries."""
    _, workspace = auth
    ticket = (
        await db.execute(
            select(Ticket).where(
                Ticket.workspace_id == workspace.id,
                Ticket.ticket_number == ticket_number,
            )
        )
    ).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    wanted = frozenset(TICKET_INCLUDES)
    ctx = await _ticket_context(db, workspace, [ticket], wanted, include_internal)
    return JSONResponse(_ticket_to_dict(ticket, ctx, workspace_zone(workspace), workspace, wanted))


# ---------------------------------------------------------------------------
# POST /api/v1/tickets
# ---------------------------------------------------------------------------

@router.post("/tickets", status_code=201)
async def create_ticket(
    payload: TicketCreateRequest,
    auth=Depends(require_api_key),
    db: AsyncSession = Depends(get_session),
):
    """Create a new ticket in your workspace."""
    _, workspace = auth

    for attempt in range(3):
        ticket = Ticket(
            workspace_id=workspace.id,
            ticket_number=await _next_ticket_number(db),
            subject=payload.subject,
            description=payload.description,
            priority=payload.priority or "medium",
            category=payload.category or "general",
            status="open",
            is_guest=True,
            guest_name=payload.guest_name,
            guest_surname=payload.guest_surname,
            guest_email=payload.guest_email,
            guest_phone=payload.guest_phone,
            guest_company=payload.guest_company,
            created_at=datetime.utcnow(),
        )
        db.add(ticket)
        try:
            await db.flush()
            break
        except IntegrityError:  # someone took that number at the same moment
            await db.rollback()
            if attempt == 2:
                raise HTTPException(status_code=409, detail="Could not give the ticket a number; try again")
    # Marks it as made by a program, so its origin reads "system"
    db.add(TicketHistory(ticket_id=ticket.id, action="created", new_value="api"))
    await db.commit()
    await db.refresh(ticket)
    return JSONResponse(_ticket_to_dict(ticket), status_code=201)


async def _next_ticket_number(db: AsyncSession) -> str:
    """The next free ticket number. Numbers are unique across every company,
    so take the highest of the year anywhere, as the e-mail importer does
    (counting this company's tickets reused numbers and failed)."""
    prefix = f"TKT-{datetime.utcnow().year}-"
    numbers = (await db.execute(
        select(Ticket.ticket_number).where(Ticket.ticket_number.like(f"{prefix}%"))
    )).scalars().all()
    highest = max((int(n[len(prefix):]) for n in numbers if n[len(prefix):].isdecimal()), default=0)
    return f"{prefix}{highest + 1:05d}"


# ---------------------------------------------------------------------------
# PATCH /api/v1/tickets/{ticket_number}
# ---------------------------------------------------------------------------

@router.patch("/tickets/{ticket_number}")
async def update_ticket(
    ticket_number: str,
    payload: TicketUpdateRequest,
    auth=Depends(require_api_key),
    db: AsyncSession = Depends(get_session),
):
    """Update ticket status, priority, or category. Each change is recorded
    in the ticket's history, as changes made in the helpdesk are."""
    _, workspace = auth
    ticket = (
        await db.execute(
            select(Ticket).where(
                Ticket.workspace_id == workspace.id,
                Ticket.ticket_number == ticket_number,
            )
        )
    ).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    now = datetime.utcnow()

    def record(action, old, new):
        if old != new:
            db.add(TicketHistory(ticket_id=ticket.id, action=action, old_value=old,
                                 new_value=new, created_at=now))

    if payload.status:
        record("status_changed", ticket.status, payload.status)
        ticket.status = payload.status
        if payload.status == "resolved" and not ticket.resolved_at:
            ticket.resolved_at = now
        if payload.status == "closed" and not ticket.closed_at:
            ticket.closed_at = now
        if payload.status in TICKET_DONE_STATUSES:
            await stop_timer(db, TicketTimeEntry.ticket_id, ticket.id)
    if payload.priority:
        record("priority_changed", ticket.priority, payload.priority)
        ticket.priority = payload.priority
    if payload.category:
        record("category_changed", ticket.category, payload.category)
        ticket.category = payload.category
    if payload.status or payload.priority or payload.category:
        ticket.updated_at = now

    await db.commit()
    await db.refresh(ticket)
    return JSONResponse(_ticket_to_dict(ticket))


# ---------------------------------------------------------------------------
# GET /api/v1/users
# ---------------------------------------------------------------------------

@router.get("/users")
async def list_users(
    auth=Depends(require_api_key),
    db: AsyncSession = Depends(get_session),
):
    """Everyone in your workspace, including deactivated accounts."""
    _, workspace = auth
    users = (await db.execute(
        select(User).where(User.workspace_id == workspace.id).order_by(User.id)
    )).scalars().all()
    return JSONResponse({"count": len(users), "users": [{
        "id": u.id, "name": u.shown_name or u.username, "username": u.username,
        "email": u.email, "is_active": u.is_active, "role": "admin" if u.is_admin else "member",
        "sees_all_tickets": bool(u.is_admin or u.can_see_all_tickets),
    } for u in users]})


# ---------------------------------------------------------------------------
# GET /api/v1/tasks
# ---------------------------------------------------------------------------

@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = Query(None, description="todo, in_progress, blocked, done; several separated by commas"),
    project_id: Optional[int] = Query(None),
    assignee_id: Optional[str] = Query(None, description="A user id, or 'none' for nobody"),
    updated_since: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    auth=Depends(require_api_key),
    db: AsyncSession = Depends(get_session),
):
    """Tasks in your workspace's projects."""
    _, workspace = auth
    conditions = [Project.workspace_id == workspace.id]
    if status:
        conditions.append(Task.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if project_id is not None:
        conditions.append(Task.project_id == project_id)
    if assignee_id:
        if assignee_id.strip().lower() == "none":
            conditions.append(~exists().where(Assignment.task_id == Task.id))
        elif assignee_id.strip().isdecimal():
            conditions.append(exists().where(Assignment.task_id == Task.id,
                                             Assignment.assignee_id == int(assignee_id)))
        else:
            raise HTTPException(status_code=400, detail="assignee_id: a user id, or 'none'")
    changed = _since(updated_since, "updated_since")
    if changed:
        conditions.append(or_(Task.updated_at >= changed, Task.created_at >= changed,
                              Task.archived_at >= changed))
    where = and_(*conditions)
    total = await db.scalar(
        select(func.count()).select_from(Task).join(Project, Project.id == Task.project_id).where(where)) or 0
    rows = (await db.execute(
        select(Task, Project.name).join(Project, Project.id == Task.project_id)
        .where(where).order_by(Task.id.asc()).offset(offset).limit(limit)
    )).all()
    ids = [task.id for task, _ in rows]
    holders, worked, entries = {}, {}, {}
    if ids:
        for task_id, user_id in (await db.execute(
            select(Assignment.task_id, Assignment.assignee_id).where(Assignment.task_id.in_(ids))
        )).all():
            holders.setdefault(task_id, []).append(user_id)
        for e in (await db.execute(
            select(TaskTimeEntry).where(TaskTimeEntry.task_id.in_(ids))
        )).scalars().all():
            entries.setdefault(e.task_id, []).append(e)
        now = datetime.utcnow()
        worked = {task_id: worked_seconds(es, now) for task_id, es in entries.items()}
    users = await _users(db, workspace.id, {u for us in holders.values() for u in us})
    today = local_today(workspace_zone(workspace))
    tasks = []
    for task, project_name in rows:
        status_value = getattr(task.status, "value", task.status)
        done = task.status == TaskStatus.done
        tasks.append({
            "id": task.id, "title": task.title,
            "project": {"id": task.project_id, "name": project_name},
            "parent_task_id": task.parent_task_id,
            "assignees": [_person(users, u) for u in dict.fromkeys(holders.get(task.id, []))],
            "status": status_value,
            "priority": getattr(task.priority, "value", task.priority),
            "is_blocked": task.status == TaskStatus.blocked,
            "start_date": _utc(task.start_date),
            "due_date": _utc(task.due_date),
            "is_overdue": bool(task.due_date and task.due_date < today and not done and not task.is_archived),
            "created_at": _utc(task.created_at),
            "updated_at": _utc(task.updated_at),
            # Finished tasks are archived as they finish, and later edits leave archived_at alone
            "completed_at": _utc(task.archived_at) if done else None,
            "is_archived": task.is_archived,
            "coverage": task.close_type,
            "time_worked_seconds": worked.get(task.id, 0),
        })
    return JSONResponse({**_page(rows, total, limit, offset), "tasks": tasks})


# ---------------------------------------------------------------------------
# GET /api/v1/projects
# ---------------------------------------------------------------------------

@router.get("/projects")
async def list_projects(
    include_archived: bool = Query(False),
    auth=Depends(require_api_key),
    db: AsyncSession = Depends(get_session),
):
    """Projects with their progress, counted the way the Team Overview counts."""
    _, workspace = auth
    query = select(Project).where(Project.workspace_id == workspace.id)
    if not include_archived:
        query = query.where(Project.is_archived == False)
    projects = (await db.execute(query.order_by(Project.id))).scalars().all()
    ids = [p.id for p in projects]
    today = local_today(workspace_zone(workspace))
    counts = await project_counts(db, ids, today)
    per_task, manual = {}, {}
    if ids:
        for project_id, task_id, started_at, ended_at in (await db.execute(
            select(Task.project_id, TaskTimeEntry.task_id, TaskTimeEntry.started_at, TaskTimeEntry.ended_at)
            .join(Task, Task.id == TaskTimeEntry.task_id).where(Task.project_id.in_(ids))
        )).all():
            per_task.setdefault((project_id, task_id), []).append(
                SimpleNamespace(started_at=started_at, ended_at=ended_at))
        manual = dict((await db.execute(
            select(Task.project_id, func.sum(TimeLog.hours)).join(Task, Task.id == TimeLog.task_id)
            .where(Task.project_id.in_(ids)).group_by(Task.project_id)
        )).all())
    now = datetime.utcnow()
    worked = {}
    for (project_id, _), entries in per_task.items():
        worked[project_id] = worked.get(project_id, 0) + worked_seconds(entries, now)
    return JSONResponse({"count": len(projects), "projects": [{
        "id": p.id, "name": p.name, "is_archived": p.is_archived,
        "start_date": _utc(p.start_date), "due_date": _utc(p.due_date),
        "tasks": counts[p.id]["total"], "tasks_done": counts[p.id]["done"],
        "tasks_open": counts[p.id]["total"] - counts[p.id]["done"],
        "tasks_overdue": counts[p.id]["overdue"], "tasks_blocked": counts[p.id]["blocked"],
        "percent_done": counts[p.id]["percent"],
        "time_worked_seconds": worked.get(p.id, 0),
        "time_logged_hours": round(float(manual.get(p.id) or 0), 2),
    } for p in projects]})


# ---------------------------------------------------------------------------
# GET /api/v1/time-entries
# ---------------------------------------------------------------------------

@router.get("/time-entries")
async def list_time_entries(
    since: Optional[str] = Query(None, description="Default: 30 days ago"),
    until: Optional[str] = Query(None, description="Default: now"),
    user_id: Optional[int] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    auth=Depends(require_api_key),
    db: AsyncSession = Depends(get_session),
):
    """Timer entries on tickets and tasks, and hours typed in by hand, that
    overlap the period. Running timers count up to now."""
    _, workspace = auth
    now = datetime.utcnow()
    start = _since(since, "since") or now - timedelta(days=30)
    end = _since(until, "until") or now
    rows = []

    def overlaps(model):
        cond = [model.started_at < end, or_(model.ended_at.is_(None), model.ended_at >= start)]
        if user_id is not None:
            cond.append(model.user_id == user_id)
        return cond

    for e, number in (await db.execute(
        select(TicketTimeEntry, Ticket.ticket_number).join(Ticket, Ticket.id == TicketTimeEntry.ticket_id)
        .where(Ticket.workspace_id == workspace.id, *overlaps(TicketTimeEntry))
    )).all():
        rows.append(("ticket", e, {"id": e.ticket_id, "ticket_number": number}, None))
    for e, title, project_id in (await db.execute(
        select(TaskTimeEntry, Task.title, Task.project_id).join(Task, Task.id == TaskTimeEntry.task_id)
        .join(Project, Project.id == Task.project_id)
        .where(Project.workspace_id == workspace.id, *overlaps(TaskTimeEntry))
    )).all():
        rows.append(("task", e, None, {"id": e.task_id, "title": title, "project_id": project_id}))
    manual_cond = [Project.workspace_id == workspace.id, TimeLog.logged_at >= start, TimeLog.logged_at < end]
    if user_id is not None:
        manual_cond.append(TimeLog.user_id == user_id)
    for log, title, project_id in (await db.execute(
        select(TimeLog, Task.title, Task.project_id).join(Task, Task.id == TimeLog.task_id)
        .join(Project, Project.id == Task.project_id).where(*manual_cond)
    )).all():
        rows.append(("manual", log, None, {"id": log.task_id, "title": title, "project_id": project_id}))

    users = await _users(db, workspace.id, {r[1].user_id for r in rows})
    out = []
    for kind, e, ticket, task in rows:
        if kind == "manual":
            item = {"id": f"manual-{e.id}", "kind": kind, "started_at": _utc(e.logged_at),
                    "ended_at": None, "running": False, "seconds": int(e.hours * 3600)}
            moment = e.logged_at
        else:
            item = {"id": f"{kind}-{e.id}", "kind": kind, "started_at": _utc(e.started_at),
                    "ended_at": _utc(e.ended_at), "running": e.ended_at is None,
                    "seconds": int(((e.ended_at or now) - e.started_at).total_seconds())}
            moment = e.started_at
        item.update({"user": _person(users, e.user_id), "ticket": ticket, "task": task})
        out.append((moment, item))
    out.sort(key=lambda pair: (pair[0], pair[1]["id"]))
    page = [item for _, item in out[offset:offset + limit]]
    return JSONResponse({**_page(page, len(out), limit, offset),
                         "since": _utc(start), "until": _utc(end), "entries": page})


# ---------------------------------------------------------------------------
# GET /api/v1/overview
# ---------------------------------------------------------------------------

@router.get("/overview")
async def overview(
    auth=Depends(require_api_key),
    db: AsyncSession = Depends(get_session),
):
    """The numbers behind the helpdesk's Team Overview page, worked out by the
    same code, so a dashboard and the page always agree."""
    _, workspace = auth
    data = await build_overview(db, workspace)
    return JSONResponse(_jsonable({
        "generated_at": datetime.utcnow(),
        "open_tickets": data["open_tickets"],
        "waiting_for_first_reply": data["tickets"]["no_reply"],
        "tickets_unassigned": data["tickets"]["unassigned"],
        "tickets_past_end_date": data["tickets"]["overdue"],
        "tickets_quiet": data["tickets"]["quiet"],
        "tasks_overdue": data["tasks"]["overdue"],
        "tasks_blocked": data["tasks"]["blocked"],
        "tasks_unassigned": data["tasks"]["unassigned"],
        "needs_someone": data["needs_someone"],
        "people_on_a_timer": data["timers_running"],
        "average_first_reply_business_hours_30d": data["first_reply_hours"],
        "last_24_hours": data["last_day"],
        "last_7_days": data["last_week"],
        "still_assigned_to_deactivated": data["left_behind"],
        "team": data["team"],
        "projects": data["projects"],
    }))


# ---------------------------------------------------------------------------
# GET /api/v1/ping  — connectivity test (no auth needed)
# ---------------------------------------------------------------------------

@router.get("/ping")
async def ping():
    """Quick connectivity check. No API key required."""
    return JSONResponse({"status": "ok", "message": "CEM API is reachable"})
