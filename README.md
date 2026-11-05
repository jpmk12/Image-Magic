# Image Magic

Back up your whole iCloud Photos library to your own computer and keep a catalog of
every photo. Search by person, place and subject comes next. See [PLAN.md](PLAN.md).

🛡️ **Image Magic only copies photos from iCloud. It never deletes anything in iCloud.**
The iCloud download tool it uses has options that delete photos, and Image Magic blocks
all of them in code. Automated tests check this.

## Install (Windows)

1. Go to the **[Releases page](../../releases/latest)** and download **`ImageMagic-Setup.exe`**.
2. Double-click it. No admin rights are needed.
   - Windows may show **"Windows protected your PC"**, because the app isn't signed by a
     paid publisher certificate. Click **More info → Run anyway**.
3. Image Magic opens in your web browser. Next time, open it from the **desktop icon** or the
   **Start menu**.

### First use
1. Go to **Settings**, enter your **Apple ID**, check the library folder, and click **Save**.
2. On **Home**, click **Test with 20 photos**.
3. When asked, enter your **Apple ID password**, then the **6-digit code** shown on your
   iPhone. You only need to sign in again about every 2 months.
   - Windows Firewall may ask whether *icloudpd* can use the network. Click **Cancel** (or
     leave "Public networks" unticked). Sign-in still works, because it stays on your
     computer.
4. Click **Open folder** to check that the photos look right, then click
   **Download everything**. The first time takes a few hours. You can **Stop** at any time
   and resume later, and it picks up where it left off.
5. After that, **Get new photos** fetches only what's new. You can also turn on
   **Automatically get new photos** in Settings.

**Quit:** Image Magic runs in the background while it's open. Use the **Quit** button in the
app to stop it. Closing the browser tab doesn't stop it.

**Uninstall:** go to Windows Settings → Apps → Image Magic → Uninstall. This removes the
program only. Your photos and settings are kept.

## Browsing your photos
- **Photos:** every photo and video, newest first, grouped by month. Filter by **year**,
  **country**, **city** or photos/videos. Click a photo to view it full size, with its date,
  place, camera and trip. Use the ← → keys to move between photos. For a Live Photo, click
  **Play motion**.
- **Trips:** found automatically. A trip is a run of photos taken more than 100 km from home,
  where "home" is where you took the most photos that year. Click a trip to see its photos.
- **Map:** every photo with a location, clustered on a world map. Click a pin to open the photo.
  The map background needs internet. Everything else works offline.

Place names are looked up **on your computer** from a bundled list of about 145,000 places
(GeoNames, CC BY 4.0). Your locations are never sent anywhere.

## Settings
| Setting | What it does |
|---|---|
| Apple ID | The iCloud account to back up |
| Advanced Data Protection | Tick only if it's on for your iCloud |
| Library folder | Where photos go (default `C:\Users\<you>\ImageMagic`). Avoid OneDrive folders |
| Folder layout | Year/Month, Year/Month/Day or Year. Choose before the first full download |
| Edited versions | Keep your edited version as well as the original |
| Videos / Live Photo clips | Include videos and the motion clip behind each Live Photo |
| Favorites, titles, keywords | Save Apple's extra info in `.xmp` files |
| Auto sync | Get new photos every hour, every 6 hours or daily while the app is open |
| "Get new photos" stops after | How many already-downloaded photos in a row before a quick sync stops |

Settings are stored in `%APPDATA%\ImageMagic\settings.json`. Your Apple ID password is
**never** stored by Image Magic. The iCloud sign-in session is kept by icloudpd in
`C:\Users\<you>\.pyicloud`.

## What a backup contains
- Original photos, edited versions, Live Photo clips and videos, sorted into
  `originals/<year>/<year-month>/`.
- `.xmp` files with favorites, titles and keywords.
- `library.db`: a catalog of every file with its date taken, GPS location, camera and
  checksum. It also links each Live Photo clip to its photo and flags exact duplicates.
- **Check backup** re-reads every file and confirms that none are missing or damaged.

## Command line (optional)
The same features are available from a terminal for advanced use or scripting. This needs
Python 3.10+ and `pip install -e .` in this folder.

| Command | What it does |
|---|---|
| `image-magic app` | Open the app |
| `image-magic init --apple-id you@icloud.com` | Save your Apple ID |
| `image-magic sync [--test \| --quick]` | Download (copy only), then catalog |
| `image-magic scan` | Catalog files already on disk |
| `image-magic verify` | Check every file against its checksum |
| `image-magic status` | Library totals |

## Development
```bash
pip install -e ".[dev]"
pytest
```
The Windows installer is built by GitHub Actions (`.github/workflows/build-windows.yml`) on
every push. Each push to `master` publishes it on the Releases page under the version in
`src/image_magic/__init__.py`.
