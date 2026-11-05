"""Desktop entry point: start the local server and open the app in the browser."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.request
import webbrowser

from .server import App, make_server

DEFAULT_PORT = 8765


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
            return b"Image Magic" in resp.read(4096)
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="image-magic-app")
    parser.add_argument("--port", type=int, default=int(os.environ.get("IMAGE_MAGIC_PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    url = f"http://127.0.0.1:{args.port}/"

    if _already_running(args.port):
        # Second double-click: just bring up the existing app.
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    stop = threading.Event()
    app = App(on_quit=stop.set)
    server = make_server(app, args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Image Magic is running at {url}  (close it from the app's Quit button)")
    if not args.no_browser:
        webbrowser.open(url)

    try:
        while not stop.wait(30):
            app.auto_sync_tick()
    except KeyboardInterrupt:
        pass
    app.jobs.cancel()
    time.sleep(0.5)
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
