"""One clock for the whole system: times show in the server's own time unless
Site Settings names a zone, and reports pick their days on that same clock."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.core.clock import format_local, get_zone, local_now, to_local, to_utc, workspace_zone
from app.core.database import apply_data_changes
from app.models.ticket import TicketComment, TicketHistory
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import _report_days, templates, web_admin_site_settings_save

STORED = datetime(2026, 10, 14, 23, 30)  # UTC: 01:30 on the 15th in Johannesburg


def _zone(name):
    zone = get_zone(name)
    if zone is None:
        pytest.skip('no timezone database here (Linux has one; on Windows: pip install tzdata)')
    return zone


def test_server_time_by_default():
    server = STORED.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert get_zone(None) is None and get_zone(' ') is None and workspace_zone(Workspace(name='x')) is None
    assert to_local(STORED) == server and to_utc(server) == STORED
    assert format_local(STORED) == f'{server:%Y-%m-%d %H:%M}'
    assert abs(local_now() - datetime.now()) < timedelta(seconds=5), 'the server clock itself'


def test_named_zones_and_what_is_not_moved():
    sa = _zone('Africa/Johannesburg')
    assert to_local(STORED, sa) == datetime(2026, 10, 15, 1, 30)
    assert to_utc(datetime(2026, 10, 15, 1, 30), sa) == STORED
    assert get_zone('utc') is timezone.utc and to_local(STORED, get_zone('UTC')) == STORED
    assert get_zone('Nowhere/Atlantis') is None, 'an unknown zone falls back to server time'
    assert format_local('2026-10-14 23:30:00.000001', sa, '%d %H:%M') == '15 01:30', 'text from a raw SQL query'
    assert format_local(date(2026, 10, 14), sa, '%d/%m') == '14/10', 'a plain date (due date) stays as picked'
    assert format_local(None, sa) == ''


def test_report_days_are_the_company_days():
    sa = _zone('Africa/Johannesburg')
    first, last, start, end = _report_days('2026-10-15', '2026-10-15', sa)
    assert (first, last) == (date(2026, 10, 15), date(2026, 10, 15))
    assert (start, end) == (datetime(2026, 10, 14, 22), datetime(2026, 10, 15, 22))
    assert start <= STORED < end, 'came in at 01:30 on the 15th, so it is in the report for the 15th'

    first, last, _, _ = _report_days(None, None, sa)
    assert last == local_now(sa).date() and (last - first).days == 30


def test_pages_comments_and_history_show_one_time():
    """The same stored time reads the same on every part of a page."""
    comment = TicketComment(id=1, ticket_id=1, content='Done', created_at=STORED)
    entry = TicketHistory(id=1, ticket_id=1, action='status_changed', new_value='closed', created_at=STORED)
    page = templates.env.from_string("{{ t | localtime('%H:%M') }}")
    for workspace, local in ((Workspace(name='Server time'), to_local(STORED)),
                             (None, to_local(STORED)),
                             (Workspace(name='Picked UTC', timezone='UTC'), STORED)):
        assert page.render(t=STORED, workspace=workspace) == f'{local:%H:%M}'
        on_comment = templates.env.get_template('tickets/_comment.html').render(
            comment=comment, comment_authors={}, comment_time_fmt='%H:%M', workspace=workspace)
        on_history = templates.env.get_template('tickets/_history_page.html').render(
            history=[entry], older_history=False, user_names={}, workspace=workspace)
        assert f'{local:%H:%M}' in on_comment and f'{local:%b %d, %I:%M %p}' in on_history


async def _engine(path):
    import app.models  # noqa: F401  (registers every model)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


@pytest.mark.asyncio
async def test_companies_move_to_server_time_once(tmp_path):
    engine = await _engine(tmp_path / 'c.db')
    db = AsyncSession(engine, expire_on_commit=False)
    try:
        db.add_all([Workspace(name='default', timezone='UTC', comment_timezone='Africa/Johannesburg'),
                    Workspace(name='blank', timezone=''),
                    Workspace(name='picked', timezone='Africa/Johannesburg')])
        await db.commit()
        async with engine.begin() as conn:
            await conn.exec_driver_sql('PRAGMA user_version = 1')  # already had the earlier changes
            await apply_data_changes(conn)
        rows = (await db.execute(text('SELECT name, timezone, comment_timezone FROM workspace ORDER BY id'))).all()
        assert [tuple(r) for r in rows] == [('default', None, None), ('blank', None, None),
                                            ('picked', 'Africa/Johannesburg', None)]

        # UTC picked on purpose afterwards: the change does not run again
        await db.execute(text("UPDATE workspace SET timezone = 'UTC' WHERE name = 'default'"))
        await db.commit()
        async with engine.begin() as conn:
            await apply_data_changes(conn)
        assert (await db.execute(text("SELECT timezone FROM workspace WHERE name = 'default'"))).scalar() == 'UTC'
        assert Workspace(name='new').timezone is None, 'new companies start on server time'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_site_settings_timezone_choice(tmp_path):
    engine = await _engine(tmp_path / 's.db')
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    try:
        acme = Workspace(name='Acme', comment_timezone='Asia/Tokyo')
        db.add(acme)
        await db.flush()
        admin = User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id)
        db.add(admin)
        await db.commit()

        async def save(zone):
            request = SimpleNamespace(session={'user_id': admin.id})
            await web_admin_site_settings_save(
                request, site_title=None, primary_color='#2563eb', timezone=zone, business_hours_start='07:30',
                business_hours_end='16:00', business_hours_exclude_weekends='1', gui_theme='crimson',
                comment_time_format='24hr', db=db)
            await db.refresh(acme)
            return request.session

        assert 'success_message' in await save('UTC') and acme.timezone == 'UTC'
        assert acme.comment_timezone is None, 'comments follow the one company time'
        assert 'success_message' in await save('') and acme.timezone is None, 'server time'
        session = await save('Nowhere/Atlantis')
        assert acme.timezone is None and 'server time' in session['error_message']
    finally:
        await db.close()
        await engine.dispose()
