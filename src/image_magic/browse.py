"""Queries behind the Photos, Trips and Map tabs."""

from __future__ import annotations

import sqlite3

# What the gallery shows: no Live Photo clips (they belong to their photo),
# no exact duplicates, nothing missing from disk.
VISIBLE = "missing = 0 AND duplicate_of IS NULL AND kind != 'live_video'"


def _filters(params: dict) -> tuple[str, list]:
    where, args = [VISIBLE], []
    if params.get("year"):
        where.append("substr(taken_at, 1, 4) = ?")
        args.append(str(params["year"]))
    if params.get("month"):
        where.append("substr(taken_at, 1, 7) = ?")
        args.append(str(params["month"])[:7])
    if params.get("day"):
        where.append("substr(taken_at, 1, 10) = ?")
        args.append(str(params["day"])[:10])
    if params.get("chapter"):
        where.append("taken_at >= (SELECT start_at FROM chapters WHERE id = ?) "
                     "AND taken_at < (SELECT date(end_at, '+1 day') FROM chapters WHERE id = ?)")
        args += [int(params["chapter"]), int(params["chapter"])]
    if params.get("country"):
        where.append("country_code = ?")
        args.append(params["country"])
    if params.get("city"):
        where.append("city = ?")
        args.append(params["city"])
    if params.get("trip"):
        where.append("trip_id = ?")
        args.append(int(params["trip"]))
    if params.get("person"):
        where.append("id IN (SELECT asset_id FROM faces WHERE person_id = ?)")
        args.append(int(params["person"]))
    if params.get("kind") in ("photo", "video"):
        where.append("kind = ?")
        args.append(params["kind"])
    return " AND ".join(where), args


def photos(conn: sqlite3.Connection, params: dict, offset: int = 0, limit: int = 200) -> dict:
    where, args = _filters(params)
    total = conn.execute(f"SELECT COUNT(*) FROM assets WHERE {where}", args).fetchone()[0]
    rows = conn.execute(
        f"""SELECT id, kind, taken_at, city, region, country, width, height, live_pair_id
            FROM assets WHERE {where}
            ORDER BY taken_at DESC, id DESC LIMIT ? OFFSET ?""",
        [*args, max(1, limit), max(0, offset)],
    ).fetchall()
    items = [dict(r) for r in rows]
    # Mark photos that have a Live Photo clip.
    ids = [r["id"] for r in items]
    if ids:
        live = {r[0] for r in conn.execute(
            f"SELECT live_pair_id FROM assets WHERE kind = 'live_video' AND missing = 0 "
            f"AND live_pair_id IN ({','.join('?' * len(ids))})", ids)}
        for it in items:
            it["live"] = it["id"] in live
            it.pop("live_pair_id", None)
    return {"total": total, "items": items}


def by_ids(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    """Rows for the given ids, in the given order (e.g. search ranking)."""
    if not ids:
        return []
    rows = conn.execute(
        f"""SELECT id, kind, taken_at, city, region, country, width, height FROM assets
            WHERE id IN ({','.join('?' * len(ids))})""", ids).fetchall()
    by = {r["id"]: dict(r) for r in rows}
    return [by[i] for i in ids if i in by]


def photo_detail(conn: sqlite3.Connection, asset_id: int) -> dict | None:
    row = conn.execute(
        """SELECT a.id, a.path, a.kind, a.taken_at, a.city, a.region, a.country, a.lat, a.lon,
                  a.camera_make, a.camera_model, a.width, a.height, a.size, a.trip_id,
                  t.title AS trip_title,
                  (SELECT v.id FROM assets v WHERE v.live_pair_id = a.id AND v.kind = 'live_video'
                     AND v.missing = 0 LIMIT 1) AS live_video_id
           FROM assets a LEFT JOIN trips t ON t.id = a.trip_id WHERE a.id = ?""",
        (asset_id,),
    ).fetchone()
    return dict(row) if row else None


def facets(conn: sqlite3.Connection) -> dict:
    years = conn.execute(
        f"""SELECT substr(taken_at, 1, 4) AS year, COUNT(*) AS n FROM assets
            WHERE {VISIBLE} AND taken_at IS NOT NULL GROUP BY year ORDER BY year DESC"""
    ).fetchall()
    countries = conn.execute(
        f"""SELECT country_code AS code, country AS name, COUNT(*) AS n FROM assets
            WHERE {VISIBLE} AND country_code IS NOT NULL
            GROUP BY country_code ORDER BY n DESC"""
    ).fetchall()
    cities = conn.execute(
        f"""SELECT country_code AS code, city AS name, COUNT(*) AS n FROM assets
            WHERE {VISIBLE} AND city IS NOT NULL
            GROUP BY country_code, city ORDER BY n DESC"""
    ).fetchall()
    by_country: dict[str, list] = {}
    for c in cities:
        by_country.setdefault(c["code"], []).append({"name": c["name"], "n": c["n"]})
    return {
        "years": [dict(y) for y in years],
        "countries": [{**dict(c), "cities": by_country.get(c["code"], [])[:50]} for c in countries],
    }


def trips(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, subtitle, start_at, end_at, photo_count, cover_id, lat, lon "
        "FROM trips ORDER BY start_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def map_points(conn: sqlite3.Connection, params: dict) -> list[list]:
    where, args = _filters(params)
    rows = conn.execute(
        f"SELECT id, lat, lon FROM assets WHERE {where} AND lat IS NOT NULL ORDER BY taken_at",
        args,
    ).fetchall()
    return [[r["id"], round(r["lat"], 5), round(r["lon"], 5)] for r in rows]
