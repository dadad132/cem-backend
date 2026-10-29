"""Accounts with no company (the super admin, and users it added) cannot break company pages.

Starting a chat as such an account crashed: chat.workspace_id is required. The
super admin opens the Dashboard and tabs by choosing a company to work in.
"""
from __future__ import annotations

from types import SimpleNamespace

import json
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.core.tenant_backup import WorkspaceBackup
from app.main import page_for_account_without_company
from app.models.chat import ChatMember
from app.models.project import Project
from app.models.ticket import Ticket
from app.models.user import User
from app.models.workspace import Workspace
from app.web.routes import web_chat_create, web_superadmin_assign_company, web_superadmin_work_in_company


# --- which pages they can open ----------------------------------------------------------

@pytest.mark.parametrize('path', ['/web/chats', '/web/admin/users/create', '/web/dashboard', '/web/tickets/5'])
def test_company_pages_send_the_super_admin_to_its_console(path):
    response = page_for_account_without_company(path, SimpleNamespace(is_superadmin=True))
    assert (response.status_code, response.headers['location']) == (303, '/web/superadmin?choose_company=1')


@pytest.mark.parametrize('path', ['/web/chats', '/web/admin/users/create'])
def test_other_accounts_without_a_company_get_told_why(path):
    response = page_for_account_without_company(path, SimpleNamespace(is_superadmin=False))
    assert response.status_code == 403 and b'not part of a company' in response.body


@pytest.mark.parametrize('path', [
    '/web/superadmin', '/web/superadmin/backup/status', '/web/login/superadmin', '/web/logout',
    '/web/download', '/web/notifications/unread', '/', '/static/sw.js', '/health',
])
def test_pages_that_need_no_company_stay_open(path):
    for is_superadmin in (True, False):
        assert page_for_account_without_company(path, SimpleNamespace(is_superadmin=is_superadmin)) is None


# --- starting a chat ------------------------------------------------------------------------

async def _seed(path):
    import app.models  # noqa: F401  (registers every model)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    db = AsyncSession(engine, expire_on_commit=False, autoflush=False)
    acme, other = Workspace(name='Acme'), Workspace(name='Other Co')
    db.add_all([acme, other])
    await db.flush()
    people = dict(
        root=User(username='root', hashed_password='x', is_superadmin=True, is_admin=True),
        ada=User(username='ada', hashed_password='x', is_admin=True, workspace_id=acme.id),
        bob=User(username='bob', hashed_password='x', workspace_id=acme.id),
        olive=User(username='olive', hashed_password='x', workspace_id=other.id),
        stray=User(username='stray', hashed_password='x'),  # added by the super admin: no company
    )
    db.add_all(people.values())
    await db.flush()
    db.add_all([Project(name='Made while stray', owner_id=people['stray'].id),
                Project(name='Acme project', owner_id=people['ada'].id, workspace_id=acme.id)])
    await db.commit()
    return engine, db, acme, people


class _Form(dict):
    def getlist(self, key):
        return self.get(key, [])


def _request(user, member_ids=()):
    async def form():
        return _Form(member_ids=list(member_ids))
    return SimpleNamespace(session={'user_id': user.id}, form=form)


@pytest.mark.asyncio
async def test_chat_members_come_from_your_own_company(tmp_path):
    engine, db, acme, p = await _seed(tmp_path / 'c.db')
    try:
        ids = [str(p['bob'].id), str(p['olive'].id), str(p['root'].id), str(p['ada'].id), 'abc', '']
        response = await web_chat_create(_request(p['ada'], ids), name=None, is_group=False, db=db)
        chat_id = int(response.headers['location'].rsplit('/', 1)[1])
        members = (await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id))).scalars().all()
        assert sorted(members) == sorted([p['ada'].id, p['bob'].id]), 'no other company, no junk, no duplicates'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_chat_without_a_company(tmp_path):
    engine, db, acme, p = await _seed(tmp_path / 'c.db')
    try:
        for who in ('root', 'stray'):
            response = await web_chat_create(_request(p[who], [str(p['bob'].id)]), name=None, is_group=False, db=db)
            assert (response.status_code, response.headers['location']) == (303, '/web/chats')
        assert (await db.execute(select(ChatMember))).first() is None
    finally:
        await db.close()
        await engine.dispose()


# --- the super admin puts such accounts in a company ------------------------------------------

async def _assign(db, actor, target, workspace_id):
    request = SimpleNamespace(session={'user_id': actor.id})
    response = await web_superadmin_assign_company(request, target.id, workspace_id=workspace_id, db=db)
    return response.headers['location']


