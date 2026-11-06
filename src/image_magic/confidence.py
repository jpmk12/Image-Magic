"""How sure the app is about its automatic decisions, and a review queue.

Three kinds of decision get a confidence from 0 to 1:

* trip membership: how far a photo is from the trip's centre compared with the
  rest of the trip (photos joined by date alone, with no location, sit at 0.5)
* face groups: how close a face's signature is to its person's centre
* chapters: how clearly the home won the vote in the chapter's first months

The review queue lists the ten least-certain decisions you haven't looked at.
Confirming locks the decision (it survives rebuilds); dismissing undoes it the
same way the viewer's corrections do. Either way it leaves the queue.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import numpy as np

from . import browse
from .places import haversine_km

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    key      TEXT PRIMARY KEY,     -- trip:<asset> | face:<face> | chapter:<id>
    decision TEXT NOT NULL,        -- confirmed | dismissed
    created  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS face_centres (
    person_id INTEGER PRIMARY KEY,
    vec       BLOB NOT NULL        -- 512 x float32, normalised
);
"""
QUEUE_SIZE = 10


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


# ---- trips ----------------------------------------------------------------------

def trip_confidences(conn: sqlite3.Connection, trip_id: int) -> dict[int, float]:
    """asset id -> confidence for every automatically assigned photo of a trip."""
    t = conn.execute("SELECT lat, lon FROM trips WHERE id = ?", (trip_id,)).fetchone()
    rows = conn.execute("SELECT id, lat, lon, trip_manual FROM assets WHERE trip_id = ? AND missing = 0", (trip_id,)).fetchall()
    if not t or not rows:
        return {}
    clat, clon = t["lat"], t["lon"]
    if clat is None:     # a trip you created by hand has no centre yet: use its located photos
        pts = [(r["lat"], r["lon"]) for r in rows if r["lat"] is not None]
        if not pts:
            return {r["id"]: 1.0 if r["trip_manual"] else 0.5 for r in rows}
        clat, clon = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    dists = {r["id"]: haversine_km(r["lat"], r["lon"], clat, clon) for r in rows if r["lat"] is not None}
    med = float(np.median(list(dists.values()))) if dists else 0.0
    scale = 2 * med + 5
    out = {}
    for r in rows:
        if r["trip_manual"]:
            out[r["id"]] = 1.0
        elif r["lat"] is None:
            out[r["id"]] = 0.5
        else:
            ratio = dists[r["id"]] / scale
            out[r["id"]] = 0.95 if ratio <= 1 else max(0.2, 0.95 - (ratio - 1) * 0.3)
    return out


