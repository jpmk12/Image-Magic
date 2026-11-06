"""Family edition, local-first: people you pair add their own photos to the library.

No server, no accounts. A paired phone (Settings → Phone access) carries a
name; photos it sends land in originals/contributed/<name>/<year>/<month>/,
are checked against the library first (exact copies and look-alikes are
refused, not stored twice), are catalogued at once with the contributor's
name, and show up everywhere with "Added by <name>". Trips, events and
chapters pick them up at the next catalog update.
"""

from __future__ import annotations

import hashlib
import io
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import catalog, importer, metadata, places

MAX_BYTES = 60_000_000
TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/heic": ".heic", "image/heif": ".heic", "image/webp": ".webp",
         "video/mp4": ".mp4", "video/quicktime": ".mov"}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")[:40] or "family"


def contribute(conn: sqlite3.Connection, library: Path, originals: Path, data: bytes, mime: str,
               contributor: str, filename: str = "") -> dict:
    """Store one photo/video from a family member. Returns what happened."""
    base = (mime or "").split(";")[0].strip().lower()
    if base not in TYPES:
        raise ValueError("Unsupported file type")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("The file is empty or too large")
    digest = hashlib.sha256(data).hexdigest()
    dup = conn.execute("SELECT id FROM assets WHERE sha256 = ? AND missing = 0", (digest,)).fetchone()
    if dup:
        return {"status": "exact", "id": dup["id"]}
    kind = "video" if base.startswith("video/") else "photo"
    taken = None
    if kind == "photo":
        try:
            with Image.open(io.BytesIO(data)) as im:
                ph = importer.dhash(im)
            if importer.informative(ph):
                for r in conn.execute("SELECT id, phash FROM assets WHERE phash IS NOT NULL AND kind = 'photo' AND missing = 0"):
                    if importer.hamming(ph, r["phash"]) <= importer.NEAR_BITS:
                        return {"status": "near", "id": r["id"]}
        except Exception:
            pass
    tmp = library / "derived" / "incoming" / f"{digest}{TYPES[base]}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        meta = metadata.read_photo_meta(tmp) if kind == "photo" else None
        taken = (meta.taken_at if meta else None) or datetime.now().isoformat(timespec="seconds")
        when = datetime.fromisoformat(taken[:19])
        stem = _safe(Path(filename).stem) if filename else digest[:12]
        rel = Path("contributed") / _safe(contributor) / f"{when:%Y}" / f"{when:%Y-%m}" / f"{stem}{TYPES[base]}"
        dest = originals / rel
        n = 2
        while dest.exists():
            dest = dest.with_name(f"{stem}-{n}{TYPES[base]}")
            n += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    aid = catalog.add_file(conn, originals, dest, contributor=contributor[:40])
    places.update_places(conn)
    return {"status": "added", "id": aid, "path": dest.relative_to(originals).as_posix()}


def contributors(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT contributor AS name, COUNT(*) AS photos, MAX(first_seen) AS last FROM assets "
        "WHERE contributor IS NOT NULL AND missing = 0 GROUP BY contributor ORDER BY photos DESC")]
