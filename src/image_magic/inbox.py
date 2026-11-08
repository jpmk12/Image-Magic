"""The curator inbox: the app proposes, you accept in one click.

Every suggestion is a card with a confidence, the photos it concerns, what
accepting does and why the app thinks so. Decisions are remembered by a stable
key so a skipped card stays skipped until the facts change. With auto-apply on,
locations above AUTO_MIN and byte-identical copies are applied after each sync
and listed in the Activity log; trips, names, blurry and blank frames always
wait for you.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

AUTO_MIN = 0.9
AUTO_KINDS = ("location", "clutter")

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_decisions (
    key      TEXT PRIMARY KEY,
    decision TEXT NOT NULL,          -- accepted | skipped | auto
    undo     TEXT,                   -- JSON needed to undo, NULL once undone or impossible
    title    TEXT,
    created  TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


def _key(kind: str, ids: list[int], extra: str = "") -> str:
    h = hashlib.sha1((",".join(map(str, sorted(ids))) + "|" + extra).encode()).hexdigest()[:12]
    return f"{kind}:{h}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- suggestion sources ---------------------------------------------------------

def _trip_additions(conn, db_key) -> list[dict]:
    from . import curate
    out = []
    for t in conn.execute("SELECT id, title FROM trips ORDER BY start_at DESC LIMIT 40").fetchall():
        try:
            s = curate.suggest_for_trip(conn, db_key, t["id"])
        except Exception:
            continue
        strong = [i for i in s["items"] if i["strong"]]
        if len(strong) < 3:
            continue
        during = sum(1 for i in strong if any("during" in r for r in i["reasons"]))
        conf = 0.92 if during == len(strong) else 0.84 if during >= len(strong) / 2 else 0.74
        ids = [i["id"] for i in strong]
        reasons = sorted({r.split(",")[0] for i in strong for r in i["reasons"][:1]})[:3]
        out.append({"key": _key("trip", ids, str(t["id"])), "kind": "trip", "conf": conf, "ids": ids, "trip_id": t["id"],
                    "title": f"{len(ids)} photos look like part of {t['title']}", "why": "; ".join(reasons).capitalize() + ".",
                    "yes": f"Add to {t['title']}", "alt": "Pick which…", "action": {"do": "trip_add", "trip": t["id"], "ids": ids}})
    return out


def _face_names(conn) -> list[dict]:
    out = []
    try:
        centres = {r["person_id"]: np.frombuffer(r["vec"], dtype=np.float32) for r in conn.execute("SELECT person_id, vec FROM face_centres")}
        named = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM persons WHERE name IS NOT NULL AND hidden = 0")}
        unnamed = conn.execute(
            """SELECT p.id, COUNT(DISTINCT f.asset_id) AS n FROM persons p JOIN faces f ON f.person_id = p.id
               WHERE p.name IS NULL AND p.hidden = 0 GROUP BY p.id HAVING n >= 10 ORDER BY n DESC LIMIT 20""").fetchall()
    except sqlite3.OperationalError:
        return out
    for u in unnamed:
        rows = conn.execute("SELECT id, asset_id FROM faces WHERE person_id = ? ORDER BY score * MIN(size_px, 160) DESC LIMIT 400", (u["id"],)).fetchall()
        ids = sorted({r["asset_id"] for r in rows})
        face_ids = [r["id"] for r in rows]
        best, sim = None, 0.0
        if u["id"] in centres:
            for pid, name in named.items():
                if pid in centres:
                    s_ = float(centres[pid] @ centres[u["id"]])
                    if s_ > sim:
                        best, sim = pid, s_
        if best is not None and sim >= 0.5:
            conf = round(min(0.95, 0.5 + (sim - 0.5) * 2.2), 2)
            out.append({"key": _key("face", ids, f"{u['id']}->{best}"), "kind": "people", "conf": conf, "ids": ids, "person_id": u["id"], "faces": face_ids, "into": best,
                        "title": f"This face group ({u['n']} photos) is probably {named[best]}", "why": f"Its signature is close to {named[best]}'s ({int(sim * 100)}%).",
                        "yes": f"It's {named[best]}", "alt": "Someone else…", "action": {"do": "face_merge", "from": u["id"], "into": best}})
        elif u["n"] >= 25:
            out.append({"key": _key("face", ids, str(u["id"])), "kind": "people", "conf": 0.5, "ids": ids, "person_id": u["id"], "faces": face_ids,
                        "title": f"An unnamed person appears in {u['n']} photos", "why": "Naming them unlocks their story, searches and milestones.",
                        "yes": "Name them…", "alt": "Hide this group", "action": {"do": "face_name", "person": u["id"]}})
    return out


def _locations(conn) -> list[dict]:
    from . import locations
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM assets WHERE missing = 0 AND kind != 'live_video' AND lat IS NULL AND taken_at IS NOT NULL AND trashed_at IS NULL "
        "ORDER BY taken_at DESC LIMIT 1500")]
    if not ids:
        return []
    groups: dict[str, list] = defaultdict(list)
    for s_ in locations.nearby_suggestions(conn, ids, 120):
        if s_["lat"] is not None:
            groups[s_["place"] or "an unnamed spot"].append(s_)
    out = []
    for place, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(items) < 3:
            continue
        gaps = [i["minutes"] for i in items]
        avg = sum(gaps) / len(gaps)
        conf = 0.93 if avg <= 30 else 0.86 if avg <= 60 else 0.76
        pids = [i["id"] for i in items]
        out.append({"key": _key("loc", pids, place), "kind": "location", "conf": conf, "ids": pids,
                    "title": f"{len(pids)} photos have no location; nearby photos say {place}",
                    "why": f"Each was taken within {max(gaps)} minutes of a located photo there (average {round(avg)} min).",
                    "yes": f"Fill in {place.split(',')[0]}", "alt": "Choose place…",
                    "action": {"do": "location", "ids": pids, "points": [[i["id"], i["lat"], i["lon"]] for i in items]}})
    return out[:6]


def _trip_merges(conn) -> list[dict]:
    from datetime import date
    trips = conn.execute("SELECT id, title, start_at, end_at, photo_count, lat, lon FROM trips ORDER BY start_at").fetchall()
    out = []
    for a, b in zip(trips, trips[1:]):
        try:
            gap = (date.fromisoformat(b["start_at"][:10]) - date.fromisoformat(a["end_at"][:10])).days
        except ValueError:
            continue
        same_place = a["lat"] is not None and b["lat"] is not None and abs(a["lat"] - b["lat"]) < 2 and abs(a["lon"] - b["lon"]) < 2
        similar_title = a["title"].split(" (")[0].lower() == b["title"].split(" (")[0].lower()
        if 0 <= gap <= 3 and (same_place or similar_title):
            ga = [r[0] for r in conn.execute("SELECT id FROM assets WHERE trip_id = ? ORDER BY taken_at LIMIT 200", (a["id"],))]
            gb = [r[0] for r in conn.execute("SELECT id FROM assets WHERE trip_id = ? ORDER BY taken_at LIMIT 200", (b["id"],))]
            ids = ga + gb
            out.append({"key": _key("merge", [a["id"], b["id"]]), "kind": "trip", "conf": 0.8 if same_place and similar_title else 0.7, "ids": ids,
                        "groups": [{"title": a["title"], "ids": ga}, {"title": b["title"], "ids": gb}],
                        "title": f"\"{a['title']}\" and \"{b['title']}\" look like one trip",
                        "why": f"{gap} day{'s' if gap != 1 else ''} apart" + (", same area" if same_place else "") + ". A quiet day without photos may have split them.",
                        "yes": "Merge", "alt": "Keep apart", "action": {"do": "trip_merge", "from": b["id"], "into": a["id"]}})
    return out


def _clutter(conn, library, originals) -> list[dict]:
    from . import cleanup
    c = cleanup.candidates(conn, library, originals)
    ids = [i["id"] for k in ("blurry", "dark", "duplicates") for i in c["groups"][k]]
    if len(ids) < 5:
        return []
    dupes = [i["id"] for i in c["groups"]["duplicates"]]
    parts = [f"{c['counts'][k]} {lbl}" for k, lbl in (("blurry", "blurry"), ("dark", "blank or dark"), ("duplicates", "exact copies")) if c["counts"][k]]
    return [{"key": _key("clutter", ids), "kind": "clutter", "conf": 0.95, "ids": ids,
             "title": f"{len(ids)} photos look like clutter", "why": ", ".join(parts) + ". They go to the app's trash, restorable; iCloud is untouched.",
             "yes": "Move to trash", "alt": "Show me", "action": {"do": "trash", "ids": ids, "auto_ids": dupes}}]


def suggestions(conn, db_key: str, library, originals) -> list[dict]:
    ensure_schema(conn)
    decided = {r[0] for r in conn.execute("SELECT key FROM inbox_decisions")}
    cards = _trip_additions(conn, db_key) + _face_names(conn) + _locations(conn) + _trip_merges(conn) + _clutter(conn, library, originals)
    out = []
    for c in cards:
        if c["key"] in decided:
            continue
        c["covers"] = c["ids"][:3]
        c["count"] = len(c["ids"])
        out.append(c)
    out.sort(key=lambda c: -c["conf"])
    return out


def overview(conn, db_key, library, originals) -> dict:
    cards = suggestions(conn, db_key, library, originals)
    counts: dict[str, int] = defaultdict(int)
    for c in cards:
        counts[c["kind"]] += 1
    week = conn.execute("SELECT COUNT(*) FROM inbox_decisions WHERE created >= datetime('now', '-7 days')").fetchone()[0]
    last = conn.execute("SELECT key, decision, title, undo FROM inbox_decisions ORDER BY created DESC LIMIT 1").fetchone()
    auto = [dict(r) for r in conn.execute("SELECT title, created FROM inbox_decisions WHERE decision = 'auto' ORDER BY created DESC LIMIT 5")]
    return {"cards": cards, "pending": len(cards), "counts": dict(counts), "high": sum(1 for c in cards if c["conf"] >= AUTO_MIN),
            "decided_week": week, "last": {"title": last["title"], "decision": last["decision"], "undoable": bool(last["undo"])} if last else None,
            "auto_recent": auto}


# ---- acting -----------------------------------------------------------------------

def restrict(card: dict, only: list[int] | None) -> dict:
    """The same card limited to the photos you left ticked (where the action is per photo)."""
    if not only:
        return card
    keep = {int(i) for i in only}
    a = dict(card.get("action") or {})
    if "ids" in a:
        a["ids"] = [i for i in a["ids"] if int(i) in keep]
    if "points" in a:
        a["points"] = [p for p in a["points"] if int(p[0]) in keep]
    if "auto_ids" in a:
        a["auto_ids"] = [i for i in a["auto_ids"] if int(i) in keep]
    if ("ids" in a and not a["ids"]) or ("points" in a and not a["points"]):
        raise ValueError("No photos left ticked")
    return {**card, "action": a, "ids": [i for i in card["ids"] if int(i) in keep] or card["ids"]}


def summary_of(card: dict) -> str:
    """One line for the Done notice."""
    a = card.get("action") or {}
    n = len(a.get("ids") or a.get("points") or card.get("ids") or [])
    return {"trip_add": f"{n} photos added to the trip", "face_merge": "Face group merged", "face_name": "Person named",
            "location": f"{n} photos given a location", "trip_merge": "Trips merged", "trash": f"{n} photos moved to the trash"}.get(a.get("do"), "Done")


def rebuild_after(conn) -> None:
    """Trips, events and milestones catch up with what just changed (once per user action)."""
    from . import chapters, events, places
    places.refresh(conn); events.rebuild(conn); chapters.rebuild_milestones(conn)


def apply(conn, library, originals, card: dict, decision: str = "accepted", name: str | None = None, rebuild: bool = True) -> dict:
    """Perform the card's action, remember it with what is needed to undo it."""
    from . import cleanup, curate, faces, locations
    ensure_schema(conn)
    a = card.get("action") or {}
    undo = None
    if a.get("do") == "trip_add":
        ids = [int(i) for i in a["ids"]]
        ph = ",".join("?" * len(ids))
        before = {r[0]: (r[1], r[2]) for r in conn.execute(f"SELECT id, trip_id, trip_manual FROM assets WHERE id IN ({ph}) OR live_pair_id IN ({ph})", ids + ids)}
        curate.set_trip(conn, ids, int(a["trip"]))
        undo = {"do": "trip_restore", "before": [[i, t, m] for i, (t, m) in before.items()]}
    elif a.get("do") == "face_merge":
        fids = [r[0] for r in conn.execute("SELECT id FROM faces WHERE person_id = ?", (int(a["from"]),))]
        faces.merge_people(conn, int(a["from"]), int(a["into"]))
        undo = {"do": "face_unmerge", "faces": fids}
    elif a.get("do") == "face_name":
        if not (name or "").strip():
            raise ValueError("Give them a name")
        faces.rename_person(conn, int(a["person"]), name.strip()[:80])
        undo = {"do": "face_unname", "person": int(a["person"])}
    elif a.get("do") == "location":
        changed = []
        for pid, lat, lon in a["points"]:
            if locations.set_manual(conn, [int(pid)], lat, lon):
                changed.append(int(pid))
        undo = {"do": "location_clear", "ids": changed}
    elif a.get("do") == "trip_merge":
        curate.merge_trips(conn, int(a["from"]), int(a["into"]))
        undo = None
    elif a.get("do") == "trash":
        ids = [int(i) for i in a["ids"]]
        cleanup.trash(conn, library, originals, ids)
        undo = {"do": "restore", "ids": ids}
    else:
        raise ValueError("unknown action")
    conn.execute("INSERT OR REPLACE INTO inbox_decisions (key, decision, undo, title, created) VALUES (?, ?, ?, ?, ?)",
                 (card["key"], decision, json.dumps(undo) if undo else None, card.get("title", ""), _now()))
    conn.commit()
    if rebuild:
        rebuild_after(conn)
    return {"ok": True, "undoable": bool(undo), "summary": summary_of(card)}


