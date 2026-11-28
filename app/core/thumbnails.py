"""Small, cached JPEG thumbnails for image previews.

The cache lives under the uploads root (``app/uploads/.thumbs``), keyed by the
resolved source path, its mtime/size and the requested dimensions. The web app
explicitly denies HTTP access to that directory — it is a private server-side
cache, not a public upload. Everything here is best effort: when Pillow is
absent or the file is not a safe raster image, callers serve the original.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - environments without Pillow
    Image = None
    ImageOps = None

MAX_THUMBNAIL_PIXELS = 40_000_000
DEFAULT_MAX_SIZE = (480, 360)
_THUMBS_DIR = '.thumbs'
_DEFAULT_UPLOADS_ROOT = Path(__file__).resolve().parents[1] / 'uploads'
# Bound disk use: two files (JPEG + cleanup sidecar) per cached version.
_CACHE_MAX_ENTRIES = 256  # versions, not files
_CACHE_MAX_BYTES = 64 * 1024 * 1024
_STALE_TMP_SECONDS = 3600
# Serializes publishing a cached JPEG with cache cleanup within this process.
_PUBLISH_LOCK = threading.RLock()


def _uploads_root(uploads_root: Path | None) -> Path:
    root = Path(uploads_root) if uploads_root is not None else _DEFAULT_UPLOADS_ROOT
    return root.resolve()


def _resolve_source(source_path: Path, root: Path) -> Path | None:
    """Only real files inside the uploads tree may be decoded."""
    try:
        resolved = Path(source_path).resolve()
        if not resolved.is_relative_to(root):
            return None
        return resolved if resolved.is_file() else None
    except (OSError, ValueError, TypeError):
        return None


def _cache_digest(resolved: Path, stat: os.stat_result, max_size: tuple[int, int]) -> str:
    payload = f'{resolved}|{stat.st_mtime_ns}|{stat.st_size}|{max_size[0]}x{max_size[1]}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    """Replace the cache entry only once it is complete, even under concurrency."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix='.tmp-', suffix=path.suffix, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _quiet_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _safe_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def _prune_cache(thumb_dir: Path, keep_path: Path) -> None:
    """Keep the private cache bounded; evict oldest versions first.

    Also removes orphaned temp files left behind by a crashed writer. This is
    best effort and never touches the entry we are about to publish.
    """
    try:
        entries = list(thumb_dir.iterdir())
    except OSError:
        return

    now = time.time()
    for entry in entries:
        if entry.name.startswith('.tmp-'):
            stat = _safe_stat(entry)
            if stat and now - stat.st_mtime > _STALE_TMP_SECONDS:
                _quiet_unlink(entry)

    cached = [
        entry for entry in entries
        if entry.suffix in ('.jpg', '.json') and entry != keep_path
    ]
    total = sum(stat.st_size for entry in cached if (stat := _safe_stat(entry)))
    cached.sort(key=lambda entry: (stat.st_mtime if (stat := _safe_stat(entry)) else 0))

    while cached and (len(cached) >= _CACHE_MAX_ENTRIES * 2 or total > _CACHE_MAX_BYTES):
        victim = cached.pop(0)
        stat = _safe_stat(victim)
        if stat:
            total -= stat.st_size
        pair = victim.with_suffix('.json' if victim.suffix == '.jpg' else '.jpg')
        _quiet_unlink(pair)
        _quiet_unlink(victim)


