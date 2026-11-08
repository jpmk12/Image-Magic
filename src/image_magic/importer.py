"""Import photos from a folder (desktop, old phone, camera card) into the library.

Two duplicate checks before anything is copied:
  1. exact: the file's SHA-256 already exists in the catalog;
  2. look-alike: a perceptual hash (dHash, 64 bits) within a few bits of a catalogued
     photo, which catches resized/re-saved copies of the same picture.
New files are copied into originals/imported/<year>/<year-month>/ and catalogued.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from . import catalog, media
from .metadata import google_sidecar_path, media_kind, read_photo_meta

NEAR_BITS = 6          # Hamming distance at or below this counts as the same picture
IMPORT_DIR = "imported"


def dhash(im: Image.Image) -> int:
    """64-bit difference hash: robust to resizing, re-encoding and small edits."""
    g = ImageOps.exif_transpose(im).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.get_flattened_data()) if hasattr(g, "get_flattened_data") else list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return bits - (1 << 64) if bits >= (1 << 63) else bits   # signed, so it fits SQLite's INTEGER


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count("1")


def informative(h: int) -> bool:
    """A flat picture (blank screenshot, black frame) hashes to almost all 0s or 1s and
    would match every other flat picture; such hashes are not used for matching."""
    bits = bin(h & 0xFFFFFFFFFFFFFFFF).count("1")
    return 6 <= bits <= 58


def ensure_schema(conn: sqlite3.Connection) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(assets)")}
    if "phash" not in have:
        conn.execute("ALTER TABLE assets ADD COLUMN phash INTEGER")
        conn.commit()


def fill_hashes(conn: sqlite3.Connection, library: Path, originals: Path,
                progress: Callable[[int, int], None] | None = None,
                cancelled: Callable[[], bool] = lambda: False) -> int:
    """Compute the perceptual hash for catalogued photos that don't have one yet."""
    ensure_schema(conn)
    rows = conn.execute("SELECT id, path, sha256 FROM assets WHERE kind = 'photo' AND missing = 0 AND phash IS NULL").fetchall()
    n = 0
    for i, r in enumerate(rows, 1):
        if cancelled():
            break
        try:
            thumb = media.thumbnail(library, originals, r["path"], r["sha256"])
            with Image.open(thumb) as im:
                h = dhash(im)
        except Exception:
            h = -1   # unreadable: don't try again
        conn.execute("UPDATE assets SET phash = ? WHERE id = ?", (h, r["id"]))
        n += 1
        if n % 100 == 0:
            conn.commit()
        if progress and i % 50 == 0:
            progress(i, len(rows))
    conn.commit()
    return n


@dataclass
class Plan:
    folder: str
    new: list[dict] = field(default_factory=list)       # {path, size, kind}
    exact: list[dict] = field(default_factory=list)     # {path, match_id}
    near: list[dict] = field(default_factory=list)      # {path, match_id, bits}
    skipped: int = 0                                     # not a photo/video
    errors: int = 0
    sidecars: int = 0                                    # Google Takeout json files found next to new photos

    def summary(self) -> dict:
        return {"folder": self.folder, "new": len(self.new), "exact": len(self.exact), "near": len(self.near),
                "skipped": self.skipped, "errors": self.errors, "sidecars": self.sidecars,
                "new_bytes": sum(f["size"] for f in self.new),
                "near_examples": self.near[:40], "new_examples": [f["path"] for f in self.new[:20]]}


