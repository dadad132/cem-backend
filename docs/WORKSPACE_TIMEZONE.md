# Times and timezones

Every time in the system is shown on one clock: if the server says 10:00,
tickets, tasks, comments, history, the calendar, the dashboard, reports, PDF and
Excel exports, job cards and e-mails all say 10:00.

## The rule

- **Stored:** in UTC (`datetime.utcnow`), as before.
- **Shown:** in the company's zone, which is **the server's own time** unless
  an admin picks a zone under **Admin → Site Settings → Timezone**.
- **Not moved:** dates and times people pick themselves (task start/due dates
  and times, ticket start/end, meeting date and time) are stored as picked and
  shown as picked.

The whole rule lives in `app/core/clock.py`. A zone of `None` means the
server's own time, which Python reads from the operating system, so it needs no
timezone database. Named zones use `zoneinfo` (Linux has the data built in; on
Windows `pip install tzdata`). A zone the server does not know falls back to
server time, and Site Settings says so when it is saved.

Comments follow the same zone; Site Settings only chooses a 12- or 24-hour
clock for them. (`workspace.comment_timezone` is no longer used.)

## In templates

```html
{{ ticket.created_at | localtime('%b %d, %Y %I:%M %p') }}
```

`localtime` finds the company from the page's `workspace`. Use it for every
stored time (`*_at`, `timestamp`). Do not use it on picked dates such as
`task.due_date` or `ticket.scheduled_date`: print those with `strftime`.
`format_datetime_tz(dt, zone_name, fmt)` still works for code that has a zone
name rather than a page.

## In Python

```python
from app.core.clock import day_start_utc, days_utc, format_local, local_now, local_today, to_local, to_utc

zone = await _company_zone(db, user.workspace_id)   # routes.py helper
local_today(zone)                     # "today" for overdue checks and calendars
format_local(ticket.created_at, zone, '%Y-%m-%d %H:%M')
first, last, start, end = _report_days(start_date, end_date, zone)  # report periods
```

Report periods are the company's days: a ticket that came in at 01:30 local
time on the 15th is in the report for the 15th, even though it is stored as
23:30 UTC on the 14th. Business hours are counted on the company's clock.

## Existing data

On the first start after this change, companies still on the old `UTC` default
move to server time, and the separate comment timezone is cleared (one-time
change 2 in `apply_data_changes`, `app/core/database.py`). A company that had
picked a zone keeps it.

Tickets and e-mail replies that arrived by e-mail before the June 2026 fix
(commit bca514f) were saved two hours ahead (a hard-coded UTC+2), so those
older e-mailed items show two hours late.
