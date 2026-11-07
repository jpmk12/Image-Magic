"""Place stories: a page per country and per city, and a world summary.

Every visit (trips there, and the years you lived there), the years, the
best photos, who was with you, and the farthest and most-returned-to places.
Built locally from the catalog.
"""

from __future__ import annotations

import sqlite3

from . import browse, quality
from .places import haversine_km

VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0"


def _people(conn: sqlite3.Connection, where: str, args: list, n: int = 5) -> list[str]:
    try:
        return [r[0] for r in conn.execute(
            f"""SELECT p.name FROM faces f JOIN persons p ON p.id = f.person_id JOIN assets a ON a.id = f.asset_id
                WHERE p.name IS NOT NULL AND p.hidden = 0 AND {where} GROUP BY p.id ORDER BY COUNT(DISTINCT a.id) DESC LIMIT ?""", [*args, n])]
    except sqlite3.OperationalError:
        return []


def _home(conn: sqlite3.Connection) -> tuple[float, float] | None:
    r = conn.execute("SELECT lat, lon FROM chapters WHERE lat IS NOT NULL ORDER BY end_at DESC LIMIT 1").fetchone()
    return (r["lat"], r["lon"]) if r else None


def world(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        f"""SELECT a.country_code AS code, a.country AS name, COUNT(*) AS photos, MIN(a.taken_at) AS first, MAX(a.taken_at) AS last,
                   COUNT(DISTINCT substr(a.taken_at, 1, 4)) AS years, COUNT(DISTINCT a.city) AS cities,
                   COUNT(DISTINCT a.trip_id) AS trips, AVG(a.lat) AS lat, AVG(a.lon) AS lon
            FROM assets a WHERE {VISIBLE} AND a.country_code IS NOT NULL GROUP BY a.country_code ORDER BY photos DESC""").fetchall()
    home = _home(conn)
    countries = []
    for r in rows:
        d = dict(r)
        d["first"], d["last"] = (r["first"] or "")[:10], (r["last"] or "")[:10]
        d["year_list"] = [x[0] for x in conn.execute(
            f"SELECT DISTINCT substr(a.taken_at, 1, 4) FROM assets a WHERE {VISIBLE} AND a.country_code = ? ORDER BY 1", (r["code"],))]
        d["home_km"] = round(haversine_km(home[0], home[1], r["lat"], r["lon"])) if home and r["lat"] is not None else None
        countries.append(d)
    by_year = {}
    for y, code, name in conn.execute(
            f"SELECT DISTINCT substr(a.taken_at, 1, 4), a.country_code, a.country FROM assets a WHERE {VISIBLE} AND a.country_code IS NOT NULL ORDER BY 1"):
        by_year.setdefault(y, []).append({"code": code, "name": name})
    far = None
    if home:
        r = conn.execute(f"SELECT a.id, a.city, a.country, a.lat, a.lon, a.taken_at FROM assets a WHERE {VISIBLE} AND a.lat IS NOT NULL").fetchall()
        if r:
            f = max(r, key=lambda x: haversine_km(home[0], home[1], x["lat"], x["lon"]))
            far = {"id": f["id"], "place": ", ".join(x for x in [f["city"], f["country"]] if x), "km": round(haversine_km(home[0], home[1], f["lat"], f["lon"])),
                   "date": (f["taken_at"] or "")[:10]}
    returned = conn.execute(
        f"""SELECT a.city, a.country, a.country_code, COUNT(DISTINCT substr(a.taken_at, 1, 4)) AS years, COUNT(*) AS photos
            FROM assets a WHERE {VISIBLE} AND a.city IS NOT NULL AND a.trip_id IS NOT NULL GROUP BY a.country_code, a.city
            ORDER BY years DESC, photos DESC LIMIT 5""").fetchall()
    return {"countries": countries, "by_year": by_year, "farthest": far,
            "returned_to": [dict(r) for r in returned], "total_countries": len(countries),
            "total_cities": conn.execute(f"SELECT COUNT(DISTINCT a.country_code || a.city) FROM assets a WHERE {VISIBLE} AND a.city IS NOT NULL").fetchone()[0]}


def place(conn: sqlite3.Connection, code: str, city: str | None = None) -> dict:
    where, args = f"{VISIBLE} AND a.country_code = ?", [code]
    if city:
        where += " AND a.city = ?"
        args.append(city)
    head = conn.execute(f"SELECT a.country, COUNT(*) AS photos, MIN(a.taken_at) AS first, MAX(a.taken_at) AS last FROM assets a WHERE {where}", args).fetchone()
    if not head or not head["photos"]:
        raise ValueError("No photos from that place")
    years = [r[0] for r in conn.execute(f"SELECT DISTINCT substr(a.taken_at, 1, 4) FROM assets a WHERE {where} ORDER BY 1", args)]
    cities = [dict(r) for r in conn.execute(
        f"SELECT a.city AS name, COUNT(*) AS n FROM assets a WHERE {where} AND a.city IS NOT NULL GROUP BY a.city ORDER BY n DESC LIMIT 20", args)]
    trips = [dict(r) for r in conn.execute(
        f"""SELECT t.id, t.title, t.start_at, t.end_at, t.photo_count, t.cover_id, COUNT(*) AS here FROM trips t
            JOIN assets a ON a.trip_id = t.id WHERE {where} GROUP BY t.id ORDER BY t.start_at DESC""", args)]
    lived = [dict(r) for r in conn.execute(
        f"""SELECT c.id, c.title, c.start_at, c.end_at FROM chapters c WHERE c.place_key LIKE ? ORDER BY c.start_at""",
        (f"{code}|{city}" if city else f"{code}|%",))]
    best = browse.by_ids(conn, quality.best_of(conn, where, args, 12))
    pts = conn.execute(f"SELECT a.lat, a.lon FROM assets a WHERE {where} AND a.lat IS NOT NULL", args).fetchall()
    centre = [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)] if pts else None
    return {"code": code, "city": city, "country": head["country"], "photos": head["photos"], "first": head["first"][:10], "last": head["last"][:10],
            "years": years, "cities": cities, "trips": trips, "lived": lived, "people": _people(conn, where, args), "best": best, "centre": centre}
