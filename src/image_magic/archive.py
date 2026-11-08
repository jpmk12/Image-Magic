"""Archive export: the whole story as a self-contained static site.

For a USB stick, a family archive or a web host of your own: index, a page per
chapter (story, milestones, trips, events, best photos), per trip, per event and
per album, plus a page per year with every photo when you ask for everything.
Images are downscaled copies with all metadata removed; private photos are never
included; voice notes only when ticked. Each photo is written once and shared by
every page that shows it.
"""

from __future__ import annotations

import html
import shutil
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from . import ask, browse, describe, faces, notes, quality, share

VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0"
BEST = 24
CSS = """
  body { margin: 0; background: #111; color: #eee; font: 16px/1.6 system-ui, sans-serif; }
  header, section { padding: 24px; max-width: 980px; margin: 0 auto; }
  h1 { font-size: 32px; margin: 0 0 6px; } h2 { font-size: 22px; margin: 28px 0 8px; }
  a { color: #5eead4; text-decoration: none; } nav a { margin-right: 14px; }
  .story p { font-size: 17px; } .muted { color: #9aa; font-size: 14px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
  .card { background: #1a1d22; border-radius: 12px; overflow: hidden; } .card img { width: 100%; aspect-ratio: 4/3; object-fit: cover; display: block; }
  .card div { padding: 8px 10px; font-size: 14px; }
  main { columns: 3 300px; column-gap: 10px; padding: 0 10px 40px; } figure { margin: 0 0 10px; break-inside: avoid; }
  main img { width: 100%; height: auto; display: block; border-radius: 8px; cursor: zoom-in; } figcaption { font-size: 13px; color: #aaa; padding: 6px 2px; }
  .lb { position: fixed; inset: 0; background: rgba(0,0,0,.95); display: none; place-items: center; } .lb img { max-width: 100vw; max-height: 100vh; }
  ul { padding-left: 18px; } footer { color: #777; font-size: 12px; text-align: center; padding: 20px; }
"""
JS = "document.querySelectorAll('main img').forEach(i => i.onclick = () => { document.getElementById('lbi').src = i.src; document.getElementById('lb').style.display = 'grid'; });"


def _fmt(d: str | None) -> str:
    try:
        return date.fromisoformat((d or "")[:10]).strftime("%b %d, %Y").replace(" 0", " ")
    except ValueError:
        return d or ""


