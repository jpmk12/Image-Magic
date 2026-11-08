"""Events: small clusters of photos a few hours and a few hundred metres apart.

Trips are the big unit (days away from home); events are the small one (a
birthday party, a hike, a day at the beach, a sortie). An event is a run of
photos with no gap longer than GAP_HOURS, staying within RADIUS_KM of where it
started, with at least MIN_PHOTOS photos. Events get an automatic title from
the day, the place and (when AI descriptions are on) the most common keyword;
titles you edit are kept across rebuilds.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

from .places import haversine_km

GAP_HOURS = 3
RADIUS_KM = 2.0
MIN_PHOTOS = 4
GENERIC = {"outdoor", "indoor", "outdoors", "indoors", "people", "person", "photo", "picture", "day", "daytime",
           "night", "family", "man", "woman", "child", "children", "kids", "adult", "group", "selfie", "portrait",
           "smile", "smiling", "home", "house", "room", "screenshot", "text", "sky", "trees", "tree", "grass",
           "street", "building", "water", "sunny", "cloudy"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY,
    key           TEXT NOT NULL UNIQUE,   -- stable: first photo's id
    title         TEXT NOT NULL,
    subtitle      TEXT,
    start_at      TEXT NOT NULL,
    end_at        TEXT NOT NULL,
    photo_count   INTEGER NOT NULL,
    cover_id      INTEGER,
    lat           REAL, lon REAL,
    city          TEXT, country TEXT,
    trip_id       INTEGER,
    title_locked  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_assets_event ON assets(event_id);
"""

VISIBLE = "missing = 0 AND duplicate_of IS NULL AND kind = 'photo'"


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


