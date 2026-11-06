"""SQLite catalog of every file in the library's originals folder."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .metadata import media_kind, read_photo_meta

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE,   -- relative to originals/, forward slashes
    kind          TEXT NOT NULL,          -- photo | video | live_video
    size          INTEGER NOT NULL,
    mtime         REAL NOT NULL,
    sha256        TEXT NOT NULL,
    taken_at      TEXT,
    lat           REAL,
    lon           REAL,
    camera_make   TEXT,
    camera_model  TEXT,
    width         INTEGER,
    height        INTEGER,
    live_pair_id  INTEGER REFERENCES assets(id),  -- live_video -> its still photo
    duplicate_of  INTEGER REFERENCES assets(id),  -- byte-identical copy of another asset
    missing       INTEGER NOT NULL DEFAULT 0,
    first_seen    TEXT NOT NULL,
    last_verified TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_sha ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_assets_taken ON assets(taken_at);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS trips (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,      -- "Jordan", "Jordan & Israel"
    subtitle      TEXT,               -- top cities
    start_at      TEXT NOT NULL,
    end_at        TEXT NOT NULL,
    photo_count   INTEGER NOT NULL,
    cover_id      INTEGER REFERENCES assets(id),
    lat           REAL,
    lon           REAL
);
"""

# Columns added after the first release; created on open if missing.
MIGRATIONS = {
    "country_code": "TEXT",
    "country": "TEXT",
    "region": "TEXT",
    "city": "TEXT",
    "place_km": "REAL",      # distance to the matched place; NULL = not looked up yet
    "trip_id": "INTEGER",
    "private": "INTEGER NOT NULL DEFAULT 0",   # never sent to AI, never shared
    "trip_manual": "INTEGER NOT NULL DEFAULT 0",   # 1 = you chose this photo's trip
    "phash": "INTEGER",                            # perceptual hash for look-alike detection
}
TRIP_MIGRATIONS = {"manual": "INTEGER NOT NULL DEFAULT 0", "title_locked": "INTEGER NOT NULL DEFAULT 0"}

LIVE_SUFFIXES = ("_HEVC",)  # icloudpd's default live-photo video suffix


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_script(conn: sqlite3.Connection, sql: str) -> None:
    """Run DDL statement by statement (executescript would commit any open transaction)."""
    buf = ""
    for line in sql.splitlines():
        buf += line + "\n"
        if sqlite3.complete_statement(buf):
            if buf.strip(" \n;"):
                conn.execute(buf)
            buf = ""
    if buf.strip(" \n;") and not buf.strip().startswith("--"):
        conn.execute(buf)


class LibraryUnavailable(RuntimeError):
    """The photo folder is missing or looks unplugged; nothing was changed."""


