"""Album ideas: things you photograph a lot, people who appear together, places you keep going back to,
and big events, each offered as an album you can accept, trim or skip. Plus "find more" for an album.

Everything runs on the laptop from the catalog, the face groups and the visual index.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS album_rejections (      -- ideas you skipped: a theme's photos, a people set, a place cell, an event
    kind     TEXT NOT NULL,
    ref      TEXT NOT NULL,
    created  TEXT NOT NULL,
    PRIMARY KEY (kind, ref)
);
CREATE TABLE IF NOT EXISTS album_find_rejections (  -- "not for this album": photos you unticked in Find more
    album_id INTEGER NOT NULL,
    asset_id INTEGER NOT NULL,
    created  TEXT NOT NULL,
    PRIMARY KEY (album_id, asset_id)
);
"""

VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0 AND a.trashed_at IS NULL"
THEME_MIN = 30            # photos a visual theme needs
THEME_TIGHT = 0.74        # average similarity to the theme's centre
PEOPLE_MIN = 15           # photos a people set needs
SOLO_MIN = 30
PLACE_MIN_DAYS = 5        # separate days at the same spot
PLACE_MIN_PHOTOS = 20
PLACE_CELL = 0.002        # about 200 m
EVENT_MIN_PHOTOS = 40
GENERIC = {"outdoor", "indoor", "outdoors", "indoors", "people", "person", "photo", "picture", "day", "daytime", "night", "nature",
           "sky", "tree", "trees", "grass", "building", "man", "woman", "child", "group", "smiling", "standing", "sitting", "wall",
           "room", "house", "street", "water", "blue", "white", "black", "green", "red", "family", "portrait", "selfie", "closeup"}


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reject(conn: sqlite3.Connection, kind: str, ref: str) -> None:
    ensure_schema(conn)
    conn.execute("INSERT OR IGNORE INTO album_rejections (kind, ref, created) VALUES (?, ?, ?)", (kind, str(ref), _now()))
    conn.commit()


def _rejected(conn: sqlite3.Connection, kind: str) -> set[str]:
    ensure_schema(conn)
    return {r[0] for r in conn.execute("SELECT ref FROM album_rejections WHERE kind = ?", (kind,))}


def _album_titles(conn: sqlite3.Connection) -> set[str]:
    try:
        return {r[0].strip().lower() for r in conn.execute("SELECT title FROM albums")}
    except sqlite3.OperationalError:
        return set()


def _in_albums(conn: sqlite3.Connection) -> set[int]:
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT asset_id FROM album_items")}
    except sqlite3.OperationalError:
        return set()


