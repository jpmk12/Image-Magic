# Image Magic

Back up your whole iCloud Photos library to your own computer and keep a catalog of
every photo. Search by person, place and subject comes next. See [PLAN.md](PLAN.md).

🛡️ **Image Magic only copies photos from iCloud. It never deletes anything in iCloud.**
The iCloud download tool it uses has options that delete photos, and Image Magic blocks
all of them in code. Automated tests check this.

## Quick start

**Before you start (2 minutes)**
1. Free space: 16,000 photos need roughly 50–100 GB.
2. Note whether **Advanced Data Protection** is on (iPhone: Settings → your name → iCloud → Advanced Data Protection).
3. Keep your iPhone next to you for the sign-in code.

**Install (1 minute)**
1. Download `ImageMagic-Setup.exe` from the [Releases page](../../releases/latest) and double-click it.
   If Windows shows "Windows protected your PC", click **More info → Run anyway**.
2. Keep **"Block network access to the iCloud sign-in helper"** ticked and approve the one administrator prompt.
3. The app opens in your browser. Later, open it from the desktop icon.

**First run (15 minutes)**
1. **Settings:** enter your Apple ID; tick the ADP box if it's on. Click **Save**.
2. **Home → Test with 20 photos.** Enter your Apple ID password, then the 6-digit code from your iPhone.
   If Windows Firewall asks about `icloudpd`, click **Cancel**.
   If the sync fails with "setup is not complete", sign in once at https://www.icloud.com in a browser, then try again.
3. Check the **Photos** tab shows your 20 most recent photos.
4. **Home → Download everything.** Leave the laptop plugged in and awake; a few hours. It resumes if interrupted.

**Once the download finishes**
1. **Home → Check backup** confirms nothing is missing or damaged.
2. **Trips, Timeline, Map** fill in automatically. Rename any chapter or trip by clicking its title.
3. **Home → Turn on People** (290 MB, then about an hour on a GPU). Name your family on the **People** tab and set birthdays.
4. **Home → Turn on smart search** (600 MB, a few minutes). Try "C-17" in the Photos search box.
5. Optional, about $3–4: add an Anthropic API key in Settings, **Try on 25 photos**, then **Describe all new photos**.
   This unlocks word search, stories and the Ask tab.

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

- **Dates and names in plain English:** the search box understands *"christmas 2019"*,
  *"last summer"*, *"winter 2017"*, *"March 2019"*, *"thanksgiving"* (every year), *"Easter
  2021"*, *"2019"*, *"Emma's 3rd birthday"* (with her birthday set), *"Emma and Jack"* (both in
  the photo) and *"the week we moved to Dover"*, on your laptop with no AI. Leftover words
  ("beach") become the visual search. A pure date or name search works without smart search.
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
  reads the catalog through a few tools (dates, places, trip and chapter names, people's names,
  captions, and any notes you wrote on a chapter); it never sees the photos. Needs your API key.
  Ask can also **do** things: *"replay the best of our Jordan trip"*, *"show me Emma's photos
  from 2021"*, *"share the best 20 from Christmas"* (opens the share dialog; you still review
  and click Export), *"make Emma's book"*. Nothing is sent or deleted by itself.
- **Growing up, birthdays and the book:** on a person's card, **Set birthday** adds ages
  everywhere and "Emma turns 3" milestones to the Timeline. **Growing up** plays their face
  through the years; **Save as GIF** exports it, and **Make the book** builds a printable PDF
  (cover, growing-up page, then the best photos of each year with captions). Exports land in
  the `exports` folder inside your library.
- **Life book:** **📖 Life book** at the top of the Timeline makes one printable PDF of
  everything: a part per chapter with its story, dates, trips, milestones and best photos, then
  a section per trip (with its story) and per biggest event. Voice notes are counted per
  chapter so you know where to listen in the app.
- **Albums:** at the top of **Trips & events**. **+ New album** makes a hand-picked album;
  **＋ add** in the viewer puts a photo into one (or starts a new one). On the Photos tab,
  any combination of filters or a search can be kept with **＋ Save as album** as a *smart
  album* that always shows what matches now. Any album can be opened, replayed (hand-picked
  ones in your order), shared or made into a book.
