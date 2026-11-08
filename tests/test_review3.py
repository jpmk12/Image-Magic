"""Regression tests for the third review round (v0.44–v0.49 changes)."""
import io
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime

import pytest
from PIL import Image

from image_magic import catalog, media, metadata, phone
from image_magic.server import App, make_server
from image_magic.settings import Settings
from test_places_browse import HOME, make_photo


def test_odd_takeout_sidecars_never_raise(tmp_path):
    p = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(p, "JPEG")
    for payload in ('{"title": "a.jpg", "photoTakenTime": "x"}',
                    '{"title": "a.jpg", "photoTakenTime": {"timestamp": "1"}, "geoData": [1, 2]}',
                    '{"title": "a.jpg", "photoTakenTime": {"timestamp": "1"}, "people": [{"name": 5}, 7]}',
                    '{"title": "a.jpg", "photoTakenTime": {"timestamp": "abc"}, "description": 3}',
                    '[1, 2, 3]', 'not json at all', ''):
        (tmp_path / "a.jpg.json").write_text(payload)
        metadata._google_index.cache_clear()
        assert metadata.read_google_sidecar(p) is None or isinstance(metadata.read_google_sidecar(p), metadata.SidecarMeta)
    # and the index notices sidecars that appear later in the same folder (once cleared, as the importer does)
    (tmp_path / "a.jpg.json").unlink()
    metadata._google_index.cache_clear()
    assert metadata.read_google_sidecar(p) is None
    (tmp_path / "odd-name.json").write_text(json.dumps({"title": "a.jpg", "photoTakenTime": {"timestamp": "1000000000"}}))
    assert metadata.read_google_sidecar(p) is None          # cached index from before
    metadata._google_index.cache_clear()
    assert metadata.read_google_sidecar(p).taken_at.startswith("2001")


def test_poster_first_writer_wins_and_failures_are_remembered(tmp_path):
    lib = tmp_path / "lib"
    make_photo(lib / "originals/a.jpg", datetime(2020, 5, 5, 10), HOME)
    (lib / "originals/clip.mov").write_bytes(b"not really a movie")
    conn = catalog.connect(lib / "library.db"); catalog.scan(conn, lib / "originals")
    vid = conn.execute("SELECT id, sha256 FROM assets WHERE kind = 'video'").fetchone(); conn.close()
    Settings(apple_id="x", library_path=str(lib)).save()
    app = App(); srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    h = {"X-Token": app.token, "Content-Type": "image/jpeg"}
    def post(url, data=b""):
        return urllib.request.urlopen(urllib.request.Request(base + url, data=data, method="POST", headers=h))
    def thumb():
        return urllib.request.urlopen(urllib.request.Request(f"{base}/media/thumb/{vid['id']}", headers=h))
    try:
        # the laptop could not decode it: a marker is kept and the thumb becomes a plain frame instead of a 404
        with pytest.raises(urllib.error.HTTPError):
            thumb()
        assert json.loads(post(f"/api/poster?id={vid['id']}&failed=1").read()) == {"ok": True}
        assert media.poster_failed_path(lib, vid["sha256"]).exists()
        got = thumb(); assert got.headers["Content-Type"] == "image/jpeg"
        with Image.open(io.BytesIO(got.read())) as im:
            assert max(im.getpixel((5, 5))) < 60                   # the plain dark frame
        # a real poster replaces the marker; a second upload cannot overwrite it
        red = io.BytesIO(); Image.new("RGB", (640, 360), (200, 30, 30)).save(red, "JPEG")
        post(f"/api/poster?id={vid['id']}", red.getvalue())
        assert not media.poster_failed_path(lib, vid["sha256"]).exists()
        blue = io.BytesIO(); Image.new("RGB", (640, 360), (30, 30, 200)).save(blue, "JPEG")
        post(f"/api/poster?id={vid['id']}", blue.getvalue())
        with Image.open(io.BytesIO(thumb().read())) as im:
            assert im.getpixel((5, 5))[0] > 150                    # still red
        # an absurdly large image is refused before it is decoded
        huge = io.BytesIO(); Image.new("1", (9000, 9000)).save(huge, "PNG")
        with pytest.raises(urllib.error.HTTPError):
            post(f"/api/poster?id={vid['id']}", huge.getvalue())
    finally:
        srv.shutdown()
    # the marker is never written over an existing poster
    media.save_poster(lib, vid["sha256"], red.getvalue())
    assert media.poster_path(lib, vid["sha256"]).is_file()


def test_phone_cannot_read_exports_or_mark_posters_failed():
    assert not phone.phone_allowed("/media/export/share-x.zip", "GET")
    assert phone.phone_allowed("/api/poster", "POST") and phone.phone_allowed("/media/thumb/1", "GET")


def test_sync_catalogues_first_account_when_second_fails(tmp_path, monkeypatch):
    """Account 1 downloads, account 2 fails: the job still catalogues and reports the failure."""
    from image_magic import jobs as J
    lib = tmp_path / "lib"
    make_photo(lib / "originals/2019/a.jpg", datetime(2019, 1, 1, 12), HOME)
    settings = Settings(apple_id="me@x.com", apple_id_2="them@x.com", contributor_2="Sam", library_path=str(lib))
    runner = J.JobRunner(lambda: settings)
    calls = []
    def fake_download(self, settings_, cmd, mode, apple_id):
        calls.append(apple_id)
        if apple_id == "them@x.com":
            return ("failed", "wrong password")
        self.state.downloaded += 3
        make_photo(lib / "originals/2019/new.jpg", datetime(2019, 1, 2, 12), HOME)
        return None
    monkeypatch.setattr(J.JobRunner, "_download", fake_download)
    monkeypatch.setattr(J.sync, "find_icloudpd", lambda: "icloudpd")
    runner.start("sync", "quick")
    for _ in range(200):
        if runner.state.state in ("done", "failed", "cancelled"):
            break
        import time; time.sleep(0.05)
    assert calls == ["me@x.com", "them@x.com"]
    assert runner.state.state == "failed" and "wrong password" in runner.state.message and "3 downloaded" in runner.state.message
    conn = catalog.connect(lib / "library.db")
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 2        # the first account's photos were catalogued
    assert (lib / "originals/accounts/Sam").is_dir()
