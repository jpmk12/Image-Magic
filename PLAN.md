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
- **Corrections** (v0.15.0): persistent trips with stable ids; manual trip assignment, new
  trips and renames survive rebuilds; "find more photos from this trip" (time window from the
  trip's core, visual similarity to the trip and the picked photo, caption keywords, distance
  penalty); face "not this person" / "this is…"; caption editing.
- **Review and hardening** (v0.16.0): two independent reviews (security/privacy; bugs/
  performance). Fixed: helper-port hijack, launcher spoofing, token-in-URL scope, CSP/nosniff,
  place names no longer sent with photos, CI least-privilege + checksums; WAL + short write
  transactions, cancellable scan/verify, unplugged-library guard, scalable face grouping with
  locked faces carried through merges, stable chapter ids, retry of failed descriptions,
  unreadable files recorded, batched queries, status cache, grid cap.
- Phase 4 complete.
- **Import** (v0.17.0): SHA-256 exact duplicates, dHash look-alikes, copies into
  originals/imported/. Migration race fixed (v0.17.3).
- **Phase 5, quality + events** (v0.18.0): local 0-100 quality score (Laplacian sharpness,
  exposure, contrast) from thumbnails, burst grouping (≤ 8 s apart, dHash ≤ 12 bits) with one
  keeper per burst; a shared ranking (local score + AI rating + named people + trip) feeds
  best-of-year, "best" replays and the shuffle; "Best only" filter. Events: time/distance
  clustering (3 h gap, 2 km, ≥ 4 photos), stable keys, auto titles (weekday, part of day,
  place or "at home", top caption keyword), rename survives rebuilds, event filter, replay
  and share.
- **Home story** (v0.19.0): per chapter, photos within ~5 km of home and off trips; seasons
  per year (December rolls into the next winter) with best-of per season; same-spot series
  (55 m cells seen in ≥ 3 years); people at home; "a year at home" replay.
- **Ask → do** (v0.20.0): action tools with the shared filter schema (incl. events and
  "best"); actions returned to the page (replay, photos, share dialog, person book).
- **Voice notes** (v0.21.0): MediaRecorder in the page, raw-body upload (25 MB cap, audio
  types only) to <library>/voice/, per photo/event/trip/chapter; replay holds a slide while its
  note plays; share export includes audio only when ticked.
- **Compare + this-day map** (v0.22.0): compare.side/compare over a shared WHERE per side
  (person at age from the birthday, year, month, day, trip, event) with best-of pairs; one pin
  per year within ±3 days of today's date, drawn as a dashed path.
- **Flying log** (v0.23.0): flying_photos table rebuilt from descriptions (aircraft term
  list), tails via `\d{2}-\d{4}|\d{5}`; `flying` filter; strict share removes photos with
  readable text or strict-term content (place names excluded from that test) and strips the page.
- **Books** (v0.24.0): books.render (cover, story, sections of ≤ 6 best photos); trip book per
  day, year book per month off-trip plus per trip with year-review lines, event book.
- **Second backup** (v0.25.0): backup.run mirrors originals/ and voice/ (skip by size+mtime,
  copy to .part, SHA-256 against the catalog or source, rename), sqlite backup() snapshot of
  the catalog, marker file, free-space and overlap checks; job + Home panel + setting.
- **Confidence + review queue** (v0.26.0): trip confidence from distance to the trip centre
  vs the trip's median spread (0.5 for date-only joins), face confidence from cosine
  similarity to cached person centres, chapter confidence from the home share of the first
  four months; reviews table; confirm locks (trip_manual / faces.locked / chapters.auto=0 /
  milestone kept), dismiss undoes; shown in the viewer.
- **Film music** (v0.27.0): Web Audio chord progressions (calm/warm/bright) scheduled over the
  film length, low-passed, faded; voice notes decoded up front, slots stretched to fit them,
  music ducked to 18% under speech.
- **Phone access** (v0.28.0): phone.py (lan_ips, ensure_certificate, Pairing, laptop-only
  list, pairing page); make_phone_server wraps the same handler in TLS 1.2+; GET/POST /pair
  without a token; the laptop token is not accepted on the Wi-Fi side and phone tokens are
  not accepted for laptop-only paths.
- **Family edition** (v0.29.0): family.contribute (sha256 exact + dHash near refusal, store
  under contributed/<name>/<Y>/<Y-M>/, catalog.add_file, place lookup); contributor column
  and filter; Pairing persisted (hashed tokens + names); /api/contribute allowed for phones.