def skip(conn, card: dict) -> None:
    ensure_schema(conn)
    conn.execute("INSERT OR REPLACE INTO inbox_decisions (key, decision, undo, title, created) VALUES (?, 'skipped', NULL, ?, ?)",
                 (card["key"], card.get("title", ""), _now()))
    conn.commit()


def undo_last(conn, library, originals) -> str:
    """Reverse the most recent undoable decision. Returns what was undone."""
    from . import cleanup, locations
    ensure_schema(conn)
    r = conn.execute("SELECT key, undo, title FROM inbox_decisions WHERE undo IS NOT NULL ORDER BY created DESC LIMIT 1").fetchone()
    if not r:
        raise ValueError("Nothing to undo")
    u = json.loads(r["undo"])
    if u["do"] == "trip_restore":
        for i, t, m in u["before"]:
            conn.execute("UPDATE assets SET trip_id = ?, trip_manual = ? WHERE id = ?", (t, m, i))
        from . import curate
        for tid in {t for _, t, _ in u["before"] if t}:
            curate._recount(conn, tid)
    elif u["do"] == "face_unmerge":
        cur = conn.execute("INSERT INTO persons (name, created) VALUES (NULL, datetime('now'))")
        pid = cur.lastrowid
        for fid in u["faces"]:
            conn.execute("UPDATE faces SET person_id = ?, locked = 0 WHERE id = ?", (pid, fid))
        from . import faces
        faces.refresh_covers(conn)
    elif u["do"] == "face_unname":
        conn.execute("UPDATE persons SET name = NULL WHERE id = ?", (u["person"],))
        conn.execute("UPDATE faces SET locked = 0 WHERE person_id = ?", (u["person"],))
    elif u["do"] == "location_clear":
        locations.clear_manual(conn, u["ids"])
    elif u["do"] == "restore":
        cleanup.restore(conn, library, originals, u["ids"])
    conn.execute("DELETE FROM inbox_decisions WHERE key = ?", (r["key"],))
    conn.commit()
    rebuild_after(conn)
    return r["title"] or r["key"]


def auto_apply(conn, db_key, library, originals, log=print) -> int:
    """Apply safe, confident suggestions on their own (locations and clutter). Returns how many."""
    n = 0
    for c in suggestions(conn, db_key, library, originals):
        if c["kind"] in AUTO_KINDS and c["conf"] >= AUTO_MIN:
            if c["kind"] == "clutter":
                # On its own the app only trashes byte-identical copies; blurry and blank frames wait for you.
                dupes = c["action"].get("auto_ids") or []
                if not dupes:
                    continue
                c = {**c, "key": c["key"] + ":auto", "title": f"{len(dupes)} exact copies went to the trash", "action": {"do": "trash", "ids": dupes}}
            try:
                apply(conn, library, originals, c, decision="auto", rebuild=False)
                log(f"Inbox, applied on its own: {c['title']}.")
                n += 1
            except Exception as e:
                log(f"Inbox could not apply '{c['title']}': {e}")
    if n:
        rebuild_after(conn)
    return n
