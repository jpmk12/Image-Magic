"""Offline place names for GPS coordinates, and automatic trips.

Place data: GeoNames cities with population > 1000 (CC BY 4.0, geonames.org),
bundled in data/cities.tsv.gz. No internet needed.
"""

from __future__ import annotations

import gzip
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from importlib import resources

CITY_MAX_KM = 30        # beyond this, don't claim a city name
COUNTRY_MAX_KM = 150    # beyond this (open sea, remote areas) leave the place empty
HOME_RADIUS_KM = 100    # photos within this distance of home are not "away"
TRIP_GAP = timedelta(days=3)       # a longer gap between away photos splits a trip
HOME_BREAK = timedelta(hours=36)   # being home this long ends a trip
MIN_TRIP_PHOTOS = 5


@dataclass(frozen=True)
class Place:
    city: str | None
    region: str | None
    country_code: str | None
    country: str | None
    km: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


@lru_cache(maxsize=1)
def countries() -> dict[str, str]:
    raw = (resources.files("image_magic") / "data" / "countries.json").read_text(encoding="utf-8")
    return json.loads(raw)


@lru_cache(maxsize=1)
def _index() -> dict[tuple[int, int], list[tuple[float, float, str, str, str]]]:
    """Places bucketed into 1-degree cells for fast nearest lookups."""
    grid: dict[tuple[int, int], list] = defaultdict(list)
    path = resources.files("image_magic") / "data" / "cities.tsv.gz"
    with path.open("rb") as raw, gzip.open(raw, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            lat, lon, name, region, cc = line.rstrip("\n").split("\t")
            la, lo = float(lat), float(lon)
            grid[(math.floor(la), math.floor(lo))].append((la, lo, name, region, cc))
    return dict(grid)


def lookup(lat: float, lon: float) -> Place:
    grid = _index()
    best, best_km = None, float("inf")
    cy, cx = math.floor(lat), math.floor(lon)
    # Search outward ring by ring; ~111 km per ring of latitude.
    for ring in range(0, 3):
        for dy in range(-ring, ring + 1):
            for dx in range(-ring, ring + 1):
                if max(abs(dy), abs(dx)) != ring:
                    continue
                gx = ((cx + dx + 180) % 360) - 180
                for p in grid.get((cy + dy, gx), ()):
                    d = haversine_km(lat, lon, p[0], p[1])
                    if d < best_km:
                        best, best_km = p, d
        if best is not None and best_km <= ring * 111:
            break
    if best is None or best_km > COUNTRY_MAX_KM:
        return Place(None, None, None, None, best_km if best else -1.0)
    _, _, name, region, cc = best
    return Place(
        city=name if best_km <= CITY_MAX_KM else None,
        region=region or None,
        country_code=cc,
        country=countries().get(cc, cc),
        km=round(best_km, 1),
    )


def update_places(conn: sqlite3.Connection) -> int:
    """Fill in place names for photos that have GPS but no place yet."""
    rows = conn.execute(
        "SELECT id, lat, lon FROM assets WHERE lat IS NOT NULL AND place_km IS NULL"
    ).fetchall()
    for row in rows:
        p = lookup(row["lat"], row["lon"])
        conn.execute(
            "UPDATE assets SET city = ?, region = ?, country_code = ?, country = ?, place_km = ? WHERE id = ?",
            (p.city, p.region, p.country_code, p.country, p.km, row["id"]),
        )
    conn.commit()
    return len(rows)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt.replace(tzinfo=None)


def _homes_by_year(photos: list) -> dict[int, tuple[float, float]]:
    """Home = the most photographed city (or ~10 km cell) in each year."""
    by_year: dict[int, Counter] = defaultdict(Counter)
    spot: dict[tuple, tuple[float, float]] = {}
    for p in photos:
        key = (p["country_code"], p["city"]) if p["city"] else (round(p["lat"], 1), round(p["lon"], 1))
        by_year[p["t"].year][key] += 1
        spot.setdefault(key, (p["lat"], p["lon"]))
    homes = {y: spot[c.most_common(1)[0][0]] for y, c in by_year.items()}
    # Years with few photos inherit the nearest busier year's home.
    overall = Counter()
    for c in by_year.values():
        overall.update(c)
    fallback = spot[overall.most_common(1)[0][0]] if overall else None
    for y, c in by_year.items():
        if sum(c.values()) < 20 and fallback:
            homes[y] = fallback
    return homes


def rebuild_trips(conn: sqlite3.Connection) -> int:
    """Group photos taken away from home into trips. Returns the number of trips."""
    rows = conn.execute(
        """SELECT id, taken_at, lat, lon, city, country_code, country FROM assets
           WHERE lat IS NOT NULL AND taken_at IS NOT NULL AND missing = 0
             AND duplicate_of IS NULL AND kind = 'photo'
           ORDER BY taken_at"""
    ).fetchall()
    photos = []
    for r in rows:
        try:
            photos.append({**dict(r), "t": _parse(r["taken_at"])})
        except ValueError:
            continue
    photos.sort(key=lambda p: p["t"])

    groups: list[list[dict]] = []
    if photos:
        homes = _homes_by_year(photos)
        current: list[dict] = []
        last_away: datetime | None = None
        home_since: datetime | None = None
        for p in photos:
            hlat, hlon = homes[p["t"].year]
            away = haversine_km(p["lat"], p["lon"], hlat, hlon) > HOME_RADIUS_KM
            if away:
                broke = (
                    last_away is None
                    or p["t"] - last_away > TRIP_GAP
                    or (home_since is not None and p["t"] - home_since > HOME_BREAK)
                )
                if broke and current:
                    groups.append(current)
                    current = []
                current.append(p)
                last_away, home_since = p["t"], None
            elif current and home_since is None:
                home_since = p["t"]
        if current:
            groups.append(current)
    groups = [g for g in groups if len(g) >= MIN_TRIP_PHOTOS]

    conn.execute("UPDATE assets SET trip_id = NULL")
    conn.execute("DELETE FROM trips")
    for g in groups:
        country_counts = Counter(p["country"] for p in g if p["country"])
        names = [c for c, _ in country_counts.most_common(3)]
        title = " & ".join(names[:2]) + (" +" if len(names) > 2 else "") if names else "Trip"
        cities = [c for c, _ in Counter(p["city"] for p in g if p["city"]).most_common(3)]
        start, end = g[0]["t"], g[-1]["t"]
        cover = g[len(g) // 2]
        cur = conn.execute(
            """INSERT INTO trips (title, subtitle, start_at, end_at, photo_count, cover_id, lat, lon)
               VALUES (?, ?, ?, ?, 0, ?, ?, ?)""",
            (title, ", ".join(cities), start.isoformat(), end.isoformat(),
             cover["id"], sum(p["lat"] for p in g) / len(g), sum(p["lon"] for p in g) / len(g)),
        )
        trip_id = cur.lastrowid
        # Every photo/video in the trip's time window joins the trip, including
        # ones without GPS (videos, photos from apps that strip location).
        conn.execute(
            """UPDATE assets SET trip_id = ?
               WHERE missing = 0 AND kind != 'live_video' AND taken_at IS NOT NULL
                 AND taken_at >= ? AND taken_at <= ?
                 AND (lat IS NULL OR id IN (%s))""" % ",".join(str(p["id"]) for p in g),
            (trip_id, start.isoformat(), (end + timedelta(seconds=1)).isoformat()),
        )
        n = conn.execute("SELECT COUNT(*) FROM assets WHERE trip_id = ? AND duplicate_of IS NULL",
                         (trip_id,)).fetchone()[0]
        conn.execute("UPDATE trips SET photo_count = ? WHERE id = ?", (n, trip_id))
    conn.commit()
    return len(groups)


def refresh(conn: sqlite3.Connection) -> tuple[int, int]:
    """Look up new places and rebuild trips. Returns (places_added, trips)."""
    return update_places(conn), rebuild_trips(conn)