- Phase 5 complete.
- **Robust locations** (v0.30.0): location_source per asset (exif/icloud/video/nearby/none);
  .xmp sidecars parsed (iCloud GPS as decimal or Adobe DMS, CreateDate, Rating→favorite/
  rejected, Make=Screenshot, title, keywords); QuickTime/MP4 atom walker (mvhd creation time,
  ©xyz / Apple keyed ISO6709, tkhd size); nearby inference (±20 min, interpolate when
  neighbours agree within 5 km, else nearer in time; recomputed each scan; dropped when the
  neighbour goes); located videos join trips by date; favourite filter; summary on Home.
- **Clean up + auto suggestions** (v0.31.0): cleanup.candidates (blur by Laplacian sharpness,
  dark/blank by exposure/contrast, screenshots from sidecar or captions, exact copies, burst
  extras); trash moves file + live clip + sidecar under <library>/trash keeping the path,
  trashed_at marks rows (missing, not counted as missing), restore and empty; scan re-trashes
  re-downloads; assigning a photo to a trip opens suggestions seeded by it.
- **Phase 6.1–6.3** (v0.32.0): books.render sections with text pages; life_book (chapter
  parts with story/notes/trips/milestones/voice-note counts, trip sections with stories, top
  events); albums module (manual with order, smart from saved filters), browse.expand resolves
  `album` everywhere (photos, ids, replay, map, share, best-of, search), album book; favourites
  were already read from the sidecars.
- **Phase 6.4** (v0.33.0): placestory.world (per-country years/cities/trips/home distance,
  countries by year, farthest from the latest home, most-returned-to) and placestory.place
  (country or city: years, cities, visits, lived-here chapters, people, best, centre); Places
  panel and dialog on the Map tab; map filters.
- **Phase 6.5** (v0.34.0): peopletime.overview (photos per person per year), together pairs
  via a faces self-join with first shared photo, `together:` milestones (≥ 3 shared photos),
  person2 filter.
- **Phase 6.7** (v0.35.0): datewords.parse (people by library names, Nth birthday from the
  birthday, year/season/month/holiday incl. Easter and Thanksgiving, "the week we moved",
  leftover query); from/to/days/birthday_md filters; date-only searches skip the model.
- **Phase 6.8** (v0.36.0): daily.memory_of_day (best on-this-day, else a day-seeded shuffle),
  week_ahead (days, birthdays, trips, milestones), Windows toast via PowerShell WinRT once a
  day from the app loop (meta last_daily_notice), Home card, days_md filter.
- **Phase 6.9 + 6.11** (v0.37.0): TV mode (best-of-300 shuffled, looping, full screen, Ken
  Burns, auto-hidden bar, `&tv` deep link); expression indexes on substr(taken_at) and
  quality/phash; App.cached keyed by WAL mtime/size for facets, timeline, year review, world.
- **Phase 6.10** (v0.38.0): archive.Site writes each photo once (metadata-stripped 1600 px),
  pages for index/chapters/trips/events/albums (+ years with everything), stories and
  milestones inline, optional voice notes; `archive` job with progress; Home panel.
- Phase 6 complete.
- **Second review round** (v0.43.0): 18 security/correctness findings and 23 performance
  findings from two independent reviewers, all fixed except the items judged not worth the
  change (best_of row scans, per-request connection reuse). Phone access is an allowlist
  (phone.phone_allowed); events match rebuilt clusters by member overlap; cleanup.trash is
  move-then-mark; quality components persisted (q_sharp/q_expo/q_contrast); App.cached on
  inbox/review/cleanup/daily/home/flying/albums/events; backup status refreshed in a thread;
  schema verification once per process via PRAGMA user_version; lasso measures tiles once.
- **Inbox review view** (v0.42.1): cards open a full review of exactly the photos concerned
  (face crops, merge groups), per-photo untick with inbox.restrict, done notice with undo.
- **Curation sprint: span + bursts** (v0.42.0): Timeline span mode (first/last month,
  /api/ids over from/to, trip/album/location/Organize actions); cleanup.bursts and keep_frame
  (keeper → burst_best, spare frames trashed, reviewed via the reviews table); burst dialog.
  The curation mockups are all built.
- **Curation sprint: Curator inbox** (v0.41.0): inbox.suggestions (trip additions from
  curate.suggest_for_trip's strong items, face groups near a named centre or large unnamed
  groups, unlocated photos with 2-hour neighbours grouped by place, adjacent same-area trips,
  clutter), stable keys hashed from the photo ids, inbox_decisions with undo payloads
  (trip_restore, face_unmerge, face_unname, location_clear, restore), accept-high, auto-apply
  after each scan for locations ≥ 0.9 and exact copies only; Home panel with chips and cards;
  `ids` filter for "adjust" to land in Organize.
