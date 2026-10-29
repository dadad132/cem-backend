"""The ticket page stays fast on long-running tickets, and its links are safe."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.models.ticket import Ticket, TicketComment, TicketHistory
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import (
    TICKET_COMMENTS_SHOWN,
    _ticket_rows_page,
    linkify,
    web_ticket_comment_full,
    web_ticket_older_comments,
    web_ticket_older_history,
)

T0 = datetime(2026, 1, 5, 9, 0, 0)
LONG = 'Start of a long e-mail. ' + 'x' * 6000 + ' https://example.com/end'


# --- linkify ---------------------------------------------------------------

def test_linkify_escapes_text_and_links_addresses():
    html = str(linkify('Mail jo.smith@acme.co.za or see https://acme.test/a?b=1&c=2. <b>hi</b>'))
    assert '<a href="mailto:jo.smith@acme.co.za"' in html
    assert '<a href="https://acme.test/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer"' in html
    assert '</a>. &lt;b&gt;hi&lt;/b&gt;' in html, 'the full stop is not part of the link; tags stay text'


def test_linkify_keeps_balanced_brackets_only():
    html = str(linkify('(see https://en.wikipedia.org/wiki/Foo_(bar))'))
    assert 'href="https://en.wikipedia.org/wiki/Foo_(bar)"' in html
    assert html.endswith('</a>)')


def test_linkify_cannot_be_used_to_inject_attributes():
    """An e-mailed URL used to break out of href (autofocus/onfocus ran script)."""
    html = str(linkify('https://example.com/"autofocus/onfocus="alert(1)'))
    assert re.findall(r'<a [^>]*>', html) == [
        '<a href="https://example.com/" target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:underline">'
    ]
    assert '&#34;autofocus/onfocus=&#34;alert(1)' in html


def test_linkify_empty():
    assert linkify(None) == '' and linkify('') == ''


@pytest.mark.parametrize('hostile', [
    'a' * 200_000,
    ('a.' * 100 + '@') * 1_000,
    'https://' + 'x' * 200_000,
], ids=['huge-word', 'many-almost-addresses', 'huge-link'])
def test_linkify_stays_fast_on_hostile_input(hostile):
    started = time.perf_counter()
    linkify(hostile)
    assert time.perf_counter() - started < 2, 'must stay linear on anything an e-mail can contain'


# --- paging through comments and history -------------------------------------

async def _seed(path, comments=75, history=30):
    """Acme: an admin and a ticket with many comments (some at the same moment). Other Co: its own admin."""
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name='Acme'), Workspace(name='Other Co')
    db.add_all([acme, other])
    await db.flush()
    admin = User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id)
    outsider = User(username='olive', hashed_password='x', is_admin=True, workspace_id=other.id)
    ticket = Ticket(ticket_number='TKT-2026-00001', subject='Printer', workspace_id=acme.id)
    second = Ticket(ticket_number='TKT-2026-00002', subject='Other', workspace_id=acme.id)
    db.add_all([admin, outsider, ticket, second])
    await db.flush()
    # Three comments share each timestamp, so paging must also order by id
    db.add_all([TicketComment(ticket_id=ticket.id, content=f'comment {i}', created_at=T0 + timedelta(minutes=i // 3))
                for i in range(comments)])
    db.add(TicketComment(ticket_id=ticket.id, content=LONG, created_at=T0 - timedelta(days=1)))
    db.add(TicketComment(ticket_id=second.id, content='on another ticket', created_at=T0))
    db.add_all([TicketHistory(ticket_id=ticket.id, action='comment_added', created_at=T0 + timedelta(minutes=i))
                for i in range(history)])
    await db.commit()
    return engine, db, admin, outsider, ticket, second


def _request(user):
    return SimpleNamespace(session={'user_id': user.id} if user else {}, state=SimpleNamespace(),
                           url=SimpleNamespace(path='/web/tickets'))


@pytest.mark.asyncio
async def test_pages_cover_every_comment_once_newest_first(tmp_path):
    engine, db, _, _, ticket, _ = await _seed(tmp_path / 't.db')
    try:
        seen, remaining, before = [], [], None
        while True:
            rows, older = await _ticket_rows_page(db, TicketComment, ticket.id, TICKET_COMMENTS_SHOWN, before=before)
            seen += rows
            remaining.append(older)
            if not older:
                break
            before = rows[-1]
        assert remaining == [46, 16, 0]
        assert len({c.id for c in seen}) == len(seen) == 76, 'no comment twice, none missing'
        keys = [(c.created_at, c.id) for c in seen]
        assert keys == sorted(keys, reverse=True)
        assert seen[-1].content == LONG
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_show_older_comments(tmp_path):
    engine, db, admin, _, ticket, _ = await _seed(tmp_path / 't.db')
    try:
        newest, _ = await _ticket_rows_page(db, TicketComment, ticket.id, TICKET_COMMENTS_SHOWN)
        response = await web_ticket_older_comments(_request(admin), ticket.id, before=newest[-1].id, db=db)
        html = response.body.decode()
        shown = re.findall(r'comment (\d+)</p>', html)
        assert shown == [str(i) for i in range(44, 14, -1)], 'the next 30, newest first'
        assert 'Show older comments (16)' in html
        assert 'Show the full message' not in html
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_long_comment_is_shortened_until_asked_for(tmp_path):
    engine, db, admin, _, ticket, _ = await _seed(tmp_path / 't.db', comments=0)
    try:
        oldest, _ = await _ticket_rows_page(db, TicketComment, ticket.id, TICKET_COMMENTS_SHOWN)
        long_comment = oldest[0]
        page = await web_ticket_older_comments(_request(admin), ticket.id, before=long_comment.id, db=db)
        assert b'tk-comment' not in page.body, 'nothing older than the oldest'

        from app.web.routes import templates
        clipped = templates.env.get_template('tickets/_comment.html').render(
            comment=long_comment, comment_authors={None: None}, comment_time_fmt='%H:%M')
        assert 'Show the full message' in clipped
        assert 'https://example.com/end' not in clipped and len(clipped) < 7000

        full = await web_ticket_comment_full(_request(admin), ticket.id, long_comment.id, db=db)
        html = full.body.decode()
        assert 'Show the full message' not in html
        assert '<a href="https://example.com/end"' in html
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_show_older_history(tmp_path):
    engine, db, admin, _, ticket, _ = await _seed(tmp_path / 't.db', history=130)
    try:
        newest, older = await _ticket_rows_page(db, TicketHistory, ticket.id, 20)
        assert older == 110
        response = await web_ticket_older_history(_request(admin), ticket.id, before=newest[-1].id, db=db)
        html = response.body.decode()
        assert html.count('Comment Added') == 100
        assert 'Show older history (10)' in html
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_parts_are_only_for_the_tickets_company(tmp_path):
    engine, db, admin, outsider, ticket, second = await _seed(tmp_path / 't.db')
    try:
        newest, _ = await _ticket_rows_page(db, TicketComment, ticket.id, TICKET_COMMENTS_SHOWN)
        anchor = newest[-1].id

        for call in (
            web_ticket_older_comments(_request(outsider), ticket.id, before=anchor, db=db),
            web_ticket_comment_full(_request(outsider), ticket.id, anchor, db=db),
            web_ticket_older_history(_request(outsider), ticket.id, before=1, db=db),
            # a comment id from another ticket does not open this one up
            web_ticket_comment_full(_request(admin), second.id, anchor, db=db),
            web_ticket_older_comments(_request(admin), second.id, before=anchor, db=db),
        ):
            response = await call
            assert (response.status_code, response.body) == (404, b'')

        signed_out = await web_ticket_older_comments(_request(None), ticket.id, before=anchor, db=db)
        assert signed_out.headers['HX-Redirect'] == '/web/login' and signed_out.body == b''
    finally:
        await db.close()
        await engine.dispose()
