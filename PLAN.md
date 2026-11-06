# Image Magic — Project Plan

Goal: get every photo out of iCloud into a backup you control, make it searchable by
**who** (my kids), **what** (my C-17) and **where** (Jordan), and then build a better way
to relive and share those memories.

## Decisions so far
| Question | Answer | What it means |
|---|---|---|
| Have a Mac? | **No** | Ingest uses **`icloudpd`** (route B). Apple's face names can't be exported, so the app does its own face recognition. |
| Library size | **~16,000 items** | Roughly 50–100 GB including videos. Small enough for one external drive, and for one AI-caption pass over everything. |
| Advanced Data Protection | Reported OK | If sync is refused, run it with `--adp` and approve the prompt on the iPhone. |
| Computer | **Windows laptop with an NVIDIA GPU** | Face recognition and AI image search (Phase 2) can run fast on the laptop's GPU, at no cost. |
| Backup location | **The laptop, for now** | Library lives at `C:\Users\<you>\ImageMagic`, outside OneDrive. **Risk:** this is only one copy besides iCloud. If the laptop dies or is stolen, the local copy is gone. Add an external drive or cloud backup later (step 3 of 3-2-1). |
| Paid AI | **Small cost OK** | Caption every photo once with a low-cost AI vision model (about $2–5 total, see Phase 2). |

The plan has three phases. Each one is useful by itself, so you can stop after any of them
and still have something worth having.

---

## Phase 1: Download and back up

### The constraint
Apple has no public iCloud Photos API. Every tool uses one of these four routes:

