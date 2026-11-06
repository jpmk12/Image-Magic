"""Year in review, plus context for a memory (sunrise/sunset, weather)."""

from __future__ import annotations

import json
import math
import sqlite3
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from . import browse
from .places import haversine_km

VISIBLE = "missing = 0 AND duplicate_of IS NULL AND kind != 'live_video' AND private = 0"
WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
       80: "rain showers", 81: "rain showers", 82: "violent showers", 85: "snow showers", 86: "snow showers",
       95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"

SCHEMA = """
CREATE TABLE IF NOT EXISTS weather (
    key   TEXT PRIMARY KEY,     -- lat2,lon2,date
    data  TEXT NOT NULL
);
"""


def years(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        f"SELECT DISTINCT substr(taken_at, 1, 4) FROM assets WHERE {VISIBLE} AND taken_at IS NOT NULL ORDER BY 1 DESC")]


def year_stats(conn: sqlite3.Connection, year: str) -> dict:
    y = str(year)
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    base = f"{VISIBLE} AND substr(taken_at, 1, 4) = ?"
    photos = q(f"SELECT COUNT(*) FROM assets WHERE {base} AND kind = 'photo'", y)
    videos = q(f"SELECT COUNT(*) FROM assets WHERE {base} AND kind = 'video'", y)
    countries = [dict(r) for r in conn.execute(
        f"SELECT country, country_code AS code, COUNT(*) AS n FROM assets WHERE {base} AND country IS NOT NULL "
        "GROUP BY country ORDER BY n DESC", (y,))]
    before = {r[0] for r in conn.execute(
        f"SELECT DISTINCT country_code FROM assets WHERE {VISIBLE} AND substr(taken_at, 1, 4) < ? AND country_code IS NOT NULL", (y,))}
    new_countries = [c["country"] for c in countries if c["code"] not in before]
    cities = q(f"SELECT COUNT(DISTINCT city) FROM assets WHERE {base} AND city IS NOT NULL", y)
    # Distance: hops between consecutive located photos (a flight counts as one hop).
    pts = conn.execute(f"SELECT lat, lon FROM assets WHERE {base} AND lat IS NOT NULL ORDER BY taken_at", (y,)).fetchall()
    km = 0.0
    for a, b in zip(pts, pts[1:]):
        d = haversine_km(a[0], a[1], b[0], b[1])
        if d >= 1:
            km += d
    trips = [dict(r) for r in conn.execute(
        "SELECT id, title, subtitle, start_at, end_at, photo_count, cover_id FROM trips WHERE substr(start_at, 1, 4) = ? "
        "ORDER BY start_at", (y,))]
    for t in trips:
        t["days"] = (date.fromisoformat(t["end_at"][:10]) - date.fromisoformat(t["start_at"][:10])).days + 1
    people = []
    try:
        people = [dict(r) for r in conn.execute(
            f"""SELECT p.id, p.name, COUNT(DISTINCT a.id) AS n FROM faces f JOIN persons p ON p.id = f.person_id
                JOIN assets a ON a.id = f.asset_id WHERE p.name IS NOT NULL AND p.hidden = 0
                AND a.{base.replace(' AND ', ' AND a.')} GROUP BY p.id ORDER BY n DESC LIMIT 5""", (y,))]
        first_seen = {r[0] for r in conn.execute(
            f"""SELECT p.id FROM persons p WHERE p.name IS NOT NULL AND (SELECT MIN(substr(a.taken_at, 1, 4))
                FROM faces f JOIN assets a ON a.id = f.asset_id WHERE f.person_id = p.id AND a.missing = 0) = ?""", (y,))}
        for p in people:
            p["new"] = p["id"] in first_seen
    except sqlite3.OperationalError:
        pass
    busiest = conn.execute(
        f"SELECT substr(taken_at, 1, 10) AS day, COUNT(*) AS n FROM assets WHERE {base} GROUP BY day ORDER BY n DESC LIMIT 1", (y,)).fetchone()
    months = [0] * 12
    for m, n in conn.execute(f"SELECT substr(taken_at, 6, 2), COUNT(*) FROM assets WHERE {base} GROUP BY 1", (y,)):
        months[int(m) - 1] = n
    best = best_of_year(conn, y, 12)
    return {
        "year": y, "photos": photos, "videos": videos, "countries": countries, "new_countries": new_countries,
        "cities": cities, "km": round(km), "trips": trips,
        "longest_trip": max(trips, key=lambda t: t["days"]) if trips else None,
        "people": people, "busiest_day": dict(busiest) if busiest else None, "months": months, "best": best,
    }


