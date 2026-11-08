"""Thumbnails and browser-friendly copies of photos (cached under derived/)."""

from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image, ImageOps

from . import metadata  # noqa: F401  (registers the HEIC opener)

THUMB_PX = 400
VIEW_PX = 2048
BROWSER_NATIVE = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
               ".3gp": "video/3gpp", ".avi": "video/x-msvideo", ".mkv": "video/x-matroska", ".webm": "video/webm"}
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp"}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def media_kind_of(rel_path: str) -> str:
    from .metadata import media_kind
    return media_kind(Path(rel_path)) or "photo"


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _render(src: Path, dest: Path, px: int, quality: int) -> Path:
    with _lock_for(str(dest)):
        if dest.exists():
            with _locks_guard:
                _locks.pop(str(dest), None)
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((px, px))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            tmp = dest.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=quality, optimize=True)
            tmp.replace(dest)
    return dest


def poster_path(library: Path, sha256: str) -> Path:
    """Where a video's poster frame lives (same place as a photo's thumbnail)."""
    return library / "derived" / "thumbs" / sha256[:2] / f"{sha256}.jpg"


MAX_POSTER_PX = 30_000_000     # a poster frame is a video frame; anything bigger is not one


def poster_failed_path(library: Path, sha256: str) -> Path:
    """Marker left when the page could not decode a video (unsupported codec), so nobody tries again."""
    return poster_path(library, sha256).with_suffix(".none")


def generic_poster(library: Path) -> Path:
    """The tile shown for videos that cannot be decoded: a plain dark frame (the page adds the ▶ badge)."""
    dest = library / "derived" / "thumbs" / "_video.jpg"
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 300), (38, 42, 48)).save(dest, "JPEG", quality=70)
    return dest


def save_poster(library: Path, sha256: str, data: bytes) -> Path:
    """Store a poster frame the page decoded from a video. Re-encoded through Pillow, so only a real image gets in.
    The first poster wins: an existing one is never overwritten through this route."""
    import io
    if len(data) > 2_000_000:
        raise ValueError("Poster too large")
    with Image.open(io.BytesIO(data)) as im:
        if im.width * im.height > MAX_POSTER_PX:
            raise ValueError("Poster too large")
        if poster_path(library, sha256).is_file():
            return poster_path(library, sha256)          # junk is refused above; a real image just does not replace the first
        im = im.convert("RGB")
        im.thumbnail((THUMB_PX, THUMB_PX))
        dest = poster_path(library, sha256)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=80, optimize=True)
        tmp.replace(dest)
    failed = poster_failed_path(library, sha256)
    if failed.exists():
        failed.unlink()
    return dest


def thumbnail(library: Path, originals: Path, rel_path: str, sha256: str) -> Path:
    dest = library / "derived" / "thumbs" / sha256[:2] / f"{sha256}.jpg"
    if dest.exists():
        return dest
    return _render(originals / rel_path, dest, THUMB_PX, 80)


def viewable(library: Path, originals: Path, rel_path: str, sha256: str) -> tuple[Path, str]:
    """A file the browser can show: the original if possible, else a JPEG copy."""
    src = originals / rel_path
    ext = src.suffix.lower()
    if ext in VIDEO_TYPES:
        return src, VIDEO_TYPES[ext]
    if ext in BROWSER_NATIVE:
        return src, IMAGE_TYPES[ext]
    dest = library / "derived" / "view" / sha256[:2] / f"{sha256}.jpg"
    return _render(src, dest, VIEW_PX, 88), "image/jpeg"