- **Books:** **📖 Book** on a trip card makes a printable PDF (cover, your story, a page per
  day with the best photos, dated and captioned); **📖 Year book** at the top of the Timeline
  makes one for a year (a page per month and per trip, with the year's numbers); **📖** on an
  event card makes a short one. Books land in the `exports` folder and download.
- **Video posters:** videos show a real frame instead of a grey ▶. The first time a video
  appears in a grid, the page decodes its first half-second and hands the frame to the app,
  which keeps it as that video's thumbnail; books and the archive use it too. A video the
  laptop cannot decode (no HEVC extension installed, unusual formats) is remembered and shown
  as a plain frame, so it is not retried on every visit; the first poster saved for a video is
  the one that stays.
- **Through the years (Memories tab):** every Christmas, New Year, Valentine's, Easter, Fourth
  of July, Halloween and Thanksgiving side by side, one cover per year, plus each person's
  birthday once they have one. **▶ Replay** plays the best photos year after year, **📖 Book**
  makes a PDF with a page per year, and a year's tile opens those days in Organize.
- **Map time-lapse:** **▶ Time-lapse** on the Map tab animates where photos were taken month by
  month; the newest month glows and earlier ones fade into a trail. It follows the current map
  filter, so a single trip or year can be replayed too.
- **Quick cataloging:** after each sync the app only looks at folders iCloud changed, so the
  catalog step on a 19,000-item library takes seconds. **Catalog new files** on the Home tab does
  the same by hand; **Catalog everything** re-reads every file (use it after editing or moving
  files outside the app).
- **Memory of the day:** the Home tab opens with the best photo from this date in a past year
  (or a good random moment), and **This week** lists what the coming seven days held in past
  years: photo-days, birthdays, trips that started, milestones. While the app is open it also
  shows one Windows notification a day with that memory (Settings → Privacy to turn it off).
- **TV mode:** **📺 TV mode** on the Home tab plays a full-screen, slow, endless slideshow of
  your best photos in random order, with your voice notes, and hides the controls until you
  move the mouse. On a TV or tablet browser, pair it like a phone and add `&tv` to the end of
  the address to start straight into it. Esc ends it.
- **Memories tab:** **Shuffle a memory** picks a random good moment (best-rated photos first,
  then photos of named people, then trips) with its date, place, trip and chapter. **On this
  day** shows photos from today's date in past years. **Then & now** pairs photos taken at the
  same spot at least a year apart. In the viewer, **Similar moments** shows look-alike photos
  from other days (needs smart search).
- **Flying log:** on the Memories tab, your aircraft photos (found from the AI captions, or from
  a keyword like "aircraft" you add in the viewer) by base, by flying day and by tail number read
  from the photo. **↗ Share (strict)** exports with the strict filter: photos with readable text,
  tail numbers, badges or documents are left out automatically, and the page shows no captions,
  places or names, only the year.
- **Compare:** on the Memories tab, put two moments side by side: a person at an age (*Emma at
  3 vs Jack at 3*, needs birthdays), two years, two months, two trips or two events. Each side
  shows its count, dates, places and people, then the best photos paired up.
- **Places:** under the map, every country you have photographed, with a world summary
  (countries, places, the farthest photo from home, the place you returned to most). Click a
  country for its page: years, who was with you, the years you lived there, each visit with
  its replay, the best photos, its cities (each with its own page), and **Show on map**.
- **This day, every year:** on the Map tab, **📅 This day, every year** shows one pin per past
  year for today's date (or the nearest day within three days), joined in order.
- **Your year in review:** at the top of the Timeline: photos, videos, countries (and first
  visits), places, kilometres travelled, trips, most-photographed people, busiest day, and the
  best photos spread across the year. **▶ The year in 30 seconds** replays them.
- **That day:** the viewer shows sunrise and sunset for a photo's place and date (computed on
  your laptop). Turn on **Show the weather** in Settings → Privacy to add that day's weather
  from Open-Meteo (sends the date and a location rounded to about 1 km).