def _ts(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        return datetime(1970, 1, 1)


def _part_of_day(hour: int) -> str:
    if hour < 5:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


def _cluster(rows: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    out: list[list[sqlite3.Row]] = []
    cur: list[sqlite3.Row] = []
    anchor: tuple[float, float] | None = None
    for r in rows:
        t = _ts(r["taken_at"])
        far = False
        if cur:
            if (t - _ts(cur[-1]["taken_at"])).total_seconds() > GAP_HOURS * 3600:
                far = True
            elif r["lat"] is not None and anchor is not None and haversine_km(anchor[0], anchor[1], r["lat"], r["lon"]) > RADIUS_KM:
                far = True
        if far:
            if len(cur) >= MIN_PHOTOS:
                out.append(cur)
            cur, anchor = [], None
        cur.append(r)
        if anchor is None and r["lat"] is not None:
            anchor = (r["lat"], r["lon"])
    if len(cur) >= MIN_PHOTOS:
        out.append(cur)
    return out


def _home_city(conn: sqlite3.Connection, day: str) -> str | None:
    try:
        r = conn.execute("SELECT place_key FROM chapters WHERE ? BETWEEN start_at AND end_at", (day,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def _title(conn: sqlite3.Connection, members: list[sqlite3.Row], keyword: str | None) -> tuple[str, str]:
    first, last = _ts(members[0]["taken_at"]), _ts(members[-1]["taken_at"])
    city = Counter(m["city"] for m in members if m["city"]).most_common(1)
    city = city[0][0] if city else None
    day = members[0]["taken_at"][:10]
    at_home = city is not None and (_home_city(conn, day) or "") == f"{members[0]['country_code']}|{city}"
    weekday = first.strftime("%A")
    span = _part_of_day(first.hour) if (last - first).total_seconds() < 6 * 3600 else "all day"
    where = "at home" if at_home else (f"in {city}" if city else "")
    core = " ".join(x for x in [weekday, span, where] if x)
    title = f"{keyword[:1].upper()}{keyword[1:]}: {core}" if keyword else core
    when = first.strftime("%B %d, %Y").replace(" 0", " ")
    country = Counter(m["country"] for m in members if m["country"]).most_common(1)
    sub = " · ".join(x for x in [when, f"{len(members)} photos", (country[0][0] if country and not at_home else "")] if x)
    return title[:120], sub


def rebuild(conn: sqlite3.Connection) -> int:
    """Recompute events from the catalog. Keeps edited titles. Returns the number of events."""
    ensure_schema(conn)
    rows = conn.execute(
        f"""SELECT id, taken_at, lat, lon, city, country, country_code, trip_id, COALESCE(quality_score, 50) AS q
            FROM assets WHERE {VISIBLE} AND taken_at IS NOT NULL ORDER BY taken_at, id""").fetchall()
    clusters = _cluster(rows)
    keywords: dict[int, list[str]] = {}
    try:
        import json
        for r in conn.execute("SELECT asset_id, keywords FROM descriptions WHERE status = 'ok'"):
            keywords[r[0]] = json.loads(r[1] or "[]")
    except sqlite3.OperationalError:
        pass
    locked = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM events WHERE title_locked = 1")}
    old_ids = {r["key"]: r["id"] for r in conn.execute("SELECT key, id FROM events")}
    # Who belonged to which event before: a rebuilt cluster that shares most of its photos with an old
    # event keeps that event's id (and so its title, story, voice notes and album filters).
    old_members: dict[int, set[int]] = defaultdict(set)
    for r in conn.execute("SELECT id, event_id FROM assets WHERE event_id IS NOT NULL"):
        old_members[r["event_id"]].add(r["id"])
    conn.execute("UPDATE assets SET event_id = NULL WHERE event_id IS NOT NULL")
    keep: set[str] = set()
    used: set[int] = set()
    for members in clusters:
        key = str(members[0]["id"])
        mids = {m["id"] for m in members}
        match = None
        if key in old_ids and old_ids[key] not in used:
            match = old_ids[key]
        else:
            best, best_ov = None, 0
            for eid, olds in old_members.items():
                if eid in used:
                    continue
                ov = len(mids & olds)
                if ov > best_ov and ov >= max(2, min(len(mids), len(olds)) // 2):
                    best, best_ov = eid, ov
            match = best
        keep.add(key)
        kw = Counter()
        for m in members:
            for k in keywords.get(m["id"], [])[:5]:
                k = k.lower().strip()
                if k and k not in GENERIC and len(k) > 2:
                    kw[k] += 1
        top = kw.most_common(1)
        keyword = top[0][0] if top and top[0][1] >= max(2, len(members) // 4) else None
        title, sub = _title(conn, members, keyword)
        if match is not None and match in locked:
            title = locked[match]
        cover = max(members, key=lambda m: m["q"])["id"]
        pts = [(m["lat"], m["lon"]) for m in members if m["lat"] is not None]
        lat = sum(p[0] for p in pts) / len(pts) if pts else None
        lon = sum(p[1] for p in pts) / len(pts) if pts else None
        city = Counter(m["city"] for m in members if m["city"]).most_common(1)
        country = Counter(m["country"] for m in members if m["country"]).most_common(1)
        trip = Counter(m["trip_id"] for m in members if m["trip_id"]).most_common(1)
        vals = dict(key=key, title=title, subtitle=sub, start_at=members[0]["taken_at"], end_at=members[-1]["taken_at"],
                    photo_count=len(members), cover_id=cover, lat=lat, lon=lon, city=city[0][0] if city else None,
                    country=country[0][0] if country else None, trip_id=trip[0][0] if trip else None)
        if match is not None:
            sets = ", ".join(f"{k} = :{k}" for k in vals)
            conn.execute(f"UPDATE events SET {sets} WHERE id = :id", {**vals, "id": match})
            eid = match
            used.add(eid)
            if key not in old_ids:
                keep.add(next((k for k, v in old_ids.items() if v == match), key))
        else:
            cols = ", ".join(vals)
            eid = conn.execute(f"INSERT INTO events ({cols}) VALUES ({', '.join(':' + k for k in vals)})", vals).lastrowid
        ids = [m["id"] for m in members]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            conn.execute(f"UPDATE assets SET event_id = ? WHERE id IN ({','.join('?' * len(chunk))})", [eid, *chunk])
    gone = [v for k, v in old_ids.items() if v not in used and k not in keep]
    for i in range(0, len(gone), 500):
        chunk = gone[i:i + 500]
        conn.execute(f"DELETE FROM events WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    conn.commit()
    return len(clusters)


def events(conn: sqlite3.Connection, year: str | None = None, trip_id: int | None = None, limit: int = 2000) -> list[dict]:
    ensure_schema(conn)
    where, args = ["1 = 1"], []
    if year:
        where.append("substr(start_at, 1, 4) = ?")
        args.append(str(year))
    if trip_id:
        where.append("trip_id = ?")
        args.append(int(trip_id))
    rows = conn.execute(
        f"""SELECT id, title, subtitle, start_at, end_at, photo_count, cover_id, lat, lon, city, country, trip_id, title_locked
            FROM events WHERE {' AND '.join(where)} ORDER BY start_at DESC LIMIT ?""", [*args, limit]).fetchall()
    return [dict(r) for r in rows]


def event_for(conn: sqlite3.Connection, asset_id: int) -> dict | None:
    ensure_schema(conn)
    r = conn.execute("SELECT e.id, e.title, e.photo_count FROM events e JOIN assets a ON a.event_id = e.id WHERE a.id = ?",
                     (asset_id,)).fetchone()
    return dict(r) if r else None


def rename(conn: sqlite3.Connection, event_id: int, title: str) -> None:
    title = title.strip()[:120]
    if not title:
        raise ValueError("An event needs a title")
    conn.execute("UPDATE events SET title = ?, title_locked = 1 WHERE id = ?", (title, event_id))
    conn.commit()
