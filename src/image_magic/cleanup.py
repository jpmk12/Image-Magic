"""Clean up the library: find blurry, dark, accidental and duplicate shots, and a safe trash.

Nothing here touches iCloud. "Trash" moves the laptop's copy (with its Live Photo
clip and .xmp sidecar) into <library>/trash/ keeping the folder path, so Restore
puts it back exactly. Empty trash deletes those files from the laptop for good;
the photo still exists in iCloud. Because iCloud still has it, a later sync would
download it again: the app remembers what you trashed and trashes it again on the
next catalog update, so your decision sticks. To stop the re-download for good,
delete the photo in iCloud Photos yourself.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from . import media, quality

BLUR_SHARPNESS = 0.08    # sharpness component (Laplacian): below this nothing is in focus
DARK_EXPOSURE = 0.25     # exposure component: nearly black or blown out
FLAT_CONTRAST = 0.15     # contrast component: a blank frame (the inside of a pocket, a wall)
PREFILTER = 50           # a photo with no sharpness scores at most 45, so look only below this
LIMIT_PER_GROUP = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS trash_files (
    id         INTEGER PRIMARY KEY,
    asset_id   INTEGER NOT NULL,
    rel_path   TEXT NOT NULL,          -- under originals/
    trash_path TEXT NOT NULL,          -- under trash/
    kind       TEXT NOT NULL,          -- file | live | xmp
    created    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trash_asset ON trash_files(asset_id);
"""
MIGRATIONS = {"trashed_at": "TEXT"}
VISIBLE = "missing = 0 AND kind = 'photo' AND private = 0 AND trashed_at IS NULL"


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


def _item(r, reason: str, score=None) -> dict:
    return {"id": r["id"], "taken_at": r["taken_at"], "size": r["size"], "reason": reason,
            "score": round(score, 1) if score is not None else r["quality_score"]}


def candidates(conn: sqlite3.Connection, library: Path, originals: Path) -> dict:
    """Groups of photos worth a look. Low scorers are re-read from the thumbnail to tell blur from darkness."""
    ensure_schema(conn)
    groups: dict[str, list] = {"blurry": [], "dark": [], "screenshots": [], "duplicates": [], "bursts": []}
    shots = conn.execute(
        f"""SELECT a.id, a.taken_at, a.size, a.quality_score FROM assets a WHERE {VISIBLE.replace(' AND ', ' AND a.').replace('missing', 'a.missing')}
            AND a.duplicate_of IS NULL AND (a.screenshot = 1 OR EXISTS (SELECT 1 FROM descriptions d WHERE d.asset_id = a.id
            AND d.status = 'ok' AND d.setting IN ('screenshot', 'document'))) ORDER BY a.taken_at DESC LIMIT ?""", (LIMIT_PER_GROUP,)).fetchall()
    groups["screenshots"] = [_item(r, "a screenshot or document, not a photo") for r in shots]
    shot_ids = {i["id"] for i in groups["screenshots"]}
    low = conn.execute(
        f"""SELECT id, path, sha256, taken_at, size, quality_score FROM assets WHERE {VISIBLE} AND duplicate_of IS NULL
            AND quality_score IS NOT NULL AND quality_score < ? ORDER BY quality_score LIMIT ?""", (PREFILTER, 3000)).fetchall()
    for r in low:
        if r["id"] in shot_ids:
            continue
        try:
            with Image.open(media.thumbnail(library, originals, r["path"], r["sha256"])) as im:
                s = quality.score_image(im)
        except Exception:
            continue
        if s["exposure"] < DARK_EXPOSURE or s["contrast"] < FLAT_CONTRAST:
            groups["dark"].append(_item(r, "almost black, blown out or blank: a pocket or accidental shot", s["score"]))
        elif s["sharpness"] < BLUR_SHARPNESS:
            groups["blurry"].append(_item(r, "nothing in focus", s["score"]))
    dups = conn.execute(
        f"""SELECT id, taken_at, size, quality_score, duplicate_of FROM assets WHERE missing = 0 AND trashed_at IS NULL
            AND duplicate_of IS NOT NULL ORDER BY taken_at DESC LIMIT ?""", (LIMIT_PER_GROUP,)).fetchall()
    groups["duplicates"] = [{**_item(r, "byte-for-byte copy of another photo"), "of": r["duplicate_of"]} for r in dups]
    extras = conn.execute(
        f"""SELECT id, taken_at, size, quality_score, burst_id FROM assets WHERE {VISIBLE} AND duplicate_of IS NULL
            AND burst_id IS NOT NULL AND burst_best = 0 ORDER BY taken_at DESC LIMIT ?""", (LIMIT_PER_GROUP,)).fetchall()
    groups["bursts"] = [{**_item(r, "an extra frame of a burst; the keeper stays"), "burst": r["burst_id"]} for r in extras]
    total_bytes = sum(i["size"] or 0 for g in groups.values() for i in g)
    return {"groups": groups, "counts": {k: len(v) for k, v in groups.items()}, "bytes": total_bytes,
            "trash": trash_summary(conn)}