def check_folder(conn: sqlite3.Connection, folder: Path, library: Path, originals: Path,
                 progress: Callable[[int, int], None] | None = None,
                 cancelled: Callable[[], bool] = lambda: False) -> Plan:
    """Classify every photo/video under `folder` as new, exact duplicate, or look-alike."""
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    try:
        folder.resolve().relative_to(originals.resolve())
        raise ValueError("That folder is inside the library already.")
    except ValueError as e:
        if "inside the library" in str(e):
            raise
    ensure_schema(conn)
    from . import metadata
    metadata._google_index.cache_clear()           # the folder may have gained files since it was last indexed
    plan = Plan(folder=str(folder))
    known_sha = {r[0] for r in conn.execute("SELECT sha256 FROM assets WHERE missing = 0")}
    known_hash = [(r[0], r[1]) for r in conn.execute("SELECT id, phash FROM assets WHERE phash IS NOT NULL AND phash != -1 AND missing = 0")
                  if informative(r[1])]
    sha_to_id = {}
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.relative_to(folder).parts))
    seen_in_batch: dict[str, str] = {}
    for i, f in enumerate(files, 1):
        if cancelled():
            break
        if progress and i % 20 == 0:
            progress(i, len(files))
        kind = media_kind(f)
        if kind is None:
            if f.suffix.lower() != ".json":
                plan.skipped += 1
            continue
        try:
            sha = catalog.sha256_file(f)
        except OSError:
            plan.errors += 1
            continue
        rel = f.relative_to(folder).as_posix()   # same spelling on Windows and elsewhere
        if sha in known_sha:
            if sha not in sha_to_id:
                sha_to_id[sha] = conn.execute("SELECT id FROM assets WHERE sha256 = ? LIMIT 1", (sha,)).fetchone()[0]
            plan.exact.append({"path": rel, "match_id": sha_to_id[sha]})
            continue
        if sha in seen_in_batch:
            plan.exact.append({"path": rel, "match_id": None, "same_as": seen_in_batch[sha]})
            continue
        seen_in_batch[sha] = rel
        if kind == "photo" and known_hash:
            try:
                with Image.open(f) as im:
                    im.draft("RGB", (400, 400))
                    h = dhash(im)
                best = min(known_hash, key=lambda kh: hamming(kh[1], h)) if informative(h) else None
                bits = hamming(best[1], h) if best else 99
                if best and bits <= NEAR_BITS:
                    plan.near.append({"path": rel, "match_id": best[0], "bits": bits, "size": f.stat().st_size, "kind": kind})
                    continue
            except Exception:
                pass
        plan.new.append({"path": rel, "size": f.stat().st_size, "kind": kind})
        if google_sidecar_path(f) is not None:
            plan.sidecars += 1
    return plan


def _dest_for(originals: Path, src: Path) -> Path:
    meta = read_photo_meta(src) if media_kind(src) == "photo" else None
    when = None
    if meta and meta.taken_at:
        try:
            when = datetime.fromisoformat(meta.taken_at)
        except ValueError:
            when = None
    if when is None:
        when = datetime.fromtimestamp(src.stat().st_mtime)
    folder = originals / IMPORT_DIR / f"{when:%Y}" / f"{when:%Y-%m}"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / src.name
    n = 2
    while dest.exists():
        dest = folder / f"{src.stem}-{n}{src.suffix}"
        n += 1
    return dest


def run_import(conn: sqlite3.Connection, plan: Plan, library: Path, originals: Path, include_near: bool = False,
               progress: Callable[[int, int], None] | None = None,
               cancelled: Callable[[], bool] = lambda: False, contributor: str | None = None) -> dict:
    """Copy the planned files into the library (originals are left untouched), then catalogue them.

    Google Takeout sidecars travel with their photos (renamed to <file>.json), so dates, locations,
    favourites and descriptions are read when the files are catalogued. `contributor` names who
    the photos came from (a spouse's Google account, an old Android phone)."""
    folder = Path(plan.folder)
    from . import metadata
    metadata._google_index.cache_clear()
    todo = list(plan.new) + (list(plan.near) if include_near else [])
    copied = failed = 0
    placed: list[str] = []
    for i, item in enumerate(todo, 1):
        if cancelled():
            break
        src = folder.joinpath(*item["path"].split("/"))
        try:
            dest = _dest_for(originals, src)
            shutil.copy2(src, dest)          # keeps the file's dates
            side = google_sidecar_path(src)
            if side is not None:
                shutil.copy2(side, dest.with_name(dest.name + ".json"))
        except Exception:
            failed += 1
            continue
        copied += 1
        placed.append(dest.relative_to(originals).as_posix())
        if progress and i % 10 == 0:
            progress(i, len(todo))
    r = catalog.scan(conn, originals, cancelled=cancelled)
    if contributor and placed:
        catalog.attribute(conn, placed, contributor)
    fill_hashes(conn, library, originals, cancelled=cancelled)
    return {"copied": copied, "failed": failed, "catalogued_new": r.new, "total": r.total, "sidecars": plan.sidecars}
