"""AI descriptions: a caption and keywords for each photo, written by Claude.

Opt-in and paid (your own Anthropic API key). What is sent: a downscaled JPEG
copy (longest side 1024 px) re-encoded without EXIF, so no GPS or camera data
leaves the computer. Photos marked Private are never sent. Everything that comes
back is stored in the catalog and searchable.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from . import metadata  # noqa: F401  (HEIC opener)
from .settings import settings_dir

SEND_PX = 1024
WORKERS = 4

# USD per million tokens (input, output). Used only for the on-screen estimate.
MODELS = {
    "claude-haiku-5-5": ("Claude Haiku 5.5 (fast, about $0.0002 per photo)", 0.10, 0.50),
    "claude-sonnet-5-5": ("Claude Sonnet 5.5 (richer, about $0.004 per photo)", 2.00, 10.00),
    "claude-opus-5-5": ("Claude Opus 5.5 (best, about $0.008 per photo)", 4.00, 20.00),
}
DEFAULT_MODEL = "claude-haiku-5-5"
EST_IN_TOKENS, EST_OUT_TOKENS = 1450, 160   # per photo, measured on 1024 px images + prompt

SYSTEM = """You describe photos from someone's personal iPhone library so they can search and
relive them later. Be specific and concrete: name what is visible (objects, animals, vehicles,
aircraft types if clear, food, landmarks, weather, activities, emotions), not what you guess.
Do not identify people by name. Describe people by role or appearance only (e.g. "a toddler",
"two adults"). If the image is a screenshot, document, receipt or meme, say so in the caption
and keywords. Keep the caption to one natural sentence."""

SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string", "description": "One sentence describing the photo."},
        "keywords": {"type": "array", "items": {"type": "string"},
                     "description": "3 to 10 short lowercase search tags, most important first."},
        "setting": {"type": "string", "enum": ["indoor", "outdoor", "vehicle", "screenshot", "document", "other"]},
        "people_count": {"type": "integer", "description": "Number of people visible, 0 if none."},
        "text": {"type": "string", "description": "Readable text in the image, or an empty string."},
        "quality": {"type": "integer", "description": "1 (blurry, accidental) to 5 (a keeper)."},
    },
    "required": ["caption", "keywords", "setting", "people_count", "text", "quality"],
    "additionalProperties": False,
}

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS descriptions (
    asset_id     INTEGER PRIMARY KEY REFERENCES assets(id),
    model        TEXT NOT NULL,
    caption      TEXT NOT NULL,
    keywords     TEXT NOT NULL,     -- JSON list
    setting      TEXT,
    people_count INTEGER,
    text         TEXT,
    quality      INTEGER,
    in_tokens    INTEGER NOT NULL DEFAULT 0,
    out_tokens   INTEGER NOT NULL DEFAULT 0,
    created      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ok'   -- ok | refused | error
);
"""


# ---- API key ---------------------------------------------------------------

def _secrets_path() -> Path:
    return settings_dir() / "secrets.json"


def get_api_key() -> str:
    try:
        return json.loads(_secrets_path().read_text(encoding="utf-8")).get("anthropic_api_key", "") or ""
    except (OSError, ValueError):
        return os.environ.get("ANTHROPIC_API_KEY", "") if os.environ.get("IMAGE_MAGIC_ALLOW_ENV_KEY") else ""


def set_api_key(key: str) -> None:
    p = _secrets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    key = (key or "").strip()
    data = {"anthropic_api_key": key} if key else {}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


def key_hint() -> str:
    k = get_api_key()
    return f"{k[:7]}…{k[-4:]}" if len(k) > 12 else ("set" if k else "")


# ---- image prep --------------------------------------------------------------