def _keywords(conn: sqlite3.Connection, ids: list[int], n: int = 3) -> list[str]:
    words: Counter = Counter()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(f"SELECT keywords FROM descriptions WHERE status = 'ok' AND asset_id IN ({','.join('?' * len(chunk))})", chunk):
            for w in json.loads(r[0] or "[]"):
                w = str(w).lower().strip()
                if len(w) > 2 and w not in GENERIC:
                    words[w] += 1
    return [w for w, c in words.most_common(n) if c >= max(3, len(ids) // 10)]


# ---- 1. themes: what you photograph a lot --------------------------------------------------

def theme_ideas(conn: sqlite3.Connection, db_key: str, limit: int = 4) -> list[dict]:
    from . import search
    search.ensure_schema(conn)
    try:
        ids, mat = search._Index.load(conn, db_key)
    except Exception:
        return []
    if len(ids) < THEME_MIN * 3:
        return []
    visible = {r[0] for r in conn.execute(f"SELECT a.id FROM assets a WHERE {VISIBLE}")}
    rejected_ids: set[int] = set()
    for ref in _rejected(conn, "theme"):
        rejected_ids.update(int(x) for x in ref.split(",") if x)
    in_albums = _in_albums(conn)
    keep = np.array([i for i, a in enumerate(ids) if int(a) in visible and int(a) not in rejected_ids], dtype=np.int64)
    if len(keep) < THEME_MIN * 3:
        return []
    ids, mat = ids[keep], mat[keep]
    mat = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-8)
    k = int(min(40, max(8, len(ids) // 150)))
    rng = np.random.default_rng(0)
    centres = mat[rng.choice(len(ids), size=k, replace=False)].copy()
    for _ in range(12):                                   # spherical k-means
        sims = mat @ centres.T
        lab = sims.argmax(axis=1)
        for j in range(k):
            m = lab == j
            if m.any():
                c = mat[m].mean(axis=0)
                centres[j] = c / max(np.linalg.norm(c), 1e-8)
    sims = mat @ centres.T
    lab = sims.argmax(axis=1)
    out = []
    for j in range(k):
        m = np.where(lab == j)[0]
        if len(m) < THEME_MIN:
            continue
        tight = float(sims[m, j].mean())
        if tight < THEME_TIGHT:
            continue
        members = [int(ids[i]) for i in m[np.argsort(-sims[m, j])]]
        if len(in_albums & set(members)) >= 0.8 * len(members):
            continue                                       # already an album
        words = _keywords(conn, members)
        title = " & ".join(w.capitalize() for w in words[:2]) if words else "Look-alikes"
        out.append({"kind": "theme", "ref": ",".join(map(str, sorted(members))), "title": title, "ids": members,
                    "why": f"{len(members)} photos that look alike" + (f" (captions say {', '.join(words)})" if words else "") + f"; {int(tight * 100)}% similar on average.",
                    "conf": round(min(0.9, 0.5 + (tight - THEME_TIGHT) * 2 + min(0.2, len(members) / 500)), 2)})
    out.sort(key=lambda c: -c["conf"])
    return out[:limit]


# ---- 2. people who appear together ----------------------------------------------------------

def people_set_ideas(conn: sqlite3.Connection, limit: int = 4) -> list[dict]:
    try:
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM persons WHERE name IS NOT NULL AND hidden = 0")}
        rows = conn.execute(f"""SELECT f.asset_id, f.person_id FROM faces f JOIN assets a ON a.id = f.asset_id
                                WHERE {VISIBLE} AND f.person_id IS NOT NULL""").fetchall()
    except sqlite3.OperationalError:
        return []
    by_asset: dict[int, set] = defaultdict(set)
    unnamed_in: set[int] = set()
    for r in rows:
        if r["person_id"] in names:
            by_asset[r["asset_id"]].add(r["person_id"])
        else:
            unnamed_in.add(r["asset_id"])
    sets: dict[frozenset, list[int]] = defaultdict(list)
    for aid, people in by_asset.items():
        if 2 <= len(people) <= 4:
            sets[frozenset(people)].append(aid)
        if len(people) == 1 and aid not in unnamed_in:
            sets[frozenset(people)].append(aid)
    rejected = _rejected(conn, "people")
    titles = _album_titles(conn)
    out = []
    for people, aids in sets.items():
        ref = ",".join(map(str, sorted(people)))
        if ref in rejected:
            continue
        ns = sorted(names[p] for p in people)
        if len(people) == 1:
            if len(aids) < SOLO_MIN:
                continue
            title = f"{ns[0]} on their own"
        else:
            if len(aids) < PEOPLE_MIN:
                continue
            title = " & ".join(ns) + " together"
        if title.lower() in titles:
            continue
        aids.sort()
        out.append({"kind": "people", "ref": ref, "title": title, "ids": aids,
                    "why": f"{len(aids)} photos with " + (f"exactly {ns[0]} and nobody else" if len(ns) == 1 else " and ".join(ns) + " in the same frame") + ".",
                    "conf": round(min(0.9, 0.6 + len(aids) / 300), 2)})
    out.sort(key=lambda c: -len(c["ids"]))
    return out[:limit]


# ---- 3. places you keep going back to -------------------------------------------------------

def place_ideas(conn: sqlite3.Connection, limit: int = 3) -> list[dict]:
    rows = conn.execute(f"""SELECT a.id, a.lat, a.lon, a.city, a.country, substr(a.taken_at, 1, 10) AS d FROM assets a
                            WHERE {VISIBLE} AND a.lat IS NOT NULL AND a.taken_at IS NOT NULL""").fetchall()
    cells: dict[tuple, list] = defaultdict(list)
    for r in rows:
        cells[(round(r["lat"] / PLACE_CELL), round(r["lon"] / PLACE_CELL))].append(r)
    if not cells:
        return []
    home_cell = max(cells, key=lambda k: len(cells[k]))
    rejected = _rejected(conn, "place")
    titles = _album_titles(conn)
    out = []
    for key, rs in cells.items():
        days = {r["d"] for r in rs}
        if len(days) < PLACE_MIN_DAYS or len(rs) < PLACE_MIN_PHOTOS:
            continue
        ref = f"{key[0]},{key[1]}"
        if ref in rejected:
            continue
        city = next((r["city"] for r in rs if r["city"]), None)
        where = f"in {city}" if city else f"at {rs[0]['lat']:.3f}, {rs[0]['lon']:.3f}"
        title = (f"Home ({city})" if city else "Home") if key == home_cell else f"A spot {where}"
        if title.lower() in titles:
            continue
        years = sorted({d[:4] for d in days})
        ids = sorted(r["id"] for r in rs)
        out.append({"kind": "place", "ref": ref, "title": title, "ids": ids,
                    "why": f"The same spot {where} on {len(days)} different days" + (f" across {years[0]}–{years[-1]}" if len(years) > 1 else "") + f": {len(ids)} photos.",
                    "conf": round(min(0.85, 0.5 + len(days) / 60), 2), "lat": rs[0]["lat"], "lon": rs[0]["lon"]})
    out.sort(key=lambda c: -len(c["ids"]))
    return out[:limit]


# ---- 4. big events ----------------------------------------------------------------------------

def event_ideas(conn: sqlite3.Connection, limit: int = 3) -> list[dict]:
    try:
        evs = conn.execute("SELECT id, title, start_at, photo_count FROM events WHERE photo_count >= ? ORDER BY photo_count DESC LIMIT 40",
                           (EVENT_MIN_PHOTOS,)).fetchall()
    except sqlite3.OperationalError:
        return []
    rejected = _rejected(conn, "event")
    titles = _album_titles(conn)
    out = []
    for e in evs:
        if str(e["id"]) in rejected or e["title"].strip().lower() in titles:
            continue
        people = [r[0] for r in conn.execute("""SELECT DISTINCT p.name FROM faces f JOIN assets a ON a.id = f.asset_id JOIN persons p ON p.id = f.person_id
                                                 WHERE a.event_id = ? AND p.name IS NOT NULL AND p.hidden = 0""", (e["id"],))]
        if len(people) < 2:
            continue
        ids = [r[0] for r in conn.execute(f"SELECT a.id FROM assets a WHERE {VISIBLE} AND a.event_id = ? ORDER BY a.taken_at", (e["id"],))]
        out.append({"kind": "event", "ref": str(e["id"]), "title": e["title"], "ids": ids,
                    "why": f"A big day: {len(ids)} photos on {e['start_at'][:10]} with {', '.join(people[:4])}" + (" and others" if len(people) > 4 else "") + ".",
                    "conf": round(min(0.85, 0.55 + len(ids) / 400 + len(people) * 0.03), 2)})
    return out[:limit]


def ideas(conn: sqlite3.Connection, db_key: str) -> list[dict]:
    out = []
    for fn in (lambda: theme_ideas(conn, db_key), lambda: people_set_ideas(conn), lambda: place_ideas(conn), lambda: event_ideas(conn)):
        try:
            out += fn()
        except Exception:
            continue
    return out


# ---- find more for an album -------------------------------------------------------------------

def reject_for_album(conn: sqlite3.Connection, album_id: int, ids: list[int]) -> int:
    ensure_schema(conn)
    ids = [int(i) for i in ids]
    if ids:
        conn.executemany("INSERT OR IGNORE INTO album_find_rejections (album_id, asset_id, created) VALUES (?, ?, ?)",
                         [(int(album_id), i, _now()) for i in ids])
        conn.commit()
    return len(ids)


def suggest_for_album(conn: sqlite3.Connection, db_key: str, album_id: int, limit: int = 80) -> dict:
    """Photos that look like the album's, or show the people it is about, not yet in it and not rejected for it."""
    from . import albums, browse, search
    ensure_schema(conn)
    a = albums.get(conn, album_id)
    if not a:
        raise ValueError("Unknown album")
    members = [r[0] for r in conn.execute("SELECT asset_id FROM album_items WHERE album_id = ?", (album_id,))]
    if not members:
        return {"album": a, "items": []}
    skip = set(members) | {r[0] for r in conn.execute("SELECT asset_id FROM album_find_rejections WHERE album_id = ?", (album_id,))}
    visible = {r[0] for r in conn.execute(f"SELECT a.id FROM assets a WHERE {VISIBLE}")}
    scores: dict[int, float] = {}
    reasons: dict[int, list[str]] = defaultdict(list)
    # looks
    search.ensure_schema(conn)
    try:
        ids, mat = search._Index.load(conn, db_key)
    except Exception:
        ids, mat = np.array([]), np.array([])
    if len(ids):
        index = {int(x): k for k, x in enumerate(ids)}
        rows = [index[m] for m in members if m in index]
        if rows:
            c = mat[rows].mean(axis=0); c /= max(np.linalg.norm(c), 1e-8)
            sims = mat @ c
            for k in np.where(sims >= 0.7)[0]:
                aid = int(ids[k])
                if aid in visible and aid not in skip:
                    scores[aid] = scores.get(aid, 0) + float(sims[k]); reasons[aid].append(f"looks like the album's photos ({int(sims[k] * 100)}%)")
    # people
    try:
        ph = ",".join("?" * len(members))
        cast = Counter(r[0] for r in conn.execute(f"""SELECT f.person_id FROM faces f JOIN persons p ON p.id = f.person_id
                                                      WHERE f.asset_id IN ({ph}) AND p.name IS NOT NULL AND p.hidden = 0""", members))
        core = {p for p, n in cast.items() if n >= max(2, len(members) // 2)}
        if core:
            names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM persons WHERE name IS NOT NULL")}
            seen: dict[int, set] = defaultdict(set)
            for r in conn.execute(f"""SELECT f.asset_id, f.person_id FROM faces f JOIN assets a ON a.id = f.asset_id
                                      WHERE {VISIBLE} AND f.person_id IN ({','.join('?' * len(core))})""", list(core)):
                seen[r[0]].add(r[1])
            for aid, people in seen.items():
                if aid in skip or people != core and len(people) < len(core):
                    continue
                scores[aid] = scores.get(aid, 0) + 0.8; reasons[aid].append("shows " + " and ".join(sorted(names[p] for p in people)))
    except sqlite3.OperationalError:
        pass
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
    items = browse.by_ids(conn, [x for x, _ in ranked])
    by = {it["id"]: it for it in items}
    out = []
    for aid, sc in ranked:
        if aid in by:
            it = by[aid]; it["score"] = round(sc, 2); it["reasons"] = reasons[aid]; it["strong"] = sc >= 0.8; out.append(it)
    return {"album": a, "items": out}
