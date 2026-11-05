import json
import threading
import time
import urllib.error
import urllib.request

import pytest
from PIL import Image

from image_magic import cli
from image_magic.server import App, make_server
from image_magic.settings import Settings


@pytest.fixture
def running(tmp_path):
    app = App()
    server = make_server(app, 0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{port}"
    server.shutdown()


def call(base, path, body=None, token=None, host=None):
    req = urllib.request.Request(base + path)
    if token:
        req.add_header("X-Token", token)
    if host:
        req.add_header("Host", host)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_page_does_not_contain_token(running):
    """Any local program can fetch the page, so it must not leak the token."""
    app, base = running
    html = urllib.request.urlopen(base + "/").read().decode()
    assert "Image Magic" in html and app.token not in html


def test_session_file_roundtrip(tmp_path):
    from image_magic import app as appmod
    appmod._write_session(8765, "tok123")
    assert appmod._read_session()["token"] == "tok123"
    assert appmod.app_url(8765, "tok123") == "http://127.0.0.1:8765/#t=tok123"
    appmod._clear_session("other")          # someone else's token: keep
    assert appmod._read_session() is not None
    appmod._clear_session("tok123")
    assert appmod._read_session() is None


def test_api_requires_token(running):
    _, base = running
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/status")
    assert e.value.code == 403


def test_api_rejects_foreign_host(running):
    app, base = running
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/status", token=app.token, host="evil.example.com")
    assert e.value.code == 403


def test_settings_save_and_load(running, tmp_path):
    app, base = running
    lib = tmp_path / "lib"
    saved = call(base, "/api/settings", {"apple_id": "me@example.com", "library_path": str(lib),
                                         "download_videos": False}, token=app.token)
    assert saved["download_videos"] is False
    assert Settings.load().apple_id == "me@example.com"
    st = call(base, "/api/status", token=app.token)
    assert st["configured"] and st["library"] == str(lib)


def test_sync_needs_apple_id(running):
    app, base = running
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/run", {"kind": "sync", "mode": "test"}, token=app.token)
    assert e.value.code == 400


def test_scan_and_verify_jobs(running, tmp_path):
    app, base = running
    lib = tmp_path / "lib"
    Settings(apple_id="me@example.com", library_path=str(lib)).save()
    photo = lib / "originals/2019/2019-03/IMG_0001.JPG"
    photo.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(photo)

    for kind in ("scan", "verify"):
        call(base, "/api/run", {"kind": kind}, token=app.token)
        for _ in range(50):
            job = call(base, "/api/status", token=app.token)["job"]
            if job["state"] not in ("running",):
                break
            time.sleep(0.1)
        assert job["state"] == "done", job
    st = call(base, "/api/status", token=app.token)["stats"]
    assert st["photos"] == 1 and st["last_verify"]
    log = call(base, "/api/log?since=0", token=app.token)
    assert any("Check finished" in l["line"] for l in log)


def test_cli_init_scan_status(tmp_path, capsys):
    lib = tmp_path / "lib"
    assert cli.main(["--library", str(lib), "init", "--apple-id", "me@example.com"]) == 0
    photo = lib / "originals/2019/2019-03/IMG_0001.JPG"
    photo.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8)).save(photo)
    # settings now remember the library, so --library is no longer needed
    assert cli.main(["scan"]) == 0
    assert cli.main(["verify"]) == 0
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "1 new" in out and "1 OK" in out and "Photos:       1" in out
