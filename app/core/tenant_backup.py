"""
Per-workspace (per-company) backup and restore.

Unlike :mod:`app.core.backup`, which copies the whole ``data.db`` file and the
entire uploads tree, this module exports **only the rows and attachment files
that belong to a single workspace**. A workspace admin can therefore create,
download, and restore a backup without ever seeing — or destroying — another
company's data.

Archive layout (``.zip``)::

    manifest.json          metadata: workspace id/name, counts, format version
    data/<table>.json      rows of that table belonging to the workspace
    files/<relpath>        attachment files, relative to app/uploads/

Restore re-inserts the rows under **new primary keys** and rewrites every
foreign key to match, so an archive can be restored onto the same server or
onto a fresh one without colliding with IDs another workspace already uses.
The whole restore runs inside one SQLite transaction: either the workspace is
fully replaced or nothing changes.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1

# Already-compressed formats — storing them saves time without costing size.
_INCOMPRESSIBLE_EXTENSIONS: Set[str] = {
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.heic', '.heif',
    '.ico', '.mp4', '.mkv', '.webm', '.avi', '.mov', '.mp3', '.aac', '.ogg',
    '.flac', '.m4a', '.opus', '.zip', '.gz', '.bz2', '.xz', '.7z', '.rar',
    '.zst', '.tgz', '.pdf', '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.epub',
}

# ---------------------------------------------------------------------------
# Tenant scope map
# ---------------------------------------------------------------------------
# How each table is tied back to a workspace:
#   ('root',)               the workspace row itself  -> WHERE id = :ws
#   ('direct',)             has a workspace_id column -> WHERE workspace_id = :ws
#   ('via', parent, fk)     scoped through a parent   -> WHERE fk IN (parent ids)
#
# Every table in the database must appear here. `verify_scope_coverage` reports
# anything missing so a newly added table can never be silently left out of a
# backup (which would also mean it was never checked for tenant isolation).
TENANT_SCOPE: Dict[str, Tuple] = {
    'workspace': ('root',),

    # Tables carrying workspace_id directly
    'activity': ('direct',),
    'activitylog': ('direct',),
    'apikey': ('direct',),
    'call': ('direct',),
    'chat': ('direct',),
    'company': ('direct',),
    'contact': ('direct',),
    'customfield': ('direct',),
    'deal': ('direct',),
    'emailsettings': ('direct',),
    'goal': ('direct',),
    'incoming_email_account': ('direct',),
    'kbdiagnostictree': ('direct',),
    'kbresolvedcase': ('direct',),
    'lead': ('direct',),
    'meeting': ('direct',),
    'pending_email': ('direct',),
    'processedmail': ('direct',),
    'project': ('direct',),
    'savedview': ('direct',),
    'smartsuggestion': ('direct',),
    'supportarticle': ('direct',),
    'supportcategory': ('direct',),
    'supportconversation': ('direct',),
    'systemlog': ('direct',),
    'tasktemplate': ('direct',),
    'ticket': ('direct',),
    'user': ('direct',),
    'userbehavior': ('direct',),
    'userpreference': ('direct',),
    'webhook': ('direct',),

    # Tables scoped through a parent row
    'assignment': ('via', 'task', 'task_id'),
    'call_ice_candidate': ('via', 'call', 'call_id'),
    'chatmember': ('via', 'chat', 'chat_id'),
    'comment': ('via', 'task', 'task_id'),
    'comment_attachment': ('via', 'comment', 'comment_id'),
    'customfieldvalue': ('via', 'customfield', 'custom_field_id'),
    'focustask': ('via', 'task', 'task_id'),
    'meetingattendee': ('via', 'meeting', 'meeting_id'),
    'message': ('via', 'chat', 'chat_id'),
    'messageattachment': ('via', 'message', 'message_id'),
    'milestone': ('via', 'project', 'project_id'),
    'notification': ('via', 'user', 'user_id'),
    'project_member': ('via', 'project', 'project_id'),
    'recurringtask': ('via', 'project', 'project_id'),
    'recurringtaskinstance': ('via', 'recurringtask', 'recurring_task_id'),
    'subtask': ('via', 'task', 'task_id'),
    'task': ('via', 'project', 'project_id'),
    'taskattachment': ('via', 'task', 'task_id'),
    'taskdependency': ('via', 'task', 'task_id'),
    'taskhistory': ('via', 'task', 'task_id'),
    'taskwatcher': ('via', 'task', 'task_id'),
    'ticketattachment': ('via', 'ticket', 'ticket_id'),
    'ticketcomment': ('via', 'ticket', 'ticket_id'),
    'tickethistory': ('via', 'ticket', 'ticket_id'),
    'ticketwatcher': ('via', 'ticket', 'ticket_id'),
    'timelog': ('via', 'task', 'task_id'),
}

# Columns holding a path to a file under app/uploads/.
FILE_COLUMNS: Dict[str, List[str]] = {
    'ticketattachment': ['file_path'],
    'taskattachment': ['file_path'],
    'comment_attachment': ['file_path'],
    'messageattachment': ['file_path'],
    'user': ['profile_picture'],
    'workspace': ['logo_url', 'favicon_url'],
}

# Text columns with a global UNIQUE constraint. When restoring onto a server
# that already has a different workspace using the same value, the incoming
# value gets a suffix rather than blowing up the whole restore.
UNIQUE_TEXT_COLUMNS: Dict[str, List[str]] = {
    'user': ['username'],
    'ticket': ['ticket_number'],
}

_BLOB_PREFIX = '__b64__:'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_upload_path(raw: Optional[str]) -> Optional[str]:
    """Reduce a stored file reference to a path relative to ``app/uploads``.

    Handles both formats used in the database: filesystem-relative
    (``app/uploads/tickets/x.png``) and URL-style (``/uploads/branding/y.png``).
    Returns None for anything that does not live under uploads.
    """
    if not raw or not isinstance(raw, str):
        return None
    p = raw.replace('\\', '/').strip()
    for marker in ('app/uploads/', '/uploads/', 'uploads/'):
        idx = p.find(marker)
        if idx != -1:
            rel = p[idx + len(marker):]
            # Reject traversal attempts outright
            if not rel or '..' in rel.split('/'):
                return None
            return rel
    return None


def _table_columns(cur: sqlite3.Cursor, table: str) -> List[str]:
    cur.execute(f'PRAGMA table_info("{table}")')
    return [row[1] for row in cur.fetchall()]


def _primary_key(cur: sqlite3.Cursor, table: str) -> Optional[str]:
    cur.execute(f'PRAGMA table_info("{table}")')
    for row in cur.fetchall():
        if row[5]:  # pk flag
            return row[1]
    return None


def _foreign_keys(cur: sqlite3.Cursor, table: str) -> List[Tuple[str, str, str]]:
    """Return (from_column, parent_table, parent_column) for each FK."""
    cur.execute(f'PRAGMA foreign_key_list("{table}")')
    out = []
    for row in cur.fetchall():
        from_col, parent_table, to_col = row[3], row[2], row[4]
        out.append((from_col, parent_table, to_col or 'id'))
    return out


def _not_null_columns(cur: sqlite3.Cursor, table: str) -> Set[str]:
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall() if row[3]}


def _existing_tables(cur: sqlite3.Cursor) -> List[str]:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return [r[0] for r in cur.fetchall()]


def verify_scope_coverage(db_path: str = 'data.db') -> Dict[str, List[str]]:
    """Compare the live schema against TENANT_SCOPE.

    Returns ``{'unmapped': [...], 'stale': [...]}``. ``unmapped`` tables are
    present in the database but missing from the scope map — they are excluded
    from every per-workspace backup until mapped.
    """
    path = Path(db_path)
    if not path.exists():
        return {'unmapped': [], 'stale': []}
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        cur = conn.cursor()
        live = set(_existing_tables(cur))
    finally:
        conn.close()
    mapped = set(TENANT_SCOPE)
    # alembic bookkeeping is server-level, not tenant data
    ignore = {'alembic_version'}
    return {
        'unmapped': sorted(live - mapped - ignore),
        'stale': sorted(mapped - live),
    }


def _fk_insert_order(cur: sqlite3.Cursor,
                     tables: Iterable[str]) -> Tuple[List[str], Set[Tuple[str, str]]]:
    """Order tables so every table a foreign key points at is inserted first.

    Scope order is not enough on its own: ``project.owner_id`` is NOT NULL and
    points at ``user``, and both are scoped directly by ``workspace_id``, so
    only the real foreign-key graph puts them in a workable order.

    Returns ``(ordered_tables, deferred)`` where ``deferred`` holds
    ``(table, column)`` pairs whose parent could not be inserted first — self
    references, plus any genuine cycle. Those columns are written as NULL and
    patched once every row exists.
    """
    tables = list(tables)
    table_set = set(tables)
    deps: Dict[str, Set[str]] = {}
    deferred: Set[Tuple[str, str]] = set()

    for table in tables:
        parents = set()
        for col, parent, _ in _foreign_keys(cur, table):
            if parent == table:
                deferred.add((table, col))  # self reference
            elif parent in table_set:
                parents.add(parent)
        deps[table] = parents

    ordered: List[str] = []
    placed: Set[str] = set()
    pending = set(tables)
    while pending:
        ready = sorted(t for t in pending if deps[t] <= placed)
        if not ready:
            # A cycle remains. Break it on the table with the fewest unmet
            # dependencies and defer the offending columns.
            victim = min(pending, key=lambda t: len(deps[t] - placed))
            for col, parent, _ in _foreign_keys(cur, victim):
                if parent in (deps[victim] - placed):
                    deferred.add((victim, col))
            deps[victim] = deps[victim] & placed
            ready = [victim]
        for table in ready:
            ordered.append(table)
            placed.add(table)
            pending.discard(table)
    return ordered, deferred


def _topological_order(tables: Iterable[str]) -> List[str]:
    """Order tables so a parent always precedes the children scoped through it."""
    remaining = list(tables)
    ordered: List[str] = []
    placed: Set[str] = set()
    # Bounded loop: worst case one table lands per pass.
    for _ in range(len(remaining) + 1):
        progressed = False
        for table in list(remaining):
            rule = TENANT_SCOPE.get(table)
            if not rule:
                continue
            if rule[0] == 'via' and rule[1] not in placed and rule[1] in remaining:
                continue
            ordered.append(table)
            placed.add(table)
            remaining.remove(table)
            progressed = True
        if not remaining or not progressed:
            break
    # Anything left has a cyclic scope definition; append so it is not lost.
    ordered.extend(remaining)
    return ordered


def _encode_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return _BLOB_PREFIX + base64.b64encode(value).decode('ascii')
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_BLOB_PREFIX):
        return base64.b64decode(value[len(_BLOB_PREFIX):])
    return value


def _safe_component(text: str) -> str:
    """Turn a workspace name into something safe for a filename."""
    cleaned = re.sub(r'[^A-Za-z0-9]+', '-', (text or '').strip()).strip('-')
    return cleaned[:40] or 'workspace'


# ---------------------------------------------------------------------------
# Backup manager
# ---------------------------------------------------------------------------

class WorkspaceBackup:
    """Creates and restores backups scoped to a single workspace."""

    def __init__(self, db_path: str = 'data.db', backup_root: str = 'backups'):
        self.db_path = Path(db_path)
        self.backup_root = Path(backup_root)
        self.uploads_dir = Path('app/uploads')
        # Retention, per workspace
        self.max_auto = 10
        self.max_manual = 15
        self.max_uploaded = 5
        # Progress tracking, keyed by workspace so two admins never see
        # each other's backup progress.
        self._status: Dict[int, Dict[str, Any]] = {}

    # -- paths -------------------------------------------------------------

    def workspace_dir(self, workspace_id: int) -> Path:
        d = self.backup_root / f'workspace_{int(workspace_id)}'
        d.mkdir(parents=True, exist_ok=True)
        return d

    def resolve_backup_file(self, workspace_id: int, filename: str) -> Optional[Path]:
        """Resolve a filename inside this workspace's backup folder.

        Returns None if the name escapes the folder or does not exist, so a
        caller can never reach another workspace's archive.
        """
        if not filename or '/' in filename or '\\' in filename or '..' in filename:
            return None
        ws_dir = self.workspace_dir(workspace_id).resolve()
        candidate = (ws_dir / filename).resolve()
        if candidate.parent != ws_dir or not candidate.is_file():
            return None
        if candidate.suffix != '.zip' or not candidate.name.startswith('backup_'):
            return None
        return candidate

    # -- status ------------------------------------------------------------

    def get_status(self, workspace_id: int) -> Dict[str, Any]:
        return self._status.get(int(workspace_id), {
            'status': 'idle', 'progress': '', 'filename': None,
        })

    def _set_status(self, workspace_id: int, status: str, progress: str = '',
                    filename: Optional[str] = None) -> None:
        self._status[int(workspace_id)] = {
            'status': status, 'progress': progress, 'filename': filename,
        }

    # -- export ------------------------------------------------------------

    def _collect_scoped_ids(self, cur: sqlite3.Cursor, workspace_id: int,
                            tables: List[str]) -> Dict[str, Set[Any]]:
        """Resolve the set of primary keys each table contributes for a workspace."""
        ids: Dict[str, Set[Any]] = {}
        for table in tables:
            rule = TENANT_SCOPE[table]
            pk = _primary_key(cur, table)
            if rule[0] == 'root':
                cur.execute(f'SELECT id FROM "{table}" WHERE id = ?', (workspace_id,))
            elif rule[0] == 'direct':
                cur.execute(
                    f'SELECT {pk or "rowid"} FROM "{table}" WHERE workspace_id = ?',
                    (workspace_id,),
                )
            else:  # ('via', parent, fk)
                _, parent, fk = rule
                parent_ids = ids.get(parent, set())
                if not parent_ids:
                    ids[table] = set()
                    continue
                placeholders = ','.join('?' * len(parent_ids))
                cur.execute(
                    f'SELECT {pk or "rowid"} FROM "{table}" '
                    f'WHERE {fk} IN ({placeholders})',
                    tuple(parent_ids),
                )
            ids[table] = {r[0] for r in cur.fetchall()}
        return ids

    def _fetch_rows(self, cur: sqlite3.Cursor, table: str,
                    row_ids: Set[Any]) -> List[Dict[str, Any]]:
        if not row_ids:
            return []
        pk = _primary_key(cur, table) or 'rowid'
        columns = _table_columns(cur, table)
        rows: List[Dict[str, Any]] = []
        ids = list(row_ids)
        # Chunked to stay well under SQLite's variable limit
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ','.join('?' * len(chunk))
            cur.execute(
                f'SELECT * FROM "{table}" WHERE {pk} IN ({placeholders})',
                tuple(chunk),
            )
            for record in cur.fetchall():
                rows.append({
                    col: _encode_value(val) for col, val in zip(columns, record)
                })
        return rows

    def _referenced_files(self, data: Dict[str, List[Dict[str, Any]]]) -> Set[str]:
        found: Set[str] = set()
        for table, columns in FILE_COLUMNS.items():
            for row in data.get(table, []):
                for col in columns:
                    rel = normalize_upload_path(row.get(col))
                    if rel:
                        found.add(rel)
        return found

    def create_backup(self, workspace_id: int, workspace_name: str = '',
                      is_manual: bool = False,
                      protect: Optional[str] = None) -> Optional[Path]:
        """Export one workspace to a ZIP archive. Returns the path, or None."""
        workspace_id = int(workspace_id)
        if not self.db_path.exists():
            logger.warning('Database %s missing, cannot back up workspace %s',
                           self.db_path, workspace_id)
            return None

        conn = sqlite3.connect(str(self.db_path), timeout=60)
        try:
            cur = conn.cursor()
            live_tables = set(_existing_tables(cur))
            tables = _topological_order([t for t in TENANT_SCOPE if t in live_tables])
            skipped = sorted(live_tables - set(TENANT_SCOPE) - {'alembic_version'})
            if skipped:
                logger.warning(
                    'Workspace backup: tables not in the tenant scope map are '
                    'excluded: %s', ', '.join(skipped)
                )

            self._set_status(workspace_id, 'running', 'Collecting workspace data...')
            scoped_ids = self._collect_scoped_ids(cur, workspace_id, tables)

            data: Dict[str, List[Dict[str, Any]]] = {}
            for table in tables:
                data[table] = self._fetch_rows(cur, table, scoped_ids.get(table, set()))
        finally:
            conn.close()

        if not data.get('workspace'):
            logger.error('Workspace %s not found — nothing to back up', workspace_id)
            self._set_status(workspace_id, 'error', 'Workspace not found')
            return None

        name = workspace_name or (data['workspace'][0].get('name') or '')
        files = self._referenced_files(data)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        kind = 'MANUAL' if is_manual else 'AUTO'
        out_path = self.workspace_dir(workspace_id) / f'backup_{kind}_{timestamp}.zip'

        total_steps = len(tables) + len(files)
        step = 0
        missing_files = 0
        try:
            with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as zf:
                for table in tables:
                    step += 1
                    self._set_status(workspace_id, 'running',
                                     f'Exporting {table} ({step}/{total_steps})')
                    zf.writestr(
                        f'data/{table}.json',
                        json.dumps(data[table], ensure_ascii=False, default=str),
                    )

                for rel in sorted(files):
                    step += 1
                    self._set_status(workspace_id, 'running',
                                     f'Adding file {step}/{total_steps}')
                    source = self.uploads_dir / rel
                    if not source.is_file():
                        missing_files += 1
                        continue
                    compression = (
                        zipfile.ZIP_STORED
                        if source.suffix.lower() in _INCOMPRESSIBLE_EXTENSIONS
                        else zipfile.ZIP_DEFLATED
                    )
                    zf.write(source, arcname=f'files/{rel}',
                             compress_type=compression)

                manifest = {
                    'format_version': FORMAT_VERSION,
                    'workspace_id': workspace_id,
                    'workspace_name': name,
                    'created_at': datetime.utcnow().isoformat(),
                    'type': kind,
                    'table_counts': {t: len(rows) for t, rows in data.items() if rows},
                    'file_count': len(files) - missing_files,
                    'missing_files': missing_files,
                    'excluded_tables': skipped,
                }
                zf.writestr('manifest.json', json.dumps(manifest, indent=2))
        except Exception as exc:
            logger.error('Workspace %s backup failed: %s', workspace_id, exc)
            out_path.unlink(missing_ok=True)
            self._set_status(workspace_id, 'error', f'Backup failed: {exc}'[:200])
            return None

        self._cleanup(workspace_id, protect=protect)
        size_mb = out_path.stat().st_size / (1024 * 1024)
        logger.info('Workspace %s backup created: %s (%.2f MB)',
                    workspace_id, out_path.name, size_mb)
        self._set_status(workspace_id, 'done', 'Backup created successfully',
                         out_path.name)
        return out_path

    # -- listing -----------------------------------------------------------

    def read_manifest(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            with zipfile.ZipFile(path, 'r') as zf:
                return json.loads(zf.read('manifest.json').decode('utf-8'))
        except Exception:
            return None

    def list_backups(self, workspace_id: int) -> List[Dict[str, Any]]:
        ws_dir = self.workspace_dir(workspace_id)
        out: List[Dict[str, Any]] = []
        for path in sorted(ws_dir.glob('backup_*.zip'),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            stat = path.stat()
            manifest = self.read_manifest(path) or {}
            if '_MANUAL_' in path.name:
                kind = 'MANUAL'
            elif '_AUTO_' in path.name:
                kind = 'AUTO'
            else:
                kind = 'UPLOADED'
            counts = manifest.get('table_counts', {})
            out.append({
                'filename': path.name,
                'type': kind,
                'size': stat.st_size,
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'created': datetime.fromtimestamp(stat.st_mtime).strftime(
                    '%d/%m/%Y %H:%M:%S'),
                'created_timestamp': stat.st_mtime,
                'file_count': manifest.get('file_count', 0),
                'ticket_count': counts.get('ticket', 0),
                'task_count': counts.get('task', 0),
                'user_count': counts.get('user', 0),
                'workspace_name': manifest.get('workspace_name', ''),
                'valid': bool(manifest),
            })
        return out

    def get_stats(self, workspace_id: int) -> Dict[str, Any]:
        backups = self.list_backups(workspace_id)
        total = sum(b['size'] for b in backups)
        return {
            'count': len(backups),
            'auto_count': sum(1 for b in backups if b['type'] == 'AUTO'),
            'manual_count': sum(1 for b in backups if b['type'] == 'MANUAL'),
            'uploaded_count': sum(1 for b in backups if b['type'] == 'UPLOADED'),
            'total_size': total,
            'total_size_mb': round(total / (1024 * 1024), 2),
            'latest': backups[0]['filename'] if backups else None,
            'latest_time': backups[0]['created'] if backups else None,
            'limits': {
                'auto': self.max_auto,
                'manual': self.max_manual,
                'uploaded': self.max_uploaded,
            },
        }

    def _cleanup(self, workspace_id: int, protect: Optional[str] = None) -> None:
        """Apply retention limits inside one workspace's folder only.

        ``protect`` names an archive that must survive regardless of its age —
        used during a restore, where the safety backup taken first could
        otherwise push the archive being restored past the retention limit.
        """
        ws_dir = self.workspace_dir(workspace_id)
        for prefix, limit in (('backup_AUTO_', self.max_auto),
                              ('backup_MANUAL_', self.max_manual),
                              ('backup_UPLOADED_', self.max_uploaded)):
            files = sorted(ws_dir.glob(f'{prefix}*.zip'),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in files[limit:]:
                if protect and stale.name == protect:
                    continue
                try:
                    stale.unlink()
                    logger.info('Removed old workspace backup: %s', stale.name)
                except OSError as exc:
                    logger.warning('Could not remove %s: %s', stale.name, exc)

    def delete_backup(self, workspace_id: int, filename: str) -> bool:
        path = self.resolve_backup_file(workspace_id, filename)
        if not path:
            return False
        try:
            path.unlink()
            logger.info('Deleted workspace %s backup: %s', workspace_id, filename)
            return True
        except OSError as exc:
            logger.error('Could not delete %s: %s', filename, exc)
            return False

    def save_uploaded_backup(self, workspace_id: int, content: bytes,
                             filename: str) -> Tuple[Optional[Path], str]:
        """Store an uploaded archive after checking it is a workspace backup.

        Returns ``(path, error)``; ``path`` is None when rejected.
        """
        if not filename.lower().endswith('.zip'):
            return None, 'Only .zip workspace backups can be uploaded.'

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        target = self.workspace_dir(workspace_id) / f'backup_UPLOADED_{timestamp}.zip'
        try:
            target.write_bytes(content)
        except OSError as exc:
            return None, f'Could not save the upload: {exc}'

        manifest = self.read_manifest(target)
        if not manifest or 'workspace_id' not in manifest:
            target.unlink(missing_ok=True)
            return None, (
                'That file is not a workspace backup. Full-server .db backups '
                'cannot be restored here.'
            )
        if int(manifest.get('format_version', 0)) > FORMAT_VERSION:
            target.unlink(missing_ok=True)
            return None, 'That backup was made by a newer version of the app.'

        self._cleanup(workspace_id)
        return target, ''

    # -- restore -----------------------------------------------------------

    def restore_backup(self, workspace_id: int, filename: str) -> Tuple[bool, str]:
        """Replace one workspace's data with the contents of an archive.

        Other workspaces are never read or written. Runs in a single
        transaction, so a failure leaves the current data untouched.
        """
        workspace_id = int(workspace_id)
        path = self.resolve_backup_file(workspace_id, filename)
        if not path:
            return False, 'Backup file not found.'

        try:
            with zipfile.ZipFile(path, 'r') as zf:
                manifest = json.loads(zf.read('manifest.json').decode('utf-8'))
                data: Dict[str, List[Dict[str, Any]]] = {}
                for entry in zf.namelist():
                    if entry.startswith('data/') and entry.endswith('.json'):
                        table = entry[len('data/'):-len('.json')]
                        data[table] = json.loads(zf.read(entry).decode('utf-8'))
                file_entries = [n for n in zf.namelist() if n.startswith('files/')]
                staged_files = {n: zf.read(n) for n in file_entries}
        except Exception as exc:
            return False, f'Could not read the backup archive: {exc}'

        if int(manifest.get('format_version', 0)) > FORMAT_VERSION:
            return False, 'That backup was made by a newer version of the app.'

        # A safety copy of the current state, so a bad restore is recoverable.
        # `protect` keeps retention from evicting the archive being restored.
        try:
            self.create_backup(workspace_id, is_manual=True, protect=path.name)
        except Exception as exc:  # never block a restore on the safety copy
            logger.warning('Pre-restore safety backup failed: %s', exc)

        conn = sqlite3.connect(str(self.db_path), timeout=120)
        try:
            conn.execute('PRAGMA foreign_keys=OFF')
            cur = conn.cursor()
            live_tables = set(_existing_tables(cur))
            order = _topological_order(
                [t for t in TENANT_SCOPE if t in live_tables]
            )

            insert_order, deferred = _fk_insert_order(
                cur, [t for t in order if t != 'workspace']
            )

            cur.execute('BEGIN')
            # 1. Remove the workspace's current rows, children first.
            current_ids = self._collect_scoped_ids(cur, workspace_id, order)
            for table in reversed(insert_order):
                row_ids = current_ids.get(table, set())
                if not row_ids:
                    continue
                pk = _primary_key(cur, table) or 'rowid'
                ids = list(row_ids)
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i + 500]
                    placeholders = ','.join('?' * len(chunk))
                    cur.execute(
                        f'DELETE FROM "{table}" WHERE {pk} IN ({placeholders})',
                        tuple(chunk),
                    )

            # 2. Re-insert with fresh primary keys, rewriting foreign keys.
            id_maps: Dict[str, Dict[Any, Any]] = {}
            counts: Dict[str, int] = {}

            workspace_rows = data.get('workspace', [])
            id_maps['workspace'] = {}
            if workspace_rows:
                self._restore_workspace_row(cur, workspace_id, workspace_rows[0])
                # The archive's workspace id maps onto the live one.
                id_maps['workspace'][workspace_rows[0].get('id')] = workspace_id

            for table in insert_order:
                rows = data.get(table, [])
                if not rows or table not in live_tables:
                    id_maps.setdefault(table, {})
                    continue
                counts[table] = self._insert_rows(
                    cur, table, rows, id_maps, workspace_id, deferred
                )

            # 3. Patch the foreign keys that had to be deferred.
            for table in insert_order:
                if table not in live_tables:
                    continue
                self._fix_deferred_refs(
                    cur, table, data.get(table, []), id_maps, deferred
                )

            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error('Workspace %s restore failed, rolled back: %s',
                         workspace_id, exc)
            return False, f'Restore failed and nothing was changed: {exc}'
        finally:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.close()

        # 4. Put attachment files back on disk (after the DB commit succeeded).
        restored_files = 0
        for entry, blob in staged_files.items():
            rel = entry[len('files/'):]
            if not rel or '..' in rel.split('/'):
                continue
            target = self.uploads_dir / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
                restored_files += 1
            except OSError as exc:
                logger.warning('Could not restore file %s: %s', rel, exc)

        total_rows = sum(counts.values())
        logger.info('Workspace %s restored from %s (%d rows, %d files)',
                    workspace_id, filename, total_rows, restored_files)
        return True, (
            f'Restored {total_rows} records and {restored_files} files '
            f'from {filename}.'
        )

    def _restore_workspace_row(self, cur: sqlite3.Cursor, workspace_id: int,
                               row: Dict[str, Any]) -> None:
        """Update the live workspace row in place, keeping its id."""
        columns = [c for c in _table_columns(cur, 'workspace') if c != 'id']
        updates = {c: _decode_value(row[c]) for c in columns if c in row}
        if not updates:
            return
        assignments = ', '.join(f'"{c}" = ?' for c in updates)
        cur.execute(
            f'UPDATE workspace SET {assignments} WHERE id = ?',
            (*updates.values(), workspace_id),
        )

    def _insert_rows(self, cur: sqlite3.Cursor, table: str,
                     rows: List[Dict[str, Any]],
                     id_maps: Dict[str, Dict[Any, Any]],
                     workspace_id: int,
                     deferred: Set[Tuple[str, str]]) -> int:
        columns = _table_columns(cur, table)
        pk = _primary_key(cur, table)
        fks = _foreign_keys(cur, table)
        not_null = _not_null_columns(cur, table)
        # Foreign keys pointing at a table we are also restoring get remapped.
        remap = {col: parent for col, parent, _ in fks if parent in TENANT_SCOPE}
        defer_cols = {col for tbl, col in deferred if tbl == table}
        unique_cols = [c for c in UNIQUE_TEXT_COLUMNS.get(table, []) if c in columns]

        insert_cols = [c for c in columns if c != pk]
        placeholders = ','.join('?' * len(insert_cols))
        column_list = ','.join(f'"{c}"' for c in insert_cols)
        sql = f'INSERT INTO "{table}" ({column_list}) VALUES ({placeholders})'

        table_map: Dict[Any, Any] = {}
        inserted = 0
        skipped = 0
        for row in rows:
            values = []
            unresolved = False
            for col in insert_cols:
                value = _decode_value(row.get(col))
                if col in defer_cols:
                    value = None  # patched in _fix_deferred_refs
                elif col in remap and value is not None:
                    parent_map = id_maps.get(remap[col], {})
                    mapped = parent_map.get(value)
                    if mapped is None and col in not_null:
                        # The parent no longer exists (e.g. a deleted user).
                        # Drop the row rather than inventing a reference:
                        # substituting another user here would hand out project
                        # membership or an assignment nobody actually had. Never
                        # keep the raw id either — it could point at another
                        # tenant's row.
                        unresolved = True
                    value = mapped
                elif col == 'workspace_id' and value is not None:
                    value = workspace_id
                values.append(value)

            if unresolved:
                skipped += 1
                continue

            for col in unique_cols:
                idx = insert_cols.index(col)
                values[idx] = self._deconflict(cur, table, col, values[idx])

            try:
                cur.execute(sql, values)
            except sqlite3.IntegrityError as exc:
                logger.warning('Skipping unrestorable %s row: %s', table, exc)
                skipped += 1
                continue
            if pk is not None and row.get(pk) is not None:
                table_map[row[pk]] = cur.lastrowid
            inserted += 1

        if skipped:
            logger.warning('%s: skipped %d row(s) with unresolvable references',
                           table, skipped)
        id_maps[table] = table_map
        return inserted

    def _deconflict(self, cur: sqlite3.Cursor, table: str, column: str,
                    value: Any) -> Any:
        """Give a globally-unique text value a suffix if it is already taken."""
        if not value or not isinstance(value, str):
            return value
        cur.execute(f'SELECT 1 FROM "{table}" WHERE "{column}" = ? LIMIT 1', (value,))
        if cur.fetchone() is None:
            return value
        for suffix in range(1, 1000):
            candidate = f'{value}-r{suffix}'
            cur.execute(
                f'SELECT 1 FROM "{table}" WHERE "{column}" = ? LIMIT 1', (candidate,)
            )
            if cur.fetchone() is None:
                logger.info('Renamed conflicting %s.%s "%s" -> "%s" during restore',
                            table, column, value, candidate)
                return candidate
        return value

    def _fix_deferred_refs(self, cur: sqlite3.Cursor, table: str,
                           rows: List[Dict[str, Any]],
                           id_maps: Dict[str, Dict[Any, Any]],
                           deferred: Set[Tuple[str, str]]) -> None:
        """Fill in foreign keys written as NULL because their parent came later."""
        defer_cols = {col for tbl, col in deferred if tbl == table}
        pk = _primary_key(cur, table)
        table_map = id_maps.get(table, {})
        if not defer_cols or not rows or not pk or not table_map:
            return
        parent_of = {
            col: parent for col, parent, _ in _foreign_keys(cur, table)
            if col in defer_cols
        }
        for row in rows:
            new_id = table_map.get(row.get(pk))
            if new_id is None:
                continue
            for col in defer_cols:
                old_parent = row.get(col)
                if old_parent is None:
                    continue
                parent_map = id_maps.get(parent_of.get(col, table), {})
                new_parent = parent_map.get(old_parent)
                if new_parent is None:
                    continue
                cur.execute(
                    f'UPDATE "{table}" SET "{col}" = ? WHERE "{pk}" = ?',
                    (new_parent, new_id),
                )

    # -- storage accounting ------------------------------------------------

    def workspace_file_usage(self, workspace_id: int) -> Dict[str, Any]:
        """Measure disk used by one workspace's attachments, grouped by folder."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            cur = conn.cursor()
            live_tables = set(_existing_tables(cur))
            tables = _topological_order([t for t in TENANT_SCOPE if t in live_tables])
            scoped_ids = self._collect_scoped_ids(cur, workspace_id, tables)
            data: Dict[str, List[Dict[str, Any]]] = {}
            for table in FILE_COLUMNS:
                if table in live_tables:
                    data[table] = self._fetch_rows(
                        cur, table, scoped_ids.get(table, set())
                    )
        finally:
            conn.close()

        folders: Dict[str, Dict[str, int]] = {}
        total = 0
        missing = 0
        for rel in self._referenced_files(data):
            source = self.uploads_dir / rel
            folder = rel.split('/')[0] if '/' in rel else '(root files)'
            bucket = folders.setdefault(folder, {'count': 0, 'size': 0})
            if not source.is_file():
                missing += 1
                continue
            size = source.stat().st_size
            bucket['count'] += 1
            bucket['size'] += size
            total += size

        breakdown = [
            {
                'name': name,
                'file_count': info['count'],
                'size_mb': round(info['size'] / (1024 * 1024), 2),
                'size_bytes': info['size'],
                'percent': round(info['size'] / total * 100, 1) if total else 0,
            }
            for name, info in folders.items()
        ]
        breakdown.sort(key=lambda f: f['size_bytes'], reverse=True)
        return {
            'total_bytes': total,
            'total_mb': round(total / (1024 * 1024), 2),
            'folders': breakdown,
            'missing_files': missing,
        }

    def workspace_row_counts(self, workspace_id: int) -> List[Dict[str, Any]]:
        """Row counts per table for one workspace."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            cur = conn.cursor()
            live_tables = set(_existing_tables(cur))
            tables = _topological_order([t for t in TENANT_SCOPE if t in live_tables])
            scoped_ids = self._collect_scoped_ids(cur, workspace_id, tables)
        finally:
            conn.close()
        counts = [
            {'name': table, 'count': len(ids)}
            for table, ids in scoped_ids.items() if ids
        ]
        counts.sort(key=lambda c: c['count'], reverse=True)
        return counts


# Global instance
workspace_backup = WorkspaceBackup()


# ---------------------------------------------------------------------------
# Scheduled per-company backups
# ---------------------------------------------------------------------------

BACKUP_INTERVAL_SECONDS = 43200  # every 12 hours, matching the server-wide job


def _all_workspaces(db_path: str = 'data.db') -> List[Tuple[int, str]]:
    path = Path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        cur = conn.cursor()
        cur.execute('SELECT id, name FROM workspace ORDER BY id')
        return [(row[0], row[1] or '') for row in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def backup_all_workspaces() -> int:
    """Create an automatic backup for every workspace. Returns how many ran."""
    done = 0
    for workspace_id, name in _all_workspaces(str(workspace_backup.db_path)):
        try:
            if workspace_backup.create_backup(workspace_id, name, is_manual=False):
                done += 1
        except Exception as exc:
            # One company's failure must not stop the others
            logger.error('Auto-backup failed for workspace %s: %s', workspace_id, exc)
    return done


async def start_workspace_backup_scheduler():
    """Background task creating per-company backups on a fixed interval."""
    import asyncio

    coverage = verify_scope_coverage(str(workspace_backup.db_path))
    if coverage['unmapped']:
        logger.warning(
            'Per-company backups exclude unmapped tables: %s. Add them to '
            'TENANT_SCOPE in app/core/tenant_backup.py.',
            ', '.join(coverage['unmapped'])
        )

    logger.info('Per-company backup scheduler started (every %dh)',
                BACKUP_INTERVAL_SECONDS // 3600)
    while True:
        try:
            await asyncio.sleep(BACKUP_INTERVAL_SECONDS)
            count = await asyncio.to_thread(backup_all_workspaces)
            logger.info('Per-company auto-backup complete (%d companies)', count)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error('Per-company backup scheduler error: %s', exc)
            await asyncio.sleep(3600)