- **Share:** with a trip, person, chapter, day or month selected in Photos, click **↗ Share**.
  The app first lists photos that look sensitive (aircraft, bases, uniforms, documents, and
  similar, from the AI captions); they are **left out unless you tick them**, and Export asks
  once more how many are being left out. It then makes a web page and 1600 px copies of the
  photos with **all metadata removed**, zipped into one file that your browser downloads
  (the folder is kept in the library's exports too). Private photos are never included, and
  paired phones cannot read the exports folder.
- **Film:** in any replay, click **🎬 Film** to render a video in your browser (slow zooms,
  captions). Pick one of three **built-in music** tracks (generated on your computer, nothing
  to download or license) or a file of your own; music fades out at the end. Your **voice
  notes** are mixed in: the music ducks under them and each photo stays up until the note
  ends. Saved as a `.webm` in Downloads.
- **Read aloud:** a story can be read out by your browser's voice (🔊 in the story window).
- **Import from a folder:** on the Home tab, enter a folder (your desktop, an old phone's
  export, a camera card) and click **Check folder**. Each file is compared with the library:
  exact copies are skipped, look-alikes (the same picture resized or re-saved) are listed with
  the library photo they match, and the rest are new. Click **Import** to copy the new ones into
  `originals/imported/<year>/<month>/`. Your files are copied, never moved. The optional
  **From** box names whose photos they are (a spouse, an old Android phone); that name becomes a
  filter on the Home tab's Family panel.
- **On a phone:** the paired-phone view (and any narrow touch screen) gets a bottom tab bar
  with Home, Photos, Memories, People, Map and a More sheet for Timeline, Trips & events and
  Ask; three photos across; 44 px buttons; and in the viewer a swipe left or right moves
  between photos and a swipe down closes it. Laptop-only panels (sync, import) stay off the
  phone's Home tab.
- **Google Photos Takeout:** point the import at a `Takeout/Google Photos` folder. Google's json
  sidecars (dates, places, favourites, descriptions, even the truncated and renamed ones) travel
  with their photos, so a picture whose file carries no date or location still lands on the right
  day and place, marked as located "from Google".
- **Two iCloud accounts, one library:** Settings → Account → *A second Apple ID*. Every sync
  downloads both accounts in turn (you sign in to the second one the first time); the second
  account's photos live in their own folder (`originals/accounts/<name>/`, so they never mix
  with the year folders) and carry that person's name everywhere the contributor shows: the
  Family panel, filters, books. If the second account fails to sign in, the first account's
  new photos are still catalogued. Nothing is ever deleted from either iCloud.
- **Stories for events and years:** the **✍** button on an event card and **✍ Story** on the
  year panel work like trip and chapter stories: Claude drafts a few paragraphs from the dates,
  places, people and captions (never the pixels), you edit them, and the event book, year book
  and life book carry them.
- **Tell the story:** **🎤 Tell** on a trip, an event or a year walks you through its twenty
  best photos one at a time: type one line and press Enter, or press R and say it as a voice
  note, → to skip, Esc to stop. Captions and notes show in the viewer, replays and books, and
  search finds the words.
- **Share as one file:** the share dialog now makes a single .zip of the page and the stripped
  photos and downloads it when ready; untick the box to get the folder instead.
- **Learning from your decisions:** every inbox accept or skip, every review-queue confirm or
  dismiss, every trip you merge or dissolve and every two people you merge is remembered (on
  the laptop only). After a handful of each, the automatic rules tune themselves to your
  library: how many quiet days still count as one trip, how few photos make a trip, how
  similar a face must be to join a person, how confident a location or clutter suggestion
  must be before it is applied on its own, and how much to trust each kind of suggestion
  when showing its confidence. Each number moves only within a safe range. **Settings → What
  the app has learned** lists the changes in plain words, with a **Forget** button.
- **Curator inbox:** the top of the Home tab. After each sync the app sorts what it found and
  files suggestions as cards with a confidence: photos that look like part of a trip, a face
  group that is probably someone you named, photos with no location whose neighbours agree on a
  place, two trips that look like one, and clutter. Each card has three choices: accept (the
  action happens at once), adjust (opens the right tool with those photos selected) or skip
  (the card stays gone until the facts change). Click a card to open the **review view**: every
  photo the decision is about and nothing else (face crops for a face question, both trips
  labelled for a merge). Untick any photo that doesn't belong, double-click to look closer,
  then accept or skip; you land back on the Home tab with a "✓ Done" notice and an Undo
  button. **Accept all high-confidence** takes everything
  at 90 percent or above except naming people; **Undo last** reverses the most recent decision.
  With *Let the inbox apply safe suggestions by itself* on (Settings → Privacy, on by default),
  the app fills in confident locations and trashes byte-identical copies after each sync on its
  own and lists them in the Activity log; trips, names, blurry and blank frames always wait for
  you.
- **Review queue:** on the Home tab, the app's least-certain guesses, ten at a time: a photo that
  joined a trip by date alone or was taken far from the rest, a face that only loosely matches
  its group, a chapter whose first months are spread over several places, and new milestones.
  **Yes** locks the decision so rebuilds keep it; **No** undoes it the way the viewer's
  corrections do. The viewer shows how sure the app is about a photo's trip, and marks
  loosely matched faces with "?".
