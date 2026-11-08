"""Video posters: the page sends a decoded first frame; the app keeps it as the video's thumbnail."""
import io
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime

import pytest
from PIL import Image

from image_magic import catalog, media
from image_magic.server import App, make_server
from image_magic.settings import Settings
from test_places_browse import HOME, make_photo


def test_poster_roundtrip(tmp_path):
    lib = tmp_path / "lib"
    make_photo(lib / "originals/a.jpg", datetime(2020, 5, 5, 10), HOME)
    (lib / "originals/clip.mov").write_bytes(b"not really a movie")
    conn = catalog.connect(lib / "library.db"); catalog.scan(conn, lib / "originals")
    vid = conn.execute("SELECT id, sha256 FROM assets WHERE kind = 'video'").fetchone()
    conn.close()
    Settings(apple_id="x", library_path=str(lib)).save()
    app = App(); srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(f"{base}/media/thumb/{vid['id']}", headers={"X-Token": app.token}))
        assert e.value.code == 404
        buf = io.BytesIO(); Image.new("RGB", (640, 360), (200, 30, 30)).save(buf, "JPEG")
        req = urllib.request.Request(f"{base}/api/poster?id={vid['id']}", data=buf.getvalue(), method="POST", headers={"X-Token": app.token, "Content-Type": "image/jpeg"})
        assert json.loads(urllib.request.urlopen(req).read()) == {"ok": True}
        got = urllib.request.urlopen(urllib.request.Request(f"{base}/media/thumb/{vid['id']}", headers={"X-Token": app.token}))
        assert got.headers["Content-Type"] == "image/jpeg"
        with Image.open(io.BytesIO(got.read())) as im:
            assert max(im.size) <= 400
        assert media.poster_path(lib, vid["sha256"]).is_file()
        # junk is refused; photos cannot be overwritten through this route
        bad = urllib.request.Request(f"{base}/api/poster?id={vid['id']}", data=b"garbage", method="POST", headers={"X-Token": app.token, "Content-Type": "image/jpeg"})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
        photo = conn_id = None
        c2 = catalog.connect(lib / "library.db"); photo = c2.execute("SELECT id FROM assets WHERE kind = 'photo'").fetchone()[0]; c2.close()
        with pytest.raises(urllib.error.HTTPError) as e2:
            urllib.request.urlopen(urllib.request.Request(f"{base}/api/poster?id={photo}", data=buf.getvalue(), method="POST", headers={"X-Token": app.token, "Content-Type": "image/jpeg"}))
        assert e2.value.code == 404
    finally:
        srv.shutdown()