- **Curation sprint (mockups approved): Organize + Set location** (v0.40.0): Organize tab
  (range/lasso/Ctrl+A selection, quick-select filters nolocation/notrip/noalbum, action bar,
  drag-and-drop and number-key targets); locations.set_manual/clear_manual/nearby_suggestions
  (window up to 12 h), places.search over the bundled list, /api/ids, set-location dialog.
- **Trip editing + multi-select** (v0.39.0): curate.delete_trip (members released with
  trip_manual so the automatic pass leaves them), merge_trips, split_trip, set_trip_dates,
  set_trip_cover; Edit menu on trip cards; Select mode on Photos with bulk trip/album/trash.

---

## Phase 6: ideas for the next level (proposed, not yet built)

Ordered by how much they add for the effort. Each one is local and private unless marked.

1. **The life book** (built, v0.32.0). One printable (and one on-screen) book of everything: chapters as
   parts, trips and events as sections, your stories and voice-note transcripts as text, the
   best photos per section. The trip/year/person books become its pages.
2. **Albums and saved searches** (built, v0.32.0). Hand-picked albums ("Emma's best", "The C-17 years") and
   saved filters that update themselves. Any album can be replayed, filmed, booked, shared.
3. **Favourites from iCloud** (built with the locations work, v0.30.0). icloudpd already saves favourites, titles and keywords as
   .xmp sidecars; read them into the catalog so a heart in Photos counts in the ranking and
   "Favourites" is a filter.
4. **Place stories** (built, v0.33.0). A page per country and per city: every visit, years, best photos, the
   map of where you went there, and a "world" summary (countries by year, farthest from home,
   most-returned-to place).
5. **People over time** (built, v0.34.0). Who was in your life when: a strip per named person across the
   years, who appears together, and "first photo together" milestones.
6. **Clutter and duplicates** (built as Clean up, v0.31.0). Screenshots, receipts and documents (from captions) and
   near-duplicate sets grouped into a "clutter" view that hides them from stories without
   deleting anything. Also a "one of each burst" tidy-up for the Photos grid.
7. **Plain-English dates in search** (built, v0.35.0). "last summer", "Christmas 2019", "Emma's 3rd birthday",
   "the week we moved" parsed on the laptop, no AI needed; plus person AND person filters.
