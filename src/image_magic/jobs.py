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
from dataclasses import dataclass, field

from . import catalog, sync
from .settings import Settings

_TOTAL_RE = re.compile(r"Downloading (?:the first )?([\d,]+) ")
_DONE_RE = re.compile(r"\bDownloaded \S")


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

    def start(self, kind: str, mode: str = "full") -> None:
        with self._lock:
            if self.busy:
                raise RuntimeError("Another task is already running")
            self._cancel.clear()
            self.state = JobState(kind=kind, state="running", started=time.time())
            target = {"sync": self._run_sync, "scan": self._run_scan, "verify": self._run_verify,
                      "index": self._run_index}[kind]
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
                                 f"{r.trips:,} trips.",
                         {"new": r.new, "total": r.total, "missing": r.missing})
        except Exception as e:  # report, never crash the app
            self._finish("failed", f"Cataloging failed: {e}")

    def _scan(self, settings: Settings):
        self.state.message = "Cataloging files..."
        conn = catalog.connect(settings.db)
        try:
            return catalog.scan(conn, settings.originals, progress=self._progress("Cataloged"))
        finally:
            conn.close()

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

    def _run_verify(self, _mode: str) -> None:
        try:
            settings = self._settings_provider()
            self.state.message = "Checking files..."
            conn = catalog.connect(settings.db)
            try:
                r = catalog.verify(conn, settings.originals, progress=self._progress("Checked"))
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

        if self._cancel.is_set():
            self._finish("cancelled", "Sync stopped. Run it again any time; it picks up where it left off.")
            return
        if code != 0:
            self._finish("failed", f"Sync did not finish (icloudpd exit code {code}). See the log for details.")
            return
        conn = catalog.connect(settings.db)
        catalog.set_meta(conn, "last_sync", catalog.now_iso())
        conn.commit()
        conn.close()
        try:
            r = self._scan(settings)
        except Exception as e:
            self._finish("failed", f"Download finished but cataloging failed: {e}")
            return
        if settings.smart_search:
            try:
                self._index(settings, standalone=False)
            except Exception as e:
                self._add_log(f"Smart search indexing failed: {e}")
        self._finish("done",
                     f"Sync complete: {self.state.downloaded:,} downloaded, {r.total:,} items in your library, "
                     f"{r.trips:,} trips.",
                     {"downloaded": self.state.downloaded, "total": r.total, "missing": r.missing})

    def _watch_auth(self) -> None:
        """Poll icloudpd's sign-in page to learn when it needs a password or code."""
        while self._proc is not None and self._proc.poll() is None:
            try:
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
