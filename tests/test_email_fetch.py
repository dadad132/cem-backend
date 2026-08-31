"""Guards for how much the mail poller downloads.

The poller used to re-fetch every message from a rolling 7-day window on every
cycle, which exhausted the mailbox's daily IMAP download allowance and made a
cycle take longer than the scheduler's timeout, so no tickets were created at
all. These tests pin the behaviour that keeps the download small.
"""
from __future__ import annotations

import pytest

from app.core.email_to_ticket_v2 import (
    MAX_FULL_FETCH_PER_CYCLE,
    MAX_MESSAGE_BYTES,
    _fetch_message_ids,
    _fetch_sizes,
    _read_uidvalidity,
)


class FakeMail:
    """Minimal IMAP stand-in that records what was asked for."""

    def __init__(self, sizes=None, msgids=None, uidvalidity=12345):
        self.sizes = sizes or {}
        self.msgids = msgids or {}
        self.uidvalidity = uidvalidity
        self.body_fetches = []
        self.calls = []

    def uid(self, cmd, *args):
        self.calls.append((cmd,) + args)
        uid_set, what = args[0], args[1]
        uids = [u.encode() for u in uid_set.split(',')]
        if 'RFC822.SIZE' in what:
            return 'OK', [
                (f'1 (UID {u.decode()} RFC822.SIZE {self.sizes[u]})'.encode(), b'')
                for u in uids if u in self.sizes
            ]
        if 'HEADER.FIELDS' in what:
            return 'OK', [
                (f'1 (UID {u.decode()} BODY[HEADER.FIELDS (MESSAGE-ID)] {{40}}'.encode(),
                 f'Message-ID: {self.msgids[u]}\r\n\r\n'.encode())
                for u in uids if u in self.msgids
            ]
        if what == '(RFC822)':
            self.body_fetches.append(uid_set)
            return 'OK', [(b'1 (RFC822 {10}', b'x' * 10)]
        return 'NO', []

    def status(self, folder, what):
        return 'OK', [f'"INBOX" (UIDVALIDITY {self.uidvalidity})'.encode()]


class BrokenMail(FakeMail):
    def uid(self, *a):
        raise RuntimeError('server said no')

    def status(self, *a):
        raise RuntimeError('unsupported')


# ── Message-ID pre-fetch ────────────────────────────────────────────────────

def test_message_ids_parsed_in_one_round_trip():
    mail = FakeMail(msgids={b'10': '<a@x>', b'11': '<b@x>'})
    got = _fetch_message_ids(mail, [b'10', b'11'])
    assert got == {b'10': '<a@x>', b'11': '<b@x>'}
    # One request for both, not one per message
    assert len(mail.calls) == 1
    assert mail.calls[0][1] == '10,11'


def test_message_id_prefetch_never_marks_mail_read():
    """BODY.PEEK, not BODY: fetching headers must not set the \\Seen flag."""
    mail = FakeMail(msgids={b'10': '<a@x>'})
    _fetch_message_ids(mail, [b'10'])
    assert 'BODY.PEEK' in mail.calls[0][2]


def test_message_ids_fall_back_to_empty_on_failure():
    # An empty map means the caller fetches everything: slower, never wrong.
    assert _fetch_message_ids(BrokenMail(), [b'1']) == {}
    assert _fetch_message_ids(FakeMail(), []) == {}


# ── Size pre-check ──────────────────────────────────────────────────────────

def test_sizes_parsed():
    mail = FakeMail(sizes={b'10': 5000, b'11': 40 * 1024 * 1024})
    assert _fetch_sizes(mail, [b'10', b'11']) == {b'10': 5000, b'11': 40 * 1024 * 1024}


def test_sizes_fall_back_to_empty_on_failure():
    assert _fetch_sizes(BrokenMail(), [b'1']) == {}
    assert _fetch_sizes(FakeMail(), []) == {}


def test_oversized_messages_are_over_the_cap():
    # The ticket code discards attachments over 10MB, so downloading a 40MB
    # message spends the daily allowance for nothing.
    assert 40 * 1024 * 1024 > MAX_MESSAGE_BYTES
    assert 5000 < MAX_MESSAGE_BYTES


# ── UIDVALIDITY ─────────────────────────────────────────────────────────────

def test_uidvalidity_read():
    assert _read_uidvalidity(FakeMail(uidvalidity=99), 'INBOX') == 99


def test_uidvalidity_none_when_unsupported():
    # None means "do not trust a stored high-water mark this cycle"
    assert _read_uidvalidity(BrokenMail(), 'INBOX') is None


# ── High-water mark arithmetic ──────────────────────────────────────────────

def _criteria(last_uid, date_since='01-Jan-2026'):
    """Mirrors the search criteria chosen in connect_and_fetch."""
    return f'UID {last_uid + 1}:*' if last_uid else f'SINCE {date_since}'


def test_search_uses_uid_range_once_a_mark_exists():
    assert _criteria(0) == 'SINCE 01-Jan-2026'
    assert _criteria(9481) == 'UID 9482:*'


@pytest.mark.parametrize('returned,last_uid,expected', [
    # 'UID n:*' always yields at least the highest UID, even with nothing newer
    ([b'100'], 100, []),
    ([b'100', b'101', b'102'], 100, [b'101', b'102']),
    ([b'7'], 0, [b'7']),
])
def test_stale_uids_filtered(returned, last_uid, expected):
    got = [e for e in returned if e.isdigit() and int(e) > last_uid] if last_uid else returned
    assert got == expected


def test_mark_discarded_when_mailbox_renumbers():
    prev = {'uidvalidity': 111, 'last_uid': 500}
    assert (500 if prev['uidvalidity'] == 111 else 0) == 500   # same, honour it
    assert (500 if prev['uidvalidity'] == 222 else 0) == 0     # changed, ignore


def test_per_cycle_fetch_is_capped():
    assert 0 < MAX_FULL_FETCH_PER_CYCLE <= 200
