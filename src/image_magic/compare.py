"""Comparison pages: two moments side by side.

"Emma at 3 vs Jack at 3", "Christmas 2017 vs Christmas 2023", "Jordan vs Japan".
Each side is a set of photos (a person at an age, a year, a month, a trip, an
event) summarised with its best photos, places and people.
"""

from __future__ import annotations

import sqlite3

from . import browse, quality

VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0"


def _person_age_where(conn: sqlite3.Connection, pid: int, age: int | None) -> tuple[str, list, str]:
    p = conn.execute("SELECT name, birthday FROM persons WHERE id = ?", (pid,)).fetchone()
    if not p:
        raise ValueError("Unknown person")
    where = f"{VISIBLE} AND a.id IN (SELECT asset_id FROM faces WHERE person_id = ?)"
    args: list = [pid]
    label = p["name"] or "Unnamed"
    if age is not None:
        if not p["birthday"]:
            raise ValueError(f"Set {label}'s birthday on the People tab first.")
        where += " AND a.taken_at >= date(?, ?) AND a.taken_at < date(?, ?)"
        args += [p["birthday"], f"+{int(age)} years", p["birthday"], f"+{int(age) + 1} years"]
        label += f" at {int(age)}"
    return where, args, label


def side(conn: sqlite3.Connection, spec: dict, n: int = 12) -> dict:
    kind = str(spec.get("kind") or "")
    if kind == "person":
        where, args, label = _person_age_where(conn, int(spec.get("id") or 0),
                                               int(spec["age"]) if spec.get("age") not in (None, "") else None)
    elif kind == "year":
        y = str(spec.get("year") or "")[:4]
        where, args, label = f"{VISIBLE} AND substr(a.taken_at, 1, 4) = ?", [y], y
    elif kind == "month":
        m = str(spec.get("month") or "")[:7]
        where, args, label = f"{VISIBLE} AND substr(a.taken_at, 1, 7) = ?", [m], m
    elif kind == "day":
        d = str(spec.get("day") or "")[:10]
        where, args, label = f"{VISIBLE} AND substr(a.taken_at, 1, 10) = ?", [d], d
    elif kind in ("trip", "event"):
        row = conn.execute(f"SELECT title FROM {kind}s WHERE id = ?", (int(spec.get("id") or 0),)).fetchone()
        if not row:
            raise ValueError(f"Unknown {kind}")
        where, args, label = f"{VISIBLE} AND a.{kind}_id = ?", [int(spec["id"])], row["title"]
    else:
        raise ValueError("Pick a person, year, month, day, trip or event for each side.")
    count = conn.execute(f"SELECT COUNT(*) FROM assets a WHERE {where}", args).fetchone()[0]
    places = [r[0] for r in conn.execute(
        f"SELECT a.city FROM assets a WHERE {where} AND a.city IS NOT NULL GROUP BY a.city ORDER BY COUNT(*) DESC LIMIT 4", args)]
    people = []
    try:
        people = [r[0] for r in conn.execute(
            f"""SELECT p.name FROM faces f JOIN persons p ON p.id = f.person_id JOIN assets a ON a.id = f.asset_id
                WHERE p.name IS NOT NULL AND p.hidden = 0 AND {where} GROUP BY p.id ORDER BY COUNT(DISTINCT a.id) DESC LIMIT 4""", args)]
    except sqlite3.OperationalError:
        pass
    span = conn.execute(f"SELECT MIN(a.taken_at), MAX(a.taken_at) FROM assets a WHERE {where}", args).fetchone()
    best = browse.by_ids(conn, quality.best_of(conn, where, args, n))
    return {"spec": spec, "label": label, "count": count, "places": places, "people": people,
            "first": (span[0] or "")[:10], "last": (span[1] or "")[:10], "best": best}


def compare(conn: sqlite3.Connection, a: dict, b: dict, n: int = 12) -> dict:
    left, right = side(conn, a, n), side(conn, b, n)
    pairs = [{"a": x, "b": y} for x, y in zip(left["best"], right["best"])]
    return {"a": left, "b": right, "pairs": pairs}