class Site:
    def __init__(self, conn: sqlite3.Connection, originals: Path, folder: Path, voice: bool, progress, cancelled):
        self.conn, self.originals, self.folder, self.voice = conn, originals, folder, voice
        self.progress, self.cancelled = progress, cancelled
        self.written: dict[int, dict] = {}        # asset id -> item
        self.photos_done = 0
        self.total_photos = 0
        (folder / "images").mkdir(parents=True, exist_ok=True)

    def item(self, aid: int) -> dict | None:
        if aid in self.written:
            return self.written[aid]
        a = self.conn.execute("SELECT path, taken_at, city, country, private, sha256 FROM assets WHERE id = ? AND missing = 0", (aid,)).fetchone()
        if not a or a["private"]:
            return None
        name = f"images/{aid}.jpg"
        try:
            src = self.originals / a["path"]
            cached = self.originals.parent / "derived" / "view" / a["sha256"][:2] / f"{a['sha256']}.jpg" if a["sha256"] else None
            if cached is not None and cached.is_file():
                src = cached                 # already decoded and downscaled; the export strips its metadata again
            from . import media
            if media.media_kind_of(a["path"]) != "photo":
                poster = media.poster_path(self.originals.parent, a["sha256"])
                if not poster.is_file():
                    return None
                src = poster
            w, h = share._export_image(src, self.folder / name)
        except Exception:
            return None
        d = describe.for_photo(self.conn, aid)
        names = [f["name"] for f in faces.faces_in_photo(self.conn, aid) if f.get("name") and not f.get("hidden")]
        audio = ""
        if self.voice:
            vn = notes.list_for(self.conn, "photo", aid)
            found = notes.path_of(self.conn, self.originals.parent, vn[-1]["id"]) if vn else None
            if found:
                audio = f"images/{aid}{found[0].suffix}"
                shutil.copyfile(found[0], self.folder / audio)
        it = {"src": name, "w": w, "h": h, "date": (a["taken_at"] or "")[:10], "place": ", ".join(x for x in [a["city"], a["country"]] if x),
              "caption": (d or {}).get("caption", "") if d and d.get("status") == "ok" else "", "people": names, "audio": audio}
        self.written[aid] = it
        self.photos_done += 1
        if self.progress and self.photos_done % 25 == 0:
            self.progress(self.photos_done, max(self.total_photos, self.photos_done))
        return it

    def items(self, ids: list[int]) -> list[dict]:
        out = []
        for i in ids:
            if self.cancelled():
                break
            it = self.item(i)
            if it:
                out.append(it)
        return out

    def page(self, name: str, title: str, subtitle: str, story: str = "", sections: list[tuple[str, str]] = (),
             photos: list[dict] = (), crumbs: list[tuple[str, str]] = ()) -> str:
        esc = html.escape
        nav = "".join(f'<a href="{esc(h)}">{esc(t)}</a>' for t, h in [("Home", "index.html"), *crumbs])
        body = [f"<header><nav>{nav}</nav><h1>{esc(title)}</h1><p class='muted'>{esc(subtitle)}</p>"]
        if story.strip():
            body.append("<div class='story'>" + "".join(f"<p>{esc(p.strip())}</p>" for p in story.split("\n") if p.strip()) + "</div>")
        body.append("</header>")
        for heading, inner in sections:
            body.append(f"<section><h2>{esc(heading)}</h2>{inner}</section>")
        if photos:
            tiles = "\n".join(
                f'<figure><img src="{it["src"]}" loading="lazy" alt="" width="{it["w"]}" height="{it["h"]}">'
                f'<figcaption>{esc(" · ".join(x for x in [it["date"], it["place"]] if x))}'
                f'{("<br>" + esc(it["caption"])) if it["caption"] else ""}'
                f'{("<br><small>" + esc(", ".join(it["people"])) + "</small>") if it["people"] else ""}'
                f'{("<br><audio controls preload=none src=" + chr(34) + it["audio"] + chr(34) + "></audio>") if it.get("audio") else ""}</figcaption></figure>'
                for it in photos)
            body.append(f"<main>{tiles}</main><div class='lb' id='lb' onclick=\"this.style.display='none'\"><img id='lbi' alt=''></div>")
        body.append("<footer>Made with Image Magic. Copies with all metadata removed. Please don't repost.</footer>")
        doc = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
               f"<title>{esc(title)}</title><style>{CSS}</style></head><body>{''.join(body)}<script>{JS}</script></body></html>")
        (self.folder / name).write_text(doc, encoding="utf-8")
        return name

    def cards(self, rows: list[tuple[str, str, str, int | None]]) -> str:
        """(title, subtitle, href, cover asset id) -> card grid HTML."""
        esc = html.escape
        out = []
        for title, sub, href, cover in rows:
            it = self.item(cover) if cover else None
            img = f'<img src="{it["src"]}" loading="lazy" alt="">' if it else ""
            out.append(f'<a class="card" href="{esc(href)}">{img}<div><b>{esc(title)}</b><br><span class="muted">{esc(sub)}</span></div></a>')
        return f'<div class="cards">{"".join(out)}</div>'