- **On your phone:** turn on **Phone access** in Settings, open the address it shows on your
  phone (same Wi-Fi), accept the certificate warning once, type the pairing code, and the app
  opens on the phone for browsing, replays, Ask and voice notes.
- **Family:** anyone who pairs a phone gives their name, and from the phone's Home tab **📷 Add
  photos** sends their own pictures into your library. Each is checked first (exact copies and
  look-alikes are refused), stored under `originals/contributed/<name>/`, marked **Added by**
  that person in the viewer, and shows up in search, replays and books. The Home tab's Family
  panel lists who added what. Pairings survive restarts; **Disconnect all phones** ends them.
- **Second copy:** set a backup folder on another drive in Settings, then **Back up now** on
  the Home tab. Every file under `originals` and `voice` is copied (only new or changed files
  on later runs), read back and checked against its SHA-256 in the catalog, and a consistent
  snapshot of the catalog goes with it. Nothing is ever deleted from the backup folder; an
  unplugged drive or a folder with other files in it is refused.
- **Archive:** **Write the archive** on the Home tab produces a self-contained web site of your
  whole story in the `exports` folder: an index, a page per chapter (story, milestones, trips,
  events, best photos), per trip, per event and per album, and with **Every photo** ticked a
  page per year with everything. Images are downscaled copies with all metadata removed,
  private photos are never included, voice notes only when ticked. Copy the folder to a USB
  stick or any web host and open `index.html`.
- **Best photos:** every photo gets a local quality rating (sharpness, exposure, contrast,
  computed on your laptop from the thumbnail, free) and bursts of near-identical shots are
  grouped so one keeper stands for the burst. Tick **Best only** on the Photos tab, and the
  year review, replays marked "best", books and the shuffle all prefer keepers. The viewer
  shows the rating and lets you open the whole burst. Rating runs after each sync; **Rate
  photos** on the Home tab rates older ones.
- **Events:** the small unit of life, next to trips: photos taken within a few hours and a
  couple of kilometres of each other (a party, a hike, a day at the beach). They are found
  automatically with titles like *"Saturday morning in Lewes"* or *"Birthday cake: Sunday
  afternoon at home"* (the keyword comes from AI captions when they are on). The **Trips &
  events** tab lists them by year; click one to see its photos, **▶ Replay** it, **↗ Share**
  it, or click the title to rename it. Renames survive rebuilds.
- **At home:** on the Timeline, each chapter has a **🏠 At home** button: a year at home season by
  season (the best photos of each winter, spring, summer and autumn, with **▶ A year at home**
  replays), the spots you photographed again and again through the years (the backyard, the
  front door), and who was there most. Trips are left out so home gets its own story.
- **Voice notes:** in the viewer, **🎙 Record your voice** on any photo (or **🎙 Note** on an
  event card) records a voice note with your microphone: who was there, what happened, how it
  felt. Notes are stored only in your library folder and play back during replays, holding the
  photo until you finish speaking. A share export leaves them out unless you tick **Include my
  voice notes**.
- **Clean up:** **Find clutter** on the Home tab sorts likely clutter into categories you
  review one at a time: exact copies, screenshots, saved images (GIFs, PNGs and WebPs with no
  camera data: memes, stickers, downloads), documents and receipts, black or white frames,
  blank frames, blurry shots, very dark photos, and spare burst frames. The safe categories
  are ticked for you; **very dark** and **documents** are not, because many of those are
  keepers. Click any photo to see it full size (arrow keys move through the category), tick
  the box in its corner to send it, Shift-click for a range. Ticked photos go to the app's **trash**
  inside your library folder, together with their Live Photo clip and sidecar, and can be
  restored exactly until you **Empty trash**. Nothing is ever deleted from iCloud; if iCloud
  sends a trashed photo again at the next sync, it goes straight back to the trash. The viewer
  also has a **Move to the app's trash** link. To remove a photo from iCloud itself, do that in
  Apple Photos.
- **Editing trips:** every trip card has **✎ Edit**: rename, change the first and last day
  (photos in the new range join, others leave), split at a date into two trips, merge into
  another trip, choose the cover (from the viewer), or **Not a trip**, which takes the trip out
  while its photos stay and are never grouped into it again. All of these survive rebuilds.