def prepare_image(path: Path) -> bytes:
    """A small JPEG with no metadata (no GPS, no camera info)."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((SEND_PX, SEND_PX))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)   # no exif= argument: metadata dropped
        return buf.getvalue()


# ---- Claude ------------------------------------------------------------------

def make_client(api_key: str):
    import anthropic
    return anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=120)


def describe_one(client, model: str, jpeg: bytes, context: str = "") -> dict:
    """Ask Claude about one photo. Returns the parsed description plus token usage."""
    prompt = "Describe this photo for the library." + (f" Context: {context}" if context else "")
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(jpeg).decode("ascii")}},
            {"type": "text", "text": prompt},
        ]}],
    )
    usage = getattr(resp, "usage", None)
    in_t, out_t = (getattr(usage, "input_tokens", 0) or 0), (getattr(usage, "output_tokens", 0) or 0)
    if getattr(resp, "stop_reason", None) == "refusal":
        return {"status": "refused", "in_tokens": in_t, "out_tokens": out_t}
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        return {"status": "error", "in_tokens": in_t, "out_tokens": out_t}
    kws = [str(k).strip().lower() for k in data.get("keywords", []) if str(k).strip()][:12]
    return {
        "status": "ok", "caption": str(data.get("caption", "")).strip()[:500], "keywords": kws,
        "setting": data.get("setting"), "people_count": int(data.get("people_count") or 0),
        "text": str(data.get("text", ""))[:2000], "quality": int(data.get("quality") or 0),
        "in_tokens": in_t, "out_tokens": out_t,
    }


# ---- catalog integration -------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, DB_SCHEMA)


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    ensure_schema(conn)
    return conn.execute(
        """SELECT a.id, a.path, a.taken_at, a.city, a.country FROM assets a
           LEFT JOIN descriptions d ON d.asset_id = a.id AND d.status != 'error'
           WHERE d.asset_id IS NULL AND a.kind = 'photo' AND a.missing = 0 AND a.duplicate_of IS NULL
             AND a.private = 0
           ORDER BY a.taken_at DESC""").fetchall()


def counts(conn: sqlite3.Connection, model: str = DEFAULT_MODEL) -> dict:
    ensure_schema(conn)
    photos = conn.execute("SELECT COUNT(*) FROM assets WHERE kind = 'photo' AND missing = 0 "
                          "AND duplicate_of IS NULL AND private = 0").fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM descriptions WHERE status = 'ok'").fetchone()[0]
    spent = conn.execute("SELECT model, SUM(in_tokens), SUM(out_tokens) FROM descriptions GROUP BY model").fetchall()
    cost = 0.0
    for m, i, o in spent:
        _, pin, pout = MODELS.get(m, MODELS[DEFAULT_MODEL])
        cost += (i or 0) * pin / 1e6 + (o or 0) * pout / 1e6
    _, pin, pout = MODELS.get(model, MODELS[DEFAULT_MODEL])
    est = max(0, photos - done) * (EST_IN_TOKENS * pin + EST_OUT_TOKENS * pout) / 1e6
    return {"described": done, "photos": photos, "spent_usd": round(cost, 4),
            "estimate_usd": round(est, 2), "has_key": bool(get_api_key())}


def run(conn: sqlite3.Connection, originals: Path, client, model: str,
        progress: Callable[[int, int], None] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        limit: int | None = None) -> dict:
    """Describe photos that don't have a description yet. Returns counts."""
    rows = pending(conn)
    if limit:
        rows = rows[:limit]
    total = len(rows)
    done = refused = errors = 0
    lock = threading.Lock()

    def work(row):
        # Only the picture goes out: no date, place or file name.
        jpeg = prepare_image(originals / row["path"])
        return row["id"], describe_one(client, model, jpeg)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {}
        it = iter(rows)
        # Keep a small window of in-flight requests so Stop reacts quickly.
        for row in it:
            futures[pool.submit(work, row)] = row
            if len(futures) >= WORKERS * 2:
                break
        while futures:
            for fut in as_completed(list(futures)):
                row = futures.pop(fut)
                try:
                    asset_id, d = fut.result()
                except Exception as e:  # unreadable file, network error after retries
                    errors += 1
                    d = None
                    asset_id = row["id"]
                    msg = str(e)
                    if "authentication" in msg.lower() or "401" in msg:
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise RuntimeError("The API key was rejected. Check it in Settings.") from e
                if d is not None:
                    with lock:
                        conn.execute(
                            """INSERT OR REPLACE INTO descriptions
                               (asset_id, model, caption, keywords, setting, people_count, text, quality,
                                in_tokens, out_tokens, created, status)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (asset_id, model, d.get("caption", ""), json.dumps(d.get("keywords", [])),
                             d.get("setting"), d.get("people_count"), d.get("text"), d.get("quality"),
                             d["in_tokens"], d["out_tokens"], datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             d["status"]))
                        conn.commit()
                    if d["status"] == "ok":
                        done += 1
                    elif d["status"] == "refused":
                        refused += 1
                    else:
                        errors += 1
                if progress:
                    progress(done + refused + errors, total)
                if cancelled():
                    pool.shutdown(wait=False, cancel_futures=True)
                    futures.clear()
                    break
                nxt = next(it, None)
                if nxt is not None:
                    futures[pool.submit(work, nxt)] = nxt
                break
    return {"described": done, "refused": refused, "errors": errors, "total": total}


def for_photo(conn: sqlite3.Connection, asset_id: int) -> dict | None:
    ensure_schema(conn)
    r = conn.execute("SELECT caption, keywords, setting, people_count, text, quality, status FROM descriptions "
                     "WHERE asset_id = ?", (asset_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["keywords"] = json.loads(d["keywords"] or "[]")
    return d


def text_search(conn: sqlite3.Connection, query: str, allowed: set[int] | None = None, limit: int = 500) -> list[int]:
    """Photos whose caption, keywords or visible text contain every word of the query."""
    ensure_schema(conn)
    words = [w.lower() for w in query.split() if len(w) > 1][:8]
    if not words:
        return []
    where = " AND ".join("(lower(caption) LIKE ? OR lower(keywords) LIKE ? OR lower(text) LIKE ?)" for _ in words)
    args = []
    for w in words:
        args += [f"%{w}%"] * 3
    rows = conn.execute(
        f"""SELECT d.asset_id FROM descriptions d JOIN assets a ON a.id = d.asset_id
            WHERE d.status = 'ok' AND a.missing = 0 AND a.duplicate_of IS NULL AND {where}
            ORDER BY a.taken_at DESC LIMIT ?""", [*args, limit]).fetchall()
    ids = [r[0] for r in rows]
    return [i for i in ids if allowed is None or i in allowed]


def set_private(conn: sqlite3.Connection, asset_id: int, private: bool) -> None:
    ensure_schema(conn)
    conn.execute("UPDATE assets SET private = ? WHERE id = ?", (1 if private else 0, asset_id))
    if private:
        conn.execute("DELETE FROM descriptions WHERE asset_id = ?", (asset_id,))
    conn.commit()


def sleep_backoff(attempt: int) -> None:  # pragma: no cover - used only on live rate limits
    time.sleep(min(30, 2 ** attempt))
