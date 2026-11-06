"""Voice notes: saved locally, listed, played in replays, exported only on request."""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime

from image_magic import browse, catalog, notes, share
from image_magic.server import App, make_server
from image_magic.settings import Settings
from test_places_browse import HOME, make_photo


def test_voice_notes_roundtrip(tmp_path):
    lib = tmp_path / "lib"
    make_photo(lib / "originals/a.jpg", datetime(2020, 5, 5, 10), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, lib / "originals")
    aid = conn.execute("SELECT id FROM assets").fetchone()[0]
    n = notes.save(conn, lib, "photo", aid, b"OggS fake audio", "audio/ogg; codecs=opus", 3.5)
    assert (lib / "voice" / f"photo-{aid}-{n['id']}.ogg").read_bytes() == b"OggS fake audio"
    assert notes.list_for(conn, "photo", aid)[0]["seconds"] == 3.5 and notes.count(conn) == 1
    p, mime = notes.path_of(conn, lib, n["id"])
    assert mime == "audio/ogg" and p.is_file()
    assert browse.replay(conn, {"year": "2020"})[0]["voice_id"] == n["id"]
    # exports leave the voice out unless asked
    folder = share.export(conn, lib / "originals", lib, {"year": "2020"}, "Quiet")
    assert "<audio" not in (folder / "index.html").read_text()
    folder = share.export(conn, lib / "originals", lib, {"year": "2020"}, "Loud", voice=True)
    assert "<audio" in (folder / "index.html").read_text() and (folder / "images/0001.ogg").is_file()
    import pytest
    with pytest.raises(ValueError):
        notes.save(conn, lib, "photo", aid, b"x", "video/mp4")
    with pytest.raises(ValueError):
        notes.save(conn, lib, "nope", aid, b"x", "audio/webm")
    notes.delete(conn, lib, n["id"])
    assert notes.list_for(conn, "photo", aid) == [] and not p.exists()


def test_voice_api(tmp_path):
    lib = tmp_path / "lib"
    make_photo(lib / "originals/a.jpg", datetime(2020, 5, 5, 10), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, lib / "originals")
    aid = conn.execute("SELECT id FROM assets").fetchone()[0]
    conn.close()
    Settings(apple_id="x", library_path=str(lib)).save()
    app = App()
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        req = urllib.request.Request(f"{base}/api/voice?kind=photo&id={aid}&seconds=2", data=b"\x1aE\xdf\xa3webm", method="POST",
                                     headers={"X-Token": app.token, "Content-Type": "audio/webm"})
        note = json.loads(urllib.request.urlopen(req).read())
        got = urllib.request.urlopen(urllib.request.Request(f"{base}/media/voice/{note['id']}?t={app.token}"))
        assert got.headers["Content-Type"] == "audio/webm" and got.read() == b"\x1aE\xdf\xa3webm"
        d = json.loads(urllib.request.urlopen(urllib.request.Request(f"{base}/api/photo/{aid}", headers={"X-Token": app.token})).read())
        assert d["voice"][0]["id"] == note["id"]
        # no token: refused
        try:
            urllib.request.urlopen(urllib.request.Request(f"{base}/api/voice?kind=photo&id={aid}", data=b"x", method="POST",
                                                          headers={"Content-Type": "audio/webm"}))
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        srv.shutdown()
