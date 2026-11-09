"""Albums: hand-picked sets, and smart albums that are a saved search.

A manual album is a list of photos you add to. A smart album stores the Photos
tab's filters (year, place, person, trip, event, text query, best, favourite)
and always shows what matches now. Either kind can be browsed, replayed,
filmed, made into a book or shared like a trip.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

FILTER_KEYS = ("q", "year", "month", "day", "country", "city", "trip", "event", "person", "chapter", "kind", "loose",
               "best", "favorite", "flying", "contributor")

SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
    id        INTEGER PRIMARY KEY,
    title     TEXT NOT NULL,
    kind      TEXT NOT NULL DEFAULT 'manual',   -- manual | smart
    filters   TEXT,                             -- JSON, smart albums only
    cover_id  INTEGER,
    created   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS album_items (
    album_id  INTEGER NOT NULL REFERENCES albums(id),
    asset_id  INTEGER NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (album_id, asset_id)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_filters(filters: dict) -> dict:
    return {k: str(v)[:200] for k, v in (filters or {}).items() if k in FILTER_KEYS and v not in (None, "", 0, "0", False)}


def create(conn: sqlite3.Connection, title: str, kind: str = "manual", filters: dict | None = None,
           ids: list[int] | None = None) -> int:
    ensure_schema(conn)
    title = title.strip()[:120]
    if not title:
        raise ValueError("An album needs a name")
    if kind not in ("manual", "smart"):
        raise ValueError("kind must be manual or smart")
    f = clean_filters(filters or {}) if kind == "smart" else None
    if kind == "smart" and not f:
        raise ValueError("A smart album needs at least one filter")
    cur = conn.execute("INSERT INTO albums (title, kind, filters, created) VALUES (?, ?, ?, ?)",
                       (title, kind, json.dumps(f) if f else None, _now()))
    aid = cur.lastrowid
    if ids:
        add(conn, aid, ids)
    conn.commit()
    return aid


def add(conn: sqlite3.Connection, album_id: int, ids: list[int]) -> int:
    ensure_schema(conn)
    a = conn.execute("SELECT kind FROM albums WHERE id = ?", (album_id,)).fetchone()
    if not a:
        raise ValueError("Unknown album")
    if a["kind"] != "manual":
        raise ValueError("A smart album fills itself; change its filters instead")
    pos = conn.execute("SELECT COALESCE(MAX(position), 0) FROM album_items WHERE album_id = ?", (album_id,)).fetchone()[0]
    n = 0
    for i in ids:
        if conn.execute("SELECT 1 FROM assets WHERE id = ?", (int(i),)).fetchone():
            pos += 1
            cur = conn.execute("INSERT OR IGNORE INTO album_items (album_id, asset_id, position) VALUES (?, ?, ?)", (album_id, int(i), pos))
            n += cur.rowcount
    conn.commit()
    return n


def remove(conn: sqlite3.Connection, album_id: int, ids: list[int]) -> None:
    ensure_schema(conn)
    for i in ids:
        conn.execute("DELETE FROM album_items WHERE album_id = ? AND asset_id = ?", (album_id, int(i)))
    conn.commit()


def rename(conn: sqlite3.Connection, album_id: int, title: str) -> None:
    title = title.strip()[:120]
    if not title:
        raise ValueError("An album needs a name")
    conn.execute("UPDATE albums SET title = ? WHERE id = ?", (title, album_id))
    conn.commit()


def set_cover(conn: sqlite3.Connection, album_id: int, asset_id: int | None) -> None:
    conn.execute("UPDATE albums SET cover_id = ? WHERE id = ?", (asset_id, album_id))
    conn.commit()


def delete(conn: sqlite3.Connection, album_id: int) -> None:
    ensure_schema(conn)
    conn.execute("DELETE FROM album_items WHERE album_id = ?", (album_id,))
    conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    conn.commit()


def get(conn: sqlite3.Connection, album_id: int) -> dict | None:
    ensure_schema(conn)
    r = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["filters"] = json.loads(r["filters"]) if r["filters"] else {}
    return d


def params_for(conn: sqlite3.Connection, album_id: int) -> dict:
    """Browse filters for an album: a smart album's saved filters, or a manual album's membership."""
    a = get(conn, album_id)
    if not a:
        raise ValueError("Unknown album")
    if a["kind"] == "smart":
        return dict(a["filters"])
    return {"_album_items": album_id}


def listing(conn: sqlite3.Connection) -> list[dict]:
    from . import browse, quality
    ensure_schema(conn)
    out = []
    for r in conn.execute("SELECT * FROM albums ORDER BY created DESC").fetchall():
        d = dict(r)
        d["filters"] = json.loads(r["filters"]) if r["filters"] else {}
        try:
            params = params_for(conn, r["id"])
            if params.get("q"):           # a text query needs the search engine; count the filtered base instead
                params = {k: v for k, v in params.items() if k != "q"}
            ids = browse.ids(conn, params)
        except Exception:
            ids = set()
        d["photo_count"] = len(ids)
        if not d["cover_id"] or d["cover_id"] not in ids:
            best = quality.best_of(conn, f"a.id IN ({','.join('?' * len(ids))})", list(ids), 1) if ids and len(ids) <= 900 else []
            if not best and ids:
                best = [max(ids)]
            d["cover_id"] = best[0] if best else None
        out.append(d)
    return out


def albums_of(conn: sqlite3.Connection, asset_id: int) -> list[dict]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT a.id, a.title FROM album_items i JOIN albums a ON a.id = i.album_id WHERE i.asset_id = ? ORDER BY a.title", (asset_id,))]
