"""Life chapters, the timeline and milestones.

A chapter is a period with one home. Home for a month is the place most
photographed in the months around it, ignoring trips. When home changes and
stays changed, a new chapter starts. You can rename chapters; renamed chapters
are kept as they are when the automatic ones are rebuilt.

Milestones are notable moments found in the data (a move, a person's first
photo, the year's biggest trip, the busiest day). Each is a suggestion: you keep
or dismiss it, and your choice survives rebuilds.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta

WINDOW_MONTHS = 3        # months either side that vote on a month's home
MIN_CHAPTER_MONTHS = 4   # a new home must last this long to become a chapter
MIN_HOME_PHOTOS = 8      # ...with at least this many photos there

SCHEMA = """
CREATE TABLE IF NOT EXISTS chapters (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    subtitle    TEXT,
    start_at    TEXT NOT NULL,     -- YYYY-MM-DD
    end_at      TEXT NOT NULL,     -- inclusive
    place_key   TEXT,
    lat         REAL, lon REAL,
    cover_id    INTEGER,
    photo_count INTEGER NOT NULL DEFAULT 0,
    auto        INTEGER NOT NULL DEFAULT 1,   -- 0 once you edit it
    note        TEXT
);
CREATE TABLE IF NOT EXISTS milestones (
    id        INTEGER PRIMARY KEY,
    key       TEXT NOT NULL UNIQUE,  -- stable id, e.g. 'move:2019-06' or 'person-first:12'
    date      TEXT NOT NULL,
    kind      TEXT NOT NULL,
    title     TEXT NOT NULL,
    detail    TEXT,
    asset_id  INTEGER,
    person_id INTEGER,
    trip_id   INTEGER,
    state     TEXT NOT NULL DEFAULT 'new'   -- new | kept | dismissed
);
"""

VISIBLE = "missing = 0 AND duplicate_of IS NULL AND kind = 'photo'"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    from . import faces
    faces.ensure_schema(conn)   # milestones look at people


def _month(ts: str) -> str:
    return ts[:7]


def _add_months(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _months_between(a: str, b: str) -> list[str]:
    out, cur = [], a
    while cur <= b:
        out.append(cur)
        cur = _add_months(cur, 1)
    return out


def _place_key(row) -> str | None:
    if row["lat"] is None:
        return None
    if row["city"]:
        return f"{row['country_code']}|{row['city']}"
    return f"cell|{round(row['lat'], 1)}|{round(row['lon'], 1)}"


def _place_label(row) -> str:
    if row["city"]:
        return f"{row['city']}, {row['country']}" if row["country"] else row["city"]
    if row["region"]:
        return f"{row['region']}, {row['country']}" if row["country"] else row["region"]
    return row["country"] or "Unknown place"


def home_by_month(conn: sqlite3.Connection) -> dict[str, tuple[str, str, float, float]]:
    """month -> (place_key, label, lat, lon) for every month with photos."""
    rows = conn.execute(
        f"""SELECT taken_at, lat, lon, city, region, country, country_code, trip_id
            FROM assets WHERE {VISIBLE} AND taken_at IS NOT NULL ORDER BY taken_at"""
    ).fetchall()
    if not rows:
        return {}
    votes: dict[str, Counter] = defaultdict(Counter)
    info: dict[str, tuple[str, float, float]] = {}
    months_with_photos: set[str] = set()
    for r in rows:
        m = _month(r["taken_at"])
        months_with_photos.add(m)
        if r["trip_id"] is not None:
            continue
        key = _place_key(r)
        if key is None:
            continue
        votes[m][key] += 1
        info.setdefault(key, (_place_label(r), r["lat"], r["lon"]))
    first, last = _month(rows[0]["taken_at"]), _month(rows[-1]["taken_at"])
    homes: dict[str, tuple[str, str, float, float]] = {}
    prev = None
    for m in _months_between(first, last):
        tally: Counter = Counter()
        for d in range(-WINDOW_MONTHS, WINDOW_MONTHS + 1):
            w = 1.0 / (1 + abs(d))
            for key, n in votes.get(_add_months(m, d), {}).items():
                tally[key] += n * w
        if tally:
            key = tally.most_common(1)[0][0]
            prev = (key, *info[key])
        if prev and (m in months_with_photos):
            homes[m] = prev
    return homes


def _segments(homes: dict[str, tuple]) -> list[dict]:
    """Runs of months with the same home; short runs are absorbed by the previous one."""
    months = sorted(homes)
    if not months:
        return []
    runs: list[dict] = []
    for m in months:
        key = homes[m][0]
        if runs and runs[-1]["key"] == key:
            runs[-1]["months"].append(m)
        else:
            runs.append({"key": key, "label": homes[m][1], "lat": homes[m][2], "lon": homes[m][3], "months": [m]})
    merged: list[dict] = []
    for run in runs:
        if merged and len(run["months"]) < MIN_CHAPTER_MONTHS:
            merged[-1]["months"].extend(run["months"])
        else:
            merged.append(run)
    # A short *first* run joins the next one.
    if len(merged) > 1 and len(merged[0]["months"]) < MIN_CHAPTER_MONTHS:
        merged[1]["months"] = merged[0]["months"] + merged[1]["months"]
        merged.pop(0)
    return merged


def rebuild_chapters(conn: sqlite3.Connection) -> int:
    ensure_schema(conn)
    homes = home_by_month(conn)
    segs = _segments(homes)
    manual = conn.execute("SELECT start_at, end_at FROM chapters WHERE auto = 0").fetchall()
    conn.execute("DELETE FROM chapters WHERE auto = 1")
    last_photo = conn.execute(f"SELECT MAX(taken_at) FROM assets WHERE {VISIBLE}").fetchone()[0]
    made = 0
    for seg in segs:
        start = seg["months"][0] + "-01"
        end_month = seg["months"][-1]
        if seg is segs[-1]:
            end = (last_photo or end_month + "-28")[:10]
        else:   # inclusive: the day before the next chapter starts
            end = (date.fromisoformat(_add_months(end_month, 1) + "-01") - timedelta(days=1)).isoformat()
        after = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
        n = conn.execute(f"SELECT COUNT(*) FROM assets WHERE {VISIBLE} AND taken_at >= ? AND taken_at < ?",
                         (start, after)).fetchone()[0]
        if n < MIN_HOME_PHOTOS:
            continue
        # Skip periods you've already described yourself.
        if any(start <= e and end >= s for s, e in manual):
            continue
        cover = conn.execute(
            f"""SELECT id FROM assets WHERE {VISIBLE} AND taken_at >= ? AND taken_at < ? AND trip_id IS NULL
                AND lat IS NOT NULL ORDER BY taken_at LIMIT 1 OFFSET ?""",
            (start, after, max(0, n // 2 - 1))).fetchone()
        years = f"{start[:4]}–{end[:4]}" if start[:4] != end[:4] else start[:4]
        conn.execute(
            "INSERT INTO chapters (title, subtitle, start_at, end_at, place_key, lat, lon, cover_id, photo_count, auto) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (seg["label"], years, start, end, seg["key"], seg["lat"], seg["lon"], cover[0] if cover else None, n))
        made += 1
    conn.commit()
    return made


def rebuild_milestones(conn: sqlite3.Connection) -> int:
    ensure_schema(conn)
    found: list[dict] = []
    first = conn.execute(f"SELECT id, taken_at FROM assets WHERE {VISIBLE} AND taken_at IS NOT NULL "
                         "ORDER BY taken_at LIMIT 1").fetchone()
    if first:
        found.append({"key": "first-photo", "date": first["taken_at"][:10], "kind": "first",
                      "title": "Your first photo", "asset_id": first["id"]})
    chapters = conn.execute("SELECT id, title, start_at, cover_id FROM chapters ORDER BY start_at").fetchall()
    for ch in chapters[1:]:
        found.append({"key": f"move:{ch['start_at'][:7]}", "date": ch["start_at"], "kind": "move",
                      "title": f"Moved to {ch['title']}", "asset_id": ch["cover_id"]})
    # People: first photo of each named person.
    for p in conn.execute("SELECT id, name FROM persons WHERE name IS NOT NULL AND hidden = 0").fetchall():
        r = conn.execute(
            f"""SELECT a.id, a.taken_at FROM faces f JOIN assets a ON a.id = f.asset_id
                WHERE f.person_id = ? AND a.{VISIBLE.replace(' AND ', ' AND a.')} AND a.taken_at IS NOT NULL
                ORDER BY a.taken_at LIMIT 1""", (p["id"],)).fetchone()
        if r:
            found.append({"key": f"person-first:{p['id']}", "date": r["taken_at"][:10], "kind": "person",
                          "title": f"First photo of {p['name']}", "asset_id": r["id"], "person_id": p["id"]})
    # Biggest trip each year, busiest day each year.
    for t in conn.execute(
            """SELECT id, title, start_at, end_at, photo_count, cover_id, substr(start_at, 1, 4) AS y
               FROM trips t1 WHERE photo_count = (SELECT MAX(photo_count) FROM trips t2
               WHERE substr(t2.start_at, 1, 4) = substr(t1.start_at, 1, 4))""").fetchall():
        days = (date.fromisoformat(t["end_at"][:10]) - date.fromisoformat(t["start_at"][:10])).days + 1
        found.append({"key": f"trip-year:{t['y']}", "date": t["start_at"][:10], "kind": "trip",
                      "title": f"Biggest trip of {t['y']}: {t['title']}",
                      "detail": f"{days} days, {t['photo_count']} photos", "asset_id": t["cover_id"], "trip_id": t["id"]})
    busiest = conn.execute(
        f"""WITH days AS (SELECT substr(taken_at, 1, 10) AS day, substr(taken_at, 1, 4) AS y, COUNT(*) AS n
                         FROM assets WHERE {VISIBLE} AND taken_at IS NOT NULL GROUP BY day)
            SELECT day, y, n FROM days d1 WHERE n = (SELECT MAX(n) FROM days d2 WHERE d2.y = d1.y) AND n >= 15
            GROUP BY y""").fetchall()
    for b in busiest:
        aid = conn.execute(f"SELECT id FROM assets WHERE {VISIBLE} AND substr(taken_at, 1, 10) = ? "
                           "ORDER BY taken_at LIMIT 1 OFFSET ?", (b["day"], b["n"] // 2)).fetchone()
        try:
            nice = date.fromisoformat(b["day"]).strftime("%B %d, %Y").replace(" 0", " ")
        except ValueError:
            nice = b["day"]
        found.append({"key": f"busy-day:{b['y']}", "date": b["day"], "kind": "day",
                      "title": f"Busiest day of {b['y']}: {nice}", "detail": f"{b['n']} photos",
                      "asset_id": aid[0] if aid else None})

    keys = {m["key"] for m in found}
    conn.execute("DELETE FROM milestones WHERE key NOT IN (%s)" % ",".join("?" * len(keys)) if keys
                 else "DELETE FROM milestones", list(keys))
    for m in found:
        conn.execute(
            """INSERT INTO milestones (key, date, kind, title, detail, asset_id, person_id, trip_id)
               VALUES (:key, :date, :kind, :title, :detail, :asset_id, :person_id, :trip_id)
               ON CONFLICT(key) DO UPDATE SET date = excluded.date, title = excluded.title,
                 detail = excluded.detail, asset_id = excluded.asset_id""",
            {"detail": None, "asset_id": None, "person_id": None, "trip_id": None, **m})
    conn.commit()
    return len(found)


def rebuild(conn: sqlite3.Connection) -> tuple[int, int]:
    return rebuild_chapters(conn), rebuild_milestones(conn)


# ---- queries ---------------------------------------------------------------

def timeline(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    months = {r["m"]: r["n"] for r in conn.execute(
        f"SELECT substr(taken_at, 1, 7) AS m, COUNT(*) AS n FROM assets WHERE {VISIBLE} "
        "AND taken_at IS NOT NULL GROUP BY m")}
    chapters = [dict(r) for r in conn.execute("SELECT * FROM chapters ORDER BY start_at DESC")]
    trips = [dict(r) for r in conn.execute(
        "SELECT id, title, subtitle, start_at, end_at, photo_count, cover_id FROM trips ORDER BY start_at DESC")]
    milestones = [dict(r) for r in conn.execute(
        "SELECT * FROM milestones WHERE state != 'dismissed' ORDER BY date DESC")]
    people = [dict(r) for r in conn.execute(
        f"""SELECT p.id, p.name, MIN(a.taken_at) AS first_at, MAX(a.taken_at) AS last_at, COUNT(DISTINCT a.id) AS n
            FROM persons p JOIN faces f ON f.person_id = p.id JOIN assets a ON a.id = f.asset_id
            WHERE p.name IS NOT NULL AND p.hidden = 0 AND a.{VISIBLE.replace(' AND ', ' AND a.')}
            GROUP BY p.id ORDER BY first_at""")]
    return {"months": months, "chapters": chapters, "trips": trips, "milestones": milestones, "people": people}


def update_chapter(conn: sqlite3.Connection, cid: int, data: dict) -> None:
    fields = {}
    for k in ("title", "subtitle", "note"):
        if k in data:
            fields[k] = (str(data[k]).strip() or None) if k != "title" else str(data[k]).strip()[:120]
    for k in ("start_at", "end_at"):
        if k in data:
            date.fromisoformat(str(data[k])[:10])   # validate
            fields[k] = str(data[k])[:10]
    if not fields.get("title", "x"):
        raise ValueError("A chapter needs a title")
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        conn.execute(f"UPDATE chapters SET {sets}, auto = 0 WHERE id = :id", {**fields, "id": cid})
        conn.commit()


def set_milestone_state(conn: sqlite3.Connection, mid: int, state: str) -> None:
    if state not in ("new", "kept", "dismissed"):
        raise ValueError("bad state")
    conn.execute("UPDATE milestones SET state = ? WHERE id = ?", (state, mid))
    conn.commit()
