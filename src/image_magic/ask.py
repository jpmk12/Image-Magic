"""Stories and "Ask my life": Claude reads the catalog, never the photos.

Both features send only text: dates, places, trip and chapter names, people's
names, and the AI captions already in the catalog. Private photos are left out.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from . import browse, describe, faces

STORY_MODEL = "claude-opus-5-5"
ASK_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ROUNDS = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    key      TEXT PRIMARY KEY,      -- trip:ID | chapter:ID
    text     TEXT NOT NULL,
    model    TEXT NOT NULL,
    edited   INTEGER NOT NULL DEFAULT 0,
    created  TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)
    describe.ensure_schema(conn)
    faces.ensure_schema(conn)


# ---- shared catalog views ----------------------------------------------------

def _items(conn: sqlite3.Connection, params: dict, limit: int = 40) -> list[dict]:
    """Photos matching the filters, newest first, with caption and people names."""
    params = {k: v for k, v in params.items() if v not in (None, "", 0)}
    res = browse.photos(conn, params, 0, limit)
    return enrich_all(conn, res["items"]), res["total"]


def enrich_all(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """Captions, keywords and people for many photos in two queries."""
    if not items:
        return []
    ids = [it["id"] for it in items]
    caps: dict[int, tuple[str, list]] = {}
    names: dict[int, list[str]] = {}
    private: set[int] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT asset_id, caption, keywords FROM descriptions WHERE status = 'ok' AND asset_id IN ({ph})", chunk):
            caps[r[0]] = (r[1], json.loads(r[2] or "[]"))
        for r in conn.execute(f"""SELECT f.asset_id, p.name FROM faces f JOIN persons p ON p.id = f.person_id
                                  WHERE p.name IS NOT NULL AND p.hidden = 0 AND f.asset_id IN ({ph})""", chunk):
            names.setdefault(r[0], []).append(r[1])
        private |= {r[0] for r in conn.execute(f"SELECT id FROM assets WHERE private = 1 AND id IN ({ph})", chunk)}
    out = []
    for it in items:
        if it["id"] in private:
            continue
        o = {"id": it["id"], "date": (it.get("taken_at") or "")[:16], "kind": it.get("kind"),
             "place": ", ".join(x for x in [it.get("city"), it.get("country")] if x)}
        if it["id"] in caps:
            o["caption"], kws = caps[it["id"]]
            o["keywords"] = kws[:6]
        if it["id"] in names:
            o["people"] = sorted(set(names[it["id"]]))
        out.append(o)
    return out


def _enrich(conn: sqlite3.Connection, it: dict) -> dict:
    return (enrich_all(conn, [it]) or [{"id": it["id"], "date": "", "kind": it.get("kind"), "place": ""}])[0]


def library_summary(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    q = lambda sql, *a: [dict(r) for r in conn.execute(sql, a)]  # noqa: E731
    vis = "missing = 0 AND duplicate_of IS NULL AND kind != 'live_video' AND private = 0"
    return {
        "years": q(f"SELECT substr(taken_at,1,4) AS year, COUNT(*) AS photos FROM assets WHERE {vis} "
                   "AND taken_at IS NOT NULL GROUP BY year ORDER BY year"),
        "countries": q(f"SELECT country, COUNT(*) AS photos FROM assets WHERE {vis} AND country IS NOT NULL "
                       "GROUP BY country ORDER BY photos DESC LIMIT 30"),
        "chapters": q("SELECT id, title, start_at, end_at, photo_count FROM chapters ORDER BY start_at"),
        "trips": q("SELECT id, title, subtitle, start_at, end_at, photo_count FROM trips ORDER BY start_at"),
        "people": [{"id": p["id"], "name": p["name"], "photos": p["photo_count"]}
                   for p in faces.people(conn) if p["name"]],
        "milestones": q("SELECT date, title FROM milestones WHERE state != 'dismissed' ORDER BY date"),
        "recent_events": q("SELECT id, title, substr(start_at, 1, 10) AS day, photo_count FROM events ORDER BY start_at DESC LIMIT 60"),
        "described_photos": conn.execute("SELECT COUNT(*) FROM descriptions WHERE status = 'ok'").fetchone()[0],
    }


# ---- stories ---------------------------------------------------------------------

def story_context(conn: sqlite3.Connection, kind: str, ident: int) -> dict:
    ensure_schema(conn)
    if kind == "trip":
        row = conn.execute("SELECT * FROM trips WHERE id = ?", (ident,)).fetchone()
        if not row:
            raise ValueError("Unknown trip")
        params = {"trip": ident}
        head = {"what": "trip", "title": row["title"], "places": row["subtitle"],
                "start": row["start_at"][:10], "end": row["end_at"][:10], "photos": row["photo_count"]}
    elif kind == "chapter":
        row = conn.execute("SELECT * FROM chapters WHERE id = ?", (ident,)).fetchone()
        if not row:
            raise ValueError("Unknown chapter")
        params = {"chapter": ident}
        head = {"what": "chapter of life", "title": row["title"], "start": row["start_at"], "end": row["end_at"],
                "photos": row["photo_count"], "your_note": row["note"]}
    else:
        raise ValueError("kind must be trip or chapter")
    items, total = _items(conn, {**params, "kind": "photo"}, limit=400)
    # Compress: day-by-day places, the people seen, and a sample of captions.
    days: dict[str, Counter] = {}
    people: Counter = Counter()
    for it in items:
        day = it["date"][:10]
        days.setdefault(day, Counter())
        if it["place"]:
            days[day][it["place"]] += 1
        for n in it.get("people", []):
            people[n] += 1
    captions = [{"id": i["id"], "date": i["date"], "caption": i["caption"]} for i in items if i.get("caption")]
    step = max(1, len(captions) // 40)
    head.update({
        "days": [{"day": d, "places": [p for p, _ in c.most_common(3)]} for d, c in sorted(days.items())],
        "people": [{"name": n, "photos": c} for n, c in people.most_common(10)],
        "sample_captions": captions[::step][:40],
        "milestones": [dict(r) for r in conn.execute(
            "SELECT date, title FROM milestones WHERE state != 'dismissed' AND date >= ? AND date <= ? ORDER BY date",
            (head["start"], head["end"]))],
    })
    return head


STORY_SYSTEM = """You write short, warm, specific stories for someone's private photo library,
to help them relive a trip or a period of their life. You only know what is in the data
you're given: dates, places, who appears, and captions of some photos. Never invent events,
feelings, or details that aren't supported by the data; where the data is thin, keep it
short rather than padding. Write in second person ("you"), in 2 to 4 short paragraphs,
mentioning real places and people by the names given. No headings, no lists, no emojis."""


def write_story(client, context: dict, model: str = STORY_MODEL) -> str:
    resp = client.beta.messages.create(
        model=model, max_tokens=4000,
        betas=[FALLBACK_BETA], fallbacks="default",
        system=STORY_SYSTEM,
        messages=[{"role": "user", "content": "Write the story for this " + context["what"] + ":\n" +
                   json.dumps(context, ensure_ascii=False)}],
    )
    if getattr(resp, "stop_reason", None) == "refusal":
        return ""
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


def get_story(conn: sqlite3.Connection, key: str) -> dict | None:
    ensure_schema(conn)
    r = conn.execute("SELECT * FROM stories WHERE key = ?", (key,)).fetchone()
    return dict(r) if r else None


def save_story(conn: sqlite3.Connection, key: str, text: str, model: str, edited: bool) -> None:
    ensure_schema(conn)
    conn.execute("INSERT OR REPLACE INTO stories (key, text, model, edited, created) VALUES (?, ?, ?, ?, ?)",
                 (key, text.strip()[:20000], model, 1 if edited else 0,
                  datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


# ---- Ask my life ------------------------------------------------------------------

ASK_SYSTEM = """You are the memory of someone's private photo library. Answer their questions
about their own life using the tools; never guess. Use library_summary first when you need
the big picture (years, countries, trips, people), and search_photos to find specific moments.
Dates and places come from the photos' metadata; captions and people names are from the
library. When you find relevant photos, call show_photos with up to 12 of their ids so they
appear next to your answer. Answer in a few friendly sentences, in second person, with real
dates and places. If the library has no answer, say so plainly.

