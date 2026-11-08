"""Read date, GPS, camera and size from photo files."""

from __future__ import annotations

from dataclasses import dataclass
import os
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
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".3gp", ".mkv", ".webm"}

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


# ---- XMP sidecars (icloudpd writes <file>.xmp with iCloud's own record) -------------------------

import re as _re
import struct as _struct
import xml.etree.ElementTree as _ET

_NS = {"exif": "http://ns.adobe.com/exif/1.0/", "xmp": "http://ns.adobe.com/xap/1.0/",
       "dc": "http://purl.org/dc/elements/1.1/", "tiff": "http://ns.adobe.com/tiff/1.0/",
       "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}


@dataclass
class SidecarMeta:
    lat: float | None = None
    lon: float | None = None
    taken_at: str | None = None
    favorite: bool = False
    rejected: bool = False          # hidden or deleted in iCloud
    screenshot: bool = False
    title: str | None = None
    keywords: list[str] | None = None


def _xmp_coord(text: str | None) -> float | None:
    """Adobe XMP GPS ('39,7.8N' or '39,7,48N') or a plain decimal ('39.13')."""
    if not text:
        return None
    t = text.strip()
    m = _re.fullmatch(r"([+-]?\d+(?:\.\d+)?)", t)
    if m:
        return round(float(m.group(1)), 7)
    m = _re.fullmatch(r"(\d+),(\d+(?:\.\d+)?)(?:,(\d+(?:\.\d+)?))?\s*([NSEW])", t, _re.I)
    if not m:
        return None
    deg = float(m.group(1)) + float(m.group(2)) / 60 + (float(m.group(3)) / 3600 if m.group(3) else 0)
    return round(-deg if m.group(4).upper() in "SW" else deg, 7)


def _xmp_date(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(t).isoformat(timespec="seconds")
    except ValueError:
        return None


def read_sidecar(path: Path) -> SidecarMeta | None:
    """Parse <photo>.xmp if present. Never raises."""
    side = path.with_name(path.name + ".xmp")
    if not side.is_file():
        return None
    try:
        root = _ET.parse(side).getroot()
    except Exception:
        return None
    meta = SidecarMeta()
    find = lambda tag: next((e.text for e in root.iter(tag) if e.text), None)  # noqa: E731
    lat = _xmp_coord(find(f"{{{_NS['exif']}}}GPSLatitude"))
    lon = _xmp_coord(find(f"{{{_NS['exif']}}}GPSLongitude"))
    if lat is not None and lon is not None and not (lat == 0 and lon == 0) and abs(lat) <= 90 and abs(lon) <= 180:
        meta.lat, meta.lon = lat, lon
    meta.taken_at = _xmp_date(find(f"{{{_NS['xmp']}}}CreateDate"))
    rating = find(f"{{{_NS['xmp']}}}Rating")
    try:
        meta.favorite = int(rating or 0) >= 5
        meta.rejected = int(rating or 0) < 0
    except ValueError:
        pass
    meta.screenshot = (find(f"{{{_NS['tiff']}}}Make") or "").strip().lower() == "screenshot"
    meta.title = _clean(find(f"{{{_NS['dc']}}}title"))
    subj = next((e for e in root.iter(f"{{{_NS['dc']}}}subject")), None)
    if subj is not None:
        meta.keywords = [li.text.strip() for li in subj.iter(f"{{{_NS['rdf']}}}li") if li.text and li.text.strip()] or None
    return meta


# ---- Google Photos Takeout sidecars (<photo>.jpg.json, .supplemental-metadata.json, truncated names) ----

import json as _json
from functools import lru_cache as _lru_cache


@_lru_cache(maxsize=256)
def _google_index(folder: str) -> dict[str, str]:
    """title -> json file name for every Takeout sidecar in a folder (Takeout truncates and renames the
    json files, but each one names its photo in "title")."""
    out: dict[str, str] = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        if not n.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, n), "rb") as f:
                head = f.read(200_000)
            data = _json.loads(head)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("title"), str) and "photoTakenTime" in data:
            out.setdefault(data["title"], n)
    return out


def google_sidecar_path(path: Path) -> Path | None:
    """The Takeout json that describes this file, if there is one. Never raises."""
    for cand in (path.name + ".json", path.name + ".supplemental-metadata.json", path.with_suffix("").name + ".json"):
        p = path.with_name(cand)
        if p.is_file():
            return p
    m = _re.fullmatch(r"(.*)(\(\d+\))(\.[^.]+)", path.name)      # IMG(1).jpg -> IMG.jpg(1).json
    if m:
        p = path.with_name(f"{m.group(1)}{m.group(3)}{m.group(2)}.json")
        if p.is_file():
            return p
    name = _google_index(str(path.parent)).get(path.name)
    return path.with_name(name) if name else None


def read_google_sidecar(path: Path) -> SidecarMeta | None:
    """Dates, location, favourite, description and people from a Google Photos Takeout sidecar. Never raises."""
    side = google_sidecar_path(path)
    if side is None:
        return None
    try:
        data = _json.loads(side.read_bytes()[:400_000])
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return _google_fields(data)
    except Exception:          # legal JSON with unexpected shapes (lists where objects were expected, numbers for names)
        return None


