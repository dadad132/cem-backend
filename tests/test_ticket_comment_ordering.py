"""Active tickets are ordered by their latest comment: oldest activity at the top.

A new comment on any active ticket (public, internal, guest reply, e-mail or API)
refreshes that ticket's MAX(TicketComment.created_at), which moves it down both
the Board and the List view. Search keeps relevance first; Team Overview
drill-downs keep their oldest-created-first order.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel
from starlette.datastructures import QueryParams

from app.models.project import Project
from app.models.ticket import Ticket, TicketComment
from app.models.user import User
from app.models.workspace import Workspace
from app.web import routes as web
from app.web.routes import web_tickets_add_comment, web_tickets_list, web_tickets_track_reply

NOW = datetime.utcnow().replace(microsecond=0)
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'templates' / 'tickets' / 'list.html'
COLUMNS = ('open', 'in_progress', 'waiting')


@pytest.fixture(autouse=True)
def _legacy_template_call(monkeypatch):
    """Keep the repo's legacy TemplateResponse call shape working in this venv."""
    def render(name, context, *args, **kwargs):
        return Jinja2Templates.TemplateResponse(
            web.templates, context.get('request'), name, context, *args, **kwargs
        )

    monkeypatch.setattr(web, '_original_template_response', render)


class _Form(dict):
    def getlist(self, key):
        return self.get(key, [])


def _request(user, query=None, form=None, session=None, path='/web/tickets'):
    async def form_data():
        return _Form(form or {})

    session_data = dict(session or {})
    if user:
        session_data['user_id'] = user.id
    return SimpleNamespace(
        session=session_data,
        headers={},
        state=SimpleNamespace(),
        url=SimpleNamespace(path=path, query=''),
        query_params=QueryParams(query or {}),
        form=form_data,
    )


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    """Two tenants, an admin, and helpers that seed exact comment timestamps."""
    monkeypatch.chdir(tmp_path)  # the comment route writes logs to ./logs/
    import app.models  # noqa: F401  (register every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ordering.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)

    acme, other = Workspace(name='Acme', timezone='UTC'), Workspace(name='Other Co', timezone='UTC')
    db.add_all([acme, other])
    await db.flush()
    ada = User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id)
    bob = User(username='bob', hashed_password='x', workspace_id=acme.id)
    olive = User(username='olive', hashed_password='x', is_admin=True, workspace_id=other.id)
    db.add_all([ada, bob, olive])
    await db.flush()

    async def add_ticket(number, status='open', created_days=10, comment_days=None,
                         *, internal=False, workspace=None, assigned_to='ada', **fields):
        tenant = workspace or acme
        owner = {'ada': ada, 'bob': bob, 'olive': olive, None: None}[assigned_to]
        fields.setdefault('subject', f'Subject {number}')
        ticket = Ticket(ticket_number=number, subject=fields.pop('subject'),
                        workspace_id=tenant.id, status=status,
                        assigned_to_id=owner.id if owner else None,
                        created_at=NOW - timedelta(days=created_days),
                        updated_at=NOW - timedelta(hours=1), **fields)
        db.add(ticket)
        await db.flush()
        if comment_days is not None:
            db.add(TicketComment(
                ticket_id=ticket.id,
                user_id=bob.id if tenant is acme else olive.id,
                content=f'Comment on {number}',
                is_internal=internal,
                created_at=NOW - timedelta(days=comment_days),
            ))
            await db.flush()
        return ticket

    async def comment(request_user, ticket, content, internal=False):
        """Post a real comment through the web route, as the browser does."""
        response = await web_tickets_add_comment(
            _request(request_user, form={'content': content}), ticket.id,
            content=content, is_internal=internal, files=[],
            background_tasks=BackgroundTasks(), db=db,
        )
        assert response.status_code == 303
        return response

    yield SimpleNamespace(db=db, acme=acme, ada=ada, bob=bob, olive=olive,
                          add_ticket=add_ticket, comment=comment)
    await db.close()
    await engine.dispose()


