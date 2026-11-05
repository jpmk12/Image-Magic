"""Download the iCloud Photos library with icloudpd.

icloudpd talks to iCloud the same way iCloud.com does. It handles the Apple ID
password and the 2FA code prompt itself, so we run it attached to the terminal.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Year/Year-Month folders, e.g. originals/2019/2019-03/IMG_1234.HEIC
FOLDER_STRUCTURE = "{:%Y/%Y-%m}"


def find_icloudpd() -> str:
    """Prefer the icloudpd installed next to this Python (same virtualenv)."""
    bindir = Path(sys.executable).parent
    for name in ("icloudpd", "icloudpd.exe"):
        candidate = bindir / name
        if candidate.exists():
            return str(candidate)
    found = shutil.which("icloudpd")
    if found:
        return found
    raise FileNotFoundError(
        "icloudpd was not found. Install it with:  pip install icloudpd"
    )


def build_command(
    icloudpd: str,
    apple_id: str,
    originals: Path,
    *,
    recent: int | None = None,
    until_found: int | None = None,
    adp: bool = False,
    dry_run: bool = False,
    auth_only: bool = False,
) -> list[str]:
    cmd = [
        icloudpd,
        "--username", apple_id,
        "--directory", str(originals),
        "--folder-structure", FOLDER_STRUCTURE,
        # Keep the untouched original AND the edited version when one exists.
        "--size", "original",
        "--size", "adjusted",
        # Keep Live Photo videos (default) and write Apple's metadata to .xmp
        # sidecars (favorites, titles, descriptions, keywords).
        "--xmp-sidecar",
        "--keep-unicode-in-filenames",
        "--no-progress-bar",
        "--log-level", "info",
    ]
    if recent:
        cmd += ["--recent", str(recent)]
    if until_found:
        cmd += ["--until-found", str(until_found)]
    if adp:
        cmd.append("--adp-web-access")
    if dry_run:
        cmd.append("--only-print-filenames")
    if auth_only:
        cmd.append("--auth-only")
    return cmd


def run(cmd: list[str]) -> int:
    """Run icloudpd interactively so it can prompt for password and 2FA code."""
    return subprocess.call(cmd)
