"""Entry script for the packaged desktop app (PyInstaller)."""

import sys

from image_magic.app import main

if __name__ == "__main__":
    sys.exit(main())