| Route | How it works | Pros | Cons |
|---|---|---|---|
| **A. Mac Photos app + `osxphotos`** (recommended if you have a Mac) | Turn on "Download Originals to this Mac" in Photos, then export with [osxphotos](https://github.com/RhetTbull/osxphotos) | Most reliable. Exports Apple's own metadata too: **named faces, places, albums, favorites, captions, Apple's scene labels** | Needs a Mac with enough disk space (or an external drive) |
| **B. `icloudpd`** (any OS, or a home server / NAS) | [icloud_photos_downloader](https://github.com/icloud-photos-downloader/icloud_photos_downloader) logs in like iCloud.com and downloads everything | Runs on a schedule. Picks up new photos automatically | Uses an unofficial API that can break. 2FA re-auth about every 2 months. With Advanced Data Protection on, "Access iCloud Data on the Web" must be enabled. Does **not** get face names |
| C. privacy.apple.com export | Ask Apple for a copy of your data | Official and complete | Slow (days), comes as many large zip files, one-time only |
| D. iCloud for Windows | Syncs photos to a Windows folder | Simple | Flaky with large libraries |

**Decision: route B (`icloudpd`)**, since there's no Mac. The first run downloads all
~16k items, which takes a few hours. After that it runs on a schedule and fetches only new
photos. Route C (privacy.apple.com) is a fallback if the unofficial API ever breaks.

### What the backup has to handle
- **HEIC/HEVC** originals: keep them as they are, and create JPEG/WebP thumbnails for viewing.
- **Live Photos**: the still and the `.MOV` are two separate files. Keep them paired.
- **Edited vs. original**: keep both.
- **Videos, screenshots, bursts, slo-mo**: tag them so they can be filtered out of memories.
- **Duplicates**: dedupe by content hash. Flag near-duplicates with a perceptual hash.
- **Metadata**: keep the date taken, GPS, camera and album membership in a database, not
  only inside the files.

### Folder layout
```
Photos/
  originals/2024/2024-03/IMG_1234.HEIC
  originals/2024/2024-03/IMG_1234.MOV      # live-photo partner
  derived/thumbs/…                          # regenerable, not backed up
  library.db                                # SQLite catalog
```

### Backup (3-2-1 rule)
3 copies, 2 kinds of media, 1 offsite:
1. iCloud (already there)
2. Local external drive or NAS
3. Offsite cloud backup (e.g. Backblaze B2 or a similar provider), encrypted

**Phase 1 deliverable:** one command that syncs iCloud to the local library, catalogs it,
verifies checksums and reports "X new, Y total, 0 missing."

---

## Phase 2: Categorize

Each of the three example searches needs a different technique.

### Where: "photos I took in Jordan" (easy)
- Read the GPS coordinates from each photo and reverse-geocode them **offline** (GeoNames
  data) into country, region and city.
- Group photos into **trips** automatically: runs of photos taken far from home, split by
  date gaps. For example, "Jordan, Mar 2019, 412 photos."
- Photos with no GPS (old scans, some downloads) can borrow a location from photos taken
  close to the same time.

### Who: "photos of my kids" (medium)
- Run local face detection and embedding (InsightFace), then **cluster** the
  faces. You label each cluster once ("this is Emma") and every match gets tagged.
- A review screen lets you confirm uncertain matches. Kids' faces change a lot with age,
  so the model keeps several reference faces per person across the years.

### What: "photos of my C-17" (medium, and the most powerful)
- Compute a **CLIP-style image embedding** for every photo, locally and for free. This
  makes plain-English search work right away: "C-17 on the flight line", "kids at the beach",
  "birthday cake", "sunset over desert".
- For things you care about a lot (C-17, a specific pet, your house), mark around 20
  examples and the app trains a small classifier on top of the embeddings. That makes
  those tags very accurate.
- **AI captions (decided: yes).** Send a downscaled copy (~1000 px) of each photo to an AI
  vision model, which returns a caption plus keywords, for example "C-17 Globemaster on
  the flight line at dusk". These are stored in the catalog and are searchable.
  Rough cost for 16,000 photos (about 1,100 input and 200 output tokens per photo, using
  the batch API at half price):
  | Model | Approx. cost, whole library |
  |---|---|
  | Claude Haiku 5.5 (default for bulk) | **about $2–5** |
  | Claude Sonnet 5.5 (richer, for favorites and trips) | about $30–60 |
  After the first pass, new photos cost a fraction of a cent each. Photos are sent as
  downscaled copies with location data removed. Anything you mark private is never sent.

### Privacy and OPSEC note
Family photos and military aircraft photos are both sensitive. The default design runs
**everything locally**, so no photo leaves your machines for categorization. Anything
cloud-based (AI captions, sharing) is opt-in, and the app can flag photos taken on bases
or showing aircraft before they're shared.

**Phase 2 deliverable:** a local web app with search ("kids + Jordan + 2019"), filters
for people, places, tags and dates, and review screens for faces and tags.

---

## Phase 3: Relive and share (the creative part)

Ideas, roughly in order of effort:
1. **Map view**: every photo pinned on a world map. Zoom into Jordan and see the trip.
2. **Timeline / "On this day"**: photos from the same date in past years.
3. **Auto-generated trip stories**: an ordered page with a map route, the best shots
   (picked by quality score and no blur or duplicates), and optional short AI-written captions.
4. **Person timelines**: watch each kid grow up, in a scrollable or time-lapse view of their face.
5. **Themed collections**: "Every C-17 sortie", "Sunsets around the world".
6. **Slideshow / video export** with music, ready to share.
7. **Sharing**: private links to a curated album or story, with GPS removed and an
   OPSEC check before anything goes out.

---

## Phase 4: The story of my life

The goal: the app uses the photos to tell the story of a life, so you can explore it and
relive what you've seen and done. Everything builds on the data the earlier phases produce:
**when** (dates), **where** (places and trips), **who** (faces) and **what** (smart search,
AI descriptions).

### Features, grouped by the experience they give

**Your life as a story**
- **Life chapters.** Found automatically from home moves (each new home location), new faces
  appearing (a child is born), and trip clusters (a deployment). Each becomes a chapter such
  as "The Dover years, 2016–2019". You rename, merge and split them.
- **Life timeline.** One scrollable line from the first photo to today, with lanes for
  chapters, trips, people and milestones. Zoom from decades to a single afternoon.
- **AI-written stories.** Opt-in: Claude drafts a short narrative for a chapter or trip from
  the dates, places, people and photo descriptions. You edit it into your own words. Cheap,
  because it sends text, not photos.
- **Milestones found for you.** A child's first photos, birthdays, graduations, a new house,
  a first flight in a new aircraft. Spotted from faces, places and search, confirmed by you.

**Reliving moments**
- **Trip replay.** An animated map flies along the route; photos appear where and when they
  were taken, Live Photos move, music plays.
- **A day in...** replays one day hour by hour.
- **Then and now.** Photos taken at the same spot years apart, side by side or with a slider.
- **The rabbit hole.** From any photo, jump to similar moments from other years (smart search
  on the photo itself).
- **Shuffle a memory.** One button: a random good moment, with its story and people.

**Your kids' stories**
- **Growing-up time-lapse** of each child's face from baby to now.
- **Birthday reel** every year, made automatically.
- **A book for each child** as a printable PDF photo book.
- **Their voices.** Record what they remember about a photo; the voice note stays attached.

**Your service**
- **Flying and deployment log.** C-17 photos on a map of bases and places, with dates. Private
  by default; every photo gets an OPSEC check before sharing.
- **Squadron yearbook.** A curated book of the people and moments from one assignment.

**Ask my life**
- Plain-language questions: "When did we last go to the beach?", "Show me Emma's first bike
  ride", "How many countries have the kids been to?" Claude answers from the catalog and
  shows the photos.

**Your year, reviewed**
- **Year in review**: countries, kilometres travelled, most-photographed person, longest trip,
  best sunset, the year in 30 seconds.
- **Context for each memory**: weather that day, sunrise time, the number-one song.

**Sharing the story**
- **Short films**: slow zooms across photos, music, optional narration of the chapter text.
- **Private story links** for family, with locations removed and an OPSEC check first.
- **Family contributions**: others add their own stories and photos to shared chapters.

### Build order
Each step is usable on its own and feeds the next.

| Step | What | Depends on |
|---|---|---|
| **S1 – People** | Face detection and grouping (local, on the GPU). Name a group once; filter photos by person. The foundation for every "kids" feature. | Nothing new |
| **S2 – Life timeline and chapters** | Automatic chapters from home moves, new people and trips; the zoomable timeline; milestones list. | S1 for people-based chapters |
| **S3 – Trip replay and "day in"** | Animated map route with photos appearing in order; Live Photo motion; a day-by-hour player. | Nothing new |
| **S4 – AI descriptions** | One opt-in pass over the library (about $2–5) adding a caption and keywords per photo. Makes search richer and enables narratives and questions. | Nothing new |
| **S5 – Stories and "Ask my life"** | AI-written chapter and trip stories you edit; plain-language questions answered from the catalog. | S2, S4 |
| **S6 – Kids' stories** | Growing-up time-lapse, birthday reels, per-child PDF book. | S1 |
| **S7 – Then and now, rabbit hole, shuffle** | Same-spot pairs across years; similar-moment browsing; random memory. | S4 helps |
| **S8 – Year in review and context** | Yearly stats and highlight reel; weather, sunrise and song for a memory. | S1, S4 |
| **S9 – Films and sharing** | Video export with music and narration; private links with GPS removed and an OPSEC check; family contributions. | S5 |

Service features (flying log, squadron yearbook) slot into S2 (log) and S9 (yearbook).

### Principles
- Everything runs on the laptop unless you opt in to a paid AI step, and even then only
  text (or downscaled, location-stripped copies) leaves the machine.
- The app proposes, you decide: chapters, milestones and stories are drafts you can edit.
- Nothing is shared without an OPSEC check and location removal.

---

## Proposed tech stack
| Layer | Choice | Why |
|---|---|---|
| Ingest | Python + `osxphotos` / `icloudpd` | The best tools available are Python |
| Catalog | SQLite + `sqlite-vec` | One file, no server, handles 100k+ photos and vector search |
| ML | InsightFace (faces), OpenCLIP (search), reverse_geocoder (places) | All run locally. A GPU or Apple Silicon helps but isn't required |
| Backend | FastAPI | Simple API for the UI |
| UI | Web app (React or Svelte), installable on the iPhone as a PWA | Works on phone and desktop without App Store hassle |
| Runs on | Mac, PC or home server/NAS | Decided by the open questions below |

## Milestones
1. **M1 – Backup:** first full export, catalog and checksum verification. *(Do this first:
   it's valuable on its own.)*
2. **M2 – Places and trips:** GPS search and auto-trips. Quick win.
3. **M3 – Search:** CLIP plain-English search plus the basic gallery UI.
4. **M4 – People:** face clustering, labeling and review.
5. **M5 – Custom tags:** the C-17 classifier and similar.
6. **M6 – Memories:** map, on-this-day, trip stories.
7. **M7 – Sharing:** links, export and the OPSEC scrub.

## Open questions
1. Who will you share with: family only, or wider (social media)?
2. Later: which second backup do you want (an external drive or a cloud service)?

## Status
- **M1 – Backup: built** (v0.2.0). A one-click Windows installer (built by GitHub Actions)
  opens the app in the browser, with Home, Settings and Activity log tabs. The iCloud
  password and iPhone code are entered in the app. Deleting from iCloud is blocked in code
  and covered by tests.
- **M2 – Places and trips: built** (v0.3.0). Photos tab (grid, year/country/city filters,
  full-size viewer), automatic trips, and a map. Place names are looked up offline from bundled
  GeoNames data (~145k places).
- **Security hardening** (v0.3.1): access code no longer embedded in the page, firewall rule for
  the sign-in helper, setting to turn off the map background.
- **M3 – Smart search: built** (v0.4.0). CLIP ViT-B/16 (ONNX) runs locally on the GPU through
  DirectML, or on the CPU. One-time model download of about 600 MB, verified by SHA-256.
  Search combines with the year, place and trip filters.
- Phase 3 is folded into Phase 4 (above), which is the roadmap from here.
- **S1 – People: built** (v0.5.0). SCRFD detection + ArcFace signatures (InsightFace
  buffalo_l, ONNX, GPU via DirectML or CPU). Faces grouped by person; name, merge, hide,
  per-person filter; names shown in the viewer. Grouping keeps your decisions on re-runs.
- **S2 – Timeline and chapters: built** (v0.6.0). Home-by-month voting (±3 months, trips
  excluded) → chapters at moves; milestones (first photo, moves, biggest trip and busiest day
  per year, first photo of each named person) with keep/dismiss that survives rebuilds.
- **S3 – Replay: built** (v0.7.0). One player for trips, days and months: time-ordered
  photos with Live Photo motion, map route drawn as you go, fly-to on each stop.
- **S4 – AI descriptions: built** (v0.8.0). Claude (Haiku 5.5 by default, model selectable)
  returns caption/keywords/setting/people count/text/quality as JSON per photo; downscaled,
  metadata-stripped copies; Private flag; cost estimate and spend tracking; descriptions feed
  the search box alongside smart search.
- **S5 – Stories and Ask: built** (v0.9.0). Claude Opus 5.5 with server-side fallback;
  stories from a compact text context (days → places, people, sampled captions, milestones),
  editable and persisted; Ask uses three tools (library_summary, search_photos, show_photos)
  in a bounded loop, with per-session conversation history.
- **S6 – Kids' stories: built** (v0.10.0). Birthday per person with age labels; growing-up
  time-lapse (best face per period, in-app player and GIF export); birthday milestones; per-person
  PDF book with reportlab.
- **S7 – Memories: built** (v0.11.0). On this day, shuffle (quality → people → trips), then &
  now by ~55 m GPS cell and ≥ 1 year, similar moments via CLIP image-image similarity.
- **S8 – Year in review and context: built** (v0.12.0). Per-year stats, best-of-year
  selection by slot, 30-second replay; local sunrise/sunset (NOAA approximation); optional
  Open-Meteo weather, cached, off by default.
- **S9 – Films and sharing: built** (v0.13.0). In-browser film rendering (canvas +
  MediaRecorder, optional music via Web Audio, WebM); story read-aloud via speech synthesis;
  share export as a self-contained folder with metadata-stripped copies after an OPSEC check
  on captions/keywords/places. Family contributions (others adding to shared chapters) need
  hosting and are left for a later, server-based version.
- **Restyle** (v0.14.0): sidebar navigation with icons, page title bar, cool-neutral palette
  with a teal accent, consistent panels/controls/focus states, polished dark mode, phone layout.
- Phase 4 complete. Next: real-library feedback, then tuning (trip and chapter heuristics,
  face thresholds on children), and a signed installer.
