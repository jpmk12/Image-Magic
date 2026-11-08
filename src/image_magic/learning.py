"""Learning from your decisions.

Every curation decision you make (accept or skip an inbox card, confirm or dismiss a review item,
merge or dissolve a trip, merge two people) is logged here, and `learn()` turns the log into a few
tuned numbers the automatic passes read instead of their built-in defaults:

    trip.gap_days      how many quiet days split two trips (places.rebuild_trips)
    trip.min_photos    the fewest photos that still make a trip
    face.join          how close a face must be to a person's centre to join them
    auto.<kind>.min    how confident an inbox card must be before it is applied on its own
    calib.<kind>       how often cards of that kind were accepted, to temper the shown confidence

Each number moves only within a safe range, only once there is enough evidence, and the Settings
tab shows what was learned and lets you forget it. Nothing here leaves the laptop.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_log (
    id      INTEGER PRIMARY KEY,
    kind    TEXT NOT NULL,          -- inbox:<kind> | review:<kind> | trip_merge | trip_delete | people_merge
    outcome TEXT NOT NULL,          -- accepted | skipped | confirmed | dismissed | done
    value   REAL,                   -- the confidence, gap in days, photo count or similarity the decision was about
    detail  TEXT,
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_kind ON decision_log(kind);
CREATE TABLE IF NOT EXISTS tuning (
    key      TEXT PRIMARY KEY,
    value    REAL NOT NULL,
    evidence INTEGER NOT NULL,
    note     TEXT,
    updated  TEXT NOT NULL
);
"""

DEFAULTS = {"trip.gap_days": 3.0, "trip.min_photos": 5.0, "face.join": 0.45}
RANGES = {"trip.gap_days": (3.0, 8.0), "trip.min_photos": (3.0, 8.0), "face.join": (0.40, 0.55)}
AUTO_DEFAULT = 0.9
MIN_EVIDENCE = 5          # decisions of one sort before anything moves


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(conn: sqlite3.Connection, kind: str, outcome: str, value: float | None = None, detail: dict | None = None) -> None:
    """Remember one decision. Never raises: a logging hiccup must not undo the decision itself."""
    try:
        ensure_schema(conn)
        conn.execute("INSERT INTO decision_log (kind, outcome, value, detail, created) VALUES (?, ?, ?, ?, ?)",
                     (kind, outcome, value, json.dumps(detail) if detail else None, _now()))
    except sqlite3.Error:
        pass


