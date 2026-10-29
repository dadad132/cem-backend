"""One clock for the whole system.

Times are stored in UTC (datetime.utcnow) and shown everywhere in one zone:
the company's timezone from Site Settings, or by default the server's own
time, so when the server says 10:00 every page, report and export says 10:00.

A zone of None means the server's own time. Python reads that from the
operating system, so it needs no timezone database.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_FORMAT = '%Y-%m-%d %H:%M'


@lru_cache(maxsize=64)
def get_zone(name: Optional[str]) -> Optional[tzinfo]:
    """The zone named in Site Settings, or None (server time) when none is set
    or the name is not known on this machine."""
    if not name or not name.strip():
        return None
    if name.strip().upper() == 'UTC':
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name.strip())
    except Exception:
        return None


def workspace_zone(workspace) -> Optional[tzinfo]:
    """The zone a company's times are shown in."""
    return get_zone(getattr(workspace, 'timezone', None) if workspace is not None else None)


def to_local(dt, zone: Optional[tzinfo] = None):
    """A stored (UTC) time as the local wall-clock time in the zone.
    Anything that is not a datetime (a date, None) comes back unchanged."""
    if not isinstance(dt, datetime):
        return dt
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(zone).replace(tzinfo=None)


def to_utc(dt, zone: Optional[tzinfo] = None):
    """A local wall-clock time in the zone as a stored (UTC) time."""
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone) if zone is not None else dt.astimezone()
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def local_now(zone: Optional[tzinfo] = None) -> datetime:
    return to_local(datetime.now(timezone.utc), zone)


def local_today(zone: Optional[tzinfo] = None) -> date:
    return local_now(zone).date()


def day_start_utc(day: date, zone: Optional[tzinfo] = None) -> datetime:
    """When a local day starts, as a stored (UTC) time."""
    return to_utc(datetime.combine(day, time.min), zone)


def days_utc(first: date, last: date, zone: Optional[tzinfo] = None) -> Tuple[datetime, datetime]:
    """Stored-time bounds of the local days first..last: start <= t < end."""
    return day_start_utc(first, zone), day_start_utc(last + timedelta(days=1), zone)


def format_local(dt, zone: Optional[tzinfo] = None, fmt: str = DEFAULT_FORMAT) -> str:
    """A stored time as text in the zone; a plain date is printed as it is.
    Also takes the text a raw SQL query returns for a stored time."""
    if dt is None or dt == '':
        return ''
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    if isinstance(dt, (datetime, date)):
        return to_local(dt, zone).strftime(fmt)
    return str(dt)


def zone_label(zone: Optional[tzinfo] = None) -> str:
    """A short name for the zone right now, such as SAST or UTC+02:00."""
    now = datetime.now(timezone.utc).astimezone(zone)
    name = now.tzname() or ''
    if not name or ' ' in name or len(name) > 6 or name[0] in '+-':
        offset = now.utcoffset() or timedelta(0)
        minutes = int(offset.total_seconds() // 60)
        sign = '-' if minutes < 0 else '+'
        name = 'UTC' if not minutes else f'UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}'
    return name


@lru_cache(maxsize=1)
def server_zone_name() -> str:
    """The server's own time zone, as named by the operating system."""
    tz = os.environ.get('TZ', '').lstrip(':').strip()
    if tz and '/' in tz:
        return tz
    try:
        name = Path('/etc/timezone').read_text().strip()
        if name:
            return name
    except OSError:
        pass
    try:
        target = str(Path('/etc/localtime').resolve())
        if 'zoneinfo/' in target:
            return target.split('zoneinfo/', 1)[1]
    except OSError:
        pass
    return zone_label(None)
