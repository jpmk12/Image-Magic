"""User settings, editable from the app's Settings tab.

Stored outside the photo library so the library folder itself can be changed:
  Windows: %APPDATA%\\ImageMagic\\settings.json
  macOS/Linux: ~/.config/image-magic/settings.json
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DB_NAME = "library.db"
ORIGINALS_DIR = "originals"

FOLDER_STRUCTURES = {
    "year_month": "{:%Y/%Y-%m}",   # 2019/2019-03/IMG_1234.HEIC
    "year_month_day": "{:%Y/%m/%d}",  # 2019/03/14/IMG_1234.HEIC
    "year": "{:%Y}",               # 2019/IMG_1234.HEIC
}


def settings_dir() -> Path:
    env = os.environ.get("IMAGE_MAGIC_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "ImageMagic"
    return Path.home() / ".config" / "image-magic"


def default_library_path() -> str:
    # Not ~/Pictures: on many Windows laptops that folder is synced to OneDrive,
    # which would quietly upload the whole library a second time.
    return str(Path.home() / "ImageMagic")


@dataclass
class Settings:
    apple_id: str = ""
    library_path: str = ""
    download_edited: bool = True       # also keep the edited version of a photo
    download_videos: bool = True
    download_live_videos: bool = True  # the short clip behind each Live Photo
    save_xmp: bool = True              # favorites, titles, keywords as .xmp sidecars
    folder_structure: str = "year_month"
    quick_sync_stop_after: int = 50    # quick sync stops after N known photos in a row
    advanced_data_protection: bool = False
    auto_sync_hours: int = 0           # 0 = off; otherwise quick sync every N hours while open
    map_online: bool = True            # load map background tiles from OpenStreetMap
    weather_online: bool = False       # fetch that day's weather from open-meteo.com (sends rounded location)
    smart_search: bool = False         # index photos for "search by description" after each sync
    people: bool = False               # find and group faces after each sync
    ai_descriptions: bool = False      # describe new photos with Claude after each sync (paid)
    ai_model: str = "claude-haiku-5-5"
    backup_path: str = ""              # second copy of the library (external drive, NAS); empty = off
    phone_access: bool = False         # also listen on the Wi-Fi address (HTTPS + pairing code)
    daily_memory: bool = True          # a Windows notification with the memory of the day while the app is open
    inbox_auto: bool = True            # after each sync, apply confident location and clutter suggestions by itself

    def __post_init__(self) -> None:
        if not self.library_path:
            self.library_path = default_library_path()

    # ---- persistence -------------------------------------------------
    @classmethod
    def path(cls) -> Path:
        return settings_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls.from_dict(data)

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")

    # ---- validation --------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        """Build from untrusted input, coercing types and dropping unknown keys."""
        s = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            default = getattr(s, f.name)
            if isinstance(default, bool):
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "yes")
            elif isinstance(default, int):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = default
            else:
                value = str(value).strip()
            setattr(s, f.name, value)
        if s.ai_model not in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"):
            s.ai_model = "claude-haiku-5-5"
        if s.folder_structure not in FOLDER_STRUCTURES:
            s.folder_structure = "year_month"
        s.quick_sync_stop_after = max(1, min(s.quick_sync_stop_after, 10_000))
        s.auto_sync_hours = max(0, min(s.auto_sync_hours, 24 * 7))
        if not s.library_path:
            s.library_path = default_library_path()
        return s

    def to_dict(self) -> dict:
        return asdict(self)

    # ---- derived paths -----------------------------------------------
    @property
    def library(self) -> Path:
        return Path(self.library_path).expanduser()

    @property
    def originals(self) -> Path:
        return self.library / ORIGINALS_DIR

    @property
    def db(self) -> Path:
        return self.library / DB_NAME

    @property
    def backup(self) -> Path | None:
        return Path(self.backup_path).expanduser() if self.backup_path else None
