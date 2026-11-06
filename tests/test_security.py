import http.server
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from image_magic import app as appmod, sync
from image_magic.jobs import JobRunner
from image_magic.server import App, make_server
from image_magic.settings import Settings


@pytest.fixture
def running(tmp_path):
    Settings(apple_id="me@example.com", library_path=str(tmp_path / "lib")).save()
    app = App()
    server = make_server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_page_has_csp_with_nonce_and_nosniff(running):
    app, base = running
    with urllib.request.urlopen(base + "/") as r:
        html = r.read().decode()
        csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert f'<script nonce="{app.nonce}">' in html and app.token not in html


def test_query_token_only_for_get_media(running):
    app, base = running
    # a POST with the token in the query string must be refused
    req = urllib.request.Request(base + f"/api/cancel?t={app.token}", data=b"{}", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403
    # ...and so must a GET on an API route
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + f"/api/status?t={app.token}")
    assert e.value.code == 403
    # the header still works
    req = urllib.request.Request(base + "/api/status", headers={"X-Token": app.token})
    assert urllib.request.urlopen(req).status == 200


def test_hide_person_returns_ok(running, tmp_path):
    app, base = running
    from image_magic import catalog, faces
    s = Settings.load()
    conn = catalog.connect(s.db)
    faces.ensure_schema(conn)
    conn.execute("INSERT INTO persons (name) VALUES ('X')"); conn.commit()
    pid = conn.execute("SELECT id FROM persons").fetchone()[0]
    req = urllib.request.Request(base + "/api/people/hide", data=json.dumps({"id": pid, "hidden": True}).encode(),
                                 headers={"X-Token": app.token, "Content-Type": "application/json"})
    assert json.loads(urllib.request.urlopen(req).read()) == {"ok": True}


def test_sync_refuses_when_helper_port_is_taken(tmp_path, monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); s.listen(1)
        port = s.getsockname()[1]
        monkeypatch.setattr(sync, "ICLOUDPD_PORT", port)
        monkeypatch.setattr(sync, "ICLOUDPD_WEBUI", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("IMAGE_MAGIC_ICLOUDPD", "/bin/true")
        settings = Settings(apple_id="me@example.com", library_path=str(tmp_path / "lib"))
        runner = JobRunner(lambda: settings)
        runner.start("sync", "test")
        for _ in range(50):
            if runner.state.state == "failed":
                break
            import time; time.sleep(0.1)
        assert runner.state.state == "failed" and "using port" in runner.state.message


def test_credentials_not_sent_without_verified_helper(tmp_path, monkeypatch):
    """Nothing is POSTed anywhere unless icloudpd's own page was confirmed."""
    posted = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            posted.append(self.path); self.send_response(200); self.end_headers()
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(sync, "ICLOUDPD_WEBUI", f"http://127.0.0.1:{srv.server_address[1]}")
    runner = JobRunner(lambda: Settings(apple_id="x", library_path=str(tmp_path)))
    assert runner.submit_auth("password", "hunter2") is False
    assert posted == [] and runner.state.auth_error
    srv.shutdown()


def test_launcher_distinguishes_our_app_from_a_foreign_server(running, monkeypatch, tmp_path):
    app, base = running
    port = int(base.rsplit(":", 1)[1])
    appmod._write_session(port, app.token)
    assert appmod._already_running(port) == "ours"
    appmod._write_session(port, "wrong-token")
    assert appmod._already_running(port) == "foreign"

    class Fake(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"<title>Image Magic</title>")
    srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    assert appmod._already_running(srv.server_address[1]) == "foreign"
    srv.shutdown()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]
    assert appmod._already_running(free) == ""
