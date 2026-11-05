# Image Magic

Back up your whole iCloud Photos library to your own computer and keep a catalog of
every photo. Search by person, place and subject comes next. See [PLAN.md](PLAN.md).

**Milestone 1 (this version):** download everything from iCloud, catalog it, and verify the
backup is complete and undamaged.

## What it does
- Downloads **originals and edited versions**, **Live Photo videos**, and regular videos,
  using [icloudpd](https://github.com/icloud-photos-downloader/icloud_photos_downloader).
- Sorts them into `originals/<year>/<year-month>/` folders.
- Writes Apple's extra info (favorites, titles, keywords) to `.xmp` files next to each photo.
- Builds a catalog (`library.db`) with the date taken, GPS location, camera, size and a
  checksum for each file. It also links each Live Photo video to its still photo and flags
  exact duplicates.
- `verify` re-reads every file and confirms it still matches its checksum.

## Setup on Windows

1. Install **Python 3.12 or newer** from <https://www.python.org/downloads/>.
   On the first installer screen, tick **"Add python.exe to PATH"**.
2. Get this project. Either use `git clone https://github.com/jpmk12/Image-Magic`, or on
   GitHub click **Code → Download ZIP** and unzip it.
3. Open **PowerShell** in the project folder and run:
   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -e .
   ```
   If PowerShell refuses to run `Activate.ps1`, run this once and try again:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
4. Set up the library:
   ```powershell
   image-magic init --apple-id you@icloud.com
   ```
   The library goes in `C:\Users\<you>\ImageMagic`. To put it somewhere else, add
   `--library D:\Photos` to every command, or set the environment variable
   `IMAGE_MAGIC_LIBRARY`.

> **Disk space:** about 16,000 items is roughly 50–100 GB. Check that the laptop has enough
> free space first.
> **Don't put the library inside a OneDrive folder**, or it will upload everything again.

## Using it

Each time you open a new PowerShell window, run `.venv\Scripts\Activate.ps1` first.

```powershell
# 1. Test run: download only the 20 most recent items
image-magic sync --recent 20

# 2. Full download (a few hours the first time; safe to stop and re-run, it resumes)
image-magic sync

# 3. Later syncs: fetch only new photos, stopping after 50 already-downloaded in a row
image-magic sync --until-found 50

# Check the backup at any time
image-magic status
image-magic verify
```

**Signing in:** the first `sync` asks for your Apple ID password and then the 6-digit code
that appears on your iPhone. The sign-in is saved for about 2 months. After that, `sync`
asks you to sign in again.

**Advanced Data Protection:** if it's on (Settings → [your name] → iCloud → Advanced Data
Protection), add `--adp` to `sync` and approve the prompt that appears on your iPhone.

Your Apple ID password is never stored by Image Magic. icloudpd keeps the sign-in session
in `C:\Users\<you>\.pyicloud`.

## Commands
| Command | What it does |
|---|---|
| `image-magic init` | Create the library and save your Apple ID |
| `image-magic sync` | Download from iCloud, then update the catalog |
| `image-magic scan` | Update the catalog from files already on disk (no download) |
| `image-magic verify` | Re-check every file against its checksum |
| `image-magic status` | Show totals: photos, videos, size, GPS coverage, duplicates, last sync |

## Development
```bash
pip install -e ".[dev]"
pytest
```
