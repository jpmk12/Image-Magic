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


def _already_running(port: int) -> str:
    """'ours' if our own app answers on the port with the saved token, 'foreign' if
    something else is listening, '' if the port is free."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
            resp.read(4096)
    except Exception:
        return ""
    s = _read_session()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status", headers={"X-Token": (s or {}).get("token", "")})
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200 and "version" in json.loads(resp.read().decode("utf-8")):
                return "ours"
    except Exception:
        pass
    return "foreign"


def _alert(message: str) -> None:
    """Tell the user something when there is no console (windowed exe)."""
    print(message)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Image Magic", 0x10)
        except Exception:
            pass


def app_url(port: int, token: str) -> str:
    return f"http://127.0.0.1:{port}/#t={token}"


def self_test(out_path: str, download: bool) -> int:
    """Used by the Windows build to check the packaged app end to end.

    Writes a JSON report (a windowed .exe has no console to print to).
    """
    report: dict = {"ok": False}
    try:
        import numpy as np
        import onnxruntime as ort
        from PIL import Image
        from . import faces, places, search
        from .clip_tokenizer import Tokenizer

        from . import describe
        report["ai_client_ok"] = bool(describe.make_client("sk-ant-not-a-real-key"))   # SDK bundled, no network
        from reportlab.pdfgen import canvas as _pdf   # noqa: F401  (book export bundled)
        report["pdf_ok"] = True
        from . import phone
        cert, _key = phone.ensure_certificate(["192.0.2.1"])       # cryptography bundled: certificate can be made
        report["phone_cert_ok"] = cert.exists()
        report["providers"] = ort.get_available_providers()
        report["tokens"] = Tokenizer().encode("a photo of a cat")
        report["place"] = places.lookup(30.3285, 35.4444).city
        if download:
            search.download_model()
        if search.model_ready():
            model = search.Model()
            report["device"] = model.device
            red = Image.new("RGB", (300, 200), (230, 20, 20))
            green = Image.new("RGB", (300, 200), (20, 200, 40))
            vecs = model.encode_images([red, green])
            q = search.query_vector(model, "something bright red")
            report["red_vs_green"] = [round(float(x), 4) for x in vecs @ q]
            report["search_ok"] = bool(np.argmax(vecs @ q) == 0)
        if download:
            faces.download_model()
        if faces.model_ready():
            fm = faces.FaceModel()
            report["face_device"] = fm.device
            # A synthetic 'face': the detector should find nothing in a flat image and not crash.
            report["faces_in_blank"] = len(fm.detect(Image.new("RGB", (640, 480), (90, 90, 90))))
            e = fm.embed([Image.new("RGB", (112, 112), (120, 100, 90))])
            report["face_embed_ok"] = bool(e.shape == (1, 512) and abs(float(np.linalg.norm(e[0])) - 1) < 1e-3)
        report["ok"] = report["tokens"] == [320, 1125, 539, 320, 2368] and report["place"] == "Petra" \
            and report.get("ai_client_ok") and report.get("phone_cert_ok") \
            and report.get("search_ok", not download) \
            and report.get("face_embed_ok", not download) and report.get("faces_in_blank", 0) == 0
    except Exception as e:  # report, don't crash
        import traceback
        report["error"] = traceback.format_exc()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="image-magic-app")
    parser.add_argument("--port", type=int, default=int(os.environ.get("IMAGE_MAGIC_PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--self-test", metavar="REPORT.json", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-download", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test(args.self_test, args.self_test_download)

    running = _already_running(args.port)
    if running == "ours":
        # Second double-click: reopen the existing app with its token.
        s = _read_session()
        if not args.no_browser and s and s.get("port") == args.port:
            webbrowser.open(app_url(args.port, s["token"]))
        return 0
    if running == "foreign":
        _alert(f"Another program is using port {args.port} on this computer, so Image Magic can't start. "
               "Close it, or start Image Magic with --port <number>.")
        return 1

    stop = threading.Event()
    app = App(on_quit=stop.set)
    server = make_server(app, args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _write_session(args.port, app.token)
    app.apply_phone_setting(port=args.port + 1)
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
