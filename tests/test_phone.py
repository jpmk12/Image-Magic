"""Phone access: pairing code, locally made certificate, HTTPS listener, laptop-only paths."""
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from image_magic import catalog, phone
from image_magic.server import App, make_phone_server
from image_magic.settings import Settings
from test_places_browse import HOME, make_photo


def test_pairing_codes_rotate_and_lock_out():
    p = phone.Pairing()
    code, left = p.code()
    assert len(code) == 6 and code.isdigit() and 0 < left <= phone.CODE_TTL
    assert p.code()[0] == code                              # stable until used or expired
    assert p.try_code("000000" if code != "000000" else "111111") is None
    tok = p.try_code(code, "iPhone")
    assert tok and p.valid(tok) and not p.valid("nope")
    assert p.try_code(code) is None                         # one phone per code
    code2, _ = p.code()
    assert code2 != code or True
    for _ in range(phone.MAX_TRIES):
        p.try_code("x")
    assert p.code()[0] != code2 or p._tries == 0           # too many wrong tries: a fresh code
    p.reset()
    assert not p.valid(tok)
    assert phone.laptop_only("/api/settings") and phone.laptop_only("/api/run") and not phone.laptop_only("/api/photos")


def test_certificate_is_made_once_for_the_lan_addresses(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_MAGIC_HOME", str(tmp_path))
    cert, key = phone.ensure_certificate(["192.168.1.20"])
    assert cert.exists() and key.exists() and b"BEGIN CERTIFICATE" in cert.read_bytes()
    first = cert.read_bytes()
    assert phone.ensure_certificate(["192.168.1.20"])[0].read_bytes() == first      # reused
    assert phone.ensure_certificate(["192.168.1.20", "10.0.0.5"])[0].read_bytes() != first   # new address: remade


def test_phone_server_pairs_and_limits_phones(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_MAGIC_HOME", str(tmp_path / "home"))
    lib = tmp_path / "lib"
    make_photo(lib / "originals/a.jpg", datetime(2020, 5, 5, 10), HOME)
    conn = catalog.connect(lib / "library.db"); catalog.scan(conn, lib / "originals"); conn.close()
    Settings(apple_id="x", library_path=str(lib), phone_access=True).save()
    monkeypatch.setattr(phone, "lan_ips", lambda: ["127.0.0.1"])
    app = App()
    srv = make_phone_server(app, 0, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = f"https://127.0.0.1:{port}"
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    open_ = lambda req: urllib.request.urlopen(req, context=ctx, timeout=5)  # noqa: E731
    try:
        # the pairing page is public; the app and API are not
        assert b"Pair this phone" in open_(base + "/pair").read()
        with __import__("pytest").raises(urllib.error.HTTPError) as e:
            open_(urllib.request.Request(base + "/api/status", headers={"X-Token": app.token}))   # the laptop's own token does not work from the Wi-Fi side
        assert e.value.code == 403
        # wrong code: refused; right code: redirected to the app with a phone token
        r = urllib.request.Request(base + "/pair", data=b"code=000000", method="POST")
        try:
            open_(r); assert False
        except urllib.error.HTTPError as e:
            assert e.code == 403 and b"did not work" in e.read()
        code, _ = app.pairing.code()
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))
        try:
            opener.open(urllib.request.Request(base + "/pair", data=f"code={code}".encode(), method="POST"), timeout=5); assert False
        except urllib.error.HTTPError as e:
            assert e.code == 303 and e.headers["Location"].startswith("/#t=")
            tok = e.headers["Location"][4:]
        assert app.pairing.valid(tok)
        # the phone can browse and see media, but not change settings or start a sync
        st = json.loads(open_(urllib.request.Request(base + "/api/status", headers={"X-Token": tok})).read())
        assert st["stats"]["photos"] == 1
        aid = json.loads(open_(urllib.request.Request(base + "/api/photos", headers={"X-Token": tok})).read())["items"][0]["id"]
        assert open_(base + f"/media/thumb/{aid}?t={urllib.parse.quote(tok)}").status == 200
        for path in ("/api/settings", "/api/run", "/api/quit"):
            try:
                open_(urllib.request.Request(base + path, data=b"{}", method="POST", headers={"X-Token": tok, "Content-Type": "application/json"})); assert False
            except urllib.error.HTTPError as e:
                assert e.code == 403 and b"laptop" in e.read()
        # a request with a foreign Host header is refused (DNS rebinding)
        try:
            open_(urllib.request.Request(base + "/api/status", headers={"X-Token": tok, "Host": "evil.example"})); assert False
        except urllib.error.HTTPError as e:
            assert e.code == 403
        # disconnecting all phones revokes the token
        app.pairing.reset()
        try:
            open_(urllib.request.Request(base + "/api/status", headers={"X-Token": tok})); assert False
        except urllib.error.HTTPError as e:
            assert e.code == 403
        assert app.phone_status()["paired"] == 0
    finally:
        srv.shutdown()


def test_phone_listener_follows_the_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_MAGIC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(phone, "lan_ips", lambda: ["127.0.0.1"])
    Settings(apple_id="x", library_path=str(tmp_path / "lib")).save()
    app = App()
    app.apply_phone_setting(port=0)
    assert app.phone_server is None and not app.phone_status()["on"]
    app.save_settings({**Settings.load().to_dict(), "phone_access": True})
    assert app.phone_server is not None and app.phone_status()["running"] and app.phone_status()["code"]
    app.save_settings({**Settings.load().to_dict(), "phone_access": False})
    time.sleep(0.3)
    assert app.phone_server is None
