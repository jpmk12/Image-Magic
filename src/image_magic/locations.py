"""Fill in missing locations and dates from every source we have, and say where each came from.

Order of trust for a photo's location:
  exif    the camera wrote GPS into the file
  icloud  iCloud's own record, from the .xmp sidecar icloudpd saves next to the file
          (iCloud often knows a location the file itself lacks: received, edited, some screenshots)
  video   the QuickTime/MP4 atoms of a video
  nearby  inferred: a located photo taken within NEARBY_MINUTES, by the same camera roll logic
          a person uses ("that was the same afternoon at the beach")
  none    nothing found; tried, so not re-read every time

Inferred locations are marked and shown as such; they count for places, trips,
events and the map, because leaving them blank hides the memory from every story.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import metadata
from .places import haversine_km

NEARBY_MINUTES = 20
NEARBY_MAX_KM = 5.0          # the two neighbours must agree this closely to interpolate between them

MIGRATIONS = {
    "location_source": "TEXT",                       # exif | icloud | video | nearby | none
    "favorite": "INTEGER NOT NULL DEFAULT 0",        # a heart in iCloud Photos (from the sidecar)
    "screenshot": "INTEGER NOT NULL DEFAULT 0",      # iCloud says this is a screenshot
    "title": "TEXT",                                 # caption you typed in Photos
}


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None).timestamp()
    except ValueError:
        return None


def read_sources(conn: sqlite3.Connection, originals: Path, progress: Callable[[int, int], None] | None = None,
                 cancelled=lambda: False) -> dict:
    """Sidecars and video atoms for every asset not yet examined. Returns counts by source."""
    rows = conn.execute(
        "SELECT id, path, kind, lat, taken_at FROM assets WHERE missing = 0 AND location_source IS NULL").fetchall()
    counts = {"exif": 0, "icloud": 0, "video": 0, "none": 0, "favorites": 0, "dates_fixed": 0}
    for i, r in enumerate(rows, 1):
        if cancelled():
            break
        if progress and i % 200 == 0:
            progress(i, len(rows))
        path = originals / r["path"]
        source = "exif" if r["lat"] is not None else None
        sets: dict = {}
        side = metadata.read_sidecar(path)
        if side:
            if source is None and side.lat is not None:
                sets.update(lat=side.lat, lon=side.lon, place_km=None)
                source = "icloud"
            if side.favorite:
                sets["favorite"] = 1
                counts["favorites"] += 1
            if side.screenshot:
                sets["screenshot"] = 1
            if side.title:
                sets["title"] = side.title[:200]
            if side.taken_at and r["kind"] != "photo":
                sets["taken_at"] = side.taken_at     # videos had only the file time until now
                counts["dates_fixed"] += 1
        if r["kind"] != "photo":
            v = metadata.read_video_meta(path)
            if source is None and v.lat is not None:
                sets.update(lat=v.lat, lon=v.lon, place_km=None)
                source = "video"
            if v.taken_at and "taken_at" not in sets:
                sets["taken_at"] = v.taken_at
                counts["dates_fixed"] += 1
            if v.width and not conn.execute("SELECT width FROM assets WHERE id = ?", (r["id"],)).fetchone()[0]:
                sets.update(width=v.width, height=v.height)
        sets["location_source"] = source or "none"
        counts[source or "none"] += 1
        conn.execute(f"UPDATE assets SET {', '.join(f'{k} = :{k}' for k in sets)} WHERE id = :id", {**sets, "id": r["id"]})
        if i % 200 == 0:
            conn.commit()
    conn.commit()
    return counts


def infer_nearby(conn: sqlite3.Connection) -> int:
    """Give unlocated photos the location of a neighbour taken minutes apart. Returns how many got one."""
    rows = conn.execute(
        """SELECT id, taken_at, lat, lon, location_source FROM assets
           WHERE missing = 0 AND kind != 'live_video' AND taken_at IS NOT NULL
             AND (lat IS NOT NULL OR location_source IN ('none', 'nearby')) ORDER BY taken_at""").fetchall()
    # Inferred positions are recomputed from scratch each time, from real ones only.
    real = [(t, r) for r in rows if r["lat"] is not None and r["location_source"] != "nearby" and (t := _ts(r["taken_at"])) is not None]
    todo = [(t, r) for r in rows if (r["lat"] is None or r["location_source"] == "nearby") and (t := _ts(r["taken_at"])) is not None]
    if not real or not todo:
        return 0
    import bisect
    times = [t for t, _ in real]
    window = NEARBY_MINUTES * 60
    n = 0
    for t, r in todo:
        k = bisect.bisect_left(times, t)
        before = real[k - 1] if k > 0 else None
        after = real[k] if k < len(real) else None
        if before and t - before[0] > window:
            before = None
        if after and after[0] - t > window:
            after = None
        lat = lon = None
        if before and after:
            b, a = before[1], after[1]
            if haversine_km(b["lat"], b["lon"], a["lat"], a["lon"]) <= NEARBY_MAX_KM:
                w = (t - before[0]) / max(1.0, after[0] - before[0])
                lat, lon = b["lat"] + (a["lat"] - b["lat"]) * w, b["lon"] + (a["lon"] - b["lon"]) * w
            else:   # the two sides disagree: take the closer one in time
                pick = b if t - before[0] <= after[0] - t else a
                lat, lon = pick["lat"], pick["lon"]
        elif before or after:
            pick = (before or after)[1]
            lat, lon = pick["lat"], pick["lon"]
        if lat is None:
            if r["location_source"] == "nearby":          # its neighbour went away
                conn.execute("UPDATE assets SET lat = NULL, lon = NULL, place_km = NULL, city = NULL, region = NULL, "
                             "country = NULL, country_code = NULL, location_source = 'none' WHERE id = ?", (r["id"],))
            continue
        if r["lat"] is not None and abs(r["lat"] - lat) < 1e-6 and abs(r["lon"] - lon) < 1e-6:
            n += 1
            continue
        conn.execute("UPDATE assets SET lat = ?, lon = ?, place_km = NULL, location_source = 'nearby' WHERE id = ?",
                     (round(lat, 6), round(lon, 6), r["id"]))
        n += 1
    conn.commit()
    return n


def refresh(conn: sqlite3.Connection, originals: Path, progress=None, cancelled=lambda: False) -> dict:
    counts = read_sources(conn, originals, progress, cancelled)
    counts["nearby"] = infer_nearby(conn)
    return counts


def summary(conn: sqlite3.Connection) -> dict:
    vis = "missing = 0 AND kind != 'live_video'"
    by = {r[0] or "pending": r[1] for r in conn.execute(
        f"SELECT CASE WHEN lat IS NULL THEN 'none' ELSE COALESCE(location_source, 'exif') END, COUNT(*) FROM assets WHERE {vis} GROUP BY 1")}
    total = sum(by.values())
    return {"total": total, "located": total - by.get("none", 0), "by_source": by,
            "favorites": conn.execute(f"SELECT COUNT(*) FROM assets WHERE {vis} AND favorite = 1").fetchone()[0],
            "no_sidecars": not any(True for _ in conn.execute("SELECT 1 FROM assets WHERE location_source = 'icloud' LIMIT 1"))}
