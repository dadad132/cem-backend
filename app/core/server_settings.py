"""Settings for the whole server, set on the super admin console.

They are the same for every company, so they live in one small JSON file
beside the database (server_settings.json, ignored by git) rather than in any
company's data.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Beside data.db: both are relative to the app's working directory
SETTINGS_FILE = Path('server_settings.json')

# Shown at the bottom of the mobile app download page
DEFAULT_SUPPORT_EMAIL = 'support@myit.africa'
_EMAIL_RE = re.compile(r'^[^@\s"<>]+@[^@\s"<>]+\.[^@\s"<>]+$')


def read() -> dict:
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def update(key: str, value) -> None:
    """Set one setting, keeping the others."""
    settings = read()
    settings[key] = value
    # Write a new file and swap it in, so a crash cannot leave half a file
    temp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + '.tmp')
    temp.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    temp.replace(SETTINGS_FILE)


def support_email() -> str:
    return read().get('support_email') or DEFAULT_SUPPORT_EMAIL


def is_valid_email(address: str) -> bool:
    return len(address) <= 254 and bool(_EMAIL_RE.match(address))