def best_of_year(conn: sqlite3.Connection, year: str, n: int = 30) -> list[dict]:
    """About n photos spread across the year: the best-rated one per slot."""
    rows = conn.execute(
        f"""SELECT a.id, a.taken_at, COALESCE(d.quality, 0) AS quality,
                   (SELECT COUNT(*) FROM faces f WHERE f.asset_id = a.id AND f.person_id IS NOT NULL) AS faces,
                   (a.trip_id IS NOT NULL) AS on_trip
            FROM assets a LEFT JOIN descriptions d ON d.asset_id = a.id
            WHERE a.{VISIBLE.replace(' AND ', ' AND a.')} AND a.kind = 'photo' AND substr(a.taken_at, 1, 4) = ?
            ORDER BY a.taken_at""", (str(year),)).fetchall() if _has(conn, "descriptions") and _has(conn, "faces") else \
        conn.execute(f"SELECT id, taken_at, 0 AS quality, 0 AS faces, (trip_id IS NOT NULL) AS on_trip FROM assets "
                     f"WHERE {VISIBLE} AND kind = 'photo' AND substr(taken_at, 1, 4) = ? ORDER BY taken_at", (str(year),)).fetchall()
    if not rows:
        return []
    slots = max(1, min(n, len(rows)))
    per = len(rows) / slots
    picked = []
    for s in range(slots):
        chunk = rows[int(s * per): int((s + 1) * per)] or rows[-1:]
        best = max(chunk, key=lambda r: (r["quality"], r["faces"], r["on_trip"], r["taken_at"]))
        if not picked or picked[-1] != best["id"]:
            picked.append(best["id"])
    return browse.by_ids(conn, picked)


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone())


# ---- context for a memory --------------------------------------------------------

def sun_times(lat: float, lon: float, when: date, tz_hours: float | None = None) -> dict:
    """Sunrise/sunset (local clock, HH:MM) by the NOAA approximation. Good to a few minutes."""
    if tz_hours is None:
        tz_hours = round(lon / 15)
    n = when.timetuple().tm_yday
    lng_hour = lon / 15
    out = {}
    for name, t0 in (("sunrise", 6), ("sunset", 18)):
        t = n + ((t0 - lng_hour) / 24)
        M = (0.9856 * t) - 3.289
        L = (M + (1.916 * math.sin(math.radians(M))) + (0.020 * math.sin(math.radians(2 * M))) + 282.634) % 360
        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360
        RA = (RA + (math.floor(L / 90) * 90) - (math.floor(RA / 90) * 90)) / 15
        sin_dec = 0.39782 * math.sin(math.radians(L))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (math.cos(math.radians(90.833)) - (sin_dec * math.sin(math.radians(lat)))) / (cos_dec * math.cos(math.radians(lat)))
        if cos_h > 1 or cos_h < -1:
            out[name] = None   # polar day/night
            continue
        H = (360 - math.degrees(math.acos(cos_h))) / 15 if name == "sunrise" else math.degrees(math.acos(cos_h)) / 15
        T = H + RA - (0.06571 * t) - 6.622
        UT = (T - lng_hour) % 24
        local = (UT + tz_hours) % 24
        out[name] = f"{int(local):02d}:{int(round((local % 1) * 60)) % 60:02d}"
    return out


def _tz_hours(taken_at: str, lon: float) -> float:
    try:
        dt = datetime.fromisoformat(taken_at)
        if dt.tzinfo:
            return dt.utcoffset().total_seconds() / 3600
    except ValueError:
        pass
    return round(lon / 15)


def fetch_weather(lat: float, lon: float, day: str) -> dict | None:   # pragma: no cover - network
    qs = urllib.parse.urlencode({"latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}", "start_date": day, "end_date": day,
                                 "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_sum",
                                 "timezone": "auto"})
    req = urllib.request.Request(f"{WEATHER_URL}?{qs}", headers={"User-Agent": "ImageMagic"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def weather_for(conn: sqlite3.Connection, lat: float, lon: float, day: str, fetch=fetch_weather) -> dict | None:
    """Daily weather from Open-Meteo (free, no key), cached per ~1 km cell and day."""
    conn.executescript(SCHEMA)
    key = f"{lat:.2f},{lon:.2f},{day}"
    row = conn.execute("SELECT data FROM weather WHERE key = ?", (key,)).fetchone()
    if row:
        return json.loads(row[0]) or None
    try:
        raw = fetch(lat, lon, day)
        d = raw.get("daily", {}) if raw else {}
        code = (d.get("weather_code") or [None])[0]
        tmax, tmin = (d.get("temperature_2m_max") or [None])[0], (d.get("temperature_2m_min") or [None])[0]
        data = None
        if tmax is not None:
            data = {"tmax_c": tmax, "tmin_c": tmin, "code": code, "text": WMO.get(code, ""),
                    "precip_mm": (d.get("precipitation_sum") or [None])[0]}
    except Exception:
        return None
    conn.execute("INSERT OR REPLACE INTO weather (key, data) VALUES (?, ?)", (key, json.dumps(data)))
    conn.commit()
    return data


def context(conn: sqlite3.Connection, asset_id: int, weather_online: bool, fetch=fetch_weather) -> dict:
    r = conn.execute("SELECT taken_at, lat, lon FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not r or not r["taken_at"] or r["lat"] is None:
        return {}
    day = r["taken_at"][:10]
    out = {"sun": sun_times(r["lat"], r["lon"], date.fromisoformat(day), _tz_hours(r["taken_at"], r["lon"]))}
    if weather_online and date.fromisoformat(day) < date.today() - timedelta(days=5):
        out["weather"] = weather_for(conn, r["lat"], r["lon"], day, fetch)
    return out