def _numbers(page):
    return [ticket.ticket_number for ticket in page.context['tickets']]


def _column_ids(html, status):
    """Ticket ids inside one Board column, in rendered order."""
    tail = html.split(f'<div id="ticket-col-{status}"', 1)[1]
    tail = re.split(r'<div id="ticket-col-|<!-- Closed/Resolved', tail, maxsplit=1)[0]
    return [int(value) for value in re.findall(r'data-ticket-id="(\d+)"', tail)]


def _list_ids(html):
    tail = html.split('<div id="listView"', 1)[1]
    return [int(value) for value in re.findall(r'href="/web/tickets/(\d+)"', tail)]


# --- ordering of the default (unfiltered) view -------------------------------

@pytest.mark.asyncio
async def test_active_tickets_are_ordered_by_oldest_latest_comment(env):
    old = await env.add_ticket('A-OPEN', 'open', created_days=12, comment_days=10)
    busy = await env.add_ticket('B-BUSY', 'in_progress', created_days=8, comment_days=5)
    # Created long ago but commented recently: activity, not creation, decides.
    recent = await env.add_ticket('C-RECENT', 'waiting', created_days=20, comment_days=3)
    newest = await env.add_ticket('D-NEWEST', 'open', created_days=2, comment_days=1)

    page = await web_tickets_list(_request(env.ada), db=env.db)
    assert _numbers(page) == ['A-OPEN', 'B-BUSY', 'C-RECENT', 'D-NEWEST']
    assert {t.id for t in [old, busy, recent, newest]} == {t.id for t in page.context['tickets']}


@pytest.mark.asyncio
async def test_filters_other_than_search_keep_activity_order(env):
    await env.add_ticket('A-P1', 'open', created_days=12, comment_days=10)
    await env.add_ticket('B-HIGH', 'in_progress', created_days=8, comment_days=5,
                         priority='high', assigned_to='bob')
    await env.add_ticket('C-LOW', 'waiting', created_days=4, comment_days=1, priority='low')

    by_status = await web_tickets_list(_request(env.ada, {'status': 'in_progress'}), db=env.db)
    assert _numbers(by_status) == ['B-HIGH']
    by_priority = await web_tickets_list(_request(env.ada, {'priority': 'high'}), db=env.db)
    assert _numbers(by_priority) == ['B-HIGH']
    by_assignee = await web_tickets_list(_request(env.ada, {'assigned': 'me'}), db=env.db)
    assert _numbers(by_assignee) == ['A-P1', 'C-LOW']

    project = Project(name='Acme site', workspace_id=env.acme.id, owner_id=env.ada.id)
    env.db.add(project)
    await env.db.flush()
    await env.add_ticket('D-PROJECT-OLD', 'open', created_days=15, comment_days=7,
                         related_project_id=project.id)
    await env.add_ticket('E-PROJECT-NEW', 'open', created_days=14, comment_days=2,
                         related_project_id=project.id)
    by_project = await web_tickets_list(_request(env.ada, {'project': str(project.id)}), db=env.db)
    assert _numbers(by_project) == ['D-PROJECT-OLD', 'E-PROJECT-NEW']


# --- commenting moves a ticket to the bottom --------------------------------

@pytest.mark.asyncio
async def test_public_comment_moves_top_ticket_to_bottom(env):
    top = await env.add_ticket('PUBLIC-TOP', 'open', created_days=12, comment_days=10)
    await env.add_ticket('PUBLIC-MID', 'in_progress', created_days=8, comment_days=5)
    await env.add_ticket('PUBLIC-LOW', 'waiting', created_days=4, comment_days=1)
    assert _numbers(await web_tickets_list(_request(env.ada), db=env.db)) == [
        'PUBLIC-TOP', 'PUBLIC-MID', 'PUBLIC-LOW']

    await env.comment(env.ada, top, 'A fresh public reply', internal=False)

    assert _numbers(await web_tickets_list(_request(env.ada), db=env.db)) == [
        'PUBLIC-MID', 'PUBLIC-LOW', 'PUBLIC-TOP']