def get(conn: sqlite3.Connection, key: str, default: float | None = None) -> float:
    try:
        row = conn.execute("SELECT value FROM tuning WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        row = None
    if row is not None:
        return float(row[0])
    if default is not None:
        return default
    if key.startswith("auto."):
        return AUTO_DEFAULT
    return DEFAULTS.get(key, 0.0)


def face_similarity(conn: sqlite3.Connection, face_id: int) -> float | None:
    """How close a face is to its person's centre (the number the join threshold is compared with)."""
    import numpy as np
    row = conn.execute("""SELECT f.vec, c.vec AS centre FROM faces f JOIN face_centres c ON c.person_id = f.person_id
                          WHERE f.id = ? AND f.vec IS NOT NULL""", (face_id,)).fetchone()
    if not row:
        return None
    v = np.frombuffer(row["vec"], dtype=np.float16).astype(np.float32)
    c = np.frombuffer(row["centre"], dtype=np.float32)
    return float(v @ c)


def people_similarity(conn: sqlite3.Connection, a: int, b: int) -> float | None:
    import numpy as np
    rows = {r["person_id"]: np.frombuffer(r["vec"], dtype=np.float32) for r in conn.execute(
        "SELECT person_id, vec FROM face_centres WHERE person_id IN (?, ?)", (a, b))}
    if a in rows and b in rows:
        return float(rows[a] @ rows[b])
    return None


def _clamp(key: str, v: float) -> float:
    lo, hi = RANGES[key]
    return max(lo, min(hi, v))


def _vals(conn, kind: str, outcome: str | None = None) -> list[float]:
    q = "SELECT value FROM decision_log WHERE kind = ? AND value IS NOT NULL" + (" AND outcome = ?" if outcome else "")
    return [float(r[0]) for r in conn.execute(q, (kind, outcome) if outcome else (kind,))]


def _q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def learn(conn: sqlite3.Connection) -> dict[str, dict]:
    """Recompute every tuned number from the log. Cheap; runs after each catalog update and each decision."""
    ensure_schema(conn)
    out: dict[str, tuple[float, int, str]] = {}

    # Trips you merged by hand were split by a gap the rule thought too long: let the rule allow that gap.
    gaps = [g for g in _vals(conn, "trip_merge") if 0 < g <= 14]
    if len(gaps) >= 3:
        v = _clamp("trip.gap_days", max(DEFAULTS["trip.gap_days"], _q(gaps, 0.75) + 0.5))
        out["trip.gap_days"] = (round(v, 1), len(gaps), f"You merged {len(gaps)} pairs of trips; up to {v:g} quiet days now stay one trip.")
    # Trips you dissolved ("not a trip") tell how small a group is too small.
    sizes = _vals(conn, "trip_delete")
    if len(sizes) >= MIN_EVIDENCE and _q(sizes, 0.75) <= 12:        # raising this removes small automatic trips, so it needs more evidence
        v = _clamp("trip.min_photos", max(DEFAULTS["trip.min_photos"], float(int(statistics.median(sizes)) + 1)))
        out["trip.min_photos"] = (v, len(sizes), f"You dissolved {len(sizes)} small trips; a trip now needs at least {int(v)} photos.")
    # People you merged were further apart than the join rule allows; faces you dismissed were closer than it should trust.
    merged = [s for s in _vals(conn, "people_merge") if s > 0]
    dismissed = _vals(conn, "review:face", "dismissed")
    confirmed = _vals(conn, "review:face", "confirmed")
    if len(merged) >= MIN_EVIDENCE or len(dismissed) >= MIN_EVIDENCE:
        v = DEFAULTS["face.join"]
        if len(merged) >= MIN_EVIDENCE:
            v = min(v, _q(merged, 0.25) - 0.01)
        if len(dismissed) >= MIN_EVIDENCE:
            v = max(v, _q(dismissed, 0.75) + 0.01)
        if len(confirmed) >= MIN_EVIDENCE:
            v = min(v, _q(confirmed, 0.25))          # faces you confirmed at this distance are fine
        v = _clamp("face.join", v)
        if abs(v - DEFAULTS["face.join"]) >= 0.005:
            parts = []
            if len(merged) >= MIN_EVIDENCE:
                parts.append(f"{len(merged)} people merged")
            if len(dismissed) >= MIN_EVIDENCE:
                parts.append(f"{len(dismissed)} faces dismissed")
            out["face.join"] = (round(v, 3), len(merged) + len(dismissed), f"Faces join a person at similarity {v:.2f} ({', '.join(parts)}).")
    # Inbox: per kind, how often you agree, at what confidence.
    for (kind,) in conn.execute("SELECT DISTINCT substr(kind, 7) FROM decision_log WHERE kind LIKE 'inbox:%'").fetchall():
        acc = _vals(conn, f"inbox:{kind}", "accepted")
        skp = _vals(conn, f"inbox:{kind}", "skipped")
        n = len(acc) + len(skp)
        if n < MIN_EVIDENCE:
            continue
        rate = len(acc) / n
        out[f"calib.{kind}"] = (round(rate, 2), n, f"You accepted {len(acc)} of {n} {kind} suggestions.")
        if kind in ("location", "clutter"):
            if n >= 10 and rate >= 0.95 and (not skp or min(acc) >= 0.8):
                v, why = 0.85, "they are applied on their own from 85% confidence"
            elif rate < 0.7:
                v, why = 0.97, "they wait for you unless the app is almost certain"
            else:
                v, why = AUTO_DEFAULT, "applied on their own from 90% confidence, as usual"
            out[f"auto.{kind}.min"] = (v, n, f"{kind.capitalize()} suggestions: {why}.")

    conn.execute("DELETE FROM tuning")
    conn.executemany("INSERT INTO tuning (key, value, evidence, note, updated) VALUES (?, ?, ?, ?, ?)",
                     [(k, v, n, note, _now()) for k, (v, n, note) in out.items()])
    conn.commit()
    return {k: {"value": v, "evidence": n, "note": note} for k, (v, n, note) in out.items()}


def shown_confidence(conn: sqlite3.Connection, kind: str, conf: float) -> float:
    """Temper a card's confidence by how often you agreed with that kind before."""
    try:
        row = conn.execute("SELECT value, evidence FROM tuning WHERE key = ?", (f"calib.{kind}",)).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return conf
    rate = float(row[0])
    w = min(0.5, row[1] / 40)                       # up to half weight once there are 20 decisions
    return round(max(0.3, min(0.99, conf * (1 - w) + rate * w)), 2)


def status(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    total = conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]
    learned = [dict(r) for r in conn.execute("SELECT key, value, evidence, note, updated FROM tuning ORDER BY key")]
    return {"decisions": total, "learned": learned}


def forget(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)
    conn.execute("DELETE FROM decision_log")
    conn.execute("DELETE FROM tuning")
    conn.commit()
