"""Read date, GPS, camera and size from photo files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

try:  # HEIC/HEIF support (iPhone's default format)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - dependency is required, but stay usable
    pass

PHOTO_EXTS = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff",
              ".webp", ".dng", ".bmp"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".3gp", ".mkv"}

# EXIF tag ids
_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_MAKE = 271
_MODEL = 272
_DATETIME = 306
_DATETIME_ORIGINAL = 36867
_OFFSET_TIME_ORIGINAL = 36881


@dataclass
class PhotoMeta:
    taken_at: str | None = None  # ISO 8601, local time as recorded by the camera
    lat: float | None = None
    lon: float | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    width: int | None = None
    height: int | None = None


def media_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def _parse_exif_datetime(value: object, offset: object = None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.strptime(value.strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    iso = dt.isoformat()
    if isinstance(offset, str) and len(offset.strip()) == 6:
        iso += offset.strip()
    return iso


def _to_degrees(dms: object, ref: object) -> float | None:
    try:
        d, m, s = (float(x) for x in dms)  # type: ignore[union-attr]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    deg = d + m / 60 + s / 3600
    if isinstance(ref, bytes):
        ref = ref.decode(errors="ignore")
    if isinstance(ref, str) and ref.strip().upper() in ("S", "W"):
        deg = -deg
    return round(deg, 7)


def _clean(value: object) -> str | None:
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    if isinstance(value, str):
        value = value.strip("\x00 ").strip()
        return value or None
    return None


def read_photo_meta(path: Path) -> PhotoMeta:
    """Best-effort metadata read. Never raises for a bad or unusual file."""
    meta = PhotoMeta()
    try:
        with Image.open(path) as img:
            meta.width, meta.height = img.size
            exif = img.getexif()
    except Exception:
        return meta

    meta.camera_make = _clean(exif.get(_MAKE))
    meta.camera_model = _clean(exif.get(_MODEL))

    try:
        sub = exif.get_ifd(_EXIF_IFD)
    except Exception:
        sub = {}
    meta.taken_at = _parse_exif_datetime(
        sub.get(_DATETIME_ORIGINAL), sub.get(_OFFSET_TIME_ORIGINAL)
    ) or _parse_exif_datetime(exif.get(_DATETIME))

    try:
        gps = exif.get_ifd(_GPS_IFD)
    except Exception:
        gps = {}
    if gps:
        lat = _to_degrees(gps.get(2), gps.get(1))
        lon = _to_degrees(gps.get(4), gps.get(3))
        # (0, 0) is almost always "no fix" rather than the Gulf of Guinea.
        if lat is not None and lon is not None and not (lat == 0 and lon == 0):
            meta.lat, meta.lon = lat, lon
    return meta
