"""Corrections: put photos in the right trip, fix captions, and find more photos
that belong to a trip. Your decisions are marked manual and survive rebuilds."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np

from . import browse, describe, search
from .places import haversine_km

FAR_KM = 300              # a candidate this far from the trip is probably not part of it

VISIBLE = "missing = 0 AND duplicate_of IS NULL AND kind != 'live_video' AND private = 0"
WINDOW_DAYS = 10          # photos this close to the trip's dates are likely part of it
VISUAL_DAYS = 60          # how far from the trip to look for visually similar photos
VISUAL_MIN = 0.62         # similarity to the trip's average look
SEED_MIN = 0.72           # similarity to the photo you clicked
MAX_SUGGESTIONS = 80


def _recount(conn: sqlite3.Connection, trip_id: int) -> None:
    r = conn.execute(
        "SELECT COUNT(*) AS n, MIN(taken_at) AS s, MAX(taken_at) AS e FROM assets "
        "WHERE trip_id = ? AND duplicate_of IS NULL AND missing = 0", (trip_id,)).fetchone()
    if not r["n"]:
        conn.execute("DELETE FROM trips WHERE id = ? AND manual = 1", (trip_id,))
        return
    conn.execute("UPDATE trips SET photo_count = ?, start_at = ?, end_at = ? WHERE id = ?",
                 (r["n"], r["s"], r["e"], trip_id))
    if not conn.execute("SELECT cover_id FROM trips WHERE id = ?", (trip_id,)).fetchone()[0]:
        mid = conn.execute("SELECT id FROM assets WHERE trip_id = ? AND kind = 'photo' ORDER BY taken_at "
                           "LIMIT 1 OFFSET ?", (trip_id, max(0, r["n"] // 2 - 1))).fetchone()
        conn.execute("UPDATE trips SET cover_id = ? WHERE id = ?", (mid[0] if mid else None, trip_id))


def set_trip(conn: sqlite3.Connection, asset_ids: list[int], trip: int | None, new_title: str | None = None) -> int | None:
    """Move photos into a trip (existing id, a new one by title, or None = not a trip).

    Returns the trip id. Live Photo clips follow their still."""
    ids = [int(i) for i in asset_ids]
    if not ids:
        raise ValueError("No photos selected")
    if new_title:
        first = conn.execute(f"SELECT MIN(taken_at), MAX(taken_at) FROM assets WHERE id IN ({','.join('?' * len(ids))})", ids).fetchone()
        cur = conn.execute(
            "INSERT INTO trips (title, subtitle, start_at, end_at, photo_count, manual, title_locked) VALUES (?, '', ?, ?, 0, 1, 1)",
            (new_title.strip()[:120], first[0] or "", first[1] or ""))
        trip = cur.lastrowid
    elif trip is not None and not conn.execute("SELECT 1 FROM trips WHERE id = ?", (trip,)).fetchone():
        raise ValueError("Unknown trip")
    old = {r[0] for r in conn.execute(f"SELECT DISTINCT trip_id FROM assets WHERE id IN ({','.join('?' * len(ids))}) "
                                      "AND trip_id IS NOT NULL", ids)}
    conn.execute(f"UPDATE assets SET trip_id = ?, trip_manual = 1 WHERE id IN ({','.join('?' * len(ids))})", [trip, *ids])
    conn.execute(f"UPDATE assets SET trip_id = ?, trip_manual = 1 WHERE live_pair_id IN ({','.join('?' * len(ids))})", [trip, *ids])
    for t in old | ({trip} if trip else set()):
        _recount(conn, t)
    conn.commit()
    return trip


def rename_trip(conn: sqlite3.Connection, trip_id: int, title: str) -> None:
    title = title.strip()[:120]
    if not title:
        raise ValueError("A trip needs a title")
    conn.execute("UPDATE trips SET title = ?, title_locked = 1 WHERE id = ?", (title, trip_id))
    conn.commit()


def delete_trip(conn: sqlite3.Connection, trip_id: int) -> int:
    """'Not a trip': the photos stay, leave the trip, and are remembered so the automatic pass
    doesn't group them again. Returns how many photos were released."""
    ids = [r[0] for r in conn.execute("SELECT id FROM assets WHERE trip_id = ?", (trip_id,))]
    conn.execute("UPDATE assets SET trip_id = NULL, trip_manual = 1 WHERE trip_id = ?", (trip_id,))
    conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
    conn.execute("DELETE FROM stories WHERE key = ?", (f"trip:{trip_id}",))
    conn.commit()
    return len(ids)


def merge_trips(conn: sqlite3.Connection, src: int, dst: int) -> None:
    """Fold trip `src` into `dst` (your decision: the result is kept across rebuilds)."""
    if src == dst or not conn.execute("SELECT 1 FROM trips WHERE id = ?", (dst,)).fetchone():
        raise ValueError("Pick a different trip to merge into")
    conn.execute("UPDATE assets SET trip_id = ?, trip_manual = 1 WHERE trip_id = ?", (dst, src))
    conn.execute("UPDATE trips SET manual = 1, title_locked = 1 WHERE id = ?", (dst,))
    conn.execute("DELETE FROM trips WHERE id = ?", (src,))
    _recount(conn, dst)
    conn.commit()


