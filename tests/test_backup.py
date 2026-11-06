"""Second copy of the library, verified by checksum."""
import time
from datetime import datetime, timedelta

import pytest

from image_magic import backup, catalog
from image_magic.jobs import JobRunner
from image_magic.settings import Settings
from test_places_browse import HOME, make_photo


def _lib(tmp_path, n=6):
    lib = tmp_path / "lib"
    for i in range(n):
        make_photo(lib / "originals" / "2019" / f"IMG_{i}.JPG", datetime(2019, 1, 1) + timedelta(days=i), HOME)
    (lib / "voice").mkdir()
    (lib / "voice" / "photo-1-1.webm").write_bytes(b"audio")
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, lib / "originals")
    return conn, lib


def test_backup_copies_verifies_and_is_incremental(tmp_path):
    conn, lib = _lib(tmp_path)
    dest = tmp_path / "drive" / "backup"
    with pytest.raises(ValueError, match="not there"):
        backup.run(conn, lib, dest)
    dest.mkdir(parents=True)
    r = backup.run(conn, lib, dest)
    assert r.copied == 7 and r.verified == 7 and r.failed == [] and r.skipped == 0
    assert (dest / "originals/2019/IMG_0.JPG").read_bytes() == (lib / "originals/2019/IMG_0.JPG").read_bytes()
    assert (dest / "voice/photo-1-1.webm").read_bytes() == b"audio" and (dest / backup.MARKER).exists()
    import sqlite3
    snap = sqlite3.connect(dest / "library.db")
    assert snap.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 6
    snap.close()
    assert catalog.get_meta(conn, "last_backup")
    # second run copies nothing; a new photo is picked up
    r2 = backup.run(conn, lib, dest)
    assert r2.copied == 0 and r2.skipped == 7
    make_photo(lib / "originals" / "2019" / "IMG_new.JPG", datetime(2019, 2, 1), HOME)
    catalog.scan(conn, lib / "originals")
    r3 = backup.run(conn, lib, dest)
    assert r3.copied == 1 and r3.skipped == 7
    # the backup folder is never pruned: a file removed from the library stays in the backup
    (lib / "originals" / "2019" / "IMG_0.JPG").unlink()
    backup.run(conn, lib, dest)
    assert (dest / "originals/2019/IMG_0.JPG").exists()
    s = backup.status(conn, lib, dest)
    assert s["present"] and s["last"] and s["last_failed"] == 0 and "free_gb" in s


def test_backup_refuses_bad_destinations_and_detects_corruption(tmp_path, monkeypatch):
    conn, lib = _lib(tmp_path, 2)
    with pytest.raises(ValueError, match="outside the library"):
        backup.run(conn, lib, lib / "originals")
    with pytest.raises(ValueError, match="outside the library"):
        backup.run(conn, lib, tmp_path)
    busy = tmp_path / "busy"; busy.mkdir(); (busy / "other.txt").write_text("x")
    with pytest.raises(ValueError, match="other files"):
        backup.run(conn, lib, busy)
    # a copy that reads back differently is reported, not kept
    dest = tmp_path / "drive"; dest.mkdir()
    import shutil
    real = shutil.copy2
    def bad_copy(src, dst, *a, **k):
        real(src, dst, *a, **k)
        if str(src).endswith("IMG_1.JPG"):
            with open(dst, "ab") as f:
                f.write(b"corrupt")
        return dst
    monkeypatch.setattr(shutil, "copy2", bad_copy)
    r = backup.run(conn, lib, dest)
    assert r.copied == 2 and r.failed == ["originals/2019/IMG_1.JPG"] and not (dest / "originals/2019/IMG_1.JPG").exists()
    assert not list((dest / "originals/2019").glob("*.part"))


def test_backup_job(tmp_path):
    conn, lib = _lib(tmp_path, 3)
    conn.close()
    dest = tmp_path / "drive"; dest.mkdir()
    runner = JobRunner(lambda: Settings(apple_id="x", library_path=str(lib), backup_path=str(dest)))
    runner.start("backup")
    for _ in range(100):
        if runner.state.state in ("done", "failed"):
            break
        time.sleep(0.05)
    assert runner.state.state == "done" and runner.state.result["copied"] == 4, runner.state.message
    runner2 = JobRunner(lambda: Settings(apple_id="x", library_path=str(lib)))
    runner2.start("backup")
    for _ in range(100):
        if runner2.state.state in ("done", "failed"):
            break
        time.sleep(0.05)
    assert runner2.state.state == "failed" and "Settings" in runner2.state.message
