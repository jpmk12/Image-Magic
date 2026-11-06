"""Memories: on this day, shuffle, then & now, and similar moments."""

from __future__ import annotations

import random
import sqlite3
from collections import defaultdict
from datetime import date

import numpy as np

from . import browse, search

VISIBLE = "missing = 0 AND duplicate_of IS NULL AND kind != 'live_video' AND private = 0"
CELL = 0.0005            # ~55 m; photos in the same cell count as "the same spot"
MIN_SPAN_DAYS = 365
SIMILAR_MIN = 0.72       # image-to-image CLIP similarity floor
SIMILAR_N = 12


def _with_description(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    if not items:
        return items
    ids = [i["id"] for i in items]
    caps = {}
    try:
        caps = {r[0]: r[1] for r in conn.execute(
            f"SELECT asset_id, caption FROM descriptions WHERE status = 'ok' AND asset_id IN ({','.join('?' * len(ids))})", ids)}
    except sqlite3.OperationalError:
        pass
    for it in items:
        it["caption"] = caps.get(it["id"], "")
    return items


def on_this_day(conn: sqlite3.Connection, today: date | None = None, per_year: int = 12) -> list[dict]:
    today = today or date.today()
    md = today.strftime("-%m-%d")
    rows = conn.execute(
        f"""SELECT id, kind, taken_at, city, country, width, height FROM assets
            WHERE {VISIBLE} AND taken_at IS NOT NULL AND substr(taken_at, 5, 6) = ?
              AND substr(taken_at, 1, 4) != ? ORDER BY taken_at DESC""", (md, str(today.year))).fetchall()
    by_year: dict[str, list] = defaultdict(list)
    for r in rows:
        y = r["taken_at"][:4]
        if len(by_year[y]) < per_year:
            by_year[y].append(dict(r))
    return [{"year": y, "years_ago": today.year - int(y), "items": _with_description(conn, items)}
            for y, items in sorted(by_year.items(), reverse=True)]


def shuffle(conn: sqlite3.Connection, rng: random.Random | None = None) -> dict | None:
    """A random good moment: prefer photos rated well, else with people, else on a trip."""
    rng = rng or random.Random()
    pools = [
        f"SELECT a.id FROM assets a JOIN descriptions d ON d.asset_id = a.id WHERE a.{VISIBLE.replace(' AND ', ' AND a.')} AND d.quality >= 4 AND a.burst_best = 1",
        f"SELECT id FROM assets WHERE {VISIBLE} AND quality_score >= 70 AND burst_best = 1",
        f"SELECT DISTINCT a.id FROM assets a JOIN faces f ON f.asset_id = a.id JOIN persons p ON p.id = f.person_id "
        f"WHERE a.{VISIBLE.replace(' AND ', ' AND a.')} AND p.name IS NOT NULL",
        f"SELECT id FROM assets WHERE {VISIBLE} AND trip_id IS NOT NULL",
        f"SELECT id FROM assets WHERE {VISIBLE} AND kind = 'photo'",
    ]
    for sql in pools:
        try:
            ids = [r[0] for r in conn.execute(sql)]
        except sqlite3.OperationalError:
            continue
        if len(ids) >= 5 or (ids and sql is pools[-1]):
            aid = rng.choice(ids)
            d = browse.photo_detail(conn, aid)
            if d:
                _with_description(conn, [d])
                ch = conn.execute("SELECT id, title FROM chapters WHERE ? BETWEEN start_at AND end_at || 'T99'",
                                  (d["taken_at"],)).fetchone()
                d["chapter"] = dict(ch) if ch else None
                return d
    return None


def then_and_now(conn: sqlite3.Connection, limit: int = 40) -> list[dict]:
    """Pairs of photos taken at the same spot at least a year apart, biggest gaps first."""
    rows = conn.execute(
        f"""SELECT id, kind, taken_at, lat, lon, city, country FROM assets
            WHERE {VISIBLE} AND lat IS NOT NULL AND taken_at IS NOT NULL AND kind = 'photo' ORDER BY taken_at"""
    ).fetchall()
    cells: dict[tuple[int, int], list] = defaultdict(list)
    for r in rows:
        cells[(round(r["lat"] / CELL), round(r["lon"] / CELL))].append(r)
    pairs = []
    for members in cells.values():
        if len(members) < 2:
            continue
        a, b = members[0], members[-1]
        span = (date.fromisoformat(b["taken_at"][:10]) - date.fromisoformat(a["taken_at"][:10])).days
        if span < MIN_SPAN_DAYS:
            continue
        pairs.append({"then": dict(a), "now": dict(b), "days": span,
                      "place": ", ".join(x for x in [a["city"], a["country"]] if x)})
    pairs.sort(key=lambda p: -p["days"])
    out = pairs[:limit]
    for p in out:
        _with_description(conn, [p["then"], p["now"]])
    return out


def similar(conn: sqlite3.Connection, db_key: str, asset_id: int, n: int = SIMILAR_N) -> list[dict]:
    """Photos that look like this one (CLIP), from other days. Needs smart search."""
    search.ensure_schema(conn)
    row = conn.execute("SELECT vec FROM embeddings WHERE asset_id = ? AND model = ?",
                       (asset_id, search.MODEL_NAME)).fetchone()
    if not row:
        return []
    ids, mat = search._Index.load(conn, db_key)
    if not len(ids):
        return []
    q = np.frombuffer(row["vec"], dtype=np.float16).astype(np.float32)
    scores = mat @ q
    order = np.argsort(-scores)
    me = conn.execute("SELECT substr(taken_at, 1, 10) FROM assets WHERE id = ?", (asset_id,)).fetchone()[0]
    out: list[int] = []
    for i in order:
        if len(out) >= n or scores[i] < SIMILAR_MIN:
            break
        aid = int(ids[i])
        if aid == asset_id:
            continue
        r = conn.execute(f"SELECT substr(taken_at, 1, 10) FROM assets WHERE id = ? AND {VISIBLE}", (aid,)).fetchone()
        if r and r[0] != me:
            out.append(aid)
    return _with_description(conn, browse.by_ids(conn, out))
