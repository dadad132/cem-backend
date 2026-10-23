"""Access code for the mobile app download page (/web/download).

The super admin sets the code on the super admin console. It is one code for
the whole server, so it lives in a small settings file beside the database
rather than in any company's data, and only a hash of it is stored. The code
is checked on the server: the page used to carry it in its own script, where
anyone could read it, and the APK was a public file besides.
"""
from __future__ import annotations

import hmac
import json
import secrets
import time
from datetime import datetime
from pathlib import Path

from app.core.security import get_password_hash, verify_password

# Beside data.db: both are relative to the app's working directory
SETTINGS_FILE = Path('server_settings.json')

# The Android app. Outside app/static, so it is only sent once the code is right.
APK_PATH = Path(__file__).resolve().parents[1] / 'downloads' / 'cem-mobile.apk'

# Until the super admin sets a code, the one the page always had keeps working
DEFAULT_CODE = '203203'
MIN_LENGTH, MAX_LENGTH = 4, 64

# How long the downloads stay open after the code is entered
UNLOCK_SECONDS = 60 * 60

# Wrong codes allowed from one address before it has to wait, so the code
# cannot simply be guessed
MAX_FAILURES = 10
FAILURE_WINDOW_SECONDS = 15 * 60
_failures: dict[str, list[float]] = {}


def _stored() -> dict:
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return settings.get('app_download_code') or {}


def code_status() -> dict:
    """What the super admin console shows about the code (never the code itself)."""
    stored = _stored()
    return {
        'custom': bool(stored.get('hash')),
        'changed_at': stored.get('changed_at'),
        'changed_by': stored.get('changed_by'),
    }


def check_code(code: str) -> bool:
    stored = _stored()
    if stored.get('hash'):
        return verify_password(code, stored['hash'])
    return hmac.compare_digest(code.encode(), DEFAULT_CODE.encode())


def set_code(code: str, changed_by: str) -> None:
    """Replace the code. Downloads unlocked with the old one close again."""
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        settings = {}
    settings['app_download_code'] = {
        'hash': get_password_hash(code),
        'version': secrets.token_hex(8),
        'changed_at': datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC'),
        'changed_by': changed_by,
    }
    # Write a new file and swap it in, so a crash cannot leave half a file
    temp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + '.tmp')
    temp.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    temp.replace(SETTINGS_FILE)


def unlock(session: dict) -> None:
    session['app_download'] = {
        'version': _stored().get('version', 'default'),
        'until': time.time() + UNLOCK_SECONDS,
    }


def is_unlocked(session: dict) -> bool:
    entry = session.get('app_download') or {}
    return (
        entry.get('until', 0) > time.time()
        and entry.get('version') == _stored().get('version', 'default')
    )


def too_many_failures(client: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(client, []) if now - t < FAILURE_WINDOW_SECONDS]
    if recent:
        _failures[client] = recent
    else:
        _failures.pop(client, None)
    return len(recent) >= MAX_FAILURES


def record_failure(client: str) -> None:
    _failures.setdefault(client, []).append(time.monotonic())
