"""Local web server for the app. Listens on 127.0.0.1 only.

Every API call must carry the per-launch token embedded in the page, so other
websites open in the browser cannot drive the app.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from . import __version__, catalog
from .jobs import JobRunner
from .settings import Settings

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class App:
    def __init__(self, settings_provider=Settings.load, on_quit=None):
        self.token = secrets.token_urlsafe(24)
        self.settings_provider = settings_provider
        self.jobs = JobRunner(settings_provider)
        self.on_quit = on_quit
        self._last_auto = time.time()

    # ---- API handlers (return JSON-able dicts) -------------------------
    def status(self) -> dict:
        s = self.settings_provider()
        stats = None
        if s.db.exists():
            conn = catalog.connect(s.db)
            try:
                stats = catalog.stats(conn)
            finally:
                conn.close()
        return {
            "version": __version__,
            "configured": bool(s.apple_id),
            "library": str(s.library),
            "stats": stats,
            "job": self.jobs.snapshot(),
        }

    def get_settings(self) -> dict:
        return self.settings_provider().to_dict()

    def save_settings(self, data: dict) -> dict:
        if self.jobs.busy:
            raise ValueError("Wait for the current task to finish before changing settings.")
        s = Settings.from_dict(data)
        s.save()
        return s.to_dict()

    def auto_sync_tick(self) -> None:
        s = self.settings_provider()
        if not s.auto_sync_hours or not s.apple_id or self.jobs.busy:
            return
        if time.time() - self._last_auto >= s.auto_sync_hours * 3600:
            self._last_auto = time.time()
            self.jobs.start("sync", "quick")


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def make_handler(app: App):
    page = (resources.files("image_magic") / "static" / "index.html").read_text(encoding="utf-8")
    page = page.replace("__TOKEN__", app.token)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ImageMagic"

        def log_message(self, *args):  # keep the console quiet
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in ALLOWED_HOSTS

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _authorized(self) -> bool:
            if not self._host_ok():
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
                return False
            if not secrets.compare_digest(self.headers.get("X-Token", ""), app.token):
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad token"})
                return False
            return True

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise ValueError("request too large")
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                if not self._host_ok():
                    return self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if not self._authorized():
                return
            try:
                if path == "/api/status":
                    return self._json(200, app.status())
                if path == "/api/settings":
                    return self._json(200, app.get_settings())
                if path == "/api/log":
                    since = int(self.path.partition("since=")[2] or 0)
                    return self._json(200, app.jobs.log_since(since))
            except Exception as e:
                return self._json(500, {"error": str(e)})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorized():
                return
            path = self.path.split("?", 1)[0]
            try:
                data = self._body()
                if path == "/api/settings":
                    return self._json(200, app.save_settings(data))
                if path == "/api/run":
                    kind = data.get("kind", "sync")
                    mode = data.get("mode", "full")
                    if kind not in ("sync", "scan", "verify") or mode not in ("full", "quick", "test"):
                        raise ValueError("unknown task")
                    if kind == "sync" and not app.settings_provider().apple_id:
                        raise ValueError("Enter your Apple ID in Settings first.")
                    app.jobs.start(kind, mode)
                    return self._json(200, app.jobs.snapshot())
                if path == "/api/cancel":
                    app.jobs.cancel()
                    return self._json(200, {"ok": True})
                if path == "/api/auth":
                    field = data.get("field", "")
                    ok = app.jobs.submit_auth(field, str(data.get("value", "")))
                    return self._json(200 if ok else 400, {"ok": ok})
                if path == "/api/open-folder":
                    _open_folder(app.settings_provider().library)
                    return self._json(200, {"ok": True})
                if path == "/api/quit":
                    app.jobs.cancel()
                    self._json(200, {"ok": True})
                    if app.on_quit:
                        threading.Thread(target=app.on_quit, daemon=True).start()
                    return
            except (ValueError, RuntimeError) as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                return self._json(500, {"error": str(e)})
            self._json(404, {"error": "not found"})

    return Handler


def make_server(app: App, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    server.daemon_threads = True
    return server