def export(conn: sqlite3.Connection, originals: Path, library: Path, everything: bool = False, voice: bool = False,
           progress: Callable[[int, int], None] | None = None, cancelled=lambda: False) -> Path:
    stamp = datetime.now(timezone.utc).date().isoformat()
    folder = library / "exports" / f"archive-{stamp}"
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    site = Site(conn, originals, folder, voice, progress, cancelled)
    total = conn.execute(f"SELECT COUNT(*) FROM assets a WHERE {VISIBLE}").fetchone()[0]
    site.total_photos = total if everything else 0
    first = conn.execute(f"SELECT MIN(a.taken_at), MAX(a.taken_at) FROM assets a WHERE {VISIBLE}").fetchone()
    years_label = f"{(first[0] or '')[:4]} – {(first[1] or '')[:4]}"
    n_best = 400 if everything else BEST

    def best(where: str, args: list, n: int = n_best) -> list[dict]:
        return site.items(quality.best_of(conn, where, args, n))

    chapters = conn.execute("SELECT * FROM chapters ORDER BY start_at").fetchall()
    trips = conn.execute("SELECT * FROM trips ORDER BY start_at").fetchall()
    albums = conn.execute("SELECT * FROM albums ORDER BY created").fetchall() if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'albums'").fetchone() else []
    # trips, events, albums
    for t in trips:
        if cancelled():
            break
        story = ask.get_story(conn, f"trip:{t['id']}")
        photos = best(f"{VISIBLE} AND a.trip_id = ?", [t["id"]])
        site.page(f"trip-{t['id']}.html", t["title"], f"{_fmt(t['start_at'])} – {_fmt(t['end_at'])} · {t['photo_count']} photos" + (f" · {t['subtitle']}" if t["subtitle"] else ""),
                  story["text"] if story else "", photos=photos, crumbs=[("Trips", "index.html#trips")])
    events = conn.execute("SELECT * FROM events ORDER BY start_at").fetchall()
    for e in events:
        if cancelled():
            break
        site.page(f"event-{e['id']}.html", e["title"], e["subtitle"] or "", photos=best(f"{VISIBLE} AND a.event_id = ?", [e["id"]]), crumbs=[("Events", "index.html#events")])
    for al in albums:
        if cancelled():
            break
        from . import albums as albums_mod
        params = {k: v for k, v in albums_mod.params_for(conn, al["id"]).items() if k != "q"}
        where, args = browse._filters(params, alias="a.")
        site.page(f"album-{al['id']}.html", al["title"], "Album", photos=best(f"{where} AND a.private = 0", args), crumbs=[("Albums", "index.html#albums")])
    # chapters
    for ch in chapters:
        if cancelled():
            break
        args = [ch["start_at"], ch["end_at"]]
        story = ask.get_story(conn, f"chapter:{ch['id']}")
        ms = conn.execute("SELECT date, title FROM milestones WHERE state != 'dismissed' AND date >= ? AND date <= ? ORDER BY date", args).fetchall()
        ch_trips = [t for t in trips if ch["start_at"] <= t["start_at"][:10] <= ch["end_at"]]
        ch_events = [e for e in events if ch["start_at"] <= e["start_at"][:10] <= ch["end_at"] and not e["trip_id"]]
        sections = []
        if ms:
            sections.append(("Milestones", "<ul>" + "".join(f"<li>{html.escape(m['date'])}: {html.escape(m['title'])}</li>" for m in ms) + "</ul>"))
        if ch_trips:
            sections.append(("Trips", site.cards([(t["title"], f"{_fmt(t['start_at'])} · {t['photo_count']} photos", f"trip-{t['id']}.html", t["cover_id"]) for t in ch_trips])))
        if ch_events:
            sections.append(("Events", site.cards([(e["title"], e["subtitle"] or "", f"event-{e['id']}.html", e["cover_id"]) for e in ch_events[:60]])))
        photos = best(f"{VISIBLE} AND a.taken_at >= ? AND a.taken_at < date(?, '+1 day') AND a.trip_id IS NULL", args)
        site.page(f"chapter-{ch['id']}.html", ch["title"], f"{_fmt(ch['start_at'])} – {_fmt(ch['end_at'])} · {ch['photo_count']} photos" + (f" · {ch['note']}" if ch["note"] else ""),
                  story["text"] if story else "", sections, photos, crumbs=[("Chapters", "index.html#chapters")])
    # every photo by year
    year_pages = []
    if everything:
        for (y,) in conn.execute(f"SELECT DISTINCT substr(a.taken_at, 1, 4) FROM assets a WHERE {VISIBLE} AND a.taken_at IS NOT NULL ORDER BY 1"):
            if cancelled():
                break
            ids = [r[0] for r in conn.execute(f"SELECT a.id FROM assets a WHERE {VISIBLE} AND substr(a.taken_at, 1, 4) = ? ORDER BY a.taken_at", (y,))]
            site.page(f"year-{y}.html", y, f"{len(ids)} photos", photos=site.items(ids), crumbs=[("Years", "index.html#years")])
            year_pages.append((y, f"{len(ids)} photos", f"year-{y}.html", ids[len(ids) // 2] if ids else None))
    # index
    sections = []
    if chapters:
        sections.append(("Chapters", site.cards([(c["title"], f"{_fmt(c['start_at'])} – {_fmt(c['end_at'])}", f"chapter-{c['id']}.html", c["cover_id"]) for c in chapters])))
    if trips:
        sections.append(("Trips", site.cards([(t["title"], f"{_fmt(t['start_at'])} · {t['photo_count']} photos", f"trip-{t['id']}.html", t["cover_id"]) for t in reversed(trips)])))
    if events:
        sections.append(("Events", site.cards([(e["title"], e["subtitle"] or "", f"event-{e['id']}.html", e["cover_id"]) for e in reversed(events[-120:])])))
    if albums:
        sections.append(("Albums", site.cards([(a["title"], "Album", f"album-{a['id']}.html", a["cover_id"]) for a in albums])))
    if year_pages:
        sections.append(("Years", site.cards(year_pages)))
    sections = [(h, f'<a id="{h.lower()}"></a>' + inner) for h, inner in sections]
    site.page("index.html", "The story so far", f"{years_label} · {total:,} photos · {len(site.written):,} in this archive", sections=sections)
    (folder / "README.txt").write_text(
        f"Image Magic archive, {stamp}.\nOpen index.html in any browser. Images are downscaled copies with all metadata removed; "
        f"private photos are not included.\n{len(site.written):,} photos" + (" (everything)" if everything else " (the best of each chapter, trip, event and album)") + ".\n",
        encoding="utf-8")
    return folder
