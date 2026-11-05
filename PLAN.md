# Image Magic — Project Plan

Goal: get every photo out of iCloud into a backup you control, make it searchable by
**who** (my kids), **what** (my C-17) and **where** (Jordan), and then build a better way
to relive and share those memories.

## Decisions so far
| Question | Answer | What it means |
|---|---|---|
| Have a Mac? | **No** | Ingest uses **`icloudpd`** (route B). Apple's face names can't be exported, so the app does its own face recognition. |
| Library size | **~16,000 items** | Roughly 50–100 GB including videos. Small enough for one external drive, and for one AI-caption pass over everything. |
| Advanced Data Protection | Unknown | Check under Settings → [your name] → iCloud → Advanced Data Protection. If it's on, also turn on "Access iCloud Data on the Web". |
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
1. What computer will run this: a Windows PC, Linux, a NAS/home server? Does it have an NVIDIA GPU?
2. Where should the backup live: an external drive, a NAS, or a cloud provider?
3. Is Advanced Data Protection turned on? (See "Decisions so far".)
4. Who will you share with: family only, or wider (social media)?
