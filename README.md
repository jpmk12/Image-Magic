# Image Magic

Back up your whole iCloud Photos library to your own computer and keep a catalog of
every photo. Search by person, place and subject comes next. See [PLAN.md](PLAN.md).

🛡️ **Image Magic only copies photos from iCloud. It never deletes anything in iCloud.**
The iCloud download tool it uses has options that delete photos, and Image Magic blocks
all of them in code. Automated tests check this.

## Install (Windows)

1. Go to the **[Releases page](../../releases/latest)** and download **`ImageMagic-Setup.exe`**.
2. Double-click it. Keep **"Block network access to the iCloud sign-in helper"** ticked
   (Windows will ask for administrator approval once). See [Security](#security-and-privacy).
   - Windows may show **"Windows protected your PC"**, because the app isn't signed by a
     paid publisher certificate. Click **More info → Run anyway**.
3. Image Magic opens in your web browser. Next time, open it from the **desktop icon** or the
   **Start menu**.

### First use
1. Go to **Settings**, enter your **Apple ID**, check the library folder, and click **Save**.
2. On **Home**, click **Test with 20 photos**.
3. When asked, enter your **Apple ID password**, then the **6-digit code** shown on your
   iPhone. You only need to sign in again about every 2 months.
   - If Windows Firewall asks whether *icloudpd* can use the network, click **Cancel**.
     Sign-in still works, because it stays on your computer.
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

- **Smart search:** type what you're looking for, like *"C-17 on the flight line"*,
  *"kids at the beach"* or *"sunset over the desert"*, and the best matches appear first. It
  combines with the year, place and trip filters, for example "desert" + Jordan. Turn it on
  once from the Home tab. That downloads the search model (about 600 MB, checked against a
  fixed fingerprint) and prepares your photos. This takes a few minutes on a GPU, or roughly
  30 minutes per 16,000 photos on a CPU, and is a one-time job. New photos are prepared
  after each sync.

- **Timeline:** your life in chapters. A chapter is a period with one home, found from where
  you took most photos (ignoring trips), so each move starts a new chapter. Click a chapter
  title to rename it. Each year shows month bars (click one to see that month), trips, and
  **milestones** the app found: your first photo, each move, the biggest trip and busiest day
  of each year, and the first photo of each named person. Click ✕ on a milestone to dismiss it.
- **Replay:** click **▶ Replay** on a trip, or **▶ Replay this day** from any day (from the
  Timeline, a milestone, or the viewer's "This day" link). The photos play in order on the
  right while the map on the left follows your route. Live Photos play their motion. Space
  pauses, arrows step, Esc closes. Choose Slow, Normal or Fast.
- **AI descriptions (optional, paid):** with your own Anthropic API key (Settings), Claude
  writes a one-sentence caption and keywords for each photo. Search then matches those words
  too ("birthday cake", "snow on the flight line"). Only a small copy of each photo with all
  metadata removed is sent; nothing else about you is. Mark a photo **Private** in the viewer
  and it is never sent. Start with **Try on 25 photos**; the Home tab shows the estimated
  cost before you describe everything (about $3–4 for 16,000 photos with Haiku).
- **Stories:** on a trip card or a chapter header, click **✍ Story**. Claude drafts 2–4 short
  paragraphs from the dates, places, people and captions (text only, no photos are sent), and
  you edit it into your own words. Your edits are kept.
- **Ask:** the **Ask** tab answers questions about your own life ("When did we last go to the
  beach?", "How many countries have the kids been to?") and shows the photos it found. Claude
  reads the catalog through a few tools; it never sees the photos. Needs your API key.
- **Growing up, birthdays and the book:** on a person's card, **Set birthday** adds ages
  everywhere and "Emma turns 3" milestones to the Timeline. **Growing up** plays their face
  through the years; **Save as GIF** exports it, and **Make the book** builds a printable PDF
  (cover, growing-up page, then the best photos of each year with captions). Exports land in
  the `exports` folder inside your library.
- **Memories tab:** **Shuffle a memory** picks a random good moment (best-rated photos first,
  then photos of named people, then trips) with its date, place, trip and chapter. **On this
  day** shows photos from today's date in past years. **Then & now** pairs photos taken at the
  same spot at least a year apart. In the viewer, **Similar moments** shows look-alike photos
  from other days (needs smart search).
- **Your year in review:** at the top of the Timeline: photos, videos, countries (and first
  visits), places, kilometres travelled, trips, most-photographed people, busiest day, and the
  best photos spread across the year. **▶ The year in 30 seconds** replays them.
- **That day:** the viewer shows sunrise and sunset for a photo's place and date (computed on
  your laptop). Turn on **Show the weather** in Settings → Privacy to add that day's weather
  from Open-Meteo (sends the date and a location rounded to about 1 km).
- **Share:** with a trip, person, chapter, day or month selected in Photos, click **↗ Share**.
  The app first lists photos that look sensitive (aircraft, bases, uniforms, documents, and
  similar, from the AI captions) so you can leave them out, then exports a folder with a web
  page and 1600 px copies of the photos with **all metadata removed**. Zip it to send, or
  copy it to any web host. Private photos are never included.
- **Film:** in any replay, click **🎬 Film** to render a video in your browser (slow zooms,
  captions, optional music from a file on your computer). Saved as a `.webm` in Downloads.
- **Read aloud:** a story can be read out by your browser's voice (🔊 in the story window).
- **Fixing mistakes:** everything the app works out can be corrected, and your corrections
  stick through rebuilds. In the viewer: **Trip ✎ change** moves a photo to another trip, a
  new trip, or no trip, and **Find more photos from this trip** suggests other photos that
  belong with it (by date, by how they look, and by their captions) for you to tick and add.
  Click a person's name tag for **Not this person** or **This is someone else**. **About ✎ edit**
  rewrites a caption and keywords. Chapters can be renamed on the Timeline, milestones
  dismissed, trips renamed on their cards.
- **People:** turn it on once from the Home tab (about 290 MB of face models, verified by
  fingerprint). The app finds every face and groups them by person. On the **People** tab,
  click **"Who is this?"** to name a group, click the face to see all of that person's photos,
  use **Same as…** when two groups are the same person, and **Hide** for people you don't
  care about (strangers in the background). Photos show who's in them in the viewer. New
  photos are scanned after each sync.

Place names are looked up **on your computer** from a bundled list of about 145,000 places
(GeoNames, CC BY 4.0). Your locations are never sent anywhere.

## Security and privacy

**Your Apple ID password**
- Image Magic never saves it. You type it into the app, and the app passes it straight to
  the iCloud download tool on your own computer, which sends it to Apple over an encrypted
  connection. It is never written to disk or to the activity log.
- After you sign in, Apple issues a sign-in session (like "stay signed in" on a website),
  which lasts about 2 months. icloudpd keeps it in `C:\Users\<you>\.pyicloud`. Anyone who
  can get into your Windows account could copy it, so protect the laptop with a Windows
  password and **turn on Device Encryption or BitLocker** (Settings → Privacy & security →
  Device encryption).
- While it waits for your password, the download tool's sign-in page listens on the network.
  The installer's **"Block network access to the iCloud sign-in helper"** option adds a
  Windows Firewall rule so only your own computer can reach it. Keep that option ticked, and
  if Windows Firewall ever asks about *icloudpd*, click **Cancel**.

**Your photos**
- They stay on your laptop and in iCloud. Image Magic has no server of its own and sends no
  analytics.
- Place names are worked out on your laptop. GPS locations are never sent anywhere.
- Smart search runs on your laptop using OpenAI's open CLIP model (MIT License). Photos are
  analysed locally and never uploaded. The only network use is the one-time model download.
- AI descriptions are the one opt-in feature that sends photo content off your laptop: a
  1024 px JPEG copy with no EXIF (no GPS, camera or date) goes to Anthropic's API under your
  own key, and the caption comes back. Your API key is stored in `%APPDATA%\ImageMagic\secrets.json`.
- Face recognition uses InsightFace's open models, also on your laptop. Face data (a numeric
  signature per face, not a photo) is stored only in your library's catalog file.
- The only outside connections are to **Apple** (downloading) and, if enabled,
  **OpenStreetMap** for the map background. OpenStreetMap sees which map areas you view,
  not your photos. You can turn the map background off in Settings → Privacy.
- The app's window is a web page served only to your own computer (`127.0.0.1`). Every request
  must carry a random access code that's created at each launch and given only to your
  browser, so other websites and other accounts on the laptop can't use it.
- Image Magic never deletes anything in iCloud (see the top of this page).

**Honest limits**
- icloudpd is a popular open-source tool, but it isn't made by Apple, and Apple could change
  iCloud in a way that breaks it until it's updated.
- The installer isn't code-signed, which is why Windows shows a warning the first time.
- Photos on the laptop are ordinary files. Disk encryption (above) is what protects them if
  the laptop is lost or stolen.

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
| Map background | Turn off to keep map views from going to OpenStreetMap |
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
