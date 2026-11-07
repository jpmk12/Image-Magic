"""People over time: who was in your life when, and who appears together."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0"


def overview(conn: sqlite3.Connection) -> dict:
    """Per named person: photos per year; and pairs who appear in the same photos."""
    try:
        rows = conn.execute(
            f"""SELECT f.person_id AS pid, p.name, substr(a.taken_at, 1, 4) AS y, COUNT(DISTINCT a.id) AS n
                FROM faces f JOIN persons p ON p.id = f.person_id JOIN assets a ON a.id = f.asset_id
                WHERE p.name IS NOT NULL AND p.hidden = 0 AND {VISIBLE} AND a.taken_at IS NOT NULL
                GROUP BY f.person_id, y ORDER BY p.name, y""").fetchall()
    except sqlite3.OperationalError:
        return {"years": [], "people": [], "pairs": []}
    people: dict[int, dict] = {}
    years: set[str] = set()
    for r in rows:
        d = people.setdefault(r["pid"], {"id": r["pid"], "name": r["name"], "years": {}, "total": 0})
        d["years"][r["y"]] = r["n"]
        d["total"] += r["n"]
        years.add(r["y"])
    for d in people.values():
        ys = sorted(d["years"])
        d["first"], d["last"] = ys[0], ys[-1]
    pairs = together(conn)
    return {"years": sorted(years), "people": sorted(people.values(), key=lambda d: (d["first"], -d["total"])), "pairs": pairs}


def together(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    """Pairs of named people with how many photos they share and the first one."""
    try:
        rows = conn.execute(
            f"""SELECT f1.person_id AS a, f2.person_id AS b, p1.name AS an, p2.name AS bn, COUNT(DISTINCT x.id) AS n,
                       MIN(x.taken_at) AS first_at
                FROM faces f1 JOIN faces f2 ON f1.asset_id = f2.asset_id AND f1.person_id < f2.person_id
                JOIN persons p1 ON p1.id = f1.person_id JOIN persons p2 ON p2.id = f2.person_id
                JOIN assets x ON x.id = f1.asset_id
                WHERE p1.name IS NOT NULL AND p2.name IS NOT NULL AND p1.hidden = 0 AND p2.hidden = 0
                  AND x.missing = 0 AND x.duplicate_of IS NULL AND x.kind = 'photo' AND x.private = 0
                GROUP BY f1.person_id, f2.person_id ORDER BY n DESC LIMIT ?""", (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        first = conn.execute(
            """SELECT x.id FROM faces f1 JOIN faces f2 ON f1.asset_id = f2.asset_id JOIN assets x ON x.id = f1.asset_id
               WHERE f1.person_id = ? AND f2.person_id = ? AND x.missing = 0 AND x.private = 0 ORDER BY x.taken_at LIMIT 1""",
            (r["a"], r["b"])).fetchone()
        out.append({"a": r["a"], "b": r["b"], "names": [r["an"], r["bn"]], "photos": r["n"],
                    "first_at": (r["first_at"] or "")[:10], "first_id": first[0] if first else None})
    return out


def together_milestones(conn: sqlite3.Connection) -> list[dict]:
    """'First photo of A and B together' for pairs that share at least 3 photos."""
    return [{"key": f"together:{p['a']}-{p['b']}", "date": p["first_at"], "kind": "together",
             "title": f"First photo of {p['names'][0]} and {p['names'][1]} together", "asset_id": p["first_id"],
             "person_id": p["a"]} for p in together(conn, 50) if p["photos"] >= 3 and p["first_at"]]