@pytest.mark.asyncio
async def test_internal_comment_moves_top_ticket_to_bottom(env):
    top = await env.add_ticket('INTERNAL-TOP', 'open', created_days=12, comment_days=10)
    await env.add_ticket('INTERNAL-MID', 'in_progress', created_days=8, comment_days=5)
    await env.add_ticket('INTERNAL-LOW', 'waiting', created_days=4, comment_days=1)

    await env.comment(env.ada, top, 'Internal note from staff', internal=True)

    assert _numbers(await web_tickets_list(_request(env.ada), db=env.db)) == [
        'INTERNAL-MID', 'INTERNAL-LOW', 'INTERNAL-TOP']
    stored = (await env.db.execute(
        select(TicketComment).where(TicketComment.ticket_id == top.id)
        .order_by(TicketComment.id.asc())
    )).scalars().all()
    assert len(stored) == 2 and stored[-1].is_internal is True


@pytest.mark.asyncio
async def test_guest_reply_moves_top_ticket_to_bottom(env):
    top = await env.add_ticket('GUEST-TOP', 'open', created_days=12, comment_days=10,
                               is_guest=True, guest_name='Gina', guest_email='gina@example.test')
    await env.add_ticket('GUEST-MID', 'in_progress', created_days=8, comment_days=5)
    await env.add_ticket('GUEST-LOW', 'waiting', created_days=4, comment_days=1)

    response = await web_tickets_track_reply(
        _request(None, session={'verified_tickets': ['GUEST-TOP']},
                 path='/web/tickets/track/GUEST-TOP'),
        'GUEST-TOP', content='Any news on this?', db=env.db,
    )
    assert response.status_code == 303

    assert _numbers(await web_tickets_list(_request(env.ada), db=env.db)) == [
        'GUEST-MID', 'GUEST-LOW', 'GUEST-TOP']


@pytest.mark.asyncio
async def test_no_comment_tickets_use_created_at_then_move_when_commented(env):
    untouched = await env.add_ticket('NO-COMMENT', 'open', created_days=11)
    commented = await env.add_ticket('HAS-COMMENT', 'open', created_days=20, comment_days=5)
    assert _numbers(await web_tickets_list(_request(env.ada), db=env.db)) == [
        'NO-COMMENT', 'HAS-COMMENT'], 'the older untouched ticket stays on top'

    await env.comment(env.ada, untouched, 'First reply on the untouched ticket')

    assert _numbers(await web_tickets_list(_request(env.ada), db=env.db)) == [
        'HAS-COMMENT', 'NO-COMMENT']


@pytest.mark.asyncio
async def test_equal_activity_timestamps_break_ties_by_ticket_id(env):
    # Inserted out of ticket-number order so a name-based sort would be visible.
    zulu = await env.add_ticket('Z-TIE', 'open', created_days=9, comment_days=3)
    alpha = await env.add_ticket('A-TIE', 'in_progress', created_days=8, comment_days=3)
    mike = await env.add_ticket('M-TIE', 'waiting', created_days=7, comment_days=3)
    assert zulu.id < alpha.id < mike.id

    page = await web_tickets_list(_request(env.ada), db=env.db)
    assert _numbers(page) == ['Z-TIE', 'A-TIE', 'M-TIE']

    # No-comment ties fall back to created_at, then id as well.
    zulu2 = await env.add_ticket('Z-FRESH', 'open', created_days=6)
    alpha2 = await env.add_ticket('A-FRESH', 'open', created_days=6)
    assert zulu2.id < alpha2.id
    assert _numbers(await web_tickets_list(_request(env.ada), db=env.db))[:2] == [
        'Z-FRESH', 'A-FRESH']


