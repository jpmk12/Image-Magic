"""Local web server for the app. Listens on 127.0.0.1 only.

Every API call must carry the per-launch token. The page itself does NOT contain
the token: the launcher passes it to the browser in the URL fragment (#t=...),
which is never sent to the server and can't be read by other programs or by
other websites. Requests must also name 127.0.0.1/localhost as Host, which
blocks DNS-rebinding attacks.
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
from urllib.parse import parse_qs, urlsplit

from . import __version__, browse, catalog, media
from .jobs import JobRunner
from .settings import Settings

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
STATIC_TYPES = {".js": "text/javascript", ".css": "text/css", ".png": "image/png"}


class App:
    def __init__(self, settings_provider=Settings.load, on_quit=None):
        self.token = secrets.token_urlsafe(24)
        self.settings_provider = settings_provider
        self.jobs = JobRunner(settings_provider)
        self.on_quit = on_quit
        self._last_auto = time.time()
        self.ask_history: list = []

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
            "map_online": s.map_online,
            "smart_search": s.smart_search,
            "search": self.search_counts(s),
            "people_on": s.people,
            "people": self.people_counts(s),
            "ai_on": s.ai_descriptions,
            "ai_model": s.ai_model,
            "ai": self.ai_counts(s),
            "library": str(s.library),
            "stats": stats,
            "job": self.jobs.snapshot(),
        }

    def search_counts(self, s):
        if not s.db.exists():
            return None
        try:
            from . import search
            conn = catalog.connect(s.db)
            try:
                return search.counts(conn)
            finally:
                conn.close()
        except ImportError:   # onnxruntime/numpy not installed
            return None

    def ai_client(self):
        from . import describe
        key = describe.get_api_key()
        if not key:
            raise ValueError("Add your Anthropic API key in Settings first.")
        return describe.make_client(key)

    def ai_counts(self, s):
        from . import describe
        if not s.db.exists():
            return {"has_key": bool(describe.get_api_key())}
        conn = catalog.connect(s.db)
        try:
            return describe.counts(conn, s.ai_model)
        finally:
            conn.close()

    def people_counts(self, s):
        if not s.db.exists():
            return None
        try:
            from . import faces
            conn = catalog.connect(s.db)
            try:
                return faces.counts(conn)
            finally:
                conn.close()
        except ImportError:
            return None

    def db(self):
        """Open the catalog for one request (SQLite connections aren't shared across threads)."""
        s = self.settings_provider()
        return s, catalog.connect(s.db)

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


def find_photos(conn, settings, q: dict) -> list[tuple[int, float]]:
    """Text query + filters -> [(asset_id, score)]: AI-description word matches first, then smart search."""
    from . import describe, search
    text = (q.get("q") or "").strip()[:200]
    has_filters = any(q.get(k) for k in ("year", "country", "city", "trip", "kind", "person", "month", "day", "chapter"))
    allowed = None
    if has_filters:
        allowed = {r["id"] for r in browse.photos(conn, q, 0, 10**9)["items"]}
    hits = [(i, 1.0) for i in describe.text_search(conn, text, allowed)]
    seen = {i for i, _ in hits}
    if search.model_ready():
        hits += [(i, s) for i, s in search.search(conn, str(settings.db), search.Model.get(), text, allowed)
                 if i not in seen]
    elif not hits and not describe.counts(conn)["described"]:
        raise ValueError("Search isn't set up yet. Turn on smart search or AI descriptions from the Home tab.")
    return hits


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

        def _query(self) -> dict:
            return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

        def _authorized(self) -> bool:
            if not self._host_ok():
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
                return False
            # <img>/<video> tags can't send headers, so media URLs carry ?t=<token>.
            token = self.headers.get("X-Token") or self._query().get("t", "")
            if not secrets.compare_digest(token, app.token):
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
            if path.startswith("/static/vendor/") and self._host_ok():
                return self._static(path)
            if not self._authorized():
                return
            try:
                if path == "/api/status":
                    return self._json(200, app.status())
                if path == "/api/settings":
                    from . import describe
                    return self._json(200, {**app.get_settings(), "api_key_hint": describe.key_hint(),
                                            "ai_models": {k: v[0] for k, v in describe.MODELS.items()}})
                if path == "/api/log":
                    since = int(self._query().get("since") or 0)
                    return self._json(200, app.jobs.log_since(since))
                if path.startswith(("/api/photos", "/api/photo/", "/api/facets", "/api/trips", "/api/replay",
                                    "/api/map", "/api/people", "/api/timeline", "/api/story", "/api/memories",
                                    "/api/shuffle", "/api/similar/", "/api/review", "/api/context/", "/media/")):
                    return self._browse(path, self._query())
            except Exception as e:
                return self._json(500, {"error": str(e)})
            self._json(404, {"error": "not found"})

        def _static(self, path: str):
            """Bundled third-party files (map library). Only plain names, no '..'."""
            parts = path.split("/")[3:]
            if not parts or any(p in ("", ".", "..") or "\\" in p for p in parts) \
                    or Path(parts[-1]).suffix not in STATIC_TYPES:
                return self._json(404, {"error": "not found"})
            res = resources.files("image_magic").joinpath("static", "vendor", *parts)
            if not res.is_file():
                return self._json(404, {"error": "not found"})
            self.send_response(200)
            body = res.read_bytes()
            self.send_header("Content-Type", STATIC_TYPES[Path(parts[-1]).suffix])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(body)

        def _browse(self, path: str, q: dict):
            settings, conn = app.db()
            try:
                if path == "/api/photos":
                    if q.get("q", "").strip():
                        return self._json(200, self._search(conn, settings, q))
                    return self._json(200, browse.photos(
                        conn, q, int(q.get("offset") or 0), min(int(q.get("limit") or 200), 500)))
                if path == "/api/replay":
                    if q.get("best") and q.get("year"):
                        from . import review
                        return self._json(200, browse.replay_items(conn, [i["id"] for i in review.best_of_year(conn, q["year"], 30)]))
                    return self._json(200, browse.replay(conn, q))
                if path == "/api/facets":
                    return self._json(200, browse.facets(conn))
                if path == "/api/trips":
                    return self._json(200, browse.trips(conn))
                if path == "/api/story":
                    from . import ask
                    key = f"{q.get('kind')}:{int(q.get('id') or 0)}"
                    return self._json(200, ask.get_story(conn, key) or {"key": key, "text": ""})
                if path == "/api/review":
                    from . import review
                    ys = review.years(conn)
                    y = q.get("year") or (ys[0] if ys else None)
                    return self._json(200, {"years": ys, "stats": review.year_stats(conn, y) if y else None})
                if path == "/api/memories":
                    from . import memories
                    return self._json(200, {"on_this_day": memories.on_this_day(conn),
                                            "then_now": memories.then_and_now(conn),
                                            "shuffle": memories.shuffle(conn)})
                if path == "/api/shuffle":
                    from . import memories
                    return self._json(200, memories.shuffle(conn))
                if path == "/api/timeline":
                    from . import chapters
                    return self._json(200, chapters.timeline(conn))
                if path == "/api/people":
                    from . import faces
                    return self._json(200, faces.people(conn, include_hidden=q.get("hidden") == "1"))
                if path == "/api/people/growing":
                    from . import kids
                    return self._json(200, kids.growing_up(conn, int(q.get("id") or 0)))
                if path in ("/api/people/book", "/api/people/gif"):
                    from . import kids
                    fn = kids.make_book if path.endswith("book") else kids.make_gif
                    f = fn(conn, settings.originals, settings.library, int(q.get("id") or 0))
                    return self._json(200, {"file": f.name})
                if path == "/api/map":
                    return self._json(200, browse.map_points(conn, q))
                kind, _, ident = path.rpartition("/")
                if kind == "/media/export":
                    conn.close()
                    f = settings.library / "exports" / Path(ident).name
                    if not f.is_file():
                        return self._json(404, {"error": "not found"})
                    ctype = "application/pdf" if f.suffix == ".pdf" else "image/gif" if f.suffix == ".gif" else "application/octet-stream"
                    return self._file(f, ctype)
                asset_id = int(ident)
                if kind == "/api/context":
                    from . import review
                    return self._json(200, review.context(conn, asset_id, settings.weather_online))
                if kind == "/api/similar":
                    from . import memories
                    return self._json(200, memories.similar(conn, str(settings.db), asset_id))
                if kind == "/api/photo":
                    d = browse.photo_detail(conn, asset_id)
                    if d:
                        from . import describe, faces
                        d["faces"] = faces.faces_in_photo(conn, asset_id)
                        d["description"] = describe.for_photo(conn, asset_id)
                        d["private"] = bool(conn.execute("SELECT private FROM assets WHERE id = ?", (asset_id,)).fetchone()[0])
                    return self._json(200, d) if d else self._json(404, {"error": "not found"})
                if kind == "/media/face":
                    from . import faces
                    f = faces.face_crop(conn, settings.originals, settings.library, asset_id)
                    if not f:
                        return self._json(404, {"error": "not found"})
                    return self._file(f, "image/jpeg", cache=True)
                row = conn.execute("SELECT path, sha256, kind FROM assets WHERE id = ? AND missing = 0",
                                   (asset_id,)).fetchone()
            finally:
                conn.close()
            if not row:
                return self._json(404, {"error": "not found"})
            if kind == "/media/thumb":
                if row["kind"] != "photo":
                    return self._json(404, {"error": "no thumbnail for videos"})
                f = media.thumbnail(settings.library, settings.originals, row["path"], row["sha256"])
                return self._file(f, "image/jpeg", cache=True)
            if kind == "/media/view":
                f, ctype = media.viewable(settings.library, settings.originals, row["path"], row["sha256"])
                return self._file(f, ctype, cache=True)
            return self._json(404, {"error": "not found"})

        def _ai_action(self, path: str, data: dict):
            from . import ask
            settings, conn = app.db()
            try:
                if path == "/api/story/save":
                    key = f"{data.get('kind')}:{int(data.get('id') or 0)}"
                    ask.save_story(conn, key, str(data.get("text", "")), "you", edited=True)
                    return self._json(200, ask.get_story(conn, key))
                client = app.ai_client()
                if path == "/api/story/write":
                    kind, ident = str(data.get("kind")), int(data.get("id") or 0)
                    text = ask.write_story(client, ask.story_context(conn, kind, ident))
                    if not text:
                        raise ValueError("Claude declined to write this one.")
                    ask.save_story(conn, f"{kind}:{ident}", text, ask.STORY_MODEL, edited=False)
                    return self._json(200, ask.get_story(conn, f"{kind}:{ident}"))
                question = str(data.get("question", "")).strip()[:1000]
                if not question:
                    raise ValueError("Ask something first.")
                if data.get("reset"):
                    app.ask_history = []
                asker = ask.Asker(conn, settings, find_photos)
                r = asker.ask(client, question, app.ask_history)
                app.ask_history = r["history"]
                r["items"] = browse.by_ids(conn, r["photos"])
                return self._json(200, r)
            finally:
                conn.close()

        def _timeline_action(self, path: str, data: dict):
            from . import chapters
            settings, conn = app.db()
            try:
                action = path.rsplit("/", 1)[1]
                if action == "chapter":
                    chapters.update_chapter(conn, int(data["id"]), data)
                elif action == "milestone":
                    chapters.set_milestone_state(conn, int(data["id"]), str(data.get("state", "kept")))
                elif action == "rebuild":
                    chapters.rebuild(conn)
                else:
                    return self._json(404, {"error": "not found"})
                return self._json(200, {"ok": True})
            finally:
                conn.close()

        def _people_action(self, path: str, data: dict):
            from . import faces
            settings, conn = app.db()
            try:
                action = path.rsplit("/", 1)[1]
                if action == "rename":
                    faces.rename_person(conn, int(data["id"]), str(data.get("name", ""))[:80])
                elif action == "hide":
                    faces.hide_person(conn, int(data["id"]), bool(data.get("hidden", True)))
                elif action == "merge":
                    faces.merge_people(conn, int(data["from"]), int(data["into"]))
                elif action == "move-face":
                    pid = data.get("person")
                    faces.move_face(conn, int(data["face"]), int(pid) if pid else None)
                elif action == "birthday":
                    from . import kids
                    kids.set_birthday(conn, int(data["id"]), str(data.get("birthday") or "") or None)
                elif action == "regroup":
                    faces.group_faces(conn)
                if action in ("rename", "merge", "regroup", "birthday"):
                    from . import chapters
                    chapters.rebuild_milestones(conn)
                else:
                    return self._json(404, {"error": "not found"})
                return self._json(200, {"ok": True})
            finally:
                conn.close()

        def _search(self, conn, settings, q: dict) -> dict:
            text = q["q"].strip()[:200]
            hits = find_photos(conn, settings, q)
            offset, limit = int(q.get("offset") or 0), min(int(q.get("limit") or 200), 500)
            page = hits[offset:offset + limit]
            items = browse.by_ids(conn, [h[0] for h in page])
            score = dict(page)
            for it in items:
                it["score"] = score.get(it["id"])
            return {"total": len(hits), "items": items, "search": text}

        def _file(self, f: Path, ctype: str, cache: bool = False) -> None:
            """Serve a file, with HTTP Range support so videos can seek."""
            size = f.stat().st_size
            start, end = 0, size - 1
            rng = self.headers.get("Range", "")
            partial = rng.startswith("bytes=")
            if partial:
                a, _, b = rng[6:].split(",")[0].partition("-")
                try:
                    if a:
                        start, end = int(a), (int(b) if b else size - 1)
                    else:
                        start = max(0, size - int(b))
                except ValueError:
                    partial = False
                    start, end = 0, size - 1
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "private, max-age=86400" if cache else "no-store")
            self.end_headers()
            with f.open("rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(1 << 16, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        def do_POST(self):
            if not self._authorized():
                return
            path = self.path.split("?", 1)[0]
            try:
                data = self._body()
                if path == "/api/settings":
                    from . import describe
                    if "anthropic_api_key" in data:
                        describe.set_api_key(str(data.pop("anthropic_api_key")))
                    return self._json(200, app.save_settings(data))
                if path == "/api/run":
                    kind = data.get("kind", "sync")
                    mode = data.get("mode", "full")
                    if kind not in ("sync", "scan", "verify", "index", "faces", "describe") or mode not in ("full", "quick", "test"):
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
                if path.startswith("/api/people/"):
                    return self._people_action(path, data)
                if path.startswith("/api/timeline/"):
                    return self._timeline_action(path, data)
                if path in ("/api/story/write", "/api/story/save", "/api/ask"):
                    return self._ai_action(path, data)
                if path == "/api/private":
                    from . import describe
                    settings, conn = app.db()
                    try:
                        describe.set_private(conn, int(data["id"]), bool(data.get("private", True)))
                    finally:
                        conn.close()
                    return self._json(200, {"ok": True})
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
