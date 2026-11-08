"""Regression tests for the second review round (security, correctness, robustness)."""
import ssl
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from image_magic import browse, catalog, cleanup, datewords, events, phone, quality
from image_magic.server import App, make_phone_server
from image_magic.settings import Settings
from test_importer import make_photo as textured
from test_places_browse import HOME, make_photo


def test_phone_allowlist():
    assert phone.phone_allowed("/api/photos", "GET") and phone.phone_allowed("/media/thumb/1", "GET") and phone.phone_allowed("/api/ask", "POST")
    for path in ("/api/phone", "/api/log", "/api/settings", "/api/book", "/api/people/book", "/api/review-queue", "/api/inbox", "/api/cleanup", "/api/bursts"):
        assert not phone.phone_allowed(path, "GET"), path
    for path in ("/api/private", "/api/curate/trip-delete", "/api/albums/delete", "/api/people/rename", "/api/location/set",
                 "/api/review-queue/decide", "/api/story/write", "/api/inbox/accept", "/api/cleanup/trash", "/api/run", "/api/settings"):
        assert not phone.phone_allowed(path, "POST"), path
    assert not phone.phone_allowed("/api/photos", "DELETE")


def test_phone_cannot_read_code_or_log_or_curate(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_MAGIC_HOME", str(tmp_path / "home"))
    lib = tmp_path / "lib"
    make_photo(lib / "originals/a.jpg", datetime(2020, 5, 5, 10), HOME)
    conn = catalog.connect(lib / "library.db"); catalog.scan(conn, lib / "originals"); conn.close()
    Settings(apple_id="x", library_path=str(lib), phone_access=True).save()
    monkeypatch.setattr(phone, "lan_ips", lambda: ["127.0.0.1"])
    app = App()
    srv = make_phone_server(app, 0, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"https://127.0.0.1:{srv.server_address[1]}"
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    try:
        tok = app.pairing.try_code(app.pairing.code()[0], "kid")
        def code_of(path, data=None):
            try:
                urllib.request.urlopen(urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET",
                                                              headers={"X-Token": tok, "Content-Type": "application/json"}), context=ctx, timeout=5)
                return 200
            except urllib.error.HTTPError as e:
                return e.code
        assert code_of("/api/photos") == 200
        assert code_of("/api/phone") == 403 and code_of("/api/log") == 403 and code_of("/api/review-queue") == 403
        assert code_of("/api/private", b'{"id": 1, "private": true}') == 403
        assert code_of("/api/curate/trip-delete", b'{"id": 1}') == 403
        assert code_of("/api/location/set", b'{"ids": [1], "lat": 0, "lon": 0}') == 403
    finally:
        srv.shutdown()


def test_event_keeps_identity_when_first_photo_goes(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2019, 7, 6, 10, 0)
    for i in range(6):
        make_photo(orig / f"e{i}.jpg", t + timedelta(minutes=15 * i), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    ev = events.events(conn)[0]
    events.rename(conn, ev["id"], "Beach morning")
    first = conn.execute("SELECT id FROM assets WHERE path = 'e0.jpg'").fetchone()[0]
    cleanup.trash(conn, lib, orig, [first])
    events.rebuild(conn)
    after = events.events(conn)
    assert len(after) == 1 and after[0]["id"] == ev["id"] and after[0]["title"] == "Beach morning" and after[0]["photo_count"] == 5


def test_trash_only_marks_what_moved_and_frees_duplicates(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    textured(orig / "a.jpg", datetime(2019, 1, 1), seed=1)
    (orig / "b.jpg").write_bytes((orig / "a.jpg").read_bytes())
    textured(orig / "c.jpg", datetime(2019, 1, 2), seed=2)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    quality.score_pending(conn, lib, orig)
    ids = {r["path"]: r["id"] for r in conn.execute("SELECT id, path FROM assets")}
    orig_id, copy_id = (ids["a.jpg"], ids["b.jpg"]) if conn.execute("SELECT duplicate_of FROM assets WHERE id = ?", (ids["b.jpg"],)).fetchone()[0] else (ids["b.jpg"], ids["a.jpg"])
    assert [i["id"] for i in cleanup.candidates(conn, lib, orig)["groups"]["duplicates"]] == [copy_id]
    # trash the original: the copy stops being "a duplicate" and is not offered for the bin
    cleanup.trash(conn, lib, orig, [orig_id])
    assert conn.execute("SELECT duplicate_of FROM assets WHERE id = ?", (copy_id,)).fetchone()[0] is None
    assert cleanup.candidates(conn, lib, orig)["groups"]["duplicates"] == []
    # a move that fails leaves the photo visible and unmarked
    import shutil
    def boom(*a, **k):
        raise OSError("busy")
    monkeypatch.setattr(shutil, "move", boom)
    r = cleanup.trash(conn, lib, orig, [ids["c.jpg"]])
    assert r["trashed"] == 0 and r["failed"] and conn.execute("SELECT trashed_at, missing FROM assets WHERE id = ?", (ids["c.jpg"],)).fetchone()[:] == (None, 0)
    # trashing an id whose file is already gone does nothing (so Restore can't resurrect a ghost)
    monkeypatch.undo()
    (orig / "c.jpg").unlink()
    assert cleanup.trash(conn, lib, orig, [ids["c.jpg"]])["trashed"] == 0


def test_small_robustness_fixes(tmp_path):
    from image_magic import metadata
    assert datewords._season("winter", 2020) == (datetime(2019, 12, 1).date(), datetime(2020, 2, 29).date())
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    assert browse.ids(conn, {"days": "x"}) == set()               # a string from a URL is ignored, not a 500
    bad = tmp_path / "bad.mov"
    # a 'data' atom shorter than its header must not read the file into memory
    import struct
    def box(t, payload): return struct.pack(">I4s", 8 + len(payload), t) + payload
    keys = box(b"keys", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">I4s", 8 + 36, b"mdta") + b"com.apple.quicktime.location.ISO6709")
    ilst = box(b"ilst", box(struct.pack(">I", 1), box(b"data", b"\x00\x00\x00")))
    bad.write_bytes(box(b"ftyp", b"qt  ") + box(b"moov", box(b"meta", b"\x00\x00\x00\x00" + keys + ilst)))
    assert metadata.read_video_meta(bad).lat is None
