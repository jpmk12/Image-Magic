"""The home story: most of life happens at home, not on trips.

For a chapter (a period with one home) this gives "a year at home" by season,
the spots photographed again and again through the years (the backyard, the
kitchen table, the front door), and who was there most. Everything is built
locally from the catalog; the best photos come from the quality ranking.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from . import browse, quality

VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0"
NEAR_LAT = 0.05          # ~5.5 km box around the chapter's home counts as "at home"
NEAR_LON = 0.07
CELL = 0.0005            # ~55 m: the same spot
SPOT_MIN_YEARS = 3
SEASONS = [("winter", (12, 1, 2)), ("spring", (3, 4, 5)), ("summer", (6, 7, 8)), ("autumn", (9, 10, 11))]


def home_where(conn: sqlite3.Connection, chapter_id: int) -> tuple[str, list]:
    """WHERE fragment (alias a.) for photos taken at this chapter's home, during the chapter."""
    ch = conn.execute("SELECT start_at, end_at, lat, lon FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
    if not ch:
        raise ValueError("Unknown chapter")
    where = (f"{VISIBLE} AND a.trip_id IS NULL AND a.taken_at >= ? AND a.taken_at < date(?, '+1 day')"
             " AND a.lat IS NOT NULL AND abs(a.lat - ?) < ? AND abs(a.lon - ?) < ?")
    return where, [ch["start_at"], ch["end_at"], ch["lat"], NEAR_LAT, ch["lon"], NEAR_LON]


def _season(month: int) -> str:
    for name, months in SEASONS:
        if month in months:
            return name
    return "winter"


def story(conn: sqlite3.Connection, chapter_id: int, per_season: int = 6) -> dict:
    ch = conn.execute("SELECT id, title, start_at, end_at, lat, lon FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
    if not ch:
        raise ValueError("Unknown chapter")
    where, args = home_where(conn, chapter_id)
    rows = conn.execute(f"SELECT a.id, a.taken_at, a.lat, a.lon FROM assets a WHERE {where} ORDER BY a.taken_at", args).fetchall()
    # seasons per year
    by_year: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        y, m = r["taken_at"][:4], int(r["taken_at"][5:7])
        key = y if m != 12 else str(int(y) + 1)         # December belongs to the coming winter
        by_year[key][_season(m)] += 1
    years = []
    for y in sorted(by_year):
        seasons = []
        for name, months in SEASONS:
            n = by_year[y].get(name, 0)
            if not n:
                continue
            if name == "winter":
                cond = (f"((substr(a.taken_at, 1, 4) = ? AND substr(a.taken_at, 6, 2) IN ('01','02')) "
                        f"OR (substr(a.taken_at, 1, 4) = ? AND substr(a.taken_at, 6, 2) = '12'))")
                sargs = [y, str(int(y) - 1)]
            else:
                cond = f"substr(a.taken_at, 1, 4) = ? AND substr(a.taken_at, 6, 2) IN ({','.join('?' * len(months))})"
                sargs = [y, *[f"{m:02d}" for m in months]]
            ids = quality.best_of(conn, f"{where} AND {cond}", [*args, *sargs], per_season)
            seasons.append({"name": name, "count": n, "best": browse.by_ids(conn, ids)})
        years.append({"year": y, "count": sum(by_year[y].values()), "seasons": seasons})
    # the same spot through the years
    cells: dict[tuple[int, int], dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        cells[(round(r["lat"] / CELL), round(r["lon"] / CELL))][r["taken_at"][:4]].append(r["id"])
    spots = []
    for cell, per_year in cells.items():
        if len(per_year) < SPOT_MIN_YEARS:
            continue
        frames = []
        for y in sorted(per_year):
            ids = per_year[y]
            best = conn.execute(
                f"SELECT a.id FROM assets a WHERE a.id IN ({','.join('?' * len(ids))}) ORDER BY {quality.RANK} DESC LIMIT 1", ids).fetchone()
            frames.append({"year": y, "count": len(ids), "item": browse.by_ids(conn, [best[0]])[0]})
        spots.append({"photos": sum(len(v) for v in per_year.values()), "years": len(per_year), "frames": frames})
    spots.sort(key=lambda s: (-s["years"], -s["photos"]))
    people = []
    try:
        people = [dict(r) for r in conn.execute(
            f"""SELECT p.id, p.name, COUNT(DISTINCT a.id) AS n FROM faces f JOIN persons p ON p.id = f.person_id
                JOIN assets a ON a.id = f.asset_id WHERE p.name IS NOT NULL AND p.hidden = 0 AND {where}
                GROUP BY p.id ORDER BY n DESC LIMIT 6""", args)]
    except sqlite3.OperationalError:
        pass
    return {"chapter": dict(ch), "photos": len(rows), "years": years, "spots": spots[:8], "people": people}


def replay_ids(conn: sqlite3.Connection, chapter_id: int, year: str | None, n: int = 40) -> list[int]:
    """The best photos at home in that year (or the whole chapter), in time order."""
    where, args = home_where(conn, chapter_id)
    if year:
        where += " AND substr(a.taken_at, 1, 4) = ?"
        args.append(str(year))
    return quality.best_of(conn, where, args, n)
