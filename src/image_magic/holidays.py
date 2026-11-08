"""Holidays through the years: Christmas, Halloween, birthdays... one row per year, with the best photos of each.

Everything here is a date lookup over the catalog; nothing leaves the laptop.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import datewords, quality

VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0"

# The holidays people actually look back on, in calendar order. Keys match datewords.HOLIDAYS.
HOLIDAYS = [
    ("new year", "New Year"),
    ("valentine", "Valentine's Day"),
    ("easter", "Easter"),
    ("fourth of july", "Fourth of July"),
    ("halloween", "Halloween"),
    ("thanksgiving", "Thanksgiving"),
    ("christmas", "Christmas"),
]


def _years(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute("SELECT DISTINCT substr(taken_at, 1, 4) FROM assets WHERE missing = 0 AND taken_at IS NOT NULL ORDER BY 1").fetchall()
    out = []
    for (y,) in rows:
        try:
            out.append(int(y))
        except (TypeError, ValueError):
            continue
    return out


def _birthday_span(birthday: str, year: int) -> tuple[date, date] | None:
    try:
        b = date.fromisoformat(birthday[:10])
    except ValueError:
        return None
    if year < b.year:
        return None
    try:
        d = b.replace(year=year)
    except ValueError:          # 29 Feb
        d = date(year, 2, 28)
    return d - timedelta(days=1), d + timedelta(days=1)


def occasions(conn: sqlite3.Connection) -> list[dict]:
    """Every occasion the library can replay: the fixed holidays, then each named person's birthday."""
    out = [{"key": k, "title": t, "kind": "holiday"} for k, t in HOLIDAYS]
    for p in conn.execute("SELECT id, name, birthday FROM persons WHERE name IS NOT NULL AND hidden = 0 AND birthday IS NOT NULL ORDER BY name"):
        out.append({"key": f"birthday:{p['id']}", "title": f"{p['name']}'s birthday", "kind": "birthday", "person": p["id"], "birthday": p["birthday"][:10]})
    return out


def spans(conn: sqlite3.Connection, key: str, years: list[int] | None = None) -> list[tuple[date, date]]:
    """The date span of one occasion in every year the library covers."""
    years = years if years is not None else _years(conn)
    if key.startswith("birthday:"):
        row = conn.execute("SELECT birthday FROM persons WHERE id = ?", (int(key.split(":", 1)[1] or 0),)).fetchone()
        if not row or not row["birthday"]:
            return []
        return [s for s in (_birthday_span(row["birthday"], y) for y in years) if s]
    fn = datewords.HOLIDAYS.get(key)
    if not fn:
        raise ValueError("Unknown holiday")
    return [fn(y) for y in years]


def _label(key: str, span: tuple[date, date]) -> int:
    """The year an occasion belongs to (New Year's Eve photos count towards the new year)."""
    return span[1].year if key.startswith("new year") else span[0].year


def overview(conn: sqlite3.Connection, per_year: int = 1) -> list[dict]:
    """For each occasion: the years that have photos, how many, and a cover for each year."""
    years = _years(conn)
    out = []
    for occ in occasions(conn):
        rows = []
        for span in spans(conn, occ["key"], years):
            where = f"{VISIBLE} AND substr(a.taken_at, 1, 10) BETWEEN ? AND ?"
            args = [span[0].isoformat(), span[1].isoformat()]
            n = conn.execute(f"SELECT COUNT(*) FROM assets a WHERE {where}", args).fetchone()[0]
            if not n:
                continue
            best = quality.best_of(conn, where, args, per_year)
            rows.append({"year": _label(occ["key"], span), "count": n, "cover": best[0] if best else None,
                         "from": args[0], "to": args[1]})
        if rows:
            out.append({**occ, "years": rows, "total": sum(r["count"] for r in rows)})
    return out


def best_ids(conn: sqlite3.Connection, key: str, per_year: int = 6) -> list[int]:
    """The best few photos of an occasion from every year, oldest year first: what the replay plays."""
    ids: list[int] = []
    for span in spans(conn, key):
        where = f"{VISIBLE} AND substr(a.taken_at, 1, 10) BETWEEN ? AND ?"
        ids += quality.best_of(conn, where, [span[0].isoformat(), span[1].isoformat()], per_year)
    return ids


def title_of(conn: sqlite3.Connection, key: str) -> str:
    for occ in occasions(conn):
        if occ["key"] == key:
            return occ["title"]
    raise ValueError("Unknown holiday")
