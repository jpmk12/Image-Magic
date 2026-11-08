"""The flying log: aircraft photos by base, sortie (day) and tail number.

Built from the AI captions, keywords and readable text, plus any photo you
tagged with an aircraft keyword yourself. Sharing from here uses the strict
filter: readable text, tail numbers, badges, documents and places are never
exported, and captions are left off the page.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict

from . import browse, quality

AIRCRAFT_TERMS = [
    "c-17", "c17", "globemaster", "aircraft", "airplane", "aeroplane", "jet", "cockpit", "flight line", "flightline",
    "runway", "hangar", "tarmac", "helicopter", "flight deck", "airlifter", "cargo plane", "turboprop", "propeller",
    "fuselage", "airfield", "air base", "airbase", "afb", "sortie", "flying", "aviation", "pilot", "aircrew",
    "flight suit", "kc-135", "kc-46", "c-130", "c-5", "f-16", "f-35", "f-22", "a-10", "b-52", "b-1", "e-3", "osprey", "v-22",
]
TAIL_RE = re.compile(r"\b(\d{2}-\d{4}|\d{5})\b")      # USAF tail numbers: 07-7171 or 77171

SCHEMA = """
CREATE TABLE IF NOT EXISTS flying_photos (
    asset_id  INTEGER PRIMARY KEY,
    tails     TEXT NOT NULL DEFAULT '[]'
);
"""
VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0"


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


def _matches(text: str) -> bool:
    t = text.lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", t) for term in AIRCRAFT_TERMS)


def refresh(conn: sqlite3.Connection) -> int:
    """Rebuild the set of aircraft photos from the descriptions. Returns the count."""
    ensure_schema(conn)
    conn.execute("DELETE FROM flying_photos")
    n = 0
    try:
        rows = conn.execute("SELECT asset_id, caption, keywords, text FROM descriptions WHERE status = 'ok'").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for r in rows:
        kws = json.loads(r["keywords"] or "[]")
        if not _matches(" ".join([r["caption"] or "", " ".join(kws)])):
            continue
        tails = sorted(set(TAIL_RE.findall(r["text"] or "")))
        conn.execute("INSERT OR REPLACE INTO flying_photos (asset_id, tails) VALUES (?, ?)", (r["asset_id"], json.dumps(tails)))
        n += 1
    conn.commit()
    return n


def refresh_if_stale(conn: sqlite3.Connection) -> None:
    """Rebuild the aircraft set only when the descriptions changed since the last build."""
    from . import catalog
    ensure_schema(conn)
    try:
        stamp = str(conn.execute("SELECT COUNT(*), MAX(created) FROM descriptions WHERE status = 'ok'").fetchone()[:])
    except sqlite3.OperationalError:
        stamp = "none"
    if catalog.get_meta(conn, "flying_stamp") != stamp or not conn.execute("SELECT 1 FROM flying_photos LIMIT 1").fetchone():
        refresh(conn)
        catalog.set_meta(conn, "flying_stamp", stamp)
        conn.commit()


def log(conn: sqlite3.Connection) -> dict:
    refresh_if_stale(conn)
    rows = conn.execute(
        f"""SELECT a.id, a.taken_at, a.city, a.country, a.event_id, f.tails, {quality.RANK} AS rank
            FROM flying_photos f JOIN assets a ON a.id = f.asset_id WHERE {VISIBLE} AND a.taken_at IS NOT NULL
            ORDER BY a.taken_at""").fetchall()
    has_desc = bool(conn.execute("SELECT 1 FROM descriptions WHERE status = 'ok' LIMIT 1").fetchone()) \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'descriptions'").fetchone() else False
    bases: dict[str, list] = defaultdict(list)
    days: dict[tuple[str, str], list] = defaultdict(list)
    tails: dict[str, list] = defaultdict(list)
    for r in rows:
        place = ", ".join(x for x in [r["city"], r["country"]] if x) or "Unknown place"
        bases[place].append(r)
        days[(r["taken_at"][:10], place)].append(r)
        for t in json.loads(r["tails"] or "[]"):
            tails[t].append(r)
    cover = lambda rs: max(rs, key=lambda r: r["rank"])["id"]  # noqa: E731
    out_bases = sorted([{"place": p, "photos": len(rs), "days": len({r["taken_at"][:10] for r in rs}),
                         "first": rs[0]["taken_at"][:10], "last": rs[-1]["taken_at"][:10], "cover_id": cover(rs)}
                        for p, rs in bases.items()], key=lambda b: -b["photos"])
    out_days = [{"day": d, "place": p, "photos": len(rs), "cover_id": cover(rs), "event_id": Counter(r["event_id"] for r in rs if r["event_id"]).most_common(1)[0][0] if any(r["event_id"] for r in rs) else None,
                 "tails": sorted({t for r in rs for t in json.loads(r["tails"] or "[]")})}
                for (d, p), rs in sorted(days.items(), reverse=True)]
    out_tails = sorted([{"tail": t, "photos": len(rs), "first": rs[0]["taken_at"][:10], "last": rs[-1]["taken_at"][:10], "cover_id": cover(rs)}
                        for t, rs in tails.items()], key=lambda x: -x["photos"])
    return {"total": len(rows), "has_descriptions": has_desc, "bases": out_bases, "sorties": out_days[:200], "tails": out_tails,
            "best": browse.by_ids(conn, quality.best_of(conn, f"{VISIBLE} AND a.id IN (SELECT asset_id FROM flying_photos)", [], 12))}
