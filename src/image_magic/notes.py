"""Voice notes: your own voice on a photo, an event or a trip.

Recorded in the browser (microphone), stored as small audio files under
<library>/voice/, and played back in replays. They stay on this computer; a
share export includes them only when you tick the box.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("photo", "event", "trip", "chapter")
MIMES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/wav": ".wav", "audio/mpeg": ".mp3"}
MAX_BYTES = 25_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_notes (
    id       INTEGER PRIMARY KEY,
    kind     TEXT NOT NULL,         -- photo | event | trip | chapter
    ref_id   INTEGER NOT NULL,
    file     TEXT NOT NULL,         -- name under <library>/voice/
    mime     TEXT NOT NULL,
    seconds  REAL,
    created  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_ref ON voice_notes(kind, ref_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


def _mime(mime: str) -> str:
    base = (mime or "").split(";")[0].strip().lower()
    if base not in MIMES:
        raise ValueError("Unsupported audio format")
    return base


def save(conn: sqlite3.Connection, library: Path, kind: str, ref_id: int, data: bytes, mime: str,
         seconds: float | None = None) -> dict:
    ensure_schema(conn)
    if kind not in KINDS:
        raise ValueError("bad kind")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("The recording is empty or too large")
    base = _mime(mime)
    folder = library / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute("INSERT INTO voice_notes (kind, ref_id, file, mime, seconds, created) VALUES (?, ?, '', ?, ?, ?)",
                       (kind, int(ref_id), base, seconds, created))
    nid = cur.lastrowid
    name = f"{kind}-{int(ref_id)}-{nid}{MIMES[base]}"
    (folder / name).write_bytes(data)
    conn.execute("UPDATE voice_notes SET file = ? WHERE id = ?", (name, nid))
    conn.commit()
    return {"id": nid, "kind": kind, "ref_id": int(ref_id), "seconds": seconds, "created": created}


def list_for(conn: sqlite3.Connection, kind: str, ref_id: int) -> list[dict]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT id, kind, ref_id, seconds, created FROM voice_notes WHERE kind = ? AND ref_id = ? ORDER BY created", (kind, int(ref_id)))]


def path_of(conn: sqlite3.Connection, library: Path, note_id: int) -> tuple[Path, str] | None:
    ensure_schema(conn)
    r = conn.execute("SELECT file, mime FROM voice_notes WHERE id = ?", (int(note_id),)).fetchone()
    if not r or not r["file"]:
        return None
    p = library / "voice" / Path(r["file"]).name
    return (p, r["mime"]) if p.is_file() else None


def delete(conn: sqlite3.Connection, library: Path, note_id: int) -> None:
    ensure_schema(conn)
    r = conn.execute("SELECT file FROM voice_notes WHERE id = ?", (int(note_id),)).fetchone()
    if not r:
        return
    try:
        (library / "voice" / Path(r["file"]).name).unlink()
    except OSError:
        pass
    conn.execute("DELETE FROM voice_notes WHERE id = ?", (int(note_id),))
    conn.commit()


def count(conn: sqlite3.Connection) -> int:
    ensure_schema(conn)
    return conn.execute("SELECT COUNT(*) FROM voice_notes").fetchone()[0]
