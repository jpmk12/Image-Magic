"""Background jobs (sync, scan, verify) for the app, one at a time."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from dataclasses import dataclass, field

from . import catalog, sync
from .settings import Settings

_TOTAL_RE = re.compile(r"Downloading (?:the first )?([\d,]+) ")
_DONE_RE = re.compile(r"\bDownloaded \S")


KNOWN_FAILURES = [
    ("setup is not complete", "Apple wants you to sign in at icloud.com once in a browser first (this happens after a "
     "sign-in from a new device). Open https://www.icloud.com on this computer, sign in with the same Apple ID, accept "
     "any prompts, open Photos there once, then click Get new photos again."),
    ("Access iCloud Data on the Web", "Your account uses Advanced Data Protection. On your iPhone: Settings → your name → "
     "iCloud → Advanced Data Protection → turn on 'Access iCloud Data on the Web'. Then tick the ADP box in Image Magic's "
     "Settings and try again."),
    ("ADP", "Your account may use Advanced Data Protection. On your iPhone turn on 'Access iCloud Data on the Web' "
     "(Settings → your name → iCloud → Advanced Data Protection), tick the ADP box in Settings here, and try again."),
    ("Invalid email/password", "Apple rejected the password. Check the Apple ID in Settings and try again."),
    ("locked", "Apple has temporarily locked sign-in attempts for this account. Wait a while, then try again."),
    ("Two-factor authentication is required", "The iPhone code wasn't accepted in time. Click Get new photos again and "
     "enter the new code as soon as it appears."),
]


def _explain_icloudpd_failure(code: int, recent_log: list[str]) -> str:
    text = "\n".join(recent_log)
    for needle, advice in KNOWN_FAILURES:
        if needle.lower() in text.lower():
            return f"Sync did not finish. {advice}"
    return f"Sync did not finish (icloudpd exit code {code}). See the Activity log for details."


def _kill_tree(proc: subprocess.Popen) -> None:
    """Stop icloudpd and its child processes.

    The packaged icloudpd runs as a launcher plus a child process, so stopping
    only the launcher would leave the child running (and holding port 8080).
    """
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            import os
            import signal
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            # The child can outlive (or ignore) SIGTERM; make sure the group is gone.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except (OSError, ProcessLookupError):
        pass
    if proc.poll() is None:
        proc.kill()


@dataclass
class JobState:
    kind: str = ""          # sync | scan | verify
    state: str = "idle"     # idle | running | needs_password | needs_code | done | failed | cancelled
    message: str = ""
    started: float = 0.0
    finished: float = 0.0
    downloaded: int = 0
    total: int | None = None
    auth_error: bool = False
    result: dict = field(default_factory=dict)


class JobRunner:
    def __init__(self, settings_provider=Settings.load):
        self._settings_provider = settings_provider
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._cancel = threading.Event()
        self.state = JobState()
        self.log: deque[tuple[int, str]] = deque(maxlen=2000)
        self._log_seq = 0
        self._helper_ok = False   # True once the page on the helper port is confirmed to be icloudpd's

    # ---- public API ----------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.state.state in ("running", "needs_password", "needs_code")

    def snapshot(self) -> dict:
        s = self.state
        return {
            "kind": s.kind, "state": s.state, "message": s.message,
            "started": s.started, "finished": s.finished,
            "downloaded": s.downloaded, "total": s.total,
            "auth_error": s.auth_error, "result": s.result,
        }

    def log_since(self, seq: int) -> list[dict]:
        return [{"seq": n, "line": line} for n, line in list(self.log) if n > seq]

    def start(self, kind: str, mode: str = "full", payload: dict | None = None) -> None:
        with self._lock:
            if self.busy:
                raise RuntimeError("Another task is already running")
            self._cancel.clear()
            self.state = JobState(kind=kind, state="running", started=time.time())
            self.payload = payload or {}
            target = {"sync": self._run_sync, "scan": self._run_scan, "verify": self._run_verify,
                      "index": self._run_index, "faces": self._run_faces,
                      "describe": self._run_describe, "import_check": self._run_import_check,
                      "import": self._run_import, "quality": self._run_quality, "backup": self._run_backup,
                      "archive": self._run_archive}[kind]
            threading.Thread(target=target, args=(mode,), daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc and proc.poll() is None:
            _kill_tree(proc)

    def submit_auth(self, field_name: str, value: str) -> bool:
        """Forward the password or 2FA code to icloudpd's sign-in page."""
        if field_name not in ("password", "code"):
            raise ValueError(field_name)
        proc = self._proc
        if proc is None or proc.poll() is not None or not self._helper_ok:
            self.state.auth_error = True
            self._add_log("Refused to send sign-in details: the iCloud sign-in helper is not running.")
            return False
        data = urllib.parse.urlencode({field_name: value}).encode()
        req = urllib.request.Request(f"{sync.ICLOUDPD_WEBUI}/{field_name}", data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = resp.status == 200
        except Exception:
            ok = False
        self.state.auth_error = not ok
        if ok:
            self.state.state = "running"
            self._add_log("Sign-in details sent to iCloud.")
        return ok

    # ---- internals -----------------------------------------------------
    def _add_log(self, line: str) -> None:
        with self._lock:
            self._log_seq += 1
            self.log.append((self._log_seq, line))

    def _finish(self, state: str, message: str, result: dict | None = None) -> None:
        self.state.state = state
        self.state.message = message
        self.state.finished = time.time()
        if result is not None:
            self.state.result = result
        self._add_log(message)

    def _progress(self, label):
        def report(done, total):
            self.state.message = f"{label} {done:,} of {total:,}"
        return report

    def _run_scan(self, _mode: str) -> None:
        try:
            settings = self._settings_provider()
            r = self._scan(settings)
            self._finish("done", f"Catalog updated: {r.new:,} new, {r.total:,} total, {r.missing:,} missing, "
                                 f"{r.trips:,} trips." + (f" {r.retrashed} re-downloaded photos went back to the trash." if r.retrashed else ""),
                         {"new": r.new, "total": r.total, "missing": r.missing})
        except InterruptedError:
            self._finish("cancelled", "Cataloging stopped. Run it again to continue.")
        except InterruptedError:
            self._finish("cancelled", "Cataloging stopped. Run it again to continue.")
        except Exception as e:  # report, never crash the app
            self._finish("failed", f"Cataloging failed: {e}")

    def _scan(self, settings: Settings):
        self.state.message = "Cataloging files..."
        conn = catalog.connect(settings.db)
        try:
            r = catalog.scan(conn, settings.originals, progress=self._progress("Cataloged"),
                             cancelled=self._cancel.is_set, rebuild_events=False)
            self._quality(conn, settings)
            return r
        finally:
            conn.close()

    def _quality(self, conn, settings: Settings) -> int:
        """Score new photos and group bursts (local, free); then refresh events."""
        from . import events, quality
        self.state.message = "Rating photos..."
        n = quality.score_pending(conn, settings.library, settings.originals,
                                  progress=self._progress("Rated"), cancelled=self._cancel.is_set)
        self.state.message = "Finding events..."
        events.rebuild(conn)
        if settings.inbox_auto:
            from . import inbox
            self.state.message = "Applying confident suggestions..."
            inbox.auto_apply(conn, str(settings.db), settings.library, settings.originals, log=self._add_log)
        return n

    def _run_archive(self, _mode: str) -> None:
        from . import archive
        try:
            settings = self._settings_provider()
            self.state.message = "Writing the archive..."
            conn = catalog.connect(settings.db)
            try:
                folder = archive.export(conn, settings.originals, settings.library, everything=bool(self.payload.get("everything")),
                                        voice=bool(self.payload.get("voice")), progress=self._progress("Copied"), cancelled=self._cancel.is_set)
            finally:
                conn.close()
            n = len(list((folder / "images").glob("*.jpg")))
            self._finish("cancelled" if self._cancel.is_set() else "done",
                         f"Archive {'stopped' if self._cancel.is_set() else 'written'}: {n:,} photos in {folder}", {"folder": str(folder), "photos": n})
        except Exception as e:
            self._finish("failed", f"Archive failed: {e}")

    def _run_backup(self, _mode: str) -> None:
        from . import backup
        try:
            settings = self._settings_provider()
            dest = Path(self.payload.get("dest") or "").expanduser() if self.payload.get("dest") else settings.backup
            if not dest:
                raise ValueError("Choose a backup folder in Settings first.")
            self.state.message = "Comparing with the backup..."
            conn = catalog.connect(settings.db)
            try:
                r = backup.run(conn, settings.library, dest, progress=self._progress("Copied"), cancelled=self._cancel.is_set)
            finally:
                conn.close()
            for p in r.failed[:50]:
                self._add_log(f"BACKUP FAILED  {p}")
            self._finish("cancelled" if self._cancel.is_set() else ("failed" if r.failed else "done"),
                         f"Backup {'stopped' if self._cancel.is_set() else 'finished'}: {r.copied:,} files copied and verified, "
                         f"{r.skipped:,} already there, {len(r.failed)} failed.", r.as_dict())
        except Exception as e:
            self._finish("failed", f"Backup failed: {e}")

    def _run_quality(self, _mode: str) -> None:
        try:
            settings = self._settings_provider()
            conn = catalog.connect(settings.db)
            try:
                n = self._quality(conn, settings)
                from . import quality
                c = quality.counts(conn)
            finally:
                conn.close()
            self._finish("cancelled" if self._cancel.is_set() else "done",
                         f"Rated {n:,} photos: {c['good']:,} keepers, {c['bursts']:,} bursts.", c)
        except Exception as e:
            self._finish("failed", f"Rating failed: {e}")

    def _run_index(self, _mode: str) -> None:
        try:
            self._index(self._settings_provider(), standalone=True)
        except Exception as e:
            self._finish("failed", f"Smart search setup failed: {e}")

    def _index(self, settings: Settings, standalone: bool) -> int:
        """Download the search model if needed, then index new photos."""
        from . import search
        if not search.model_ready():
            self.state.message = "Downloading smart search model (one time, about 600 MB)..."
            self._add_log(self.state.message)

            def dl(done, total):
                self.state.downloaded, self.state.total = done, total
                self.state.message = f"Downloading smart search model: {done / 1e6:,.0f} of {total / 1e6:,.0f} MB"
            search.download_model(dl, cancelled=self._cancel.is_set)
            if self._cancel.is_set():
                if standalone:
                    self._finish("cancelled", "Stopped. The download resumes where it left off next time.")
                return 0
        self.state.downloaded, self.state.total = 0, None
        self.state.message = "Loading smart search model..."
        model = search.Model.get()
        self._add_log(f"Smart search model loaded on {model.device}.")
        conn = catalog.connect(settings.db)
        try:
            todo = len(search.pending(conn))
            self._add_log(f"Indexing {todo:,} photos for smart search...")

            def prog(done, total):
                self.state.downloaded, self.state.total = done, total
                self.state.message = f"Preparing smart search: {done:,} of {total:,} photos ({model.device})"
            n = search.index_photos(conn, settings.library, settings.originals, model,
                                    progress=prog, cancelled=self._cancel.is_set)
        finally:
            conn.close()
        if standalone:
            if self._cancel.is_set():
                self._finish("cancelled", f"Stopped after {n:,} photos. Run it again to continue.")
            else:
                self._finish("done", f"Smart search is ready: {n:,} photos added.", {"indexed": n})
        return n

    def _run_faces(self, _mode: str) -> None:
        try:
            self._faces(self._settings_provider(), standalone=True)
        except Exception as e:
            self._finish("failed", f"People setup failed: {e}")

    def _faces(self, settings: Settings, standalone: bool) -> int:
        """Download the face models if needed, scan new photos, then group faces."""
        from . import faces
        if not faces.model_ready():
            self.state.message = "Downloading face models (one time, about 290 MB)..."
            self._add_log(self.state.message)

            def dl(done, total):
                self.state.downloaded, self.state.total = done, total
                self.state.message = f"Downloading face models: {done / 1e6:,.0f} of {total / 1e6:,.0f} MB"
            faces.download_model(dl, cancelled=self._cancel.is_set)
            if self._cancel.is_set():
                if standalone:
                    self._finish("cancelled", "Stopped. The download resumes where it left off next time.")
                return 0
        self.state.downloaded, self.state.total = 0, None
        self.state.message = "Loading face models..."
        model = faces.FaceModel.get()
        self._add_log(f"Face models loaded on {model.device}.")
        conn = catalog.connect(settings.db)
        try:
            todo = len(faces.pending(conn))
            self._add_log(f"Looking for faces in {todo:,} photos...")

            def prog(done, total):
                self.state.downloaded, self.state.total = done, total
                if getattr(model, "fell_back", False) and not getattr(self, "_fallback_logged", False):
                    self._fallback_logged = True
                    self._add_log("The graphics card rejected the face models part-way; continuing on the CPU (slower, same results).")
                self.state.message = f"Finding faces: {done:,} of {total:,} photos ({model.device})"
            n = faces.scan_photos(conn, settings.originals, model, progress=prog, cancelled=self._cancel.is_set)
            self.state.message = "Grouping faces by person..."
            g = faces.group_faces(conn)
            c = faces.counts(conn)
        finally:
            conn.close()
        self._add_log(f"Scanned {n:,} photos; {c['faces']:,} faces in {c['people']:,} people.")
        if standalone:
            if self._cancel.is_set():
                self._finish("cancelled", f"Stopped after {n:,} photos. Run it again to continue.")
            else:
                self._finish("done", f"People ready: {c['faces']:,} faces grouped into {c['people']:,} people.",
                             {"scanned": n, **g})
        return n

    def _run_describe(self, mode: str) -> None:
        try:
            self._describe(self._settings_provider(), standalone=True, limit=25 if mode == "test" else None)
        except Exception as e:
            self._finish("failed", f"AI descriptions failed: {e}")

    def _describe(self, settings: Settings, standalone: bool, limit: int | None = None) -> dict:
        from . import describe
        key = describe.get_api_key()
        if not key:
            raise RuntimeError("No API key. Add your Anthropic API key in Settings.")
        client = describe.make_client(key)
        conn = catalog.connect(settings.db)
        try:
            todo = len(describe.pending(conn))
            if limit:
                todo = min(todo, limit)
            self._add_log(f"Describing {todo:,} photos with {settings.ai_model} (downscaled copies, no location data)...")

            def prog(done, total):
                self.state.downloaded, self.state.total = done, total
                self.state.message = f"Describing photos: {done:,} of {total:,}"
            r = describe.run(conn, settings.originals, client, settings.ai_model,
                             progress=prog, cancelled=self._cancel.is_set, limit=limit)
            c = describe.counts(conn, settings.ai_model)
        finally:
            conn.close()
        self._add_log(f"Described {r['described']:,} photos ({r['refused']} declined, {r['errors']} errors). "
                      f"Total spent so far: about ${c['spent_usd']:.2f}.")
        if standalone:
            state = "cancelled" if self._cancel.is_set() else "done"
            self._finish(state, f"AI descriptions: {r['described']:,} photos described"
                         f"{' (stopped early)' if state == 'cancelled' else ''}. About ${c['spent_usd']:.2f} spent in total.", r)
        return r

    def _run_import_check(self, _mode: str) -> None:
        from . import importer
        try:
            settings = self._settings_provider()
            conn = catalog.connect(settings.db)
            try:
                self.state.message = "Preparing look-alike detection for your library..."
                importer.fill_hashes(conn, settings.library, settings.originals,
                                     progress=self._progress("Prepared"), cancelled=self._cancel.is_set)
                self.state.message = "Checking the folder..."
                plan = importer.check_folder(conn, Path(self.payload.get("folder", "")), settings.library, settings.originals,
                                             progress=self._progress("Checked"), cancelled=self._cancel.is_set)
            finally:
                conn.close()
            self.import_plan = plan
            s = plan.summary()
            self._finish("cancelled" if self._cancel.is_set() else "done",
                         f"Checked {s['new'] + s['exact'] + s['near']:,} photos and videos: {s['new']:,} new, "
                         f"{s['exact']:,} already in the library, {s['near']:,} look-alikes.", s)
        except Exception as e:
            self._finish("failed", f"Could not check that folder: {e}")

    def _run_import(self, _mode: str) -> None:
        from . import importer
        plan = getattr(self, "import_plan", None)
        if plan is None:
            self._finish("failed", "Check a folder first.")
            return
        try:
            settings = self._settings_provider()
            conn = catalog.connect(settings.db)
            try:
                self.state.message = "Copying files into the library..."
                r = importer.run_import(conn, plan, settings.library, settings.originals,
                                        include_near=bool(self.payload.get("include_near")),
                                        progress=self._progress("Copied"), cancelled=self._cancel.is_set)
                self._quality(conn, settings)
            finally:
                conn.close()
            self.import_plan = None
            self._finish("cancelled" if self._cancel.is_set() else "done",
                         f"Imported {r['copied']:,} files ({r['failed']} failed). Your library now has {r['total']:,} items.", r)
        except Exception as e:
            self._finish("failed", f"Import failed: {e}")

    def _run_verify(self, _mode: str) -> None:
        try:
            settings = self._settings_provider()
            self.state.message = "Checking files..."
            conn = catalog.connect(settings.db)
            try:
                r = catalog.verify(conn, settings.originals, progress=self._progress("Checked"),
                                   cancelled=self._cancel.is_set)
            finally:
                conn.close()
            for p in r.changed[:50]:
                self._add_log(f"CHANGED  {p}")
            for p in r.missing[:50]:
                self._add_log(f"MISSING  {p}")
            bad = len(r.changed) + len(r.missing)
            self._finish("done" if not bad else "failed",
                         f"Check finished: {r.ok:,} OK, {len(r.changed)} changed, {len(r.missing)} missing.",
                         {"ok": r.ok, "changed": len(r.changed), "missing": len(r.missing)})
        except InterruptedError:
            self._finish("cancelled", "Check stopped.")
        except InterruptedError:
            self._finish("cancelled", "Check stopped.")
        except Exception as e:
            self._finish("failed", f"Check failed: {e}")

    def _run_sync(self, mode: str) -> None:
        try:
            settings = self._settings_provider()
            settings.originals.mkdir(parents=True, exist_ok=True)
            cmd = sync.build_command(sync.find_icloudpd(), settings, mode=mode, webui=True)
        except Exception as e:
            self._finish("failed", f"Could not start: {e}")
            return

        if sync.port_in_use():
            self._finish("failed", f"Another program on this computer is using port {sync.ICLOUDPD_PORT}, which the "
                         "iCloud sign-in helper needs. Close that program and try again.")
            return
        if sync.firewall_rule_present() is False:
            self._add_log("Warning: the firewall rule for the sign-in helper is missing. Re-run the installer and keep "
                          "'Block network access to the iCloud sign-in helper' ticked, or click Cancel if Windows Firewall asks.")
        self._helper_ok = False
        self.state.message = "Connecting to iCloud..."
        self._add_log(f"Starting {mode} sync of {settings.apple_id}")
        extra: dict = {}
        if sys.platform == "win32":
            extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            extra["start_new_session"] = True  # own process group, so Stop kills all of it
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1, **extra,
            )
        except Exception as e:
            self._finish("failed", f"Could not start icloudpd: {e}")
            return

        watcher = threading.Thread(target=self._watch_auth, daemon=True)
        watcher.start()
        assert self._proc.stdout
        for raw in self._proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            self._add_log(line)
            if m := _TOTAL_RE.search(line):
                self.state.total = int(m.group(1).replace(",", ""))
            if _DONE_RE.search(line):
                self.state.downloaded += 1
                total = f" of {self.state.total:,}" if self.state.total else ""
                self.state.message = f"Downloaded {self.state.downloaded:,} new{total}"
        code = self._proc.wait()
        self._proc = None
        self._helper_ok = False
        downloaded = self.state.downloaded
        downloaded = self.state.downloaded

        if self._cancel.is_set():
            self._finish("cancelled", "Sync stopped. Run it again any time; it picks up where it left off.")
            return
        if code != 0:
            self._finish("failed", _explain_icloudpd_failure(code, [line for _, line in list(self.log)[-80:]]))
            return
        conn = catalog.connect(settings.db)
        catalog.set_meta(conn, "last_sync", catalog.now_iso())
        conn.commit()
        conn.close()
        try:
            r = self._scan(settings)
        except InterruptedError:
            self._finish("cancelled", f"Sync stopped after downloading {downloaded:,}; cataloging will finish next time.")
            return
        except Exception as e:
            self._finish("failed", f"Download finished but cataloging failed: {e}")
            return
        for enabled, step, label in ((settings.smart_search, self._index, "Smart search indexing"),
                                     (settings.people, self._faces, "Finding faces"),
                                     (settings.ai_descriptions, self._describe, "AI descriptions")):
            if not enabled:
                continue
            if self._cancel.is_set():
                self._finish("cancelled", f"Sync stopped after downloading {downloaded:,}. Later steps will run next time.")
                return
            try:
                step(settings, standalone=False)
            except Exception as e:
                self._add_log(f"{label} failed: {e}")
        self._finish("cancelled" if self._cancel.is_set() else "done",
                     f"Sync complete: {downloaded:,} downloaded, {r.total:,} items in your library, "
                     f"{r.trips:,} trips.",
                     {"downloaded": downloaded, "total": r.total, "missing": r.missing})

    def _watch_auth(self) -> None:
        """Poll icloudpd's sign-in page to learn when it needs a password or code."""
        while self._proc is not None and self._proc.poll() is None:
            try:
                if not self._helper_ok:
                    # Only ever talk to icloudpd's own page, never to whatever else might sit on the port.
                    with urllib.request.urlopen(f"{sync.ICLOUDPD_WEBUI}/", timeout=3) as resp:
                        if sync.ICLOUDPD_MARKER not in resp.read(20000).decode("utf-8", "replace"):
                            time.sleep(1.5)
                            continue
                    self._helper_ok = True
                with urllib.request.urlopen(f"{sync.ICLOUDPD_WEBUI}/status", timeout=3) as resp:
                    html = resp.read().decode("utf-8", "replace")
                if 'name="password"' in html:
                    self.state.state = "needs_password"
                    self.state.message = "iCloud needs your Apple ID password."
                elif 'name="code"' in html:
                    self.state.state = "needs_code"
                    self.state.message = "Enter the 6-digit code shown on your iPhone."
                elif self.state.state in ("needs_password", "needs_code"):
                    self.state.state = "running"
            except Exception:
                pass
            time.sleep(1.5)
