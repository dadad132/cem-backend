#!/usr/bin/env python3
"""
Diagnose why incoming emails are not becoming tickets.

Read-only: opens data.db, prints a report, changes nothing.

Run it on the server, from the app directory:

    cd /opt/crm-backend && .venv/bin/python diagnose_email_tickets.py

It answers, in order:
  1. Is the mail account still being polled and authenticating at all?
  2. Are emails arriving but being dropped, and for which stated reason?
  3. Which senders are affected, and is the pattern specific to a domain?
  4. Are replies being folded into existing tickets instead of making new ones?
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(sys.argv[1] if len(sys.argv) > 1 else 'data.db')
DAYS = 30


def hdr(text):
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}")


def sub(text):
    print(f"\n--- {text} ---")


def table_exists(cur, name):
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def main():
    if not DB.exists():
        print(f"No database at {DB.resolve()}")
        print("Run this from the app directory, or pass the path as an argument.")
        return 1

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    print(f"Database : {DB.resolve()}")
    print(f"Now (UTC): {now:%Y-%m-%d %H:%M:%S}")
    print(f"Window   : last {DAYS} days")

    # ── 1. Mail accounts ─────────────────────────────────────────────────
    hdr("1. MAIL ACCOUNTS - is the poller still working?")
    if table_exists(cur, 'incoming_email_account'):
        cur.execute("""SELECT id, name, email_address, is_active, last_checked_at,
                              workspace_id
                       FROM incoming_email_account ORDER BY id""")
        rows = cur.fetchall()
        if not rows:
            print("  No incoming email accounts configured.")
        for _id, name, addr, active, last, ws in rows:
            state = "ACTIVE " if active else "DISABLED"
            age = ""
            if last:
                try:
                    delta = now - datetime.fromisoformat(str(last).replace('Z', ''))
                    mins = delta.total_seconds() / 60
                    age = f"  ({mins:.0f} min ago)"
                    if mins > 60:
                        age += "   <-- STALE: the poller is not reaching this account"
                except ValueError:
                    pass
            print(f"  [{state}] {name} <{addr}>  company={ws}")
            print(f"           last checked: {last or 'NEVER'}{age}")
    else:
        print("  (table not present)")

    # ── 2. Login / connection errors ─────────────────────────────────────
    hdr("2. ERRORS FROM THE MAIL POLLER (most recent first)")
    if table_exists(cur, 'systemlog'):
        cur.execute("""SELECT timestamp, level, source, message, details
                       FROM systemlog
                       WHERE level IN ('ERROR','WARNING','WARN')
                         AND timestamp >= ?
                       ORDER BY timestamp DESC LIMIT 15""", (cutoff,))
        rows = cur.fetchall()
        if not rows:
            print("  No errors or warnings logged. Authentication is probably fine.")
        for ts, lvl, src, msg, det in rows:
            print(f"  {str(ts)[:19]}  {lvl:<7} {src:<15} {msg[:70]}")
            if det:
                print(f"      {str(det)[:100]}")
        # Auth-specific
        cur.execute("""SELECT COUNT(*) FROM systemlog
                       WHERE timestamp >= ?
                         AND (message LIKE '%uthenticat%' OR details LIKE '%uthenticat%'
                              OR message LIKE '%LOGIN%'  OR details LIKE '%LOGIN%'
                              OR message LIKE '%assword%' OR details LIKE '%assword%')""",
                    (cutoff,))
        n_auth = cur.fetchone()[0]
        if n_auth:
            print(f"\n  {n_auth} log line(s) mention authentication/login/password.")
            print("  If Gmail revoked the app password, that is the whole story:")
            print("  generate a new one and update the account in Admin > Email Accounts.")
    else:
        print("  (systemlog table not present)")

    # ── 3. Dropped emails ────────────────────────────────────────────────
    hdr("3. EMAILS RECEIVED BUT NOT TURNED INTO A TICKET")
    if not table_exists(cur, 'processedmail'):
        print("  (processedmail table not present)")
        conn.close()
        return 0

    cur.execute("""SELECT COUNT(*),
                          SUM(CASE WHEN ticket_id IS NULL THEN 1 ELSE 0 END)
                   FROM processedmail WHERE processed_at >= ?""", (cutoff,))
    total, dropped = cur.fetchone()
    dropped = dropped or 0
    print(f"  Processed in window : {total}")
    print(f"  Produced a ticket   : {total - dropped}")
    print(f"  Produced NO ticket  : {dropped}"
          + ("   <-- these are the ones you are missing" if dropped else ""))

    if dropped:
        sub("Dropped emails, by sender domain")
        cur.execute("""SELECT LOWER(SUBSTR(email_from, INSTR(email_from,'@')+1)) dom,
                              COUNT(*)
                       FROM processedmail
                       WHERE ticket_id IS NULL AND processed_at >= ?
                       GROUP BY dom ORDER BY 2 DESC LIMIT 12""", (cutoff,))
        for dom, n in cur.fetchall():
            flag = "  <-- Gmail" if dom in ('gmail.com', 'googlemail.com') else ""
            print(f"    {n:>4}  {dom}{flag}")

        sub("Dropped emails, by day")
        cur.execute("""SELECT SUBSTR(processed_at,1,10) d, COUNT(*)
                       FROM processedmail
                       WHERE ticket_id IS NULL AND processed_at >= ?
                       GROUP BY d ORDER BY d""", (cutoff,))
        for d, n in cur.fetchall():
            print(f"    {d}  {'#' * min(n, 50)} {n}")

        sub("Most recent dropped emails")
        cur.execute("""SELECT processed_at, email_from, subject
                       FROM processedmail
                       WHERE ticket_id IS NULL AND processed_at >= ?
                       ORDER BY processed_at DESC LIMIT 15""", (cutoff,))
        for ts, frm, subj in cur.fetchall():
            print(f"    {str(ts)[:16]}  {str(frm)[:32]:<32} {str(subj)[:44]}")

    # ── 4. Why were they dropped? ────────────────────────────────────────
    hdr("4. STATED REASON FOR SKIPPING (from the system log)")
    if table_exists(cur, 'systemlog'):
        cur.execute("""SELECT message, COUNT(*) FROM systemlog
                       WHERE timestamp >= ? AND message LIKE 'Skipped%'
                       GROUP BY message ORDER BY 2 DESC""", (cutoff,))
        rows = cur.fetchall()
        if not rows:
            print("  Nothing logged as skipped in this window.")
        for msg, n in rows:
            print(f"  {n:>4}x  {msg}")
            if 'closed' in msg.lower():
                print("        ^ A reply landed on an already closed/resolved ticket and was")
                print("          discarded outright - no ticket, no comment, no notification.")
                print("          Gmail keeps whole conversations threaded, so once one ticket")
                print("          in a thread is closed, later mail in that thread is dropped.")

        cur.execute("""SELECT timestamp, details FROM systemlog
                       WHERE timestamp >= ? AND message LIKE '%closed ticket%'
                       ORDER BY timestamp DESC LIMIT 10""", (cutoff,))
        rows = cur.fetchall()
        if rows:
            sub("Recent replies discarded because the ticket was closed")
            for ts, det in rows:
                print(f"    {str(ts)[:16]}  {str(det)[:88]}")

    # ── 5. Threaded instead of new ───────────────────────────────────────
    hdr("5. MAIL FOLDED INTO AN EXISTING TICKET INSTEAD OF MAKING A NEW ONE")
    cur.execute("""SELECT p.ticket_id, t.ticket_number, t.subject, COUNT(*) n
                   FROM processedmail p JOIN ticket t ON t.id = p.ticket_id
                   WHERE p.processed_at >= ? AND p.ticket_id IS NOT NULL
                   GROUP BY p.ticket_id HAVING n > 1
                   ORDER BY n DESC LIMIT 12""", (cutoff,))
    rows = cur.fetchall()
    if not rows:
        print("  No ticket absorbed more than one email. Threading looks sane.")
    else:
        print("  Tickets that absorbed several emails (high counts can mean unrelated")
        print("  mail is being merged into one ticket rather than opening new ones):\n")
        for tid, num, subj, n in rows:
            print(f"    {n:>3} emails -> {num}  {str(subj)[:50]}")

    # ── Verdict ──────────────────────────────────────────────────────────
    hdr("SUMMARY")
    verdicts = []
    if table_exists(cur, 'incoming_email_account'):
        cur.execute("""SELECT COUNT(*) FROM incoming_email_account
                       WHERE is_active = 1 AND (last_checked_at IS NULL
                             OR last_checked_at < ?)""",
                    ((now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),))
        if cur.fetchone()[0]:
            verdicts.append("An active account has not been checked in over an hour - "
                            "the poller or the mailbox login is broken. Fix that first.")
    if dropped:
        cur.execute("""SELECT COUNT(*) FROM systemlog
                       WHERE timestamp >= ? AND message LIKE '%closed ticket%'""", (cutoff,))
        if cur.fetchone()[0]:
            verdicts.append("Replies to closed/resolved tickets are being discarded. "
                            "This is the most likely cause of missing Gmail tickets.")
        else:
            verdicts.append(f"{dropped} email(s) produced no ticket - see section 3 "
                            "for who they were from.")
    if not verdicts:
        verdicts.append("Nothing obviously wrong in this window. If mail is still "
                        "missing, it may not be reaching the mailbox at all - check "
                        "the mailbox directly, and its spam folder.")
    for v in verdicts:
        print(f"  * {v}")
    print()

    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
