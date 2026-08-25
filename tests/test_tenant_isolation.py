"""Guards for per-company (per-workspace) data isolation.

The most likely way isolation regresses is a new model being added without a
matching entry in TENANT_SCOPE: it would then be invisible to per-company
backups, and probably unfiltered in queries too. These tests catch that.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.tenant_backup import (
    TENANT_SCOPE,
    WorkspaceBackup,
    normalize_upload_path,
    verify_scope_coverage,
)

# Bookkeeping tables that hold no tenant data
IGNORED_TABLES = {'alembic_version'}


def _model_tables() -> set[str]:
    """Table names SQLModel knows about, without touching the database."""
    import app.models  # noqa: F401  (registers every model)
    from sqlmodel import SQLModel

    return set(SQLModel.metadata.tables.keys())


def test_every_model_table_is_scoped():
    """A model without a TENANT_SCOPE entry would be left out of backups."""
    missing = _model_tables() - set(TENANT_SCOPE) - IGNORED_TABLES
    assert not missing, (
        f"These tables are not in TENANT_SCOPE: {sorted(missing)}. "
        "Add them to app/core/tenant_backup.py so they are included in "
        "per-company backups and reviewed for tenant filtering."
    )


def test_scope_parents_exist():
    """Every 'via' rule must point at a table that is itself scoped."""
    for table, rule in TENANT_SCOPE.items():
        if rule[0] == 'via':
            parent = rule[1]
            assert parent in TENANT_SCOPE, (
                f"{table} is scoped through {parent}, which is not in TENANT_SCOPE"
            )


def test_exactly_one_root():
    roots = [t for t, rule in TENANT_SCOPE.items() if rule[0] == 'root']
    assert roots == ['workspace']


@pytest.mark.skipif(not Path('data.db').exists(), reason="no database present")
def test_live_schema_fully_mapped():
    """The deployed database must not contain an unmapped table."""
    coverage = verify_scope_coverage('data.db')
    assert not coverage['unmapped'], (
        f"Tables in the database but not in TENANT_SCOPE: {coverage['unmapped']}"
    )


@pytest.mark.parametrize('raw,expected', [
    ('app/uploads/tickets/a.png', 'tickets/a.png'),
    ('/uploads/branding/logo.png', 'branding/logo.png'),
    ('uploads/profile_pictures/1.jpg', 'profile_pictures/1.jpg'),
    ('app\\uploads\\tickets\\b.png', 'tickets/b.png'),
    # Anything outside uploads, or trying to climb out of it, is refused
    ('app/uploads/../../etc/passwd', None),
    ('/etc/passwd', None),
    ('', None),
    (None, None),
])
def test_normalize_upload_path(raw, expected):
    assert normalize_upload_path(raw) == expected


def test_backup_filename_resolution_is_confined(tmp_path):
    """resolve_backup_file must never escape the workspace's own folder."""
    wb = WorkspaceBackup(db_path=str(tmp_path / 'data.db'),
                         backup_root=str(tmp_path / 'backups'))
    ws1 = wb.workspace_dir(1)
    ws2 = wb.workspace_dir(2)
    secret = ws1 / 'backup_MANUAL_20250101_000000.zip'
    secret.write_bytes(b'PK\x03\x04')

    # Workspace 1 can reach its own file
    assert wb.resolve_backup_file(1, secret.name) == secret.resolve()

    # Workspace 2 cannot, by name or by traversal
    assert wb.resolve_backup_file(2, secret.name) is None
    for attempt in (
        f'../workspace_1/{secret.name}',
        f'..\\workspace_1\\{secret.name}',
        '/etc/passwd',
        '../../data.db',
    ):
        assert wb.resolve_backup_file(2, attempt) is None, attempt

    # Non-backup files in the right folder are still refused
    stray = ws2 / 'notes.txt'
    stray.write_text('hello')
    assert wb.resolve_backup_file(2, 'notes.txt') is None


def test_backup_dirs_are_per_workspace(tmp_path):
    wb = WorkspaceBackup(db_path=str(tmp_path / 'data.db'),
                         backup_root=str(tmp_path / 'backups'))
    assert wb.workspace_dir(1) != wb.workspace_dir(2)
    assert wb.workspace_dir(1).name == 'workspace_1'


def test_backup_status_is_per_workspace(tmp_path):
    """One company's backup progress must not show up for another."""
    wb = WorkspaceBackup(db_path=str(tmp_path / 'data.db'),
                         backup_root=str(tmp_path / 'backups'))
    wb._set_status(1, 'running', 'Exporting ticket (3/40)')
    assert wb.get_status(1)['status'] == 'running'
    assert wb.get_status(2)['status'] == 'idle'
    assert wb.get_status(2)['progress'] == ''


@pytest.mark.skipif(not Path('data.db').exists(), reason="no database present")
def test_workspaces_do_not_share_rows():
    """No two workspaces may claim the same row of a scoped table."""
    wb = WorkspaceBackup()
    conn = sqlite3.connect('data.db')
    try:
        workspace_ids = [r[0] for r in conn.execute('SELECT id FROM workspace')]
    finally:
        conn.close()
    if len(workspace_ids) < 2:
        pytest.skip('needs at least two workspaces')

    seen: dict[str, dict[int, int]] = {}
    for ws in workspace_ids:
        for entry in wb.workspace_row_counts(ws):
            seen.setdefault(entry['name'], {})[ws] = entry['count']

    # Sum of per-workspace counts must not exceed the table's total
    conn = sqlite3.connect('data.db')
    try:
        for table, per_ws in seen.items():
            if table == 'workspace':
                continue
            total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            claimed = sum(per_ws.values())
            assert claimed <= total, (
                f"{table}: workspaces claim {claimed} rows but only {total} exist "
                "— a row is being attributed to more than one company"
            )
    finally:
        conn.close()
