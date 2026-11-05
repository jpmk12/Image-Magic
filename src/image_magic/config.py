"""Library location and settings.

A library is a folder that looks like:

    <library>/
      image-magic.json   settings (Apple ID, ...)
      library.db         SQLite catalog
      originals/         files exactly as downloaded from iCloud
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_NAME = "image-magic.json"
DB_NAME = "library.db"
ORIGINALS_DIR = "originals"


def default_library_path() -> Path:
    env = os.environ.get("IMAGE_MAGIC_LIBRARY")
    if env:
        return Path(env).expanduser()
    # Not ~/Pictures: on many Windows laptops that folder is synced to OneDrive,
    # which would quietly upload the whole library a second time.
    return Path.home() / "ImageMagic"


@dataclass
class Config:
    apple_id: str = ""

    @staticmethod
    def load(library: Path) -> "Config":
        path = library / CONFIG_NAME
        if not path.exists():
            return Config()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
        return Config(**known)

    def save(self, library: Path) -> None:
        library.mkdir(parents=True, exist_ok=True)
        (library / CONFIG_NAME).write_text(
            json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8"
        )


def originals_dir(library: Path) -> Path:
    return library / ORIGINALS_DIR


def db_path(library: Path) -> Path:
    return library / DB_NAME
