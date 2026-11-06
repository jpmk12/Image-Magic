"""Second copy of the library on another drive, verified by checksum.

The laptop holds the first copy (the iCloud download). This writes a second
one to a folder you choose (an external drive, a NAS share, a synced folder):
every file under originals/ and voice/, plus a consistent snapshot of the
catalog. Files already there with the same size and time are skipped; every
file copied is read back and its SHA-256 compared with the catalog (or with
the source), so a bad cable or a failing drive is noticed right away.

Nothing is ever deleted in the backup folder, and the library itself is
never written to.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import catalog

COPY_DIRS = ("originals", "voice")
MARKER = "IMAGE-MAGIC-BACKUP.txt"


@dataclass
class BackupResult:
    copied: int = 0
    skipped: int = 0
    verified: int = 0
    failed: list[str] = field(default_factory=list)
    bytes_copied: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {"copied": self.copied, "skipped": self.skipped, "verified": self.verified, "failed": self.failed[:50],
                "failed_count": len(self.failed), "bytes": self.bytes_copied, "seconds": round(self.seconds)}


def check_destination(library: Path, dest: Path) -> None:
    """Refuse a destination that is missing (drive unplugged) or overlaps the library."""
    if not dest:
        raise ValueError("Choose a backup folder in Settings first.")
    if not dest.exists():
        raise ValueError(f"The backup folder {dest} is not there. Is the drive plugged in?")
    if not dest.is_dir():
        raise ValueError("The backup folder must be a folder.")
    lib, d = library.resolve(), dest.resolve()
    if d == lib or lib in d.parents or d in lib.parents:
        raise ValueError("The backup folder must be outside the library (ideally on another drive).")
    if any(dest.iterdir()) and not (dest / MARKER).exists():
        raise ValueError(f"{dest} already has other files in it. Pick an empty folder or one made by Image Magic.")


def _files(library: Path) -> list[Path]:
    out = []
    for name in COPY_DIRS:
        root = library / name
        if root.is_dir():
            out += [p for p in root.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.relative_to(library).parts)]
    return out


def run(conn: sqlite3.Connection, library: Path, dest: Path, progress: Callable[[int, int], None] | None = None,
        cancelled=lambda: False) -> BackupResult:
    check_destination(library, dest)
    t0 = time.time()
    res = BackupResult()
    files = _files(library)
    known = {r["path"]: r["sha256"] for r in conn.execute("SELECT path, sha256 FROM assets WHERE missing = 0")}
    need = 0
    todo = []
    for src in files:
        rel = src.relative_to(library)
        target = dest / rel
        st = src.stat()
        try:
            ts = target.stat()
            if ts.st_size == st.st_size and abs(ts.st_mtime - st.st_mtime) < 2:
                res.skipped += 1
                continue
        except FileNotFoundError:
            pass
        todo.append((src, target, rel, st.st_size))
        need += st.st_size
    free = shutil.disk_usage(dest).free
    if need > free:
        raise ValueError(f"Not enough space: {need / 1e9:.1f} GB needed, {free / 1e9:.1f} GB free on the backup drive.")
    for i, (src, target, rel, size) in enumerate(todo, 1):
        if cancelled():
            break
        if progress and (i % 20 == 0 or i == len(todo)):
            progress(i, len(todo))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".part")
            shutil.copy2(src, tmp)
            expected = known.get(rel.relative_to("originals").as_posix()) if rel.parts[0] == "originals" else None
            got = catalog.sha256_file(tmp)
            if expected is None:
                expected = catalog.sha256_file(src)
            if got != expected:
                tmp.unlink(missing_ok=True)
                res.failed.append(rel.as_posix())
                continue
            tmp.replace(target)
            res.copied += 1
            res.verified += 1
            res.bytes_copied += size
        except OSError as e:
            res.failed.append(f"{rel.as_posix()} ({e.strerror or e})")
    # A consistent snapshot of the catalog (safe while the app has it open).
    try:
        snap = sqlite3.connect(dest / "library.db")
        with snap:
            conn.backup(snap)
        snap.close()
    except sqlite3.Error as e:
        res.failed.append(f"library.db ({e})")
    stamp = catalog.now_iso()
    (dest / MARKER).write_text(
        f"Image Magic backup of {library}\nLast run: {stamp}\n"
        f"Files: {res.copied + res.skipped:,} present, {res.copied:,} copied this run, {len(res.failed)} failed.\n"
        "Keep this folder as it is; the app only ever adds to it.\n", encoding="utf-8")
    if not cancelled():
        catalog.set_meta(conn, "last_backup", stamp)
        catalog.set_meta(conn, "last_backup_failed", str(len(res.failed)))
        conn.commit()
    res.seconds = time.time() - t0
    return res


def status(conn: sqlite3.Connection, library: Path, dest: Path | None) -> dict:
    out = {"configured": bool(dest), "present": bool(dest and dest.exists()),
           "last": catalog.get_meta(conn, "last_backup"), "last_failed": int(catalog.get_meta(conn, "last_backup_failed") or 0)}
    if dest and dest.exists():
        try:
            out["free_gb"] = round(shutil.disk_usage(dest).free / 1e9, 1)
        except OSError:
            pass
    return out