- **Organize tab:** the workbench for bulk fixes. Click selects, **Shift**+click selects a range,
  dragging on empty space draws a lasso, **Ctrl+A** takes everything shown, **Esc** clears.
  Quick-select chips grab photos with no location, not in a trip, not in an album, or best
  only, within a year or month. The bar at the bottom moves the selection to a trip (or a new
  one), an album, out of its trips, to a location or to the trash. On the right, trips and
  albums are drop targets: drag the selection onto one, click it, or press its number key.
- **Set location:** for one photo (viewer → Place) or a selection (Organize → Set location):
  take the place of photos taken nearby in time (choose how far to look, 20 minutes to 12
  hours), search a place (a town, "Town, ST", a full postal address, whose town part is used,
  or coordinates like "37.83, -92.18") from the offline list, pin it on the map, or copy it
  from a photo that has it right. For an exact street address, **Look it up online** asks
  OpenStreetMap with just the text you typed; that button is the only part that goes online
  and it does nothing until you click it. **Pin on the map** also takes anything copied from
  Google Maps or Apple Maps: right-click a spot in Google Maps and click the coordinates at the
  top of the menu, or use its Share link, then paste into the box above the map and the pin
  lands there (the degrees-minutes form works too). Short maps.app.goo.gl links are expanded
  with one online request. Only photos without a place change unless you tick overwrite. A
  location you set is marked "set by you", survives every rebuild, and anchors its neighbours.
- **Pick a span on the Timeline:** **⇔ Pick a span**, then click a first and a last month; the
  bars light up and a bar shows how many photos that is, with **Make a trip**, **Add to album**,
  **Set location** and **Open in Organize** (where you can narrow it further).
- **Review bursts:** on the Home tab's Clean up panel, **Review bursts** lists every burst with
  its frames and scores. The app pre-picks the sharpest frame (★); click another to make it the
  keeper, then **Keep ★, trash the rest** or **Keep all**. One button handles every burst at once.
- **Select mode:** **☑ Select** on the Photos tab lets you tick many photos (or **Select all
  shown**) and then move them to a trip or a new trip, take them out of their trips, add them
  to an album, or move them to the trash in one go.
- **Fixing mistakes:** everything the app works out can be corrected, and your corrections
  stick through rebuilds. In the viewer: **Trip ✎ change** moves a photo to another trip, a
  new trip, or no trip, and as soon as you do, the app suggests other photos that belong with
  it (by date, by how they look, and by their captions) for you to tick and add. **Find more**
  on a trip card does the same at any time. Photos you leave unticked, in that dialog or in an
  inbox trip card, are remembered as "not this trip" and never suggested for it again;
  Shift-click selects a range. On a face card you can untick the faces that are not that
  person: they leave the group before the rest is named or merged.
  Click a person's name tag for **Not this person** or **This is someone else**. **About ✎ edit**
  rewrites a caption and keywords. Chapters can be renamed on the Timeline, milestones
  dismissed, trips renamed on their cards.
- **People over time:** at the bottom of the People tab, a year-by-year strip per named person
  (darker means more photos; click a cell to see them that year) and **Together**: pairs who
  appear in the same photos, how often, and their first photo together, which also becomes a
  Timeline milestone.
- **People:** turn it on once from the Home tab (about 290 MB of face models, verified by
  fingerprint). The app finds every face and groups them by person. On the **People** tab,
  click **"Who is this?"** to name a group, click the face to see all of that person's photos,
  use **Same as…** when two groups are the same person, and **Hide** for people you don't
  care about (strangers in the background). Photos show who's in them in the viewer. New
  photos are scanned after each sync.

**Where locations come from.** Most photos carry GPS from the camera. For the rest the app
looks further: iCloud's own location record, which icloudpd saves next to each file as a
`.xmp` sidecar (keep *Favorites, titles, keywords* on in Settings) and which often knows a
place the file itself lacks, such as received or edited photos; the location and date inside
a video's own metadata; and finally photos taken within 20 minutes of a located photo, which
take that location ("the same afternoon at the beach"). The viewer says which source a place
came from, and the Home tab's library panel counts them. Those sidecars also bring your
♥ **favourites** (a filter on the Photos tab), titles and screenshot flags into the catalog.

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
  own key, with no date, place name or file name attached, and the caption comes back.