def get_or_create_thumbnail(
    source_path: Path,
    max_size: tuple[int, int] = DEFAULT_MAX_SIZE,
    *,
    uploads_root: Path | None = None,
) -> Optional[Path]:
    """Return a cached JPEG for a raster image, or None to fall back to the original.

    ``uploads_root`` defaults to ``app/uploads``; the preview route passes its
    own BASE_DIR-derived root so tests and alternate deployments stay in sync
    with ``_resolve_stored_upload``.
    """
    if Image is None or ImageOps is None:
        return None
    try:
        width, height = max_size
        if width <= 0 or height <= 0:
            return None
    except (TypeError, ValueError):
        return None

    root = _uploads_root(uploads_root)
    resolved = _resolve_source(source_path, root)
    if resolved is None:
        return None
    try:
        stat = resolved.stat()
    except OSError:
        return None

    digest = _cache_digest(resolved, stat, (width, height))
    thumb_dir = root / _THUMBS_DIR
    cache_path = thumb_dir / f'{digest}.jpg'
    if cache_path.is_file():
        return cache_path

    try:
        with Image.open(resolved) as opened:
            if getattr(opened, 'is_animated', False) or getattr(opened, 'n_frames', 1) > 1:
                return None
            if opened.width * opened.height > MAX_THUMBNAIL_PIXELS:
                return None

            # JPEG can often be decoded at a reduced resolution before pixels
            # are expanded; this keeps peak memory close to the output size.
            draft = getattr(opened, 'draft', None)
            if draft:
                try:
                    draft('RGB', (width * 2, height * 2))
                except Exception:
                    pass
            opened.load()

            try:
                upright = ImageOps.exif_transpose(opened, in_place=True)
            except TypeError:  # older Pillow without in_place
                upright = ImageOps.exif_transpose(opened)
            if upright is None:
                upright = opened
            if upright.width * upright.height > MAX_THUMBNAIL_PIXELS:
                return None

            resampling = getattr(Image, 'Resampling', Image).LANCZOS
            # Shrink before flattening: alpha compositing then works on a tiny
            # image instead of a second full-resolution bitmap.
            upright.thumbnail((width, height), resampling)
            if upright.mode in ('RGBA', 'LA', 'P') or 'A' in upright.getbands():
                rgba = upright.convert('RGBA')
                image = Image.new('RGB', rgba.size, (255, 255, 255))
                image.paste(rgba, mask=rgba.getchannel('A'))
            else:
                image = upright if upright.mode == 'RGB' else upright.convert('RGB')

            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=78, optimize=True, progressive=True)
            jpeg = buffer.getvalue()
    except Exception:
        # Unidentified/corrupt images, decompression bombs and unsupported
        # modes must never break a ticket page or a preview request.
        return None

    with _PUBLISH_LOCK:
        _prune_cache(thumb_dir, cache_path)
        # Write the cleanup sidecar FIRST. A crash between the two writes then
        # leaves a catalogued entry (still removable) rather than an orphan JPEG.
        sidecar = cache_path.with_suffix('.json')
        try:
            _atomic_write(sidecar, json.dumps({'source': str(resolved)}).encode('utf-8'))
        except (OSError, TypeError, ValueError):
            return None
        try:
            _atomic_write(cache_path, jpeg)
        except OSError:
            _quiet_unlink(sidecar)
            return None
    return cache_path


def delete_thumbnail(source_path: Path, *, uploads_root: Path | None = None) -> None:
    """Remove every cached thumbnail for a source; never raise on cleanup."""
    root = _uploads_root(uploads_root)
    with _PUBLISH_LOCK:
        try:
            resolved = Path(source_path).resolve()
            if not resolved.is_relative_to(root):
                return
            current_key = None
            try:
                current_key = _cache_digest(resolved, resolved.stat(), DEFAULT_MAX_SIZE) + '.jpg'
            except OSError:
                pass
        except (OSError, ValueError, TypeError):
            return

        thumb_dir = root / _THUMBS_DIR
        try:
            entries = list(thumb_dir.iterdir())
        except OSError:
            return

        for entry in entries:
            matched = entry.name == current_key
            if not matched and entry.suffix == '.json':
                try:
                    payload = json.loads(entry.read_text(encoding='utf-8'))
                    matched = payload.get('source') == str(resolved)
                except (OSError, ValueError, TypeError, AttributeError):
                    matched = False
                if matched:
                    _quiet_unlink(entry.with_suffix('.jpg'))
            if matched:
                _quiet_unlink(entry)
