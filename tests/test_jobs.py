import os
import sys
import textwrap
import time

import pytest

from image_magic.jobs import JobRunner
from image_magic.settings import Settings


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_stop_kills_child_processes(tmp_path, monkeypatch):
    """Like the packaged icloudpd: a launcher whose child ignores SIGTERM."""
    pidfile = tmp_path / "child.pid"
    fake = tmp_path / "fake_icloudpd.py"
    fake.write_text(textwrap.dedent(f"""
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c",
            "import signal, time, os; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            "open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(600)"])
        print("INFO     Authenticating...", flush=True)
        child.wait()
    """))
    fake.chmod(0o755)
    wrapper = tmp_path / "icloudpd"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setenv("IMAGE_MAGIC_ICLOUDPD", str(wrapper))

    settings = Settings(apple_id="me@example.com", library_path=str(tmp_path / "lib"))
    runner = JobRunner(lambda: settings)
    runner.start("sync", "test")
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.05)
    child_pid = int(pidfile.read_text())

    runner.cancel()
    for _ in range(100):
        if runner.state.state == "cancelled":
            break
        time.sleep(0.1)
    assert runner.state.state == "cancelled"
    time.sleep(0.2)
    assert not _alive(child_pid)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed process may linger as a zombie until its parent reaps it.
    try:
        with open(f"/proc/{pid}/status") as f:
            return not any(line.startswith("State:") and "Z" in line for line in f)
    except FileNotFoundError:
        return False


def test_icloudpd_failures_are_explained():
    from image_magic.jobs import _explain_icloudpd_failure
    msg = _explain_icloudpd_failure(1, ["ERROR Apple iCloud setup is not complete. Please log into https://icloud.com/"])
    assert "icloud.com" in msg and "exit code" not in msg
    assert "Advanced Data Protection" in _explain_icloudpd_failure(1, ["enable Access iCloud Data on the Web"])
    assert "exit code 7" in _explain_icloudpd_failure(7, ["something else"])
