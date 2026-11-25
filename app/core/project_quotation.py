"""Validation, storage and quota handling for mandatory project quotations.

A browser supplies both a filename and a content type; neither is trusted.
Empty files, oversized files and files without a cleaned name are refused.
Everything else is accepted: bytes-verified raster photos and PDFs keep their
real extension, while all other file types are written outside the public
``/uploads`` mount under a fresh UUID ``.bin`` name with passive metadata. The
user's sanitized name is kept only for display/download. Private directory
isolation and the guarded workspace quota reservation stay unchanged.

Storage is reserved on ``workspace.quotation_bytes`` with a guarded UPDATE
before a file is written. That reservation and the Project row share one
transaction, so concurrent creates cannot both pass the same quota check.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import case, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace

from .attachment_upload import clean_upload_name, passive_mime_type, photo_type

logger = logging.getLogger(__name__)

MAX_QUOTATION_BYTES = 25 * 1024 * 1024
MAX_QUOTATION_BYTES_PER_WORKSPACE = 500 * 1024 * 1024
# Kept separate from the patchable limit so user-facing copy stays truthful.
DISPLAY_MAX_QUOTATION_MB = MAX_QUOTATION_BYTES // (1024 * 1024)

# Legacy names that older rows may still use on disk remain downloadable. New
# non-photo/non-PDF uploads always use '.bin'.
PDF_TYPES = ('.pdf',)
IMAGE_TYPES = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.heic')
DOCUMENT_TYPES = ('.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt')
ALLOWED_EXTENSIONS = PDF_TYPES + IMAGE_TYPES + DOCUMENT_TYPES + ('.bin',)
ACCEPT_ATTRIBUTE = ''


def validate_quotation(data: bytes, filename: str | None):
    """Return ``(MIME type, safe extension)`` or None when the file is unusable.

    Empty and oversized files and missing/unusable filenames are refused before
    any extension is considered. Verified photos and magic-byte PDFs keep their
    real extension. Every other type gets a passive metadata type and is stored
    with a '.bin' extension, so active web content can never be served inline.
    """
    name = clean_upload_name(filename, fallback='')
    if not data or len(data) > MAX_QUOTATION_BYTES or not name:
        return None

    extension = Path(name).suffix.lower()
    kind = photo_type(data)
    if kind:
        return kind
    if extension in PDF_TYPES and data[:5] == b'%PDF-':
        return 'application/pdf', '.pdf'
    return passive_mime_type(extension), '.bin'


def quotation_directory(base_dir: Path) -> Path:
    """Private server-owned folder, deliberately NOT under the /uploads mount."""
    return Path(base_dir) / 'private_uploads' / 'quotations'


def new_quotation_name(extension: str) -> str:
    """Generate the unguessable on-disk name before any bytes are written.

    Generating it in the caller means a cancelled or partially failed write is
    still addressable for cleanup.
    """
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError('Unsupported quotation extension')
    return f'{uuid.uuid4()}{extension}'


def store_quotation(base_dir: Path, data: bytes, extension: str,
                    stored_name: str | None = None) -> str:
    """Write the bytes under a generated name and return that stored name."""
    if stored_name is None:
        stored_name = new_quotation_name(extension)
    elif safe_quotation_path(base_dir, stored_name) is None:
        raise ValueError('Unsafe quotation storage name')

    directory = quotation_directory(base_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / stored_name).write_bytes(data)
    return stored_name


def safe_quotation_path(base_dir: Path, stored_name: str | None) -> Path | None:
    """Resolve a stored name for download, or None if it is not a bare safe name.

    The database is defence in depth here: a tampered row with separators or an
    unexpected extension must not be able to point the download route outside
    the quotation folder.
    """
    if not stored_name or not isinstance(stored_name, str):
        return None
    name = clean_upload_name(stored_name)
    if name != stored_name or Path(name).name != name:
        return None
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        return None
    directory = quotation_directory(base_dir)
    path = (directory / name).resolve()
    if path.parent != directory.resolve():
        return None
    return path


async def delete_quotation_file(base_dir: Path, stored_name: str | None) -> None:
    """Remove a stored file after its project row is gone."""
    path = safe_quotation_path(base_dir, stored_name)
    if not path:
        return
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except OSError:
        logger.exception('Could not remove quotation file after project deletion')


async def reserve_workspace_quota(db: AsyncSession, workspace_id, size: int,
                                  quota: int) -> bool:
    """Atomically reserve ``size`` bytes, or return False when over quota.

    The guarded UPDATE serialises writers on the workspace row (or SQLite's
    write lock), so two concurrent creates cannot both pass the same check.
    A super admin with no company has no workspace row to account against.
    """
    if workspace_id is None:
        return True
    result = await db.execute(
        update(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.quotation_bytes + size <= quota,
        )
        .values(quotation_bytes=Workspace.quotation_bytes + size)
    )
    return result.rowcount == 1


async def release_workspace_quota(db: AsyncSession, workspace_id, size: int) -> None:
    """Give reserved bytes back when a project and its file are deleted."""
    if workspace_id is None or not size:
        return
    await db.execute(
        update(Workspace)
        .where(Workspace.id == workspace_id)
        .values(quotation_bytes=case(
            (Workspace.quotation_bytes - size < 0, 0),
            else_=Workspace.quotation_bytes - size,
        ))
    )
