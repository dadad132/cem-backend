"""End-to-end check that a poll cycle downloads only new mail.

Drives process_email_account against a fake IMAP server and counts the bytes
it pulls. Before this behaviour existed, every cycle re-downloaded the whole
7-day window, which is what exhausted the mailbox's daily IMAP allowance.
"""
from __future__ import annotations

import asyncio
import email.utils
import imaplib
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.core import email_to_ticket_v2 as e2t


def _message(uid: int, size_bytes: int = 2000) -> bytes:
    """A syntactically valid email of roughly the requested size."""
    body = 'x' * max(size_bytes - 300, 10)
    return (
        f"Message-ID: <msg{uid}@client.example>\r\n"
        f"From: Client <client{uid}@gmail.com>\r\n"
        f"To: support@company.example\r\n"
        f"Subject: Help needed {uid}\r\n"
        f"Date: {email.utils.formatdate()}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n{body}\r\n"
    ).encode()


class FakeIMAP:
    """Counts bytes served, so a test can assert on download volume."""

    bytes_served = 0
    body_fetches = 0
    instances = []

    def __init__(self, messages, uidvalidity=4242):
        self.messages = messages          # {uid_int: bytes}
        self.uidvalidity = uidvalidity
        FakeIMAP.instances.append(self)

    # -- connection ------------------------------------------------------
    def login(self, *a):
        return 'OK', [b'']

    def select(self, folder):
        if folder != 'INBOX':
            raise imaplib.IMAP4.error('no such folder')
        return 'OK', [str(len(self.messages)).encode()]

    def logout(self):
        return 'OK', [b'']

    def close(self):
        return 'OK', [b'']

    def status(self, folder, what):
        return 'OK', [f'"INBOX" (UIDVALIDITY {self.uidvalidity})'.encode()]

    # -- data ------------------------------------------------------------
    def uid(self, cmd, *args):
        if cmd == 'search':
            criteria = args[1]
            uids = sorted(self.messages)
            if criteria.startswith('UID '):
                low = int(criteria.split()[1].split(':')[0])
                uids = [u for u in uids if u >= low] or ([max(uids)] if uids else [])
            return 'OK', [b' '.join(str(u).encode() for u in uids)]

        if cmd == 'store':
            return 'OK', [b'']

        uid_set, what = args[0], args[1]
        # Body fetches pass a single UID as bytes; the batched header/size
        # prefetch passes a comma-joined str. Accept both.
        if isinstance(uid_set, bytes):
            uid_set = uid_set.decode('ascii')
        uids = [int(u) for u in uid_set.split(',') if u.isdigit()]

        if 'RFC822.SIZE' in what:
            return 'OK', [
                (f'1 (UID {u} RFC822.SIZE {len(self.messages[u])})'.encode(), b'')
                for u in uids if u in self.messages
            ]

        if 'HEADER.FIELDS' in what:
            out = []
            for u in uids:
                if u not in self.messages:
                    continue
                hdr = f'Message-ID: <msg{u}@client.example>\r\n\r\n'.encode()
                FakeIMAP.bytes_served += len(hdr)
                out.append((f'1 (UID {u} BODY[HEADER.FIELDS (MESSAGE-ID)] {{{len(hdr)}}}'.encode(), hdr))
            return 'OK', out

        if what == '(RFC822)':
            out = []
            for u in uids:
                if u not in self.messages:
                    continue
                raw = self.messages[u]
                FakeIMAP.bytes_served += len(raw)
                FakeIMAP.body_fetches += 1
                out.append((f'1 (RFC822 {{{len(raw)}}}'.encode(), raw))
            return 'OK', out

        return 'NO', []