You can also DO things when asked: start a replay (slideshow on the map) of a trip, event,
day, month, year or person with replay_photos; open the share dialog for a set of photos
with share_photos (the person still reviews and clicks Export); make a person's printable
book with make_book; or open the Photos tab filtered with open_photos. Use the same filters
as search_photos; set best=true for "the best of". Do only what was asked, then confirm in
one sentence what you started."""

FILTER_PROPS = {
    "query": {"type": "string"}, "year": {"type": "string", "description": "YYYY or empty"},
    "month": {"type": "string", "description": "YYYY-MM or empty"}, "day": {"type": "string", "description": "YYYY-MM-DD or empty"},
    "country": {"type": "string", "description": "two-letter code like JO, or empty"},
    "city": {"type": "string"}, "person": {"type": "string", "description": "a named person, or empty"},
    "trip_id": {"type": "integer"}, "chapter_id": {"type": "integer"}, "event_id": {"type": "integer"},
    "best": {"type": "boolean", "description": "only the best photos (sharp, well exposed, one per burst)"},
}
FILTER_KEYS = list(FILTER_PROPS)

TOOLS = [
    {"name": "library_summary", "description": "Overview of the whole library: photos per year, countries, "
     "chapters of life, trips, named people, milestones.", "strict": True,
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "search_photos", "description": "Find photos. Any filter may be empty. 'query' matches words in AI "
     "captions and does a visual search; use plain nouns ('beach', 'birthday cake', 'C-17').",
     "strict": True,
     "input_schema": {"type": "object", "properties": {**FILTER_PROPS, "limit": {"type": "integer", "description": "1-40"}},
         "required": [*FILTER_KEYS, "limit"], "additionalProperties": False}},
    {"name": "replay_photos", "description": "Start a replay (slideshow with the map) of the photos matching the filters. "
     "Give it a short title.", "strict": True,
     "input_schema": {"type": "object", "properties": {**FILTER_PROPS, "title": {"type": "string"}},
         "required": [*FILTER_KEYS, "title"], "additionalProperties": False}},
    {"name": "open_photos", "description": "Open the Photos tab filtered to these photos.", "strict": True,
     "input_schema": {"type": "object", "properties": {**FILTER_PROPS, "title": {"type": "string"}},
         "required": [*FILTER_KEYS, "title"], "additionalProperties": False}},
    {"name": "share_photos", "description": "Open the share dialog for these photos (the person reviews the sensitivity "
     "check and clicks Export; nothing leaves the computer by itself).", "strict": True,
     "input_schema": {"type": "object", "properties": {**FILTER_PROPS, "title": {"type": "string"}},
         "required": [*FILTER_KEYS, "title"], "additionalProperties": False}},
    {"name": "make_book", "description": "Build the printable PDF book of a named person (cover, growing up, best photos per year).",
     "strict": True, "input_schema": {"type": "object", "properties": {"person": {"type": "string"}},
                                      "required": ["person"], "additionalProperties": False}},
    {"name": "show_photos", "description": "Pick the photos to show next to your answer.", "strict": True,
     "input_schema": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
                      "required": ["ids"], "additionalProperties": False}},
]


class Asker:
    def __init__(self, conn: sqlite3.Connection, settings, search_fn):
        """search_fn(conn, settings, params) -> list of (asset_id, score) for a text query."""
        ensure_schema(conn)
        self.conn, self.settings, self.search_fn = conn, settings, search_fn
        self.shown: list[int] = []
        self.actions: list[dict] = []

    def _person_id(self, name: str) -> int:
        p = next((p for p in faces.people(self.conn) if (p["name"] or "").lower() == name.lower()), None)
        if not p:
            raise ValueError(f"No person named {name}. Known people: "
                             + ", ".join(x["name"] for x in faces.people(self.conn) if x["name"]))
        return p["id"]

    def _filters(self, args: dict) -> dict:
        """Tool arguments -> the page's filter names (person resolved to an id)."""
        params: dict = {}
        for k in ("year", "month", "day", "country", "city"):
            if args.get(k):
                params[k] = str(args[k])
        for k, dest in (("trip_id", "trip"), ("chapter_id", "chapter"), ("event_id", "event")):
            if args.get(k):
                params[dest] = int(args[k])
        if args.get("person"):
            params["person"] = self._person_id(args["person"])
            params["personName"] = args["person"]
        if args.get("best"):
            params["best"] = "1"
        if args.get("query"):
            params["q"] = str(args["query"])[:200]
        return params

    def run_tool(self, name: str, args: dict) -> str:
        conn = self.conn
        if name == "library_summary":
            return json.dumps(library_summary(conn), ensure_ascii=False)
        if name == "show_photos":
            ids = [int(i) for i in args.get("ids", [])][:12]
            self.shown = [i for i in ids if conn.execute("SELECT 1 FROM assets WHERE id = ? AND private = 0", (i,)).fetchone()]
            return json.dumps({"shown": self.shown})
        if name in ("replay_photos", "open_photos", "share_photos"):
            try:
                params = self._filters(args)
            except ValueError as e:
                return json.dumps({"error": str(e)})
            if not params:
                return json.dumps({"error": "Give at least one filter (a trip, event, person, day, month or year)."})
            kind = name.split("_")[0]
            if kind == "replay" and "q" in params:
                return json.dumps({"error": "A replay needs a trip, event, person, day, month or year, not a text query."})
            n = len(browse.ids(conn, {k: v for k, v in params.items() if k != "q"}))
            self.actions.append({"type": kind, "filters": params, "title": str(args.get("title") or "")[:80]})
            return json.dumps({"started": kind, "photos": n})
        if name == "make_book":
            try:
                pid = self._person_id(str(args.get("person") or ""))
            except ValueError as e:
                return json.dumps({"error": str(e)})
            self.actions.append({"type": "book", "person_id": pid, "title": str(args.get("person"))})
            return json.dumps({"started": "book", "person_id": pid})
        if name == "search_photos":
            try:
                params = self._filters({**args, "query": ""})
            except ValueError as e:
                return json.dumps({"error": str(e)})
            params.pop("personName", None)
            limit = max(1, min(int(args.get("limit") or 20), 40))
            if args.get("query"):
                hits = self.search_fn(conn, self.settings, {**params, "q": args["query"]})
                ids = [i for i, _ in hits][:limit]
                items = enrich_all(conn, browse.by_ids(conn, ids))
                total = len(hits)
            else:
                items, total = _items(conn, params, limit)
            return json.dumps({"total_matches": total, "photos": items}, ensure_ascii=False)
        return json.dumps({"error": f"unknown tool {name}"})

    def ask(self, client, question: str, history: list | None = None, model: str = ASK_MODEL) -> dict:
        messages = list(history or []) + [{"role": "user", "content": question}]
        self.shown, self.actions = [], []
        answer = ""
        for _ in range(MAX_TOOL_ROUNDS):
            resp = client.beta.messages.create(
                model=model, max_tokens=4000, betas=[FALLBACK_BETA], fallbacks="default",
                system=ASK_SYSTEM, tools=TOOLS, messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})
            if getattr(resp, "stop_reason", None) == "refusal":
                answer = "I can't help with that one."
                break
            uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            answer = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip() or answer
            if not uses:
                break
            results = []
            for u in uses:
                try:
                    out = self.run_tool(u.name, dict(u.input))
                except Exception as e:
                    results.append({"type": "tool_result", "tool_use_id": u.id, "content": str(e), "is_error": True})
                    continue
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
            messages.append({"role": "user", "content": results})
        # Keep the history compact: drop tool traffic, keep question/answer text.
        compact = list(history or []) + [{"role": "user", "content": question}, {"role": "assistant", "content": answer or "…"}]
        return {"answer": answer, "photos": self.shown, "actions": self.actions[:3], "history": compact[-12:]}