def _google_fields(data: dict) -> SidecarMeta:
    meta = SidecarMeta()
    ts = (data.get("photoTakenTime") or {}).get("timestamp")
    try:
        if ts is not None and float(ts) > 0:
            meta.taken_at = datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    for key in ("geoData", "geoDataExif"):
        g = data.get(key) or {}
        try:
            lat, lon = float(g.get("latitude", 0)), float(g.get("longitude", 0))
        except (TypeError, ValueError):
            continue
        if (lat or lon) and abs(lat) <= 90 and abs(lon) <= 180:
            meta.lat, meta.lon = round(lat, 7), round(lon, 7)
            break
    meta.favorite = bool(data.get("favorited"))
    meta.title = _clean(data.get("description")) if isinstance(data.get("description"), str) else None
    people = [p.get("name", "").strip() for p in data.get("people") or [] if isinstance(p, dict)]
    meta.keywords = [p for p in people if p] or None
    return meta


# ---- videos: QuickTime / MP4 atoms (location in ©xyz or the Apple keys, date in mvhd) ----------

@dataclass
class VideoMeta:
    taken_at: str | None = None
    lat: float | None = None
    lon: float | None = None
    width: int | None = None
    height: int | None = None


_ISO6709 = _re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")
_MAC_EPOCH = datetime(1904, 1, 1)


def _iso6709(text: str) -> tuple[float, float] | None:
    m = _ISO6709.match(text.strip())
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if lat == 0 and lon == 0 or abs(lat) > 90 or abs(lon) > 180:
        return None
    return round(lat, 7), round(lon, 7)


def _atoms(f, start: int, end: int):
    """Yield (type, payload_start, payload_end) for the boxes between start and end."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, typ = _struct.unpack(">I4s", head)
        hdr = 8
        if size == 1:
            size = _struct.unpack(">Q", f.read(8))[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        yield typ, pos + hdr, min(pos + size, end)
        pos += size


def read_video_meta(path: Path) -> VideoMeta:
    """Creation time and GPS from a .mov/.mp4 without decoding it. Never raises."""
    meta = VideoMeta()
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            for typ, a, b in _atoms(f, 0, size):
                if typ != b"moov":
                    continue
                keys: list[str] = []
                for t2, a2, b2 in _atoms(f, a, b):
                    if t2 == b"mvhd":
                        f.seek(a2)
                        ver = f.read(1)[0]
                        f.seek(a2 + 4)
                        created = _struct.unpack(">Q", f.read(8))[0] if ver == 1 else _struct.unpack(">I", f.read(4))[0]
                        if created > 0:
                            try:
                                meta.taken_at = (_MAC_EPOCH + __import__("datetime").timedelta(seconds=created)).isoformat(timespec="seconds") + "+00:00"
                            except (OverflowError, ValueError):
                                pass
                    elif t2 == b"trak":
                        for t3, a3, b3 in _atoms(f, a2, b2):
                            if t3 == b"tkhd" and meta.width is None:
                                f.seek(a3)
                                ver = f.read(1)[0]
                                f.seek(b3 - 8)
                                w, h = _struct.unpack(">II", f.read(8))
                                if w and h:
                                    meta.width, meta.height = w >> 16, h >> 16
                    elif t2 == b"udta":
                        for t3, a3, b3 in _atoms(f, a2, b2):
                            if t3 in (b"\xa9xyz", b"loci") and meta.lat is None:
                                f.seek(a3)
                                raw = f.read(b3 - a3)
                                if t3 == b"\xa9xyz" and len(raw) > 4:
                                    n = _struct.unpack(">H", raw[:2])[0]
                                    loc = _iso6709(raw[4:4 + n].decode("utf-8", "ignore"))
                                    if loc:
                                        meta.lat, meta.lon = loc
                    elif t2 == b"meta":
                        # Apple's keyed metadata: keys lists the names, ilst holds the values in the same order.
                        inner = list(_atoms(f, a2 + 4, b2))      # 'meta' is a full box: 4 bytes of version/flags
                        if not any(t == b"keys" for t, _, _ in inner):
                            inner = list(_atoms(f, a2, b2))
                        for t3, a3, b3 in inner:
                            if t3 == b"keys":
                                f.seek(a3 + 4)
                                n = _struct.unpack(">I", f.read(4))[0]
                                pos = a3 + 8
                                for _ in range(min(n, 200)):
                                    f.seek(pos)
                                    ksize, _kns = _struct.unpack(">I4s", f.read(8))
                                    keys.append(f.read(max(0, ksize - 8)).decode("utf-8", "ignore"))
                                    pos += ksize
                        for t3, a3, b3 in inner:
                            if t3 == b"ilst":
                                for t4, a4, b4 in _atoms(f, a3, b3):
                                    idx = _struct.unpack(">I", t4)[0] - 1
                                    if not (0 <= idx < len(keys)):
                                        continue
                                    for t5, a5, b5 in _atoms(f, a4, b4):
                                        if t5 == b"data":
                                            f.seek(a5 + 8)
                                            n_ = b5 - a5 - 8
                                            if n_ <= 0 or n_ > (1 << 20):
                                                continue
                                            val = f.read(n_).decode("utf-8", "ignore")
                                            k = keys[idx]
                                            if k.endswith("location.ISO6709") and meta.lat is None:
                                                loc = _iso6709(val)
                                                if loc:
                                                    meta.lat, meta.lon = loc
                                            elif k.endswith("creationdate"):
                                                d = _xmp_date(val)
                                                if d:
                                                    meta.taken_at = d
                break
    except Exception:
        pass
    return meta
