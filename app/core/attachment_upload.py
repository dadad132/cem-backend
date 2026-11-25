"""Validation shared by the web attachment upload routes.

Browsers send a filename and a MIME type, both of which the caller controls.
Neither is trusted here: every non-empty file within the size limit is accepted,
but only verified raster photos and PDFs keep their real extension on disk.
Everything else is written with a passive ``.bin`` extension so that a directly
requested ``/uploads`` path can never activate uploaded HTML/SVG/JS content.
The browser's filename is kept (sanitized) only as download/display metadata.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_UPLOAD = 20
MAX_FILENAME_LENGTH = 120

# Keep only characters that are harmless in an HTML attribute, a JS string and
# a Content-Disposition header. Quotes and angle brackets are the dangerous
# ones: they have broken out of all three contexts in this app before.
_UNSAFE_FILENAME_CHARS = re.compile(r'[^A-Za-z0-9._ ()-]')

# Passive metadata types for the database. The on-disk extension is '.bin' for
# all of these; the MIME type only describes a download, never inline content.
DOCUMENT_TYPES = {
    '.txt': 'text/plain',
    '.text': 'text/plain',
    '.log': 'text/plain',
    '.csv': 'text/csv',
    '.tsv': 'text/tab-separated-values',
    '.json': 'application/json',
    '.jsonl': 'application/x-ndjson',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.odt': 'application/vnd.oasis.opendocument.text',
    '.ods': 'application/vnd.oasis.opendocument.spreadsheet',
    '.odp': 'application/vnd.oasis.opendocument.presentation',
    '.zip': 'application/zip',
    '.rar': 'application/vnd.rar',
    '.7z': 'application/x-7z-compressed',
    '.gz': 'application/gzip',
    '.tar': 'application/x-tar',
    '.mp3': 'audio/mpeg',
    '.mp4': 'video/mp4',
}

# Extensions that can execute in a browser if a static mount ever guesses the
# type from the URL. They always get application/octet-stream in metadata, and
# always get a .bin suffix on disk.
ACTIVE_WEB_EXTENSIONS = frozenset({
    '.html', '.htm', '.xhtml', '.shtml', '.svg', '.svgz', '.js', '.mjs', '.cjs',
    '.jsx', '.ts', '.tsx', '.xml', '.xsl', '.xslt', '.swf', '.php', '.php3',
    '.php4', '.php5', '.phtml', '.asp', '.aspx', '.jsp', '.cgi', '.vbs',
    '.wsf', '.sh', '.bat', '.cmd', '.ps1', '.py', '.rb', '.pl', '.exe', '.msi',
    '.dll', '.com', '.scr', '.jar',
})

# Only bytes-verified raster photos and PDFs may ever be served inline.
SAFE_IMAGE_PREVIEW_MIMES = frozenset({
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/heic',
})
# HEIC is left out of the inline set: browsers outside Safari cannot render it
# and forcing a download is safer than guessing at an inline renderer.
SAFE_INLINE_PREVIEW_MIMES = frozenset({
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf',
})


def photo_type(data: bytes):
    """(mime type, extension) of a photo judged by its bytes, or None.

    SVG and HTML are deliberately absent: they can carry script, and an
    uploaded file is served from our own origin.
    """
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg', '.jpg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png', '.png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif', '.gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    if data[4:8] == b'ftyp' and data[8:12] in (b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1'):
        return 'image/heic', '.heic'
    return None


def clean_upload_name(filename: str | None, fallback: str = 'file') -> str:
    """Basename of a browser-supplied filename, safe in HTML, JS and headers.

    Windows and POSIX separators are both treated as separators, then every
    character outside a small allowlist is replaced. This keeps a name from
    forging a header, an HTML attribute or a JS string in attachment previews.
    """
    name = (filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    name = _UNSAFE_FILENAME_CHARS.sub('_', name)
    return name.strip().strip('.')[:MAX_FILENAME_LENGTH] or fallback


def passive_mime_type(extension: str) -> str:
    """Describe a stored file without ever claiming active web content.

    Unknown extensions, scriptable web types and unverified PDFs get
    application/octet-stream. A verified PDF never reaches this helper.
    """
    if extension in ACTIVE_WEB_EXTENSIONS or extension == '.pdf':
        return 'application/octet-stream'
    return DOCUMENT_TYPES.get(extension, 'application/octet-stream')


def validate_attachment(data: bytes, filename: str | None, declared_type: str | None = None,
                        max_bytes: int | None = None):
    """Return ``(MIME type, stored extension)`` or None for an unusable file.

    Only empty and oversized files are refused. Verified raster photos and
    magic-byte PDFs keep their real extension; every other file is stored as
    ``.bin`` with a passive metadata type. ``declared_type`` is accepted for
    backwards compatibility but ignored: browser-supplied types are untrusted.
    """
    limit = MAX_ATTACHMENT_BYTES if max_bytes is None else max_bytes
    if not data or len(data) > limit:
        return None

    extension = Path(clean_upload_name(filename)).suffix.lower()
    kind = photo_type(data)
    if kind:
        return kind
    if data[:5] == b'%PDF-' and extension == '.pdf':
        return 'application/pdf', '.pdf'
    return passive_mime_type(extension), '.bin'


def attachment_rejection_reason(name: str, data: bytes,
                                max_bytes: int | None = None) -> str:
    """Why ``validate_attachment`` refused a file, for user-facing messages."""
    limit = MAX_ATTACHMENT_BYTES if max_bytes is None else max_bytes
    if not data:
        return f'{name} is empty'
    if len(data) > limit:
        if limit >= 1024 * 1024:
            size = f'{round(limit / (1024 * 1024))} MB'
        elif limit >= 1024:
            size = f'{max(1, round(limit / 1024))} KB'
        else:
            size = f'{limit} bytes'
        return f'{name} is larger than {size}'
    return f'{name} could not be stored safely'
