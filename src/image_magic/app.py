"""Desktop entry point: start the local server and open the app in the browser.

The access token is never put in the page itself (any program on the computer,
including other Windows accounts, could fetch the page). It goes only into the
URL fragment handed to the browser, and into session.json inside the user's own
settings folder so a second double-click can reopen the app.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser

from .server import App, make_server
from .settings import settings_dir

DEFAULT_PORT = 8765


def _session_file():
    return settings_dir() / "session.json"


def _write_session(port: int, token: str) -> None:
    f = _session_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(f)


def _read_session() -> dict | None:
    try:
        return json.loads(_session_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _clear_session(token: str) -> None:
    s = _read_session()
    if s and s.get("token") == token:
        try:
            _session_file().unlink()
        except OSError:
            pass


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
            return b"Image Magic" in resp.read(4096)
    except Exception:
        return False


def app_url(port: int, token: str) -> str:
    return f"http://127.0.0.1:{port}/#t={token}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="image-magic-app")
    parser.add_argument("--port", type=int, default=int(os.environ.get("IMAGE_MAGIC_PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if _already_running(args.port):
        # Second double-click: reopen the existing app with its token.
        s = _read_session()
        if not args.no_browser and s and s.get("port") == args.port:
            webbrowser.open(app_url(args.port, s["token"]))
        return 0

    stop = threading.Event()
    app = App(on_quit=stop.set)
    server = make_server(app, args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _write_session(args.port, app.token)
    print(f"Image Magic is running at http://127.0.0.1:{args.port}/  (close it from the app's Quit button)")
    if not args.no_browser:
        webbrowser.open(app_url(args.port, app.token))

    try:
        while not stop.wait(30):
            app.auto_sync_tick()
    except KeyboardInterrupt:
        pass
    app.jobs.cancel()
    _clear_session(app.token)
    time.sleep(0.5)
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
