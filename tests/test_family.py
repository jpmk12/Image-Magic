"""Family edition: contributions from paired phones, attributed and deduplicated."""
import io
import json
import ssl
import threading
import urllib.request
from datetime import datetime

from PIL import Image

from image_magic import browse, catalog, family, phone
from image_magic.server import App, make_phone_server
from image_magic.settings import Settings
from test_importer import make_photo as textured
from test_places_browse import HOME, make_photo


def _jpeg(path):
    return path.read_bytes()


def test_contribute_adds_once_and_attributes(tmp_path):
    lib = tmp_path / "lib"
    textured(lib / "originals/a.jpg", datetime(2020, 5, 5, 10), seed=1)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, lib / "originals")
    from image_magic import quality
    quality.score_pending(conn, lib, lib / "originals")       # fills phash for look-alike checks
    # the same file again: exact duplicate, nothing stored
    r = family.contribute(conn, lib, lib / "originals", _jpeg(lib / "originals/a.jpg"), "image/jpeg", "Mom", "a.jpg")
    assert r["status"] == "exact"
    # a resized re-save: look-alike, refused
    with Image.open(lib / "originals/a.jpg") as im:
        buf = io.BytesIO(); im.resize((300, 200)).save(buf, "JPEG", quality=70)
    assert family.contribute(conn, lib, lib / "originals", buf.getvalue(), "image/jpeg", "Mom")["status"] == "near"
    # a new photo: stored under contributed/<name>/<year>/<month>, catalogued, place looked up
    textured(tmp_path / "new.jpg", datetime(2015, 6, 1, 9), HOME, seed=42)
    r = family.contribute(conn, lib, lib / "originals", _jpeg(tmp_path / "new.jpg"), "image/jpeg", "Mom", "IMG_9.jpg")
    assert r["status"] == "added" and r["path"] == "contributed/Mom/2015/2015-06/IMG_9.jpg"
    d = browse.photo_detail(conn, r["id"])
    assert d["contributor"] == "Mom" and d["taken_at"].startswith("2015-06-01") and d["city"]
    assert family.contributors(conn) == [{"name": "Mom", "photos": 1, "last": d and family.contributors(conn)[0]["last"]}]
    assert [i["id"] for i in browse.photos(conn, {"contributor": "Mom"})["items"]] == [r["id"]]
    # the next scan keeps it (same file, unchanged) and no duplicate row appears
    res = catalog.scan(conn, lib / "originals")
    assert res.new == 0 and conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 2
    import pytest
    with pytest.raises(ValueError):
        family.contribute(conn, lib, lib / "originals", b"x", "text/plain", "Mom")


def test_pairings_persist_and_carry_names(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_MAGIC_HOME", str(tmp_path))
    p = phone.Pairing()
    code, _ = p.code()
    tok = p.try_code(code, "Dad")
    again = phone.Pairing()                     # a restart
    assert again.valid(tok) and again.name_of(tok) == "Dad" and again.phones()[0]["name"] == "Dad"
    assert tok not in (tmp_path / "phones.json").read_text()   # only the hash is stored
    again.reset()
    assert not phone.Pairing().valid(tok)


def test_phone_can_add_photos(tmp_path, monkeypatch):
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
        code, _ = app.pairing.code()
        tok = app.pairing.try_code(code, "Grandma")
        textured(tmp_path / "g.jpg", datetime(2018, 8, 8, 8), HOME, seed=5)
        req = urllib.request.Request(base + "/api/contribute?name=g.jpg", data=(tmp_path / "g.jpg").read_bytes(), method="POST",
                                     headers={"X-Token": tok, "Content-Type": "image/jpeg"})
        r = json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())
        assert r["status"] == "added" and r["path"].startswith("contributed/Grandma/2018/2018-08/")
        f = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/family", headers={"X-Token": tok}), context=ctx, timeout=5).read())
        assert f["contributors"][0]["name"] == "Grandma" and f["phones"][0]["name"] == "Grandma"
    finally:
        srv.shutdown()