8. **Memory of the day** (built, v0.36.0). A Windows notification each morning the app is open ("8 years ago
   today: Petra"), and a weekly digest page.
9. **TV mode** (built, v0.37.0). A full-screen ambient slideshow of best memories, openable from a TV or
   tablet browser through phone access, with the family's voice notes.
10. **Archive export** (built, v0.38.0). The whole story as a self-contained static site (chapters, trips,
    events, stories, metadata-stripped copies) for a USB stick or a family archive.
11. **Scale and speed for 16k+** (built, v0.37.0; thumbnails were already pre-rendered by the
    quality step). Pre-render thumbnails in a background job, virtualise the
    grid, and cache the year/timeline queries.

---

## Phase 5: ideas for richer storytelling

A review of the app as built (v0.16), and what would make the stories better. Ordered by
how much they add for the effort.

### What's strong today
Backup and catalog are solid and verifiable. Places, trips, chapters, people, search and
captions give the app the four coordinates of a memory (when, where, who, what). Replay,
stories, Ask, memories, year review, books and share cover the "relive" side.

### What's thin, and the fixes
1. **Quality.** The app doesn't yet know which photos are *good*. Captions give a 1–5 quality
   score only when AI descriptions are on. Add a local, free quality signal: sharpness
   (Laplacian variance), exposure, face size/eyes open, and "near-duplicate burst" grouping so
   replay, books and year review pick the best frame of each moment. Needs no network.
2. **Events, not just days.** Life happens in events (a birthday party, a hike, a sortie):
   clusters of photos within a few hours and a few hundred metres. Trips are the big version;
   events are the small one. Events become the unit for stories, replay and sharing.
3. **The home story** (built, v0.19.0). Trips get all the attention; most of life is at home. "A year at home"
   views: seasons, the backyard through the years, the kitchen table, first days of school.
4. **Narration and voice** (built, v0.21.0). Stories are read aloud by the browser voice today. A recorded
   voice note per photo or event ("their voices" from the plan) is the most personal layer
   possible and needs only the microphone in the browser.
5. **Music for films** (built, v0.27.0: three generated tracks + own file + voice notes). The film maker takes a file; a small bundled set of royalty-free
   tracks (or a folder of the user's own) would make films one click.

### New features, by value
- **Import from folders**: built (v0.17.0). SHA-256 exact duplicates, dHash look-alikes
  (≤ 6 bits, flat images excluded), copies into originals/imported/<year>/<month>/.
- **Events**: built (v0.18.0). Automatic titles, event filter, replay and share, renames kept.
- **Photo quality + best-of selection**: built (v0.18.0).
- **Ask → do**: built (v0.20.0). Four more tools (replay_photos, open_photos, share_photos,
  make_book) that return actions the page performs; share only opens the dialog.
- **Comparison stories**: built (v0.22.0). Person-at-age, year, month, day, trip, event sides.
- **Where was I on this day each year**: built (v0.22.0) as a map layer.
- **Flying log**: built (v0.23.0). Bases, flying days, tail numbers (regex on readable
  text), strict share (auto-removal + no captions/places/names).
- **Printables**: built (v0.24.0). Trip, year and event books via a shared renderer.
- **Second backup**: built (v0.25.0) to a folder on another drive, verified by checksum. Cloud
  targets (S3/B2) would need credentials and a client; a synced folder (OneDrive, Dropbox,
  Backblaze's folder backup) as the destination covers most of that today.
- **Phone access**: built (v0.28.0). Opt-in HTTPS listener on the LAN address with a locally
  made certificate (cryptography), rotating 6-digit pairing code, per-phone tokens, Host
  allowlist of the LAN addresses, laptop-only paths refused for phones.
- **Family edition**: built local-first (v0.29.0). Paired phones carry a name and add their
  own photos over the home Wi-Fi (deduplicated, catalogued at once, attributed, filterable);
  pairings persist as hashes. A hosted, always-on family library with accounts stays out of
  scope: this app is the laptop's, and the laptop is the host while it is on.

### Accuracy and trust
- Confidence shown for automatic things (trip membership, chapter boundaries, face groups) with
  one-click confirm/dismiss, so the story is reviewed, not assumed. **Built (v0.26.0).**
- A "review queue": the ten least-certain decisions each week, in one screen. **Built (v0.26.0).**


---

## Phase 7: enhancements (approved: items 1–10; 11 deferred)

From a tab-by-tab review of v0.42, in the order presented and agreed. School-year albums were
dropped from the list before approval.

1. **Video posters** (built, v0.44.0). The first frame, decoded in the browser on first sight
   and kept as the video's thumbnail; grids, replays, books and the archive show real frames.
2. **Holidays through the years** (built, v0.45.0). `holidays.py`: every holiday the date parser
   knows, and each person's birthday, as one row per year with a cover; replay of the best
   photos per year; a holiday book; `holiday=` as a gallery filter.
3. **Incremental catalog update** (built, v0.45.0). A `dir_stamps` table keeps each folder's
   mtime and entry count; a quick scan (what runs after every sync, and "Catalog new files")
   skips unchanged folders and marks their files present without touching them. "Catalog
   everything" is the full pass and also catches files replaced in place.
4. **Learning from your decisions** (built, v0.46.0). `learning.py`: a decision log fed by the
   inbox, the review queue, trip merges/dissolves and people merges; `learn()` tunes
   trip.gap_days, trip.min_photos, face.join, per-kind auto-apply thresholds and a per-kind
   calibration of the shown confidence, each within a safe range and only with evidence.
   Settings shows what changed and can forget it.
5. **Stories for events and years** (built, v0.47.0). `ask.story_context` knows event and year;
   the event, year and life books carry the stories.
6. **Tell-the-story mode** (built, v0.47.0). A keyboard-driven pass through the best photos of a
   trip, event or year: one line (Enter) or a voice note (R) each, → skips, Esc stops.
7. **Map time-lapse** (built, v0.45.0). `browse.map_months` aggregates located photos by month
   into ~5 km cells; the Map tab's ▶ Time-lapse animates them, following the current filter.
8. **Share as one file** (built, v0.47.0). The share export zips the folder and the page
   downloads it; the folder remains in the library's exports.
9. **Google Photos / Android import** (built, v0.48.0). `metadata.read_google_sidecar` finds the
   Takeout json by name, by the `(1)` rename, or by its "title"; the import copies sidecars along
   and `locations.read_sources` reads them (source "google"); the import names its contributor.
10. **Two iCloud accounts into one library** (built, v0.48.0). `Settings.apple_id_2` and
    `contributor_2`; the sync job runs icloudpd per account into `originals/<name>/` and the
    scan attributes that folder to the person.
11. **Phone layout polish** (deferred). A bottom tab bar, swipe in the viewer, bigger touch targets.