def connect(db_file: Path) -> sqlite3.Connection:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")      # readers never block on a long write
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass
    run_script(conn, SCHEMA)
    # Every feature's tables exist from the start, so request handlers never run DDL.
    from . import ask, chapters, describe, faces, review, search
    for mod in (faces, describe, search, chapters, ask):
        run_script(conn, mod.SCHEMA if mod is not describe else describe.DB_SCHEMA)
    run_script(conn, review.SCHEMA)
    conn.commit()
    # Two connections can open a fresh library at the same moment (the page's status
    # poll and a job); take the write lock for the migrations and tolerate a column
    # the other connection added first. An up-to-date library skips this entirely.
    wanted = {"assets": MIGRATIONS, "trips": TRIP_MIGRATIONS, "persons": {"birthday": "TEXT"}}
    if any(_missing(conn, t, cols) for t, cols in wanted.items()):
        conn.execute("BEGIN IMMEDIATE")
        for t, cols in wanted.items():
            _add_columns(conn, t, cols)
        conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_country ON assets(country_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_trip ON assets(trip_id)")
    conn.commit()
    return conn


def _missing(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> list[str]:
    have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    return [c for c in columns if c not in have]


def _add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    for col in _missing(conn, table, columns):
        typ = columns[col]
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _live_key(rel_path: str) -> str:
    """Directory + stem with live-photo suffixes removed, case-insensitive."""
    p = Path(rel_path)
    stem = p.stem
    for suffix in LIVE_SUFFIXES:
        if stem.upper().endswith(suffix):
            stem = stem[: -len(suffix)]
    return f"{p.parent.as_posix()}/{stem}".lower()


@dataclass
class ScanResult:
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: int = 0
    skipped: list[str] = field(default_factory=list)
    places_added: int = 0
    trips: int = 0

    @property
    def total(self) -> int:
        return self.new + self.updated + self.unchanged


def scan(conn: sqlite3.Connection, originals: Path, progress=None, cancelled=lambda: False) -> ScanResult:
    """Add new and changed files to the catalog; flag files that disappeared."""
    result = ScanResult()
    seen: set[str] = set()
    existing = {
        row["path"]: row
        for row in conn.execute("SELECT id, path, size, mtime, missing FROM assets")
    }
    if not originals.exists():
        raise LibraryUnavailable(f"The photo folder {originals} is not there. Is the drive plugged in?")
    files = sorted(p for p in originals.rglob("*") if p.is_file())
    known = sum(1 for r in existing.values() if not r["missing"])
    if known >= 20 and len(files) < known / 2:
        raise LibraryUnavailable(
            f"Only {len(files):,} files were found where {known:,} are expected. The catalog was left alone; "
            "check that the photo folder is complete (drive plugged in, not renamed) and try again.")

    for i, path in enumerate(files, 1):
        if cancelled():
            conn.commit()
            raise InterruptedError("stopped")
        if progress and i % 100 == 0:
            progress(i, len(files))
        rel = _rel(path, originals)
        if any(part.startswith(".") for part in Path(rel).parts):
            continue  # hidden/tool folders, e.g. .icloudpd-pruned
        kind = media_kind(path)
        if kind is None:
            if path.suffix.lower() != ".xmp":
                result.skipped.append(rel)
            continue
        seen.add(rel)
        st = path.stat()
        row = existing.get(rel)
        if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1e-6:
            if row["missing"]:
                conn.execute("UPDATE assets SET missing = 0 WHERE id = ?", (row["id"],))
            result.unchanged += 1
            continue

        digest = sha256_file(path)
        meta = read_photo_meta(path) if kind == "photo" else None
        taken_at = (meta.taken_at if meta else None) or datetime.fromtimestamp(
            st.st_mtime
        ).isoformat(timespec="seconds")
        values = dict(
            path=rel, kind=kind, size=st.st_size, mtime=st.st_mtime, sha256=digest,
            taken_at=taken_at,
            lat=meta.lat if meta else None, lon=meta.lon if meta else None,
            camera_make=meta.camera_make if meta else None,
            camera_model=meta.camera_model if meta else None,
            width=meta.width if meta else None, height=meta.height if meta else None,
        )
        if row:
            sets = ", ".join(f"{k} = :{k}" for k in values)
            conn.execute(
                f"UPDATE assets SET {sets}, missing = 0, last_verified = NULL WHERE id = :id",
                {**values, "id": row["id"]},
            )
            result.updated += 1
        else:
            cols = ", ".join(values)
            params = ", ".join(f":{k}" for k in values)
            conn.execute(
                f"INSERT INTO assets ({cols}, first_seen) VALUES ({params}, :first_seen)",
                {**values, "first_seen": now_iso()},
            )
            result.new += 1
        if (result.new + result.updated) % 200 == 0:
            conn.commit()   # keep write transactions short so the UI stays responsive

    for rel, row in existing.items():
        if rel not in seen and not row["missing"]:
            conn.execute("UPDATE assets SET missing = 1 WHERE id = ?", (row["id"],))
            result.missing += 1
        elif rel not in seen:
            result.missing += 1

    _link_live_photos(conn)
    _mark_duplicates(conn)
    conn.commit()
    from . import places  # local import: places is optional data-heavy code
    result.places_added, result.trips = places.refresh(conn)
    from . import chapters
    chapters.rebuild(conn)
    set_meta(conn, "last_scan", now_iso())
    conn.commit()
    return result


def _link_live_photos(conn: sqlite3.Connection) -> None:
    photos: dict[str, int] = {}
    for row in conn.execute("SELECT id, path FROM assets WHERE kind = 'photo' AND missing = 0"):
        photos.setdefault(_live_key(row["path"]), row["id"])
    rows = conn.execute(
        "SELECT id, path FROM assets WHERE kind IN ('video', 'live_video') AND missing = 0"
    ).fetchall()
    for row in rows:
        pair = photos.get(_live_key(row["path"]))
        conn.execute(
            "UPDATE assets SET kind = ?, live_pair_id = ? WHERE id = ?",
            ("live_video" if pair else "video", pair, row["id"]),
        )


def _mark_duplicates(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE assets SET duplicate_of = NULL")
    conn.execute(
        """
        UPDATE assets SET duplicate_of = (
            SELECT MIN(b.id) FROM assets b
            WHERE b.sha256 = assets.sha256 AND b.missing = 0
        )
        WHERE missing = 0 AND id != (
            SELECT MIN(b.id) FROM assets b
            WHERE b.sha256 = assets.sha256 AND b.missing = 0
        )
        """
    )


@dataclass
class VerifyResult:
    ok: int = 0
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def verify(conn: sqlite3.Connection, originals: Path, progress=None, cancelled=lambda: False) -> VerifyResult:
    """Re-read every file and compare it with the checksum in the catalog."""
    result = VerifyResult()
    rows = conn.execute("SELECT id, path, sha256 FROM assets WHERE missing = 0").fetchall()
    if not originals.exists():
        raise LibraryUnavailable(f"The photo folder {originals} is not there. Is the drive plugged in?")
    stamp = now_iso()
    for i, row in enumerate(rows, 1):
        if cancelled():
            conn.commit()
            raise InterruptedError("stopped")
        if i % 200 == 0:
            conn.commit()
        path = originals / row["path"]
        if not path.is_file():
            result.missing.append(row["path"])
        elif sha256_file(path) != row["sha256"]:
            result.changed.append(row["path"])
        else:
            result.ok += 1
            conn.execute("UPDATE assets SET last_verified = ? WHERE id = ?", (stamp, row["id"]))
        if progress and i % 100 == 0:
            progress(i, len(rows))
    set_meta(conn, "last_verify", stamp)
    conn.commit()
    return result


def stats(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    live = "missing = 0"
    return {
        "photos": q(f"SELECT COUNT(*) FROM assets WHERE {live} AND kind = 'photo'"),
        "videos": q(f"SELECT COUNT(*) FROM assets WHERE {live} AND kind = 'video'"),
        "live_videos": q(f"SELECT COUNT(*) FROM assets WHERE {live} AND kind = 'live_video'"),
        "duplicates": q(f"SELECT COUNT(*) FROM assets WHERE {live} AND duplicate_of IS NOT NULL"),
        "missing": q("SELECT COUNT(*) FROM assets WHERE missing = 1"),
        "countries": q(f"SELECT COUNT(DISTINCT country_code) FROM assets WHERE {live}"),
        "trips": q("SELECT COUNT(*) FROM trips"),
        "with_gps": q(f"SELECT COUNT(*) FROM assets WHERE {live} AND lat IS NOT NULL"),
        "bytes": q(f"SELECT COALESCE(SUM(size), 0) FROM assets WHERE {live}"),
        "first": q(f"SELECT MIN(taken_at) FROM assets WHERE {live}"),
        "last": q(f"SELECT MAX(taken_at) FROM assets WHERE {live}"),
        "last_sync": get_meta(conn, "last_sync"),
        "last_scan": get_meta(conn, "last_scan"),
        "last_verify": get_meta(conn, "last_verify"),
    }