# --- search and Team Overview drill-downs keep their own rules --------------

@pytest.mark.asyncio
async def test_search_keeps_relevance_first_then_activity(env):
    await env.add_ticket('A-COMMENT', 'open', created_days=9, comment_days=2,
                         description='needle in the description')
    await env.add_ticket('B-COMMENT', 'open', created_days=8, comment_days=4,
                         description='another needle')
    await env.add_ticket('C-SUBJECT', 'waiting', created_days=7, comment_days=1,
                         subject='Needle in the subject')

    page = await web_tickets_list(_request(env.ada, {'search': 'needle'}), db=env.db)
    assert _numbers(page) == ['C-SUBJECT', 'B-COMMENT', 'A-COMMENT']


@pytest.mark.asyncio
async def test_attention_filter_keeps_oldest_created_first(env):
    busiest = await env.add_ticket('A-UNASSIGNED', 'open', created_days=9, comment_days=1,
                                   assigned_to=None)
    quiet = await env.add_ticket('B-UNASSIGNED', 'open', created_days=2, comment_days=8,
                                 assigned_to=None)
    assert quiet.id > busiest.id

    page = await web_tickets_list(_request(env.ada, {'attention': 'unassigned'}), db=env.db)
    assert page.context['attention_filter'] == 'unassigned'
    assert _numbers(page) == ['A-UNASSIGNED', 'B-UNASSIGNED'], (
        'drill-down is still oldest-created-first')


# --- rendered HTML and exclusions -------------------------------------------

@pytest.mark.asyncio
async def test_board_columns_and_list_view_preserve_the_server_order(env):
    for number, status, created, commented in (
        ('O-OLD', 'open', 12, 10),
        ('O-NEW', 'open', 4, 1),
        ('P-OLD', 'in_progress', 11, 9),
        ('P-NEW', 'in_progress', 3, 2),
        ('W-OLD', 'waiting', 10, 8),
        ('W-NEW', 'waiting', 5, 3),
    ):
        await env.add_ticket(number, status, created_days=created, comment_days=commented)

    page = await web_tickets_list(_request(env.ada), db=env.db)
    html = page.body.decode()
    expected = ['O-OLD', 'P-OLD', 'W-OLD', 'W-NEW', 'P-NEW', 'O-NEW']
    assert _numbers(page) == expected
    by_id = {ticket.id: ticket.ticket_number for ticket in page.context['tickets']}
    for status in COLUMNS:
        ids = _column_ids(html, status)
        assert len(ids) == 2, (status, html.count('data-ticket-id'))
        assert [by_id[ticket_id] for ticket_id in ids] == [
            ticket.ticket_number for ticket in page.context['tickets']
            if ticket.status == status
        ]
    assert [by_id[ticket_id] for ticket_id in _list_ids(html)] == expected

    source = TEMPLATE_PATH.read_text(encoding='utf-8')
    assert '|sort(' not in source and '.sort(' not in source, 'the template never re-sorts tickets'


@pytest.mark.asyncio
async def test_closed_resolved_archived_and_other_tenants_stay_excluded(env):
    for number, fields in (
        ('RESOLVED', {'status': 'resolved'}),
        ('CLOSED', {'status': 'closed'}),
        ('ARCHIVED', {'status': 'open', 'is_archived': True}),
    ):
        await env.add_ticket(number, created_days=20, comment_days=0, **fields)
    await env.add_ticket('FOREIGN', created_days=20, comment_days=0,
                         workspace=Workspace(id=env.acme.id + 100, name='Remote'))
    live = await env.add_ticket('LIVE', 'waiting', created_days=3, comment_days=2)

    page = await web_tickets_list(_request(env.ada), db=env.db)
    assert _numbers(page) == ['LIVE']
    assert 'RESOLVED' not in page.body.decode() and 'CLOSED' not in page.body.decode()
