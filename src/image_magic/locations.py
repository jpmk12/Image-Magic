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
        "SELECT id, path, kind, lat, taken_at, width FROM assets WHERE missing = 0 AND location_source IS NULL").fetchall()
    counts = {"exif": 0, "icloud": 0, "google": 0, "video": 0, "none": 0, "favorites": 0, "dates_fixed": 0}
    for i, r in enumerate(rows, 1):
        if cancelled():
            break
        if progress and i % 200 == 0:
            progress(i, len(rows))
        path = originals / r["path"]
        source = "exif" if r["lat"] is not None else None
        sets: dict = {}
        side = metadata.read_sidecar(path)
        side_name = "icloud"
        if side is None:
            side = metadata.read_google_sidecar(path)       # a Google Photos Takeout import
            side_name = "google"
            if side and side.taken_at and r["kind"] == "photo" and not metadata.read_photo_meta(path).taken_at:
                sets["taken_at"] = side.taken_at             # a photo Google had the date for but the file did not
                counts["dates_fixed"] += 1
        if side:
            if source is None and side.lat is not None:
                sets.update(lat=side.lat, lon=side.lon, place_km=None)
                source = side_name
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
            if v.width and not r["width"]:
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
    # (a 'manual' location counts as real: it is never recomputed, and it anchors its neighbours)
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


def set_manual(conn: sqlite3.Connection, ids: list[int], lat: float, lon: float, overwrite: bool = False) -> int:
    """Give these photos a place you chose. Kept through every rebuild; inference never touches it."""
    from . import places
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("That is not a valid location")
    n = 0
    for i in ids:
        cond = "" if overwrite else " AND (lat IS NULL OR location_source IN ('nearby', 'none'))"
        cur = conn.execute(f"UPDATE assets SET lat = ?, lon = ?, place_km = NULL, city = NULL, region = NULL, country = NULL, "
                           f"country_code = NULL, location_source = 'manual' WHERE id = ?{cond}", (lat, lon, int(i)))
        n += cur.rowcount
    conn.commit()
    places.update_places(conn)
    return n


def clear_manual(conn: sqlite3.Connection, ids: list[int]) -> int:
    """Undo a manual location: back to 'none' so inference may fill it again."""
    n = 0
    for i in ids:
        cur = conn.execute("UPDATE assets SET lat = NULL, lon = NULL, place_km = NULL, city = NULL, region = NULL, country = NULL, "
                           "country_code = NULL, location_source = 'none' WHERE id = ? AND location_source = 'manual'", (int(i),))
        n += cur.rowcount
    conn.commit()
    infer_nearby(conn)
    from . import places
    places.update_places(conn)
    return n


def nearby_suggestions(conn: sqlite3.Connection, ids: list[int], window_minutes: int = NEARBY_MINUTES) -> list[dict]:
    """For each photo: the location its time-neighbours suggest (within the window), with a place name."""
    from . import places
    real = conn.execute(
        """SELECT taken_at, lat, lon FROM assets WHERE missing = 0 AND lat IS NOT NULL AND taken_at IS NOT NULL
           AND location_source IN ('exif', 'icloud', 'video', 'manual') ORDER BY taken_at""").fetchall()
    times = [_ts(r["taken_at"]) for r in real]
    import bisect
    out = []
    window = window_minutes * 60
    wanted = [int(i) for i in ids]
    rows_by_id: dict[int, sqlite3.Row] = {}
    for k in range(0, len(wanted), 500):
        chunk = wanted[k:k + 500]
        for r in conn.execute(f"SELECT id, taken_at, lat, location_source FROM assets WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            rows_by_id[r["id"]] = r
    place_cache: dict[tuple[float, float], str] = {}
    for i in wanted:
        r = rows_by_id.get(i)
        if not r or not r["taken_at"]:
            continue
        t = _ts(r["taken_at"])
        if t is None:
            continue
        k = bisect.bisect_left(times, t)
        cands = [(abs(times[j] - t), real[j]) for j in (k - 1, k) if 0 <= j < len(real) and times[j] is not None and abs(times[j] - t) <= window]
        if not cands:
            out.append({"id": r["id"], "lat": None, "lon": None, "place": "", "minutes": None, "has": r["lat"] is not None})
            continue
        gap, n = min(cands, key=lambda c: c[0])
        pk = (round(n["lat"], 3), round(n["lon"], 3))
        if pk not in place_cache:
            p = places.lookup(n["lat"], n["lon"])
            place_cache[pk] = ", ".join(x for x in [p.city, p.country] if x)
        out.append({"id": r["id"], "lat": n["lat"], "lon": n["lon"], "minutes": round(gap / 60), "has": r["lat"] is not None,
                    "place": place_cache[pk]})
    return out


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