@pytest.mark.asyncio
async def test_super_admin_adds_an_account_to_a_company(tmp_path):
    engine, db, acme, p = await _seed(tmp_path / 'c.db')
    try:
        assert await _assign(db, p['root'], p['stray'], acme.id) == '/web/superadmin?success=assign_company'
        await db.refresh(p['stray'])
        assert p['stray'].workspace_id == acme.id
        made = (await db.execute(select(Project).where(Project.name == 'Made while stray'))).scalar_one()
        assert made.workspace_id == acme.id, 'what it made without a company comes along'
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_only_accounts_without_a_company_and_only_by_the_super_admin(tmp_path):
    engine, db, acme, p = await _seed(tmp_path / 'c.db')
    try:
        refused = '/web/superadmin?error=assign_company'
        assert await _assign(db, p['root'], p['olive'], acme.id) == refused, 'not a way to move people between companies'
        assert await _assign(db, p['root'], p['root'], acme.id) == refused
        assert await _assign(db, p['root'], p['stray'], 9999) == refused, 'unknown company'
        with pytest.raises(HTTPException) as not_super:
            await _assign(db, p['ada'], p['stray'], acme.id)
        assert not_super.value.status_code == 404
        await db.refresh(p['stray'])
        await db.refresh(p['olive'])
        assert p['stray'].workspace_id is None and p['olive'].workspace_id != acme.id
    finally:
        await db.close()
        await engine.dispose()


# --- the super admin works in a company it chooses -----------------------------------------

async def _work_in(db, actor, workspace_id):
    request = SimpleNamespace(session={'user_id': actor.id})
    response = await web_superadmin_work_in_company(request, workspace_id=workspace_id, db=db)
    return response.headers['location'], request.session.get('workspace_id')


@pytest.mark.asyncio
async def test_super_admin_chooses_switches_and_leaves_a_company(tmp_path):
    engine, db, acme, p = await _seed(tmp_path / 'c.db')
    try:
        assert await _work_in(db, p['root'], str(acme.id)) == ('/web/dashboard', acme.id)
        await db.refresh(p['root'])
        assert p['root'].workspace_id == acme.id and p['root'].is_superadmin, 'company pages open now'

        other = p['olive'].workspace_id
        assert await _work_in(db, p['root'], str(other)) == ('/web/dashboard', other)
        assert await _work_in(db, p['root'], '') == ('/web/superadmin?success=left_company', None)
        await db.refresh(p['root'])
        assert p['root'].workspace_id is None

        for unknown in ('9999', 'abc'):
            assert (await _work_in(db, p['root'], unknown))[0] == '/web/superadmin?error=work_in_company'
        with pytest.raises(HTTPException) as not_super:
            await _work_in(db, p['ada'], str(other))
        assert not_super.value.status_code == 404, 'company admins cannot move themselves'
        await db.refresh(p['ada'])
        assert p['ada'].workspace_id == acme.id
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_company_backup_and_restore_leave_the_super_admin_alone(tmp_path):
    """Working in a company must not put the super admin's account (and password
    hash) in that company's backups, or let its restore delete the account."""
    engine, db, acme, p = await _seed(tmp_path / 'data.db')
    try:
        await _work_in(db, p['root'], str(acme.id))
        db.add(Ticket(ticket_number='TKT-1', subject='Logged by the super admin', workspace_id=acme.id,
                      created_by_id=p['root'].id))
        await db.commit()
    finally:
        await db.close()
        await engine.dispose()

    wb = WorkspaceBackup(db_path=str(tmp_path / 'data.db'), backup_root=str(tmp_path / 'backups'))
    archive = wb.create_backup(acme.id, 'Acme', is_manual=True)
    with zipfile.ZipFile(archive) as zf:
        usernames = sorted(u['username'] for u in json.loads(zf.read('data/user.json')))
    assert usernames == ['ada', 'bob'], 'not the super admin'

    ok, message = wb.restore_backup(acme.id, archive.name)
    assert ok, message
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / 'data.db'))
    try:
        root = conn.execute("SELECT id, workspace_id, is_superadmin FROM user WHERE username = 'root'").fetchone()
        assert root == (p['root'].id, acme.id, 1), 'still there, unchanged'
        assert conn.execute("SELECT created_by_id FROM ticket WHERE ticket_number = 'TKT-1'").fetchone() == (root[0],)
        assert conn.execute("SELECT COUNT(*) FROM user WHERE username IN ('ada', 'bob')").fetchone() == (2,)
    finally:
        conn.close()
