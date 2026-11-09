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

from . import __version__, browse, catalog, media, phone
from .jobs import JobRunner
from .settings import Settings

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
STATIC_TYPES = {".js": "text/javascript", ".css": "text/css", ".png": "image/png"}


class App:
    def __init__(self, settings_provider=Settings.load, on_quit=None):
        self.token = secrets.token_urlsafe(24)
        self.nonce = secrets.token_urlsafe(16)   # for the page's inline script under CSP
        self.settings_provider = settings_provider
        self.jobs = JobRunner(settings_provider)
        self.on_quit = on_quit
        self._last_auto = time.time()
        self.ask_history: list = []
        self.pairing = phone.Pairing()
        self.phone_server: ThreadingHTTPServer | None = None
        self.phone_port: int | None = None

    # ---- API handlers (return JSON-able dicts) -------------------------
    _stats_cache: tuple[float, str, dict] = (0.0, "", {})

    def status(self) -> dict:
        s = self.settings_provider()
        stats = None
        if s.db.exists():
            # Polled every 1.5 s by the page: reuse the numbers for a few seconds unless a job just changed state.
            key = f"{s.db}|{self.jobs.state.state}|{self.jobs.state.finished}"
            t, k, cached = self._stats_cache
            if k == key and time.time() - t < 4:
                stats = cached["stats"]
            else:
                conn = catalog.connect(s.db)
                try:
                    stats = catalog.stats(conn)
                    extra = {"search": self.search_counts_conn(conn), "people": self.people_counts_conn(conn),
                             "ai": self.ai_counts_conn(conn, s), "quality": self.quality_counts_conn(conn),
                             "backup": self.backup_status_cached(conn, s)}
                finally:
                    conn.close()
                cached = {"stats": stats, **extra}
                self._stats_cache = (time.time(), key, cached)
                stats = cached["stats"]
        else:
            cached = {"search": None, "people": None, "ai": {"has_key": False}, "quality": None, "backup": None}
            from . import describe
            cached["ai"] = {"has_key": bool(describe.get_api_key())}
        return {
            "version": __version__,
            "configured": bool(s.apple_id),
            "map_online": s.map_online,
            "smart_search": s.smart_search,
            "search": cached["search"],
            "people_on": s.people,
            "people": cached["people"],
            "ai_on": s.ai_descriptions,
            "ai_model": s.ai_model,
            "ai": cached["ai"],
            "quality": cached.get("quality"),
            "backup": cached.get("backup"),
            "library": str(s.library),
            "stats": stats,
            "job": self.jobs.snapshot(),
        }

    _query_cache: dict = {}

    def cached(self, key: str, conn, fn, ttl: float = 20.0):
        """Reuse an expensive read for a few seconds unless the catalog changed (a job finished or data was written)."""
        db = self.settings_provider().db
        stamp = 0
        for f in (db, db.with_name(db.name + "-wal")):      # any write touches the WAL file
            try:
                stamp ^= f.stat().st_mtime_ns ^ f.stat().st_size
            except OSError:
                pass
        tag = f"{self.jobs.state.finished}|{stamp}|{db}"
        hit = self._query_cache.get(key)
        if hit and hit[0] == tag and time.time() - hit[1] < ttl:
            return hit[2]
        value = fn()
        self._query_cache[key] = (tag, time.time(), value)
        if len(self._query_cache) > 64:
            self._query_cache.pop(next(iter(self._query_cache)))
        return value

    def ai_client(self):
        from . import describe
        key = describe.get_api_key()
        if not key:
            raise ValueError("Add your Anthropic API key in Settings first.")
        return describe.make_client(key)

    def ai_counts_conn(self, conn, s):
        from . import describe
        return describe.counts(conn, s.ai_model)

    _backup_cache: tuple[float, str, dict] = (0.0, "", {})
    _backup_checking = False

    def backup_status_cached(self, conn, s):
        """Drive checks can block for a long time on an unplugged or network path, so they never run
        in a request: a background thread refreshes the answer at most once a minute."""
        from . import backup
        t, k, v = self._backup_cache
        key = f"{s.backup}|{self.jobs.state.finished}"
        if k == key and time.time() - t < 60:
            return v
        if not self._backup_checking:
            self._backup_checking = True

            def check():
                try:
                    c2 = catalog.connect(s.db)
                    try:
                        val = backup.status(c2, s.library, s.backup)
                    finally:
                        c2.close()
                    self._backup_cache = (time.time(), key, val)
                except Exception:
                    self._backup_cache = (time.time(), key, {"configured": bool(s.backup), "present": False, "last": None, "last_failed": 0})
                finally:
                    self._backup_checking = False
            threading.Thread(target=check, daemon=True).start()
        return v or {"configured": bool(s.backup), "present": None, "last": None, "last_failed": 0, "checking": True}

    def quality_counts_conn(self, conn):
        from . import quality
        return quality.counts(conn)

    def people_counts_conn(self, conn):
        from . import faces
        return faces.counts(conn)

    def search_counts_conn(self, conn):
        try:
            from . import search
            return search.counts(conn)
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
        self.apply_phone_setting(s)
        return s.to_dict()

    def apply_phone_setting(self, s: Settings | None = None, port: int | None = None) -> None:
        """Start or stop the Wi-Fi (HTTPS) listener to match the setting."""
        s = s or self.settings_provider()
        if port is not None:
            self.phone_port = port
        if s.phone_access and self.phone_server is None and self.phone_port is not None:
            try:
                self.phone_server = make_phone_server(self, self.phone_port)
                threading.Thread(target=self.phone_server.serve_forever, daemon=True).start()
            except Exception as e:
                self.phone_error = str(e)
        elif not s.phone_access and self.phone_server is not None:
            srv, self.phone_server = self.phone_server, None
            threading.Thread(target=srv.shutdown, daemon=True).start()
            self.pairing.reset()

    def phone_status(self) -> dict:
        s = self.settings_provider()
        out = {"on": s.phone_access, "running": self.phone_server is not None, "paired": len(self.pairing.tokens),
               "phones": self.pairing.phones(), "error": getattr(self, "phone_error", "")}
        if self.phone_server is not None:
            code, left = self.pairing.code()
            port = self.phone_server.server_address[1]
            ips = phone.lan_ips()
            self.phone_server.hosts |= set(ips)          # a new Wi-Fi address is accepted without a restart
            out.update({"code": code, "expires_in": left, "urls": [f"https://{ip}:{port}/pair" for ip in ips],
                        "cert_covers": all(ip in getattr(self.phone_server, "cert_ips", set()) for ip in ips)})
        return out

    def daily_tick(self) -> None:
        """Once a day while the app is open: the memory of the day as a desktop notification."""
        s = self.settings_provider()
        if not s.daily_memory or not s.db.exists():
            return
        today = __import__("datetime").date.today().isoformat()
        try:
            conn = catalog.connect(s.db)
            try:
                if catalog.get_meta(conn, "last_daily_notice") == today:
                    return
                from . import daily
                m = daily.memory_of_day(conn)
                catalog.set_meta(conn, "last_daily_notice", today)
                conn.commit()
            finally:
                conn.close()
        except Exception:
            return
        if m:
            from . import daily
            daily.notify("Image Magic · memory of the day", m["headline"] + ". Open Image Magic to see it.")

    def auto_sync_tick(self) -> None:
        self.daily_tick()
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
    q = browse.expand(conn, q)
    text = (q.get("q") or text).strip()[:200]
    has_filters = any(q.get(k) for k in ("year", "country", "city", "trip", "kind", "person", "person2", "month", "day", "chapter", "event", "best",
                                         "favorite", "flying", "contributor", "_album_items", "from", "to", "days", "birthday_md"))
    allowed = None
    if has_filters:
        allowed = browse.ids(conn, q)
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
    assert page.count("<script>\n") == 1, "expected exactly one inline script"
    page = page.replace("<script>\n", f'<script nonce="{app.nonce}">\n', 1)
    csp = ("default-src 'self'; script-src 'self' 'nonce-" + app.nonce + "'; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' data: blob: https://tile.openstreetmap.org; media-src 'self' blob:; connect-src 'self'; "
           "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ImageMagic"

        def log_message(self, *args):  # keep the console quiet
            pass

        def setup(self):
            super().setup()
            sock = self.request
            if hasattr(sock, "do_handshake"):           # the phone listener: finish TLS here, in this connection's thread
                try:
                    sock.settimeout(10)
                    sock.do_handshake()
                    sock.settimeout(60)
                except Exception:
                    self.close_connection = True
                    raise ConnectionAbortedError("TLS handshake failed")

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if getattr(self.server, "phone", False):
                return host in getattr(self.server, "hosts", set())
            return host in ALLOWED_HOSTS

        @property
        def from_phone(self) -> bool:
            return bool(getattr(self.server, "phone", False))

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", csp)
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
            # <img>/<video> tags can't send headers, so GET /media/* may carry ?t=<token>.
            token = self.headers.get("X-Token") or ""
            if not token and self.command == "GET" and self.path.startswith("/media/"):
                token = self._query().get("t", "")
            if self.from_phone:
                if not app.pairing.valid(token):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "This phone is not paired. Open /pair on the phone."})
                    return False
                if phone.laptop_only(self.path.split("?", 1)[0], self.command):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "That can only be done on the laptop."})
                    return False
                return True
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
            if path == "/pair" and self.from_phone and self._host_ok():
                return self._send(200, phone.PAIR_PAGE.replace("%ERR%", "").encode(), "text/html; charset=utf-8")
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
                if path == "/api/phone":
                    return self._json(200, app.phone_status())
                if path == "/api/log":
                    since = int(self._query().get("since") or 0)
                    return self._json(200, app.jobs.log_since(since))
                if path.startswith(("/api/photos", "/api/photo/", "/api/facets", "/api/trips", "/api/replay",
                                    "/api/map", "/api/people", "/api/timeline", "/api/story", "/api/memories",
                                    "/api/shuffle", "/api/similar/", "/api/review", "/api/context/", "/api/curate/", "/api/events", "/api/home", "/api/voice/", "/api/compare/", "/api/flying", "/api/book", "/api/review-queue", "/api/family", "/api/cleanup", "/api/albums", "/api/places/", "/api/daily", "/api/ids", "/api/inbox", "/api/bursts", "/api/trash", "/api/holidays", "/api/learning", "/media/")):
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
                    if q.get("q", "").strip() or (q.get("album") and browse.expand(conn, q).get("q")):
                        q = browse.expand(conn, q)
                        from . import datewords
                        parsed = datewords.parse(conn, q["q"])
                        understood = {k: v for k, v in parsed.items() if k in ("from", "to", "days", "birthday_md", "person", "person2") and not q.get(k)}
                        q = {**q, **understood, "q": parsed["q"]}
                        if not parsed["q"].strip():       # all words were dates and names: a plain filter, no search model needed
                            res = browse.photos(conn, q, int(q.get("offset") or 0), min(int(q.get("limit") or 200), 500))
                            return self._json(200, {**res, "search": "", "understood": parsed["label"]})
                        r = self._search(conn, settings, q)
                        r["understood"] = parsed["label"]
                        return self._json(200, r)
                    return self._json(200, browse.photos(
                        conn, q, int(q.get("offset") or 0), min(int(q.get("limit") or 200), 500)))
                if path == "/api/replay":
                    if q.get("home") and q.get("chapter"):
                        from . import home
                        return self._json(200, browse.replay_items(conn, home.replay_ids(conn, int(q["chapter"]), q.get("year"))))
                    if q.get("holiday") and not q.get("all"):
                        from . import holidays
                        return self._json(200, browse.replay_items(conn, holidays.best_ids(conn, str(q["holiday"]))))
                    if q.get("best"):
                        from . import quality
                        where, args = browse._filters(browse.expand(conn, {k: v for k, v in q.items() if k != "best"}), alias="a.")
                        n = max(5, min(int(q.get("n") or 30), 300))
                        return self._json(200, browse.replay_items(conn, quality.best_of(conn, where + " AND a.private = 0", args, n)))
                    return self._json(200, browse.replay(conn, q))
                if path == "/api/bursts":
                    from . import cleanup
                    return self._json(200, cleanup.bursts(conn))
                if path == "/api/inbox":
                    from . import inbox
                    return self._json(200, app.cached("inbox", conn, lambda: inbox.overview(conn, str(settings.db), settings.library, settings.originals), ttl=600))
                if path == "/api/trash":
                    from . import cleanup
                    return self._json(200, cleanup.trash_summary(conn))
                if path == "/api/learning":
                    from . import learning
                    return self._json(200, learning.status(conn))
                if path == "/api/ids":
                    ids_ = sorted(browse.ids(conn, q))[:5000]
                    return self._json(200, {"ids": ids_, "total": len(ids_)})
                if path == "/api/places/search":
                    from . import places
                    return self._json(200, places.search(str(q.get("q") or ""), 8))
                if path == "/api/facets":
                    return self._json(200, app.cached("facets", conn, lambda: browse.facets(conn)))
                if path == "/api/trips":
                    return self._json(200, browse.trips(conn))
                if path == "/api/home":
                    from . import home
                    return self._json(200, app.cached(f"home:{q.get('chapter')}", conn, lambda: home.story(conn, int(q.get("chapter") or 0)), ttl=600))
                if path == "/api/albums":
                    from . import albums
                    return self._json(200, app.cached("albums", conn, lambda: albums.listing(conn), ttl=600))
                if path == "/api/events":
                    from . import events
                    return self._json(200, app.cached(f"events:{q.get('year')}:{q.get('trip')}", conn,
                                                      lambda: events.events(conn, q.get("year"), int(q["trip"]) if q.get("trip") else None), ttl=600))
                if path == "/api/albums/suggest":
                    from . import albumideas
                    return self._json(200, albumideas.suggest_for_album(conn, str(settings.db), int(q.get("album") or 0)))
                if path == "/api/curate/stops":
                    from . import curate
                    return self._json(200, curate.stops(conn, int(q.get("trip") or 0)))
                if path == "/api/curate/suggest":
                    from . import curate
                    return self._json(200, curate.suggest_for_trip(conn, str(settings.db), int(q.get("trip") or 0),
                                                                   int(q["seed"]) if q.get("seed") else None))
                if path == "/api/story":
                    from . import ask
                    key = f"{q.get('kind')}:{int(q.get('id') or 0)}"
                    return self._json(200, ask.get_story(conn, key) or {"key": key, "text": ""})
                if path == "/api/review":
                    from . import review
                    ys = review.years(conn)
                    y = q.get("year") or (ys[0] if ys else None)
                    return self._json(200, app.cached(f"review:{y}", conn, lambda: {"years": ys, "stats": review.year_stats(conn, y) if y else None}))
                if path == "/api/daily":
                    from . import daily
                    return self._json(200, app.cached("daily:" + __import__("datetime").date.today().isoformat(), conn,
                                                      lambda: {"memory": daily.memory_of_day(conn), "week": daily.week_ahead(conn)}, ttl=3600))
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
                    return self._json(200, app.cached("timeline", conn, lambda: chapters.timeline(conn)))
                if path == "/api/people":
                    from . import faces
                    return self._json(200, faces.people(conn, include_hidden=q.get("hidden") == "1"))
                if path == "/api/people/time":
                    from . import peopletime
                    return self._json(200, peopletime.overview(conn))
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
                if path == "/api/map/months":
                    return self._json(200, app.cached("map_months:" + json.dumps(q, sort_keys=True), conn, lambda: browse.map_months(conn, q)))
                if path == "/api/holidays":
                    from . import holidays
                    return self._json(200, app.cached("holidays", conn, lambda: holidays.overview(conn), ttl=120))
                if path == "/api/review-queue":
                    from . import confidence
                    return self._json(200, app.cached("review-queue", conn, lambda: confidence.queue(conn), ttl=600))
                if path == "/api/book":
                    from . import books
                    f = books.make(conn, settings.originals, settings.library, str(q.get("kind")), str(q.get("id") or ""))
                    return self._json(200, {"file": f.name})
                if path == "/api/cleanup":
                    from . import cleanup
                    return self._json(200, app.cached("cleanup", conn, lambda: cleanup.candidates(conn, settings.library, settings.originals), ttl=600))
                if path == "/api/family":
                    from . import family
                    return self._json(200, {"contributors": family.contributors(conn), "phones": app.pairing.phones()})
                if path == "/api/flying":
                    from . import flying
                    return self._json(200, app.cached("flying", conn, lambda: flying.log(conn), ttl=600))
                if path == "/api/places/world":
                    from . import placestory
                    return self._json(200, app.cached("world", conn, lambda: placestory.world(conn)))
                if path == "/api/places/geocode":
                    from . import places
                    if not settings.map_online:
                        raise ValueError("Online lookups are off (Settings → Privacy → map background).")
                    return self._json(200, places.geocode_online(str(q.get("q") or "")))
                if path == "/api/places/one":
                    from . import placestory
                    return self._json(200, placestory.place(conn, str(q.get("country") or "")[:2].upper(), q.get("city") or None))
                if path == "/api/map/this-day":
                    from . import memories
                    return self._json(200, memories.this_day_pins(conn))
                if path == "/api/compare/options":
                    from . import faces
                    return self._json(200, {
                        "people": [{"id": p["id"], "name": p["name"], "birthday": p.get("birthday")} for p in faces.people(conn) if p["name"]],
                        "years": [r[0] for r in conn.execute("SELECT DISTINCT substr(taken_at, 1, 4) FROM assets WHERE missing = 0 AND taken_at IS NOT NULL ORDER BY 1 DESC")],
                        "trips": [dict(r) for r in conn.execute("SELECT id, title, substr(start_at, 1, 4) AS year FROM trips ORDER BY start_at DESC")],
                        "events": [dict(r) for r in conn.execute("SELECT id, title, substr(start_at, 1, 10) AS day FROM events ORDER BY start_at DESC LIMIT 300")]})
                kind, _, ident = path.rpartition("/")
                if kind == "/media/export":
                    conn.close()
                    f = settings.library / "exports" / Path(ident).name
                    if not f.is_file():
                        return self._json(404, {"error": "not found"})
                    ctype = "application/pdf" if f.suffix == ".pdf" else "image/gif" if f.suffix == ".gif" else "application/zip" if f.suffix == ".zip" else "application/octet-stream"
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
                        from . import quality
                        d["quality"] = quality.detail(conn, asset_id)
                        from . import events
                        d["event"] = events.event_for(conn, asset_id)
                        from . import notes
                        d["voice"] = notes.list_for(conn, "photo", asset_id)
                        from . import confidence
                        d["confidence"] = confidence.photo_confidence(conn, asset_id)
                        from . import albums
                        d["albums"] = albums.albums_of(conn, asset_id)
                    return self._json(200, d) if d else self._json(404, {"error": "not found"})
                if kind == "/media/voice":
                    from . import notes
                    found = notes.path_of(conn, settings.library, asset_id)
                    if not found:
                        return self._json(404, {"error": "not found"})
                    return self._file(found[0], found[1])
                if kind == "/api/voice":
                    from . import notes
                    return self._json(200, notes.list_for(conn, q.get("kind", "photo"), asset_id))
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
                    if not row["sha256"]:
                        return self._json(404, {"error": "no poster yet"})
                    poster = media.poster_path(settings.library, row["sha256"])
                    if poster.is_file():
                        return self._file(poster, "image/jpeg", cache=True)
                    if media.poster_failed_path(settings.library, row["sha256"]).exists():
                        return self._file(media.generic_poster(settings.library), "image/jpeg", cache=True)   # could not be decoded; a plain frame
                    return self._json(404, {"error": "no poster yet"})
                f = media.thumbnail(settings.library, settings.originals, row["path"], row["sha256"])
                return self._file(f, "image/jpeg", cache=True)
            if kind == "/media/view":
                f, ctype = media.viewable(settings.library, settings.originals, row["path"], row["sha256"])
                return self._file(f, ctype, cache=True)
            return self._json(404, {"error": "not found"})

        def _poster_upload(self, q: dict):
            """POST /api/poster?id=N with a JPEG the page decoded from the video's first frame, or
            POST /api/poster?id=N&failed=1 (laptop only) when the video cannot be decoded, so nobody tries again."""
            n = int(self.headers.get("Content-Length") or 0)
            failed = bool(q.get("failed"))
            if not failed and (n <= 0 or n > 2_000_000):
                raise ValueError("Poster too large")
            data = self.rfile.read(n) if n > 0 else b""
            settings, conn = app.db()
            try:
                row = conn.execute("SELECT sha256, kind FROM assets WHERE id = ? AND missing = 0", (int(q.get("id") or 0),)).fetchone()
            finally:
                conn.close()
            if not row or row["kind"] == "photo" or not row["sha256"]:
                return self._json(404, {"error": "not found"})
            if failed:
                if self.from_phone:
                    return self._json(403, {"error": "laptop only"})     # a phone's codecs say nothing about the laptop's
                if not media.poster_path(settings.library, row["sha256"]).is_file():
                    marker = media.poster_failed_path(settings.library, row["sha256"])
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.touch()
                return self._json(200, {"ok": True})
            media.save_poster(settings.library, row["sha256"], data)
            return self._json(200, {"ok": True})

        def _voice_upload(self, q: dict):
            """POST /api/voice?kind=photo&id=N&seconds=S with the audio as the raw body."""
            from . import notes
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > notes.MAX_BYTES:
                raise ValueError("The recording is empty or too large")
            data = self.rfile.read(n)
            settings, conn = app.db()
            try:
                note = notes.save(conn, settings.library, q.get("kind", "photo"), int(q.get("id") or 0), data,
                                  self.headers.get("Content-Type", ""), float(q["seconds"]) if q.get("seconds") else None)
            finally:
                conn.close()
            return self._json(200, note)

        def _contribute(self, q: dict):
            """POST /api/contribute?name=<file name> with the photo as the raw body (phones, or the laptop)."""
            from . import family
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > family.MAX_BYTES:
                raise ValueError("The file is empty or too large")
            data = self.rfile.read(n)
            who = app.pairing.name_of(self.headers.get("X-Token") or "") if self.from_phone else "me"
            settings, conn = app.db()
            try:
                r = family.contribute(conn, settings.library, settings.originals, data, self.headers.get("Content-Type", ""),
                                      who or "Family", str(q.get("name") or "")[:80])
            finally:
                conn.close()
            return self._json(200, r)

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
                    name = str(data.get("name") or "").strip()
                    if name and not pid:
                        match = next((p for p in faces.people(conn, include_hidden=True)
                                      if (p["name"] or "").lower() == name.lower()), None)
                        if match:
                            pid = match["id"]
                        else:
                            cur = conn.execute("INSERT INTO persons (name, created) VALUES (?, datetime('now'))", (name[:80],))
                            pid = cur.lastrowid
                    faces.move_face(conn, int(data["face"]), int(pid) if pid else None)
                elif action == "birthday":
                    from . import kids
                    kids.set_birthday(conn, int(data["id"]), str(data.get("birthday") or "") or None)
                elif action == "regroup":
                    faces.group_faces(conn)
                if action in ("rename", "merge", "regroup", "birthday", "move-face", "hide"):
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
            self.send_header("X-Content-Type-Options", "nosniff")
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

        def _pair(self) -> None:
            """POST /pair from a phone: check the code, hand out that phone's token in the URL fragment."""
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4096:
                raise ValueError("request too large")
            form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
            code = (form.get("code") or [""])[0]
            name = (form.get("name") or [""])[0].strip() or "Family"
            token = app.pairing.try_code(code, name)
            if not token:
                body = phone.PAIR_PAGE.replace("%ERR%", '<p class="err">That code did not work. Check it on the laptop and try again.</p>')
                return self._send(403, body.encode(), "text/html; charset=utf-8")
            self.send_response(303)
            self.send_header("Location", f"/#t={token}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/pair" and self.from_phone:
                if not self._host_ok():
                    return self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
                try:
                    return self._pair()
                except Exception as e:
                    return self._json(400, {"error": str(e)})
            if not self._authorized():
                return
            try:
                if path == "/api/voice":
                    return self._voice_upload(self._query())
                if path == "/api/poster":
                    return self._poster_upload(self._query())
                if path == "/api/contribute":
                    return self._contribute(self._query())
                data = self._body()
                if path == "/api/voice/delete":
                    from . import notes
                    settings, conn = app.db()
                    try:
                        notes.delete(conn, settings.library, int(data["id"]))
                    finally:
                        conn.close()
                    return self._json(200, {"ok": True})
                if path == "/api/phone/reset":
                    app.pairing.reset()
                    return self._json(200, app.phone_status())
                if path == "/api/settings":
                    from . import describe
                    if "anthropic_api_key" in data:
                        describe.set_api_key(str(data.pop("anthropic_api_key")))
                    return self._json(200, app.save_settings(data))
                if path == "/api/run":
                    kind = data.get("kind", "sync")
                    mode = data.get("mode", "full")
                    if kind not in ("sync", "scan", "verify", "index", "faces", "describe", "quality", "backup") or mode not in ("full", "quick", "test"):
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
                if path == "/api/import/check":
                    folder = str(data.get("folder", "")).strip()
                    if not folder:
                        raise ValueError("Enter the folder to import from.")
                    app.jobs.start("import_check", payload={"folder": folder})
                    return self._json(200, app.jobs.snapshot())
                if path == "/api/archive":
                    app.jobs.start("archive", payload={"everything": bool(data.get("everything")), "voice": bool(data.get("voice"))})
                    return self._json(200, app.jobs.snapshot())
                if path == "/api/import/run":
                    app.jobs.start("import", payload={"include_near": bool(data.get("include_near")),
                                                      "contributor": str(data.get("contributor") or "")[:80]})
                    return self._json(200, app.jobs.snapshot())
                if path.startswith("/api/curate/"):
                    from . import curate
                    settings, conn = app.db()
                    try:
                        action = path.rsplit("/", 1)[1]
                        if action == "trip":
                            ids = [int(i) for i in data.get("ids") or []]
                            t = data.get("trip")
                            if data.get("rejected") and t not in (None, "", "none") and not isinstance(t, dict):
                                from . import inbox
                                inbox.reject_for_trip(conn, int(t), [int(i) for i in data["rejected"]])
                            if isinstance(t, dict) and t.get("new"):
                                tid = curate.set_trip(conn, ids, None, new_title=str(t["new"]))
                            else:
                                tid = curate.set_trip(conn, ids, int(t) if t not in (None, "", "none") else None)
                            from . import chapters
                            chapters.rebuild_milestones(conn)
                            return self._json(200, {"trip": tid})
                        if action == "event-rename":
                            from . import events
                            events.rename(conn, int(data["id"]), str(data.get("title", "")))
                            return self._json(200, {"ok": True})
                        if action == "trip-delete":
                            n = curate.delete_trip(conn, int(data["id"]))
                            from . import chapters
                            chapters.rebuild_milestones(conn)
                            return self._json(200, {"released": n})
                        if action == "trip-merge":
                            curate.merge_trips(conn, int(data["from"]), int(data["into"]))
                            return self._json(200, {"ok": True})
                        if action == "trip-split":
                            return self._json(200, {"trip": curate.split_trip(conn, int(data["id"]), str(data.get("at", "")), data.get("title"))})
                        if action == "trip-dates":
                            return self._json(200, curate.set_trip_dates(conn, int(data["id"]), str(data.get("start", "")), str(data.get("end", ""))))
                        if action == "trip-cover":
                            curate.set_trip_cover(conn, int(data["id"]), int(data["asset"]))
                            return self._json(200, {"ok": True})
                        if action == "trip-rename":
                            curate.rename_trip(conn, int(data["id"]), str(data.get("title", "")))
                            return self._json(200, {"ok": True})
                        if action == "caption":
                            return self._json(200, curate.set_caption(conn, int(data["id"]), str(data.get("caption", "")),
                                                                      [str(k) for k in data.get("keywords") or []]))
                        return self._json(404, {"error": "not found"})
                    finally:
                        conn.close()
                if path in ("/api/share/check", "/api/share/export"):
                    from . import share
                    settings, conn = app.db()
                    try:
                        filters = {k: v for k, v in dict(data.get("filters") or {}).items() if k in
                                   ("trip", "chapter", "person", "year", "month", "day", "country", "city", "q", "event", "best", "flying", "album", "favorite", "loose")}
                        if path.endswith("check"):
                            return self._json(200, share.check(conn, filters))
                        folder = share.export(conn, settings.originals, settings.library, filters,
                                              str(data.get("title") or "My photos")[:80],
                                              exclude=[int(i) for i in data.get("exclude") or []],
                                              story_key=data.get("story_key"), voice=bool(data.get("voice")),
                                              strict=bool(data.get("strict")))
                        out = {"folder": str(folder)}
                        if data.get("zip", True):
                            out["file"] = share.zip_folder(folder).name     # downloaded by the page from /media/export/
                        else:
                            try:
                                _open_folder(folder)
                            except Exception:
                                pass   # opening the folder is a convenience; the export succeeded
                        return self._json(200, out)
                    finally:
                        conn.close()
                if path in ("/api/story/write", "/api/story/save", "/api/ask"):
                    return self._ai_action(path, data)
                if path.startswith("/api/albums/"):
                    from . import albums
                    settings, conn = app.db()
                    try:
                        action = path.rsplit("/", 1)[1]
                        ids = [int(i) for i in data.get("ids") or []]
                        if action == "create":
                            aid = albums.create(conn, str(data.get("title", "")), str(data.get("kind") or "manual"),
                                                dict(data.get("filters") or {}), ids)
                            return self._json(200, {"id": aid})
                        if action == "reject":
                            from . import albumideas
                            return self._json(200, {"rejected": albumideas.reject_for_album(conn, int(data["id"]), ids)})
                        if action == "add":
                            albums.add(conn, int(data["id"]), ids)
                        elif action == "remove":
                            albums.remove(conn, int(data["id"]), ids)
                        elif action == "rename":
                            albums.rename(conn, int(data["id"]), str(data.get("title", "")))
                        elif action == "cover":
                            albums.set_cover(conn, int(data["id"]), int(data["asset"]) if data.get("asset") else None)
                        elif action == "delete":
                            albums.delete(conn, int(data["id"]))
                        else:
                            return self._json(404, {"error": "not found"})
                        return self._json(200, {"ok": True})
                    finally:
                        conn.close()
                if path.startswith("/api/cleanup/"):
                    from . import cleanup
                    settings, conn = app.db()
                    try:
                        action = path.rsplit("/", 1)[1]
                        ids = [int(i) for i in data.get("ids") or []]
                        if action == "keep":
                            r = cleanup.keep_frame(conn, settings.library, settings.originals, int(data["burst"]),
                                                   int(data["keeper"]) if data.get("keeper") else None, bool(data.get("trash_rest")))
                        elif action == "trash":
                            r = cleanup.trash(conn, settings.library, settings.originals, ids)
                            r["kept"] = cleanup.keep(conn, [int(i) for i in data.get("keep") or []])   # unticked in the categories you looked at
                        elif action == "keep-photos":
                            r = {"kept": cleanup.keep(conn, ids)}
                        elif action == "forget-kept":
                            r = {"forgotten": cleanup.forget_kept(conn)}
                        elif action == "restore":
                            r = {"restored": cleanup.restore(conn, settings.library, settings.originals, ids or None)}
                        elif action == "empty":
                            r = cleanup.empty(conn, settings.library)
                        else:
                            return self._json(404, {"error": "not found"})
                        if action in ("trash", "restore", "keep"):
                            from . import inbox
                            inbox.rebuild_after(conn)
                        return self._json(200, {**r, "trash": cleanup.trash_summary(conn)})
                    finally:
                        conn.close()
                if path == "/api/learning/forget":
                    from . import learning
                    settings, conn = app.db()
                    try:
                        learning.forget(conn)
                        return self._json(200, learning.status(conn))
                    finally:
                        conn.close()
                if path.startswith("/api/inbox/"):
                    from . import inbox
                    settings, conn = app.db()
                    try:
                        action = path.rsplit("/", 1)[1]
                        if action == "undo":
                            return self._json(200, {"undone": inbox.undo_last(conn, settings.library, settings.originals)})
                        # Reuse the card list the page is showing (same catalog state) instead of recomputing it.
                        ov = app.cached("inbox", conn, lambda: inbox.overview(conn, str(settings.db), settings.library, settings.originals), ttl=600)
                        cards = {c["key"]: c for c in ov["cards"]}
                        if action == "accept-high":
                            n = 0
                            for c in cards.values():
                                if c["conf"] >= inbox.AUTO_MIN and c["action"]["do"] != "face_name":
                                    inbox.apply(conn, settings.library, settings.originals, c, rebuild=False); n += 1
                            if n:
                                inbox.rebuild_after(conn)
                            return self._json(200, {"applied": n})
                        card = cards.get(str(data.get("key", "")))
                        if not card:
                            raise ValueError("That suggestion is no longer current; the list has been refreshed.")
                        if action == "accept":
                            only = [int(i) for i in data.get("ids") or []] or None
                            faces_kept = [int(i) for i in data.get("faces") or []] or None
                            return self._json(200, inbox.apply(conn, settings.library, settings.originals,
                                                               inbox.restrict_faces(inbox.restrict(card, only), faces_kept), name=data.get("name")))
                        if action == "skip":
                            inbox.skip(conn, card)
                            return self._json(200, {"ok": True})
                        return self._json(404, {"error": "not found"})
                    finally:
                        conn.close()
                if path == "/api/location/set":
                    from . import locations
                    settings, conn = app.db()
                    try:
                        ids = [int(i) for i in data.get("ids") or []]
                        if data.get("from_asset"):
                            src = conn.execute("SELECT lat, lon FROM assets WHERE id = ?", (int(data["from_asset"]),)).fetchone()
                            if not src or src["lat"] is None:
                                raise ValueError("That photo has no location to copy")
                            lat, lon = src["lat"], src["lon"]
                        else:
                            lat, lon = float(data["lat"]), float(data["lon"])
                        n = locations.set_manual(conn, ids, lat, lon, bool(data.get("overwrite")))
                        from . import inbox
                        inbox.rebuild_after(conn)
                        return self._json(200, {"changed": n})
                    finally:
                        conn.close()
                if path == "/api/location/nearby":
                    from . import locations
                    settings, conn = app.db()
                    try:
                        ids = [int(i) for i in data.get("ids") or []]
                        window = max(5, min(int(data.get("window") or 20), 720))
                        sugg = locations.nearby_suggestions(conn, ids, window)
                        if data.get("apply"):
                            n = 0
                            for s_ in sugg:
                                if s_["lat"] is not None and (not s_["has"] or data.get("overwrite")):
                                    n += locations.set_manual(conn, [s_["id"]], s_["lat"], s_["lon"], bool(data.get("overwrite")))
                            from . import inbox
                            inbox.rebuild_after(conn)
                            return self._json(200, {"changed": n})
                        return self._json(200, {"suggestions": sugg})
                    finally:
                        conn.close()
                if path == "/api/location/clear":
                    from . import locations
                    settings, conn = app.db()
                    try:
                        return self._json(200, {"changed": locations.clear_manual(conn, [int(i) for i in data.get("ids") or []])})
                    finally:
                        conn.close()
                if path == "/api/review-queue/decide":
                    from . import confidence
                    settings, conn = app.db()
                    try:
                        confidence.decide(conn, str(data.get("key", "")), str(data.get("decision", "")))
                        from . import chapters
                        chapters.rebuild_milestones(conn)
                    finally:
                        conn.close()
                    return self._json(200, {"ok": True})
                if path == "/api/compare":
                    from . import compare
                    settings, conn = app.db()
                    try:
                        return self._json(200, compare.compare(conn, dict(data.get("a") or {}), dict(data.get("b") or {})))
                    finally:
                        conn.close()
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


def make_phone_server(app: App, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    """The Wi-Fi listener: HTTPS with a locally made certificate; phones pair with a code."""
    import ssl
    ips = phone.lan_ips()
    cert, key = phone.ensure_certificate(ips)
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    server.phone = True                                                   # type: ignore[attr-defined]
    server.hosts = set(ips) | {"image-magic.local", "localhost", "127.0.0.1"}   # type: ignore[attr-defined]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert), str(key))
    server.socket = ctx.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    server.cert_ips = set(ips)                                            # type: ignore[attr-defined]
    return server
