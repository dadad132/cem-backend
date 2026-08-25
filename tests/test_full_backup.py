"""Guards for the full-server backup used by the super admin.

The archive naming and the restore's extraction prefix must agree. They did not
once before: backups stored attachments as ``uploads/...`` while restore only
extracted ``app/uploads/...``, so a restore silently came back with the database
but no attachment files at all.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app.core.backup import DatabaseBackup


def _make_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute('CREATE TABLE user (id INTEGER PRIMARY KEY, username TEXT)')
    conn.execute("INSERT INTO user (username) VALUES ('someone')")
    conn.commit()
    conn.close()


def _tree(root: Path) -> dict[str, str]:
    out = {}
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            out[p.relative_to(root).as_posix()] = hashlib.md5(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A throwaway working directory holding a database and some uploads."""
    monkeypatch.chdir(tmp_path)
    _make_db(tmp_path / 'data.db')

    uploads = tmp_path / 'app' / 'uploads'
    (uploads / 'tickets').mkdir(parents=True)
    (uploads / 'branding').mkdir(parents=True)
    (uploads / 'tickets' / 'a.png').write_bytes(b'\x89PNG fake image')
    (uploads / 'tickets' / 'b.txt').write_text('some attachment text')
    (uploads / 'branding' / 'logo.png').write_bytes(b'\x89PNG logo')

    manager = DatabaseBackup(db_path='data.db', backup_dir='backups')
    return manager, uploads


def test_archive_uses_app_uploads_prefix(sandbox):
    manager, uploads = sandbox
    archive = manager.create_backup(is_manual=True, include_attachments=True)
    assert archive is not None

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()

    assert 'data.db' in names
    attachments = [n for n in names if n != 'data.db']
    assert attachments, "no attachments were stored"
    for name in attachments:
        assert name.startswith('app/uploads/'), name
        assert '\\' not in name, f"zip entries must use forward slashes: {name}"


def test_restore_brings_attachments_back(sandbox):
    manager, uploads = sandbox
    before = _tree(uploads)
    assert len(before) == 3

    archive = manager.create_backup(is_manual=True, include_attachments=True)
    shutil.rmtree(uploads)
    assert not uploads.exists()

    assert manager.restore_from_backup(archive) is True
    assert _tree(uploads) == before


def test_restore_accepts_legacy_uploads_prefix(sandbox):
    """Archives written before the naming fix must still restore."""
    manager, uploads = sandbox
    before = _tree(uploads)

    legacy = Path('backups/backup_MANUAL_legacy.zip')
    legacy.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(legacy, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write('data.db', 'data.db')
        for rel in before:
            zf.write(uploads / rel, f'uploads/{rel}')  # old-style name

    shutil.rmtree(uploads)
    assert manager.restore_from_backup(legacy) is True
    assert _tree(uploads) == before


def test_restore_refuses_path_traversal(sandbox):
    """A crafted archive must not write outside the uploads folder."""
    manager, uploads = sandbox

    evil = Path('backups/backup_MANUAL_evil.zip')
    evil.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(evil, 'w') as zf:
        zf.write('data.db', 'data.db')
        zf.writestr('app/uploads/../../pwned.txt', 'nope')
        zf.writestr('uploads/../escaped.txt', 'nope')

    manager.restore_from_backup(evil)

    assert not Path('pwned.txt').exists()
    assert not Path('../pwned.txt').exists()
    assert not Path('escaped.txt').exists()
