"""Regression tests for the review fixes (bugs, performance, robustness)."""
import json
import threading
from datetime import datetime, timedelta

import numpy as np
import pytest
from PIL import Image

from image_magic import catalog, chapters, describe, faces, search
from test_chapters import life  # noqa: F401
from test_describe import FakeClient
from test_faces import RED, GREEN, FakeFaceModel, make_photo as make_face_photo
from test_places_browse import HOME, make_photo


def test_scan_refuses_an_unplugged_library(tmp_path):
    orig = tmp_path / "lib" / "originals"
    for i in range(30):
        make_photo(orig / f"IMG_{i:04d}.JPG", datetime(2019, 1, 1) + timedelta(days=i), HOME)
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    assert catalog.scan(conn, orig).new == 30
    import shutil
    shutil.move(orig, tmp_path / "unplugged")
    with pytest.raises(catalog.LibraryUnavailable):
        catalog.scan(conn, orig)
    with pytest.raises(catalog.LibraryUnavailable):
        catalog.verify(conn, orig)
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE missing = 1").fetchone()[0] == 0   # nothing was flagged
    # most files gone (drive half-copied) is refused too
    orig.mkdir(parents=True)
    make_photo(orig / "IMG_0000.JPG", datetime(2019, 1, 1), HOME)
    with pytest.raises(catalog.LibraryUnavailable):
        catalog.scan(conn, orig)


def test_scan_and_verify_can_be_stopped(tmp_path):
    orig = tmp_path / "lib" / "originals"
    for i in range(12):
        make_photo(orig / f"IMG_{i:04d}.JPG", datetime(2019, 1, 1) + timedelta(days=i), HOME)
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    calls = {"n": 0}

    def stop_after_five():
        calls["n"] += 1
        return calls["n"] > 5
    with pytest.raises(InterruptedError):
        catalog.scan(conn, orig, cancelled=stop_after_five)
    assert 0 < conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] < 12   # partial work kept
    catalog.scan(conn, orig)
    calls["n"] = 0
    with pytest.raises(InterruptedError):
        catalog.verify(conn, orig, cancelled=stop_after_five)


def test_wal_mode_and_concurrent_reader(tmp_path):
    db = tmp_path / "library.db"
    conn = catalog.connect(db)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")   # open write transaction, not committed
    other = catalog.connect(db)
    assert other.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0   # reader is not blocked
    conn.rollback()


def test_merge_carries_locked_faces_and_keeps_placed_faces(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(4):
        make_face_photo(orig / f"a{i}.png", [(RED, 100 + i, 100, 90)])
    for i in range(2):
        make_face_photo(orig / f"g{i}.png", [(GREEN, 200 + i, 200, 85)])
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    faces.scan_photos(conn, orig, FakeFaceModel())
    faces.group_faces(conn)
    red, green = sorted(faces.people(conn), key=lambda p: -p["photo_count"])
    # you move one green face into the red group ("this is the same person")
    gface = conn.execute("SELECT id FROM faces WHERE person_id = ?", (green["id"],)).fetchone()[0]
    faces.move_face(conn, gface, red["id"])
    faces.group_faces(conn)
    assert conn.execute("SELECT person_id, locked FROM faces WHERE id = ?", (gface,)).fetchone()[:] == (red["id"], 1)
    # the other green face is now alone: below MIN_GROUP, so its group disappears...
    assert all(conn.execute("SELECT 1 FROM persons WHERE id = ?", (f[0],)).fetchone()
               for f in conn.execute("SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL"))
    # ...but a group that holds a face you placed is kept even when small
    lone = conn.execute("SELECT id FROM faces WHERE person_id IS NULL").fetchone()[0]
    conn.execute("INSERT INTO persons (name, created) VALUES (NULL, 'now')")
    pid = conn.execute("SELECT MAX(id) FROM persons").fetchone()[0]
    faces.move_face(conn, lone, pid)
    faces.group_faces(conn)
    assert conn.execute("SELECT person_id FROM faces WHERE id = ?", (lone,)).fetchone()[0] == pid


def test_face_scan_records_unreadable_files(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    orig.mkdir(parents=True)
    (orig / "bad.jpg").write_bytes(b"not a photo")
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    assert faces.scan_photos(conn, orig, FakeFaceModel()) == 1
    assert faces.pending(conn) == []                       # not retried forever
    assert faces.counts(conn)["scanned"] == 1
    from test_search import FakeModel
    assert search.index_photos(conn, lib, orig, FakeModel()) == 0
    assert search.pending(conn) == []


def test_chapter_ids_are_stable_across_rebuilds(life):  # noqa: F811
    conn, _ = life
    before = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM chapters")}
    chapters.rebuild(conn)
    chapters.rebuild(conn)
    after = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM chapters")}
    assert before == after


def test_description_errors_are_retried(tmp_path):
    lib = tmp_path / "lib"
    from test_describe import make_photo as mk
    mk(lib / "originals/IMG_0.JPG", (20, 200, 20), 0)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, lib / "originals")
    r = describe.run(conn, lib / "originals", FakeClient(fail_on={"green"}), "claude-haiku-5-5")
    assert r["errors"] == 1 and len(describe.pending(conn)) == 1
    r = describe.run(conn, lib / "originals", FakeClient(), "claude-haiku-5-5")
    assert r["described"] == 1 and describe.pending(conn) == []


def test_status_is_cached_briefly(tmp_path, monkeypatch):
    from image_magic.server import App
    from image_magic.settings import Settings
    Settings(apple_id="x", library_path=str(tmp_path / "lib")).save()
    catalog.connect(tmp_path / "lib" / "library.db").close()
    app = App()
    calls = []
    real = catalog.stats
    monkeypatch.setattr(catalog, "stats", lambda conn: (calls.append(1), real(conn))[1])
    app.status(); app.status(); app.status()
    assert len(calls) == 1