def _files_for(conn: sqlite3.Connection, originals: Path, aid: int) -> list[tuple[str, Path, str]]:
    """(kind, path, rel) for the asset's file, its Live Photo clip and its sidecar."""
    r = conn.execute("SELECT path FROM assets WHERE id = ?", (aid,)).fetchone()
    if not r:
        return []
    out = [("file", originals / r["path"], r["path"])]
    for v in conn.execute("SELECT id, path FROM assets WHERE live_pair_id = ? AND kind = 'live_video' AND missing = 0", (aid,)):
        out.append(("live", originals / v["path"], v["path"]))
    side = originals / (r["path"] + ".xmp")
    if side.is_file():
        out.append(("xmp", side, r["path"] + ".xmp"))
    return out


def trash(conn: sqlite3.Connection, library: Path, originals: Path, ids: list[int]) -> dict:
    """Move the laptop copies of these photos into <library>/trash/. iCloud is untouched."""
    ensure_schema(conn)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    moved, failed = 0, []
    for aid in ids:
        files = _files_for(conn, originals, int(aid))
        if not files:
            continue
        for kind, src, rel in files:
            dest = library / "trash" / rel
            try:
                if src.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dest))
                conn.execute("INSERT INTO trash_files (asset_id, rel_path, trash_path, kind, created) VALUES (?, ?, ?, ?, ?)",
                             (int(aid), rel, rel, kind, stamp))
            except OSError as e:
                failed.append(f"{rel}: {e.strerror or e}")
        conn.execute("UPDATE assets SET missing = 1, trashed_at = ? WHERE id = ? OR (live_pair_id = ? AND kind = 'live_video')",
                     (stamp, int(aid), int(aid)))
        moved += 1
    conn.commit()
    return {"trashed": moved, "failed": failed}


def restore(conn: sqlite3.Connection, library: Path, originals: Path, ids: list[int] | None = None) -> int:
    """Put trashed photos back where they were (all of them when ids is None)."""
    ensure_schema(conn)
    where, args = ("WHERE asset_id IN (%s)" % ",".join("?" * len(ids)), list(ids)) if ids else ("", [])
    rows = conn.execute(f"SELECT id, asset_id, rel_path, trash_path FROM trash_files {where}", args).fetchall()
    n = 0
    done: set[int] = set()
    for r in rows:
        src, dest = library / "trash" / r["trash_path"], originals / r["rel_path"]
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        conn.execute("DELETE FROM trash_files WHERE id = ?", (r["id"],))
        done.add(r["asset_id"])
    for aid in done:
        conn.execute("UPDATE assets SET missing = 0, trashed_at = NULL WHERE id = ? OR (live_pair_id = ? AND kind = 'live_video')", (aid, aid))
        n += 1
    conn.commit()
    return n


def empty(conn: sqlite3.Connection, library: Path) -> dict:
    """Delete the trashed files from the laptop for good. The catalog remembers them so a re-download is trashed again."""
    ensure_schema(conn)
    rows = conn.execute("SELECT id, trash_path FROM trash_files").fetchall()
    freed, n = 0, 0
    for r in rows:
        p = library / "trash" / r["trash_path"]
        try:
            if p.exists():
                freed += p.stat().st_size
                p.unlink()
            n += 1
        except OSError:
            continue
        conn.execute("DELETE FROM trash_files WHERE id = ?", (r["id"],))
    conn.commit()
    # tidy empty folders
    root = library / "trash"
    if root.exists():
        for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
            try:
                d.rmdir()
            except OSError:
                pass
    return {"deleted": n, "bytes": freed}


def trash_summary(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    r = conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM assets WHERE trashed_at IS NOT NULL AND kind != 'live_video'").fetchone()
    files = conn.execute("SELECT COUNT(*) FROM trash_files").fetchone()[0]
    return {"photos": r[0], "bytes": r[1], "restorable": files > 0}


def retrash_redownloads(conn: sqlite3.Connection, library: Path, originals: Path) -> int:
    """After a sync, files iCloud re-sent at a trashed path go straight back to the trash."""
    ensure_schema(conn)
    rows = conn.execute("SELECT id, path FROM assets WHERE trashed_at IS NOT NULL AND kind != 'live_video'").fetchall()
    n = 0
    for r in rows:
        p = originals / r["path"]
        if p.is_file():
            trash(conn, library, originals, [r["id"]])
            n += 1
    return n
