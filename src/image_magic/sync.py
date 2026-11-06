"""Download the iCloud Photos library with icloudpd.

SAFETY: Image Magic only ever copies photos FROM iCloud. icloudpd has options
that delete photos from iCloud; build_command() never adds them, and
assert_never_deletes() rejects any command that contains one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .settings import FOLDER_STRUCTURES, Settings

# icloudpd options that delete photos (from iCloud, or prune local copies). Never used.
FORBIDDEN_FLAGS = frozenset({
    "--auto-delete",
    "--delete-after-download",
    "--keep-icloud-recent-days",
    "--prune-orphans",
    "--prune-orphans-adopt-existing",
})

# Port icloudpd's sign-in web page listens on (fixed by icloudpd).
ICLOUDPD_PORT = 8080
ICLOUDPD_WEBUI = f"http://127.0.0.1:{ICLOUDPD_PORT}"
ICLOUDPD_MARKER = "iCloud Photos Downloader"   # title of icloudpd's own sign-in page


def port_in_use(port: int | None = None) -> bool:
    """True if something already answers on 127.0.0.1:<port>."""
    import socket
    port = port or ICLOUDPD_PORT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def firewall_rule_present() -> bool | None:
    """Windows only: is the installer's inbound block rule for icloudpd in place? None = unknown."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", 'name=Image Magic sign-in helper'],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "Image Magic sign-in helper" in out.stdout
    except Exception:
        return None


class UnsafeCommandError(RuntimeError):
    pass


def assert_never_deletes(cmd: list[str]) -> None:
    for arg in cmd:
        flag = arg.split("=", 1)[0]
        if flag in FORBIDDEN_FLAGS:
            raise UnsafeCommandError(f"Refusing to run icloudpd with {flag}: it deletes from iCloud")


def find_icloudpd() -> str:
    """Prefer the icloudpd shipped next to this program / in this virtualenv."""
    override = os.environ.get("IMAGE_MAGIC_ICLOUDPD")
    if override:
        return override
    bindir = Path(sys.executable).parent
    for folder in (bindir, bindir / "Scripts"):
        for name in ("icloudpd.exe", "icloudpd"):
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("icloudpd")
    if found:
        return found
    raise FileNotFoundError("icloudpd was not found. Install it with:  pip install icloudpd")


def build_command(
    icloudpd: str,
    settings: Settings,
    *,
    mode: str = "full",        # full | quick | test
    test_count: int = 20,
    webui: bool = False,       # ask for password / 2FA code via web page instead of console
    dry_run: bool = False,
    auth_only: bool = False,
) -> list[str]:
    if not settings.apple_id:
        raise ValueError("Apple ID is not set")
    cmd = [
        icloudpd,
        "--username", settings.apple_id,
        "--directory", str(settings.originals),
        "--folder-structure", FOLDER_STRUCTURES[settings.folder_structure],
        "--size", "original",
        "--keep-unicode-in-filenames",
        "--no-progress-bar",
        "--log-level", "info",
    ]
    if settings.download_edited:
        cmd += ["--size", "adjusted"]
    if not settings.download_videos:
        cmd.append("--skip-videos")
    if not settings.download_live_videos:
        cmd.append("--skip-live-photos")
    if settings.save_xmp:
        cmd.append("--xmp-sidecar")
    if settings.advanced_data_protection:
        cmd.append("--adp-web-access")
    if mode == "quick":
        cmd += ["--until-found", str(settings.quick_sync_stop_after)]
    elif mode == "test":
        cmd += ["--recent", str(test_count)]
    elif mode != "full":
        raise ValueError(f"unknown sync mode {mode!r}")
    if webui:
        cmd += ["--password-provider", "webui", "--mfa-provider", "webui"]
    if dry_run:
        cmd.append("--only-print-filenames")
    if auth_only:
        cmd.append("--auth-only")
    assert_never_deletes(cmd)
    return cmd


def run_interactive(cmd: list[str]) -> int:
    """Run attached to the terminal so icloudpd can prompt for password and 2FA."""
    assert_never_deletes(cmd)
    return subprocess.call(cmd)