def make_account(env):
    """A real IncomingEmailAccount row, so state can be persisted like production."""
    from app.models.incoming_email_account import IncomingEmailAccount
    from app.models.workspace import Workspace

    maker = env['maker']

    async def create():
        async with maker() as db:
            db.add(Workspace(id=1, name='Test Co'))
            await db.commit()
            acct = IncomingEmailAccount(
                workspace_id=1,
                name='Support',
                email_address='support@company.example',
                protocol='imap',
                imap_host='imap.gmail.com',
                imap_port=993,
                imap_username='support@company.example',
                imap_password='secret',
                imap_use_ssl=True,
                is_active=True,
            )
            db.add(acct)
            await db.commit()
            await db.refresh(acct)
            return acct

    return env['loop'].run_until_complete(create())


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """One event loop for the whole test: aiosqlite connections are bound to
    the loop that opened them, so mixing loops produces 'Event loop is closed'."""
    monkeypatch.chdir(tmp_path)
    FakeIMAP.bytes_served = 0
    FakeIMAP.body_fetches = 0
    FakeIMAP.instances = []

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'t.db'}")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
    loop.run_until_complete(setup())

    yield {'engine': engine, 'maker': maker, 'loop': loop}

    loop.run_until_complete(engine.dispose())
    asyncio.set_event_loop(None)
    loop.close()


def _run(env, account, mailbox):
    """Run one poll cycle against a mailbox, returning tickets created."""
    def fake_ssl(host, port, timeout=None):
        return FakeIMAP(mailbox)

    async def go():
        async with env['maker']() as db:
            return await e2t.process_email_account(db, account)

    orig = imaplib.IMAP4_SSL
    imaplib.IMAP4_SSL = fake_ssl
    try:
        return env['loop'].run_until_complete(go())
    finally:
        imaplib.IMAP4_SSL = orig


def test_second_cycle_downloads_nothing_new(sandbox):
    """The heart of it: an idle mailbox must cost almost no download."""
    env = sandbox
    mailbox = {i: _message(i, 20_000) for i in range(1, 21)}   # 20 msgs, ~400KB
    account = make_account(env)

    _run(env, account, mailbox)
    first_bytes = FakeIMAP.bytes_served
    first_bodies = FakeIMAP.body_fetches
    assert first_bodies > 0, "the first cycle should download the new mail"

    # Nothing new has arrived since
    FakeIMAP.bytes_served = 0
    FakeIMAP.body_fetches = 0
    _run(env, account, mailbox)

    assert FakeIMAP.body_fetches == 0, (
        f"second cycle re-downloaded {FakeIMAP.body_fetches} message bodies; "
        "it should download none"
    )
    assert FakeIMAP.bytes_served < first_bytes / 10, (
        f"second cycle pulled {FakeIMAP.bytes_served} bytes vs {first_bytes} "
        "on the first; it should be a small fraction"
    )


def test_high_water_mark_is_recorded(sandbox):
    env = sandbox
    mailbox = {i: _message(i) for i in range(1, 6)}
    account = make_account(env)
    _run(env, account, mailbox)

    assert account.imap_uid_state, "the poller should remember where it got to"
    import json
    state = json.loads(account.imap_uid_state)
    assert state['INBOX']['uidvalidity'] == 4242
    assert state['INBOX']['last_uid'] == 5


def test_only_new_mail_is_downloaded_on_the_next_cycle(sandbox):
    env = sandbox
    mailbox = {i: _message(i, 20_000) for i in range(1, 11)}
    account = make_account(env)
    _run(env, account, mailbox)

    # Two new messages arrive
    FakeIMAP.bytes_served = 0
    FakeIMAP.body_fetches = 0
    mailbox[11] = _message(11, 20_000)
    mailbox[12] = _message(12, 20_000)
    _run(env, account, mailbox)

    assert FakeIMAP.body_fetches == 2, (
        f"expected to download exactly the 2 new messages, got {FakeIMAP.body_fetches}"
    )


def test_oversized_message_is_not_downloaded(sandbox):
    env = sandbox
    mailbox = {
        1: _message(1, 5_000),
        2: _message(2, e2t.MAX_MESSAGE_BYTES + 1_000_000),   # over the cap
    }
    account = make_account(env)
    _run(env, account, mailbox)

    # Only the small one should have had its body pulled
    assert FakeIMAP.body_fetches == 1, (
        f"expected 1 body fetch (the small message), got {FakeIMAP.body_fetches}"
    )
    assert FakeIMAP.bytes_served < e2t.MAX_MESSAGE_BYTES, (
        "the oversized message was downloaded despite the cap"
    )