def split_trip(conn: sqlite3.Connection, trip_id: int, at_day: str, new_title: str | None = None) -> int:
    """Photos from `at_day` (YYYY-MM-DD) onward become a new trip. Returns the new trip's id."""
    day = str(at_day)[:10]
    datetime.fromisoformat(day)
    t = conn.execute("SELECT title FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not t:
        raise ValueError("Unknown trip")
    ids = [r[0] for r in conn.execute("SELECT id FROM assets WHERE trip_id = ? AND substr(taken_at, 1, 10) >= ?", (trip_id, day))]
    if not ids or len(ids) == conn.execute("SELECT COUNT(*) FROM assets WHERE trip_id = ?", (trip_id,)).fetchone()[0]:
        raise ValueError("That date doesn't split the trip; pick a day inside it")
    new = set_trip(conn, ids, None, new_title=(new_title or f"{t['title']} (part 2)"))
    conn.execute("UPDATE assets SET trip_manual = 1 WHERE trip_id = ?", (trip_id,))
    conn.execute("UPDATE trips SET manual = 1 WHERE id = ?", (trip_id,))
    _recount(conn, trip_id)
    conn.commit()
    return new


def set_trip_dates(conn: sqlite3.Connection, trip_id: int, start: str, end: str) -> dict:
    """Redraw a trip's dates: photos inside the new range join it, photos outside leave it."""
    s, e = str(start)[:10], str(end)[:10]
    datetime.fromisoformat(s); datetime.fromisoformat(e)
    if e < s:
        raise ValueError("The end is before the start")
    if not conn.execute("SELECT 1 FROM trips WHERE id = ?", (trip_id,)).fetchone():
        raise ValueError("Unknown trip")
    out = [r[0] for r in conn.execute("SELECT id FROM assets WHERE trip_id = ? AND (substr(taken_at, 1, 10) < ? OR substr(taken_at, 1, 10) > ?)",
                                      (trip_id, s, e))]
    inside = [r[0] for r in conn.execute(
        """SELECT id FROM assets WHERE missing = 0 AND kind != 'live_video' AND substr(taken_at, 1, 10) BETWEEN ? AND ?
           AND (trip_id IS NULL OR trip_id = ?) AND trashed_at IS NULL""", (s, e, trip_id))]
    if out:
        set_trip(conn, out, None)
    if inside:
        set_trip(conn, inside, trip_id)
    conn.execute("UPDATE trips SET manual = 1 WHERE id = ?", (trip_id,))
    _recount(conn, trip_id)
    conn.commit()
    return {"added": len(inside), "removed": len(out)}


def set_trip_cover(conn: sqlite3.Connection, trip_id: int, asset_id: int) -> None:
    conn.execute("UPDATE trips SET cover_id = ? WHERE id = ?", (asset_id, trip_id))
    conn.commit()


def set_caption(conn: sqlite3.Connection, asset_id: int, caption: str, keywords: list[str] | None) -> dict:
    describe.ensure_schema(conn)
    caption = caption.strip()[:500]
    kws = [k.strip().lower() for k in (keywords or []) if k.strip()][:12]
    existing = describe.for_photo(conn, asset_id)
    if existing is None:
        conn.execute(
            "INSERT INTO descriptions (asset_id, model, caption, keywords, created, status) VALUES (?, 'you', ?, ?, ?, 'ok')",
            (asset_id, caption, json.dumps(kws), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    else:
        conn.execute("UPDATE descriptions SET caption = ?, keywords = ?, model = 'you', status = 'ok' WHERE asset_id = ?",
                     (caption, json.dumps(kws), asset_id))
    conn.commit()
    return describe.for_photo(conn, asset_id)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=None)


def suggest_for_trip(conn: sqlite3.Connection, db_key: str, trip_id: int, seed_id: int | None = None) -> dict:
    """Photos that probably belong to this trip but aren't in it yet, with reasons."""
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not trip:
        raise ValueError("Unknown trip")
    members = [r[0] for r in conn.execute("SELECT id FROM assets WHERE trip_id = ?", (trip_id,))]
    member_set = set(members)
    # The time window comes from the trip's automatic core when it has one, so a single
    # photo you added by hand (maybe from another month) doesn't stretch it.
    core = conn.execute("SELECT MIN(taken_at), MAX(taken_at), COUNT(*) FROM assets WHERE trip_id = ? AND trip_manual = 0 "
                        "AND taken_at IS NOT NULL", (trip_id,)).fetchone()
    if core[2] >= 3:
        start, end = _parse(core[0]), _parse(core[1])
    else:
        start, end = _parse(trip["start_at"]), _parse(trip["end_at"])
    centre = (trip["lat"], trip["lon"]) if trip["lat"] is not None else None
    scores: dict[int, float] = {}
    reasons: dict[int, list[str]] = {}

    def note(aid: int, score: float, why: str) -> None:
        if aid in member_set:
            return
        scores[aid] = scores.get(aid, 0) + score
        reasons.setdefault(aid, []).append(why)

    # 1. Time: photos just before/after the trip, or inside its window but not in it.
    lo, hi = (start - timedelta(days=WINDOW_DAYS)).isoformat(), (end + timedelta(days=WINDOW_DAYS)).isoformat()
    for r in conn.execute(f"SELECT id, taken_at, trip_id, trip_manual, lat, lon FROM assets WHERE {VISIBLE} AND taken_at BETWEEN ? AND ?", (lo, hi)):
        if r["trip_manual"] and r["trip_id"] != trip_id:
            continue   # you put it somewhere else on purpose
        t = _parse(r["taken_at"])
        gap = 0 if start <= t <= end else min(abs((t - start).days), abs((t - end).days))
        score = 1.0 if gap == 0 else max(0.2, 0.9 - gap / WINDOW_DAYS * 0.7)
        why = "taken during the trip" if gap == 0 else f"taken {gap} day{'s' if gap != 1 else ''} from the trip"
        if centre and r["lat"] is not None:
            km = haversine_km(r["lat"], r["lon"], centre[0], centre[1])
            if km > FAR_KM:
                score *= 0.3
                why += f", but {int(km):,} km away"
        note(r["id"], score, why)

    # 2. Looks: similar to the trip's photos (and to the one you clicked).
    search.ensure_schema(conn)
    ids, mat = search._Index.load(conn, db_key)
    if len(ids):
        index = {int(a): k for k, a in enumerate(ids)}
        rows = [index[m] for m in members if m in index]
        vlo, vhi = (start - timedelta(days=VISUAL_DAYS)).isoformat(), (end + timedelta(days=VISUAL_DAYS)).isoformat()
        cand = {r[0] for r in conn.execute(f"SELECT id FROM assets WHERE {VISIBLE} AND taken_at BETWEEN ? AND ? "
                                           "AND (trip_manual = 0 OR trip_id = ?)", (vlo, vhi, trip_id))}
        if rows:
            centroid = mat[rows].mean(axis=0)
            centroid /= max(np.linalg.norm(centroid), 1e-8)
            sims = mat @ centroid
            for k in np.where(sims >= VISUAL_MIN)[0]:
                aid = int(ids[k])
                if aid in cand:
                    note(aid, float(sims[k]), f"looks like the trip's photos ({int(sims[k] * 100)}%)")
        if seed_id in index:
            sims = mat @ mat[index[seed_id]]
            for k in np.where(sims >= SEED_MIN)[0]:
                aid = int(ids[k])
                if aid in cand and aid != seed_id:
                    note(aid, float(sims[k]) * 0.8, f"looks like the photo you picked ({int(sims[k] * 100)}%)")

    # 3. Words: AI captions that mention the trip's places or top keywords.
    describe.ensure_schema(conn)
    words: Counter = Counter()
    for r in conn.execute(f"SELECT keywords FROM descriptions WHERE asset_id IN ({','.join('?' * len(members))})", members) if members else []:
        words.update(json.loads(r[0] or "[]"))
    places = {p.strip().lower() for p in (trip["subtitle"] or "").split(",") if p.strip()}
    places |= {trip["title"].lower()} | {c.strip().lower() for c in trip["title"].replace("&", ",").split(",")}
    top = {w for w, _ in words.most_common(10) if len(w) > 3} | places
    if top:
        vlo, vhi = (start - timedelta(days=VISUAL_DAYS)).isoformat(), (end + timedelta(days=VISUAL_DAYS)).isoformat()
        for r in conn.execute(
                f"""SELECT d.asset_id, d.caption, d.keywords FROM descriptions d JOIN assets a ON a.id = d.asset_id
                    WHERE a.{VISIBLE.replace(' AND ', ' AND a.')} AND a.taken_at BETWEEN ? AND ? AND d.status = 'ok'""", (vlo, vhi)):
            text = (r["caption"] or "").lower() + " " + " ".join(json.loads(r["keywords"] or "[]"))
            hits = [w for w in top if w in text]
            if len(hits) >= 2 or any(h in places for h in hits):
                note(r["asset_id"], 0.3 * len(hits), "mentions " + ", ".join(sorted(hits)[:3]))

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:MAX_SUGGESTIONS]
    items = browse.by_ids(conn, [a for a, _ in ranked])
    by = {it["id"]: it for it in items}
    out = []
    for aid, sc in ranked:
        if aid not in by:
            continue
        it = by[aid]
        it["score"] = round(sc, 2)
        it["reasons"] = reasons[aid]
        it["strong"] = sc >= 0.8
        out.append(it)
    return {"trip": dict(trip), "items": out}