def trip_confidence(conn: sqlite3.Connection, asset_id: int) -> float | None:
    r = conn.execute("SELECT trip_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not r or r["trip_id"] is None:
        return None
    return trip_confidences(conn, r["trip_id"]).get(asset_id)


# ---- faces ----------------------------------------------------------------------

def refresh_face_centres(conn: sqlite3.Connection) -> int:
    ensure_schema(conn)
    conn.execute("DELETE FROM face_centres")
    sums: dict[int, np.ndarray] = {}
    for r in conn.execute("SELECT person_id, vec, locked FROM faces WHERE person_id IS NOT NULL AND vec IS NOT NULL"):
        v = np.frombuffer(r["vec"], dtype=np.float16).astype(np.float32) * (2.0 if r["locked"] else 1.0)
        sums[r["person_id"]] = sums.get(r["person_id"], 0) + v
    for pid, v in sums.items():
        c = v / max(float(np.linalg.norm(v)), 1e-8)
        conn.execute("INSERT INTO face_centres (person_id, vec) VALUES (?, ?)", (pid, c.astype(np.float32).tobytes()))
    conn.commit()
    return len(sums)


def _sim_to_conf(sim: float) -> float:
    return float(min(1.0, max(0.0, 0.5 + (sim - 0.45) * 2.5)))


def face_confidences(conn: sqlite3.Connection, asset_id: int | None = None) -> dict[int, float]:
    """face id -> confidence for unlocked faces (of one photo, or all)."""
    ensure_schema(conn)
    centres = {r["person_id"]: np.frombuffer(r["vec"], dtype=np.float32) for r in conn.execute("SELECT person_id, vec FROM face_centres")}
    if not centres:
        return {}
    where, args = ("AND asset_id = ?", (asset_id,)) if asset_id else ("", ())
    out = {}
    for r in conn.execute(f"SELECT id, vec, person_id, locked FROM faces WHERE person_id IS NOT NULL {where}", args):
        if r["locked"]:
            out[r["id"]] = 1.0
        elif r["vec"] is not None and r["person_id"] in centres:
            v = np.frombuffer(r["vec"], dtype=np.float16).astype(np.float32)
            out[r["id"]] = _sim_to_conf(float(centres[r["person_id"]] @ v))
    return out


# ---- chapters -------------------------------------------------------------------

def chapter_confidence(conn: sqlite3.Connection, chapter_id: int) -> float | None:
    ch = conn.execute("SELECT place_key, start_at, auto FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
    if not ch:
        return None
    if not ch["auto"]:
        return 1.0
    from . import chapters
    rows = conn.execute(
        f"""SELECT lat, lon, city, region, country, country_code FROM assets WHERE {chapters.VISIBLE} AND trip_id IS NULL
            AND lat IS NOT NULL AND taken_at >= ? AND taken_at < date(?, '+4 months')""", (ch["start_at"], ch["start_at"])).fetchall()
    if not rows:
        return 0.5
    home = sum(1 for r in rows if chapters._place_key(r) == ch["place_key"])
    return round(0.3 + 0.7 * home / len(rows), 2)


# ---- the queue ------------------------------------------------------------------

def _reviewed(conn: sqlite3.Connection) -> set[str]:
    ensure_schema(conn)
    return {r[0] for r in conn.execute("SELECT key FROM reviews")}


def queue(conn: sqlite3.Connection, n: int = QUEUE_SIZE) -> dict:
    """The least-certain decisions not yet reviewed, plus counts."""
    done = _reviewed(conn)
    items: list[dict] = []
    for t in conn.execute("SELECT id, title FROM trips").fetchall():
        for aid, c in trip_confidences(conn, t["id"]).items():
            if c < 0.9 and f"trip:{aid}" not in done:
                items.append({"key": f"trip:{aid}", "kind": "trip", "confidence": round(c, 2), "asset_id": aid, "trip_id": t["id"],
                              "title": f"Is this photo from the {t['title']} trip?",
                              "detail": "It has no location, so it joined by date." if c == 0.5 else "It was taken far from the rest of the trip."})
    face_rows = {r["id"]: r for r in conn.execute(
        "SELECT f.id, f.asset_id, f.person_id, p.name FROM faces f JOIN persons p ON p.id = f.person_id WHERE p.hidden = 0")}
    for fid, c in face_confidences(conn).items():
        if c < 0.7 and f"face:{fid}" not in done and fid in face_rows:
            r = face_rows[fid]
            items.append({"key": f"face:{fid}", "kind": "face", "confidence": round(c, 2), "asset_id": r["asset_id"], "face_id": fid,
                          "person_id": r["person_id"], "title": f"Is this {r['name'] or 'the same unnamed person'}?",
                          "detail": "The face only loosely matches the group."})
    for ch in conn.execute("SELECT id, title, start_at, cover_id FROM chapters WHERE auto = 1").fetchall():
        c = chapter_confidence(conn, ch["id"])
        if c is not None and c < 0.8 and f"chapter:{ch['id']}" not in done:
            items.append({"key": f"chapter:{ch['id']}", "kind": "chapter", "confidence": round(c, 2), "asset_id": ch["cover_id"],
                          "chapter_id": ch["id"], "title": f"Did a new chapter start in {ch['start_at'][:7]}: {ch['title']}?",
                          "detail": "Photos from those months are spread over several places."})
    for m in conn.execute("SELECT id, title, date, asset_id FROM milestones WHERE state = 'new' ORDER BY date DESC LIMIT 30").fetchall():
        if f"milestone:{m['id']}" not in done:
            items.append({"key": f"milestone:{m['id']}", "kind": "milestone", "confidence": 0.6, "asset_id": m["asset_id"],
                          "milestone_id": m["id"], "title": f"Keep the milestone \"{m['title']}\" ({m['date']})?", "detail": "Found automatically."})
    items.sort(key=lambda i: (i["confidence"], i["key"]))
    reviewed = conn.execute("SELECT COUNT(*), SUM(created >= date('now', '-7 days')) FROM reviews").fetchone()
    return {"items": items[:n], "pending": len(items), "reviewed": reviewed[0] or 0, "reviewed_week": reviewed[1] or 0}


def decide(conn: sqlite3.Connection, key: str, decision: str) -> None:
    """Confirm (lock) or dismiss (undo) one decision; remember it either way."""
    ensure_schema(conn)
    if decision not in ("confirmed", "dismissed"):
        raise ValueError("decision must be confirmed or dismissed")
    kind, _, ident = key.partition(":")
    ident = int(ident)
    if kind == "trip":
        if decision == "confirmed":
            conn.execute("UPDATE assets SET trip_manual = 1 WHERE id = ?", (ident,))
        else:
            from . import curate
            curate.set_trip(conn, [ident], None)
    elif kind == "face":
        from . import faces
        if decision == "confirmed":
            conn.execute("UPDATE faces SET locked = 1 WHERE id = ?", (ident,))
        else:
            faces.move_face(conn, ident, None)
    elif kind == "chapter":
        if decision == "confirmed":
            conn.execute("UPDATE chapters SET auto = 0 WHERE id = ?", (ident,))
        # dismissing a chapter boundary is an edit (dates/title) you make on the Timeline; here it just leaves the queue
    elif kind == "milestone":
        from . import chapters
        chapters.set_milestone_state(conn, ident, "kept" if decision == "confirmed" else "dismissed")
    else:
        raise ValueError("unknown decision")
    conn.execute("INSERT OR REPLACE INTO reviews (key, decision, created) VALUES (?, ?, ?)",
                 (key, decision, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


def photo_confidence(conn: sqlite3.Connection, asset_id: int) -> dict:
    """What the viewer shows: trip confidence and per-face confidences for one photo."""
    return {"trip": trip_confidence(conn, asset_id), "faces": face_confidences(conn, asset_id)}