- The iCloud sign-in helper (icloudpd) opens a small web page on port 8080 while it waits
  for your password. Image Magic refuses to start a sync if another program is already using
  that port, only talks to that page once it has confirmed it is icloudpd's, and warns in the
  Activity log if the installer's firewall rule is missing. Your API key is stored in `%APPDATA%\ImageMagic\secrets.json`.
- Face recognition uses InsightFace's open models, also on your laptop. Face data (a numeric
  signature per face, not a photo) is stored only in your library's catalog file.
- The only outside connections are to **Apple** (downloading) and, if enabled,
  **OpenStreetMap** for the map background. OpenStreetMap sees which map areas you view,
  not your photos. You can turn the map background off in Settings → Privacy.
- The app's window is a web page served only to your own computer (`127.0.0.1`). Every request
  must carry a random access code that's created at each launch and given only to your
  browser, so other websites and other accounts on the laptop can't use it.
- Image Magic never deletes anything in iCloud (see the top of this page).
- **Phone access** is off by default. When you turn it on (Settings → Phone access), a second
  listener on your Wi-Fi address serves the app over HTTPS with a certificate made on your
  laptop (the phone's browser warns about it once; accept it there). A phone gets in only
  with the 6-digit pairing code shown in Settings, which changes every ten minutes and dies
  after five wrong tries. Each phone gets its own access code; **Disconnect all phones**
  revokes them, and turning the setting off closes the listener. From a phone you can browse,
  replay, ask and record voice notes, but not change settings, sync, import, export or quit.
  Windows Firewall will ask about Image Magic the first time; allow it on **private** networks
  only, never public ones. Paired phones are remembered in `%APPDATA%\ImageMagic\phones.json`
  as hashes (the phone keeps its own code); photos they add are checked against the library
  and stored with the sender's name. Nothing a phone sends can delete or overwrite anything.

**What was reviewed**
- The code had an independent security and privacy review and a bugs-and-performance review
  (v0.16.0). Everything they found was fixed: the sign-in helper is only ever spoken to after
  it is confirmed to be icloudpd's own page and never when its port is held by another program;
  the launcher checks that a running instance is really Image Magic before opening it; the
  access code is accepted in a URL only for loading photos; every page carries a strict
  Content-Security-Policy; AI descriptions send only the picture; the release pipeline runs
  with read-only permissions except for the publish step and ships a checksum file.

**If a model download says the connection could not be verified**
- Smart search and People download their models over HTTPS and check each file against a
  fixed fingerprint. If Windows is missing a root certificate, or security software or a work
  network inspects HTTPS, the download can fail with "certificate verify failed". The app
  verifies against both the Windows store and a bundled, current set of public roots, so this
  is rare. If it still happens: run Windows Update, try from home Wi-Fi, or download the file
  in your browser and save it where the error message says (the folder under
  `%APPDATA%\ImageMagic\models`). The app checks the fingerprint either way.

**If People stops with a graphics-card error**
- Some graphics drivers reject the face models part-way through (an "ONNXRuntimeError …
  BatchNormalization … The parameter is incorrect" message). The app now notices this, switches
  the face models to the CPU on the spot and carries on; the Activity log says so. Results are
  the same, it just takes longer. Smart search is unaffected.

**Second review (v0.43.0).** Everything added since the first review (Phase 5, Phase 6 and the
curation tools) had another independent security and bugs pass and a performance pass. Fixed:
paired phones are now limited to an explicit allowlist (browse, replay, ask, voice notes, adding
photos) and can no longer read the pairing code, the activity log or curation and export
endpoints; events keep their identity when their first photo is trashed; the trash never hides a
photo whose file could not be moved; exact-copy detection ignores copies whose original is gone;
undo of a trip addition restores Live Photo clips too; leap-day winters, malformed video atoms
and odd URL parameters no longer cause errors. Performance: clutter detection is a plain query
over stored quality components, the inbox, review queue, clean-up, memory of the day, events,
albums and flying log are cached per catalog change, the status poll no longer touches the
backup drive, the Home tab reloads its panels only when the catalog changed, and the catalog's
schema check runs once per launch instead of on every request.

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
| Let the inbox apply safe suggestions | After each sync, fill confident locations and trash exact copies on its own; everything else waits (on by default) |
| Memory of the day notification | One Windows notification a day with a past memory, while the app is open (on by default) |
| Phone access | Let phones on the same Wi-Fi open the app over HTTPS with a pairing code (off by default) |
| Second copy (backup folder) | An empty folder on another drive; **Back up now** on the Home tab mirrors the library there with checksum verification |

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
