"""Printable books: one PDF per trip, year or event (the person book lives in kids.py).

Pages are built locally with reportlab: a cover, the story if you wrote one,
then a page per day (trips, events) or per month and trip (years), each with
the best photos of that span by the quality ranking, dated and captioned.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

from . import ask, browse, media, quality

PER_PAGE = 6
VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "book"


def _rows(conn: sqlite3.Connection, ids: list[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    rows = conn.execute(
        f"""SELECT a.id, a.path, a.sha256, a.taken_at, a.city, a.country,
                   COALESCE((SELECT caption FROM descriptions d WHERE d.asset_id = a.id AND d.status = 'ok'), '') AS caption
            FROM assets a WHERE a.id IN ({','.join('?' * len(ids))})""", ids).fetchall()
    by = {r["id"]: r for r in rows}
    return [by[i] for i in ids if i in by]


def _fmt(when: str) -> str:
    return date.fromisoformat(when[:10]).strftime("%b %d, %Y").replace(" 0", " ")


def render(library: Path, originals: Path, out_name: str, title: str, subtitle: str, cover: sqlite3.Row | None,
           story: str, sections: list[tuple[str, list[sqlite3.Row]]]) -> Path:
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as pdfcanvas

    out = library / "exports" / f"{_safe(out_name)}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = landscape(letter)
    c = pdfcanvas.Canvas(str(out), pagesize=(W, H))
    c.setTitle(title)

    def draw(ph, x0, y0, cw, chh) -> bool:
        try:
            if media.media_kind_of(ph["path"]) != "photo":
                f = media.poster_path(library, ph["sha256"])
                if not f.is_file():
                    return False
            else:
                f, _ = media.viewable(library, originals, ph["path"], ph["sha256"])
            img = ImageReader(str(f))
            iw, ih = img.getSize()
            scale = min((cw - 16) / iw, (chh - 40) / ih)
            dw, dh = iw * scale, ih * scale
            c.drawImage(img, x0 + (cw - dw) / 2, y0 + 30 + (chh - 40 - dh) / 2, dw, dh)
            return True
        except Exception:
            return False

    # Cover
    if cover is not None:
        draw(cover, 40, H / 2 - 40, W - 80, H / 2)
    c.setFont("Helvetica-Bold", 40)
    c.drawCentredString(W / 2, H / 2 - 110, title[:60])
    c.setFont("Helvetica", 18)
    c.drawCentredString(W / 2, H / 2 - 140, subtitle[:90])
    c.showPage()
    # Story
    if story.strip():
        c.setFont("Helvetica-Bold", 26)
        c.drawString(40, H - 60, "The story")
        c.setFont("Helvetica", 12.5)
        y = H - 100
        for para in story.split("\n"):
            words, line = para.split(), ""
            for w in words:
                if c.stringWidth(line + " " + w, "Helvetica", 12.5) > W - 100:
                    c.drawString(50, y, line.strip()); y -= 18; line = w
                else:
                    line += " " + w
                if y < 50:
                    c.showPage(); c.setFont("Helvetica", 12.5); y = H - 60
            if line.strip():
                c.drawString(50, y, line.strip()); y -= 18
            y -= 8
        c.showPage()
    # Sections: (heading, photos) or (heading, photos, text). Text gets its own page before the photos.
    for sec in sections:
        heading, photos = sec[0], sec[1]
        text = sec[2] if len(sec) > 2 else ""
        if text and text.strip():
            c.setFont("Helvetica-Bold", 26)
            c.drawString(40, H - 60, heading[:80])
            c.setFont("Helvetica", 12.5)
            y = H - 100
            for para in text.split("\n"):
                words, line = para.split(), ""
                for w in words:
                    if c.stringWidth(line + " " + w, "Helvetica", 12.5) > W - 100:
                        c.drawString(50, y, line.strip()); y -= 18; line = w
                    else:
                        line += " " + w
                    if y < 50:
                        c.showPage(); c.setFont("Helvetica", 12.5); y = H - 60
                if line.strip():
                    c.drawString(50, y, line.strip()); y -= 18
                y -= 8
            c.showPage()
        if not photos:
            continue
        c.setFont("Helvetica-Bold", 26)
        c.drawString(40, H - 60, heading[:80])
        n = len(photos)
        cols = 3 if n > 2 else n
        rows = (n + cols - 1) // cols
        cw, chh = (W - 80) / cols, (H - 110) / rows
        for i, ph in enumerate(photos):
            x0 = 40 + (i % cols) * cw
            y0 = H - 80 - (i // cols + 1) * chh
            if not draw(ph, x0, y0, cw, chh):
                continue
            c.setFont("Helvetica", 8.5)
            place = ", ".join(x for x in [ph["city"], ph["country"]] if x)
            cap = ph["caption"][:90] + ("…" if len(ph["caption"]) > 90 else "")
            c.drawCentredString(x0 + cw / 2, y0 + 18, " · ".join(x for x in [_fmt(ph["taken_at"]) if ph["taken_at"] else "", place] if x))
            if cap:
                c.drawCentredString(x0 + cw / 2, y0 + 7, cap)
        c.showPage()
    c.save()
    return out


def _best(conn: sqlite3.Connection, where: str, args: list, n: int = PER_PAGE) -> list[sqlite3.Row]:
    return _rows(conn, quality.best_of(conn, where, args, n))


def trip_book(conn: sqlite3.Connection, originals: Path, library: Path, trip_id: int) -> Path:
    t = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not t:
        raise ValueError("Unknown trip")
    days = [r[0] for r in conn.execute(
        f"SELECT DISTINCT substr(a.taken_at, 1, 10) FROM assets a WHERE {VISIBLE} AND a.trip_id = ? ORDER BY 1", (trip_id,))]
    if not days:
        raise ValueError("No photos on this trip.")
    sections = []
    for i, d in enumerate(days, 1):
        photos = _best(conn, f"{VISIBLE} AND a.trip_id = ? AND substr(a.taken_at, 1, 10) = ?", [trip_id, d])
        places = sorted({p["city"] for p in photos if p["city"]})
        sections.append((f"Day {i} · {_fmt(d)}" + (f" · {', '.join(places[:2])}" if places else ""), photos))
    story = ask.get_story(conn, f"trip:{trip_id}")
    cover = _rows(conn, [t["cover_id"]])[0] if t["cover_id"] else None
    sub = f"{_fmt(t['start_at'])} – {_fmt(t['end_at'])} · {t['photo_count']} photos" + (f" · {t['subtitle']}" if t["subtitle"] else "")
    return render(library, originals, f"{t['title']}-{t['start_at'][:4]}-trip-book", t["title"], sub, cover,
                  story["text"] if story else "", sections)


def event_book(conn: sqlite3.Connection, originals: Path, library: Path, event_id: int) -> Path:
    e = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if not e:
        raise ValueError("Unknown event")
    photos = _best(conn, f"{VISIBLE} AND a.event_id = ?", [event_id], 18)
    sections = [(e["title"], photos[i:i + PER_PAGE]) for i in range(0, len(photos), PER_PAGE)]
    cover = _rows(conn, [e["cover_id"]])[0] if e["cover_id"] else None
    return render(library, originals, f"{e['title'][:40]}-{e['start_at'][:10]}-event-book", e["title"], e["subtitle"] or "", cover, "", sections)


def year_book(conn: sqlite3.Connection, originals: Path, library: Path, year: str) -> Path:
    from . import review
    y = str(year)[:4]
    stats = review.year_stats(conn, y)
    if not stats["photos"]:
        raise ValueError("No photos in that year.")
    lines = [f"{stats['photos']:,} photos and {stats['videos']:,} videos."]
    if stats["countries"]:
        lines.append("Countries: " + ", ".join(c["country"] for c in stats["countries"]) + ".")
    if stats["new_countries"]:
        lines.append("First time in: " + ", ".join(stats["new_countries"]) + ".")
    if stats["km"]:
        lines.append(f"About {stats['km']:,} km travelled, {len(stats['trips'])} trips.")
    if stats["people"]:
        lines.append("Most photographed: " + ", ".join(p["name"] for p in stats["people"]) + ".")
    if stats["busiest_day"]:
        lines.append(f"Busiest day: {_fmt(stats['busiest_day']['day'])} with {stats['busiest_day']['n']} photos.")
    sections = []
    for m in range(1, 13):
        key = f"{y}-{m:02d}"
        photos = _best(conn, f"{VISIBLE} AND substr(a.taken_at, 1, 7) = ? AND a.trip_id IS NULL", [key])
        if photos:
            sections.append((date(int(y), m, 1).strftime("%B %Y"), photos))
    for t in stats["trips"]:
        photos = _best(conn, f"{VISIBLE} AND a.trip_id = ?", [t["id"]])
        if photos:
            sections.append((f"Trip: {t['title']} · {_fmt(t['start_at'])} – {_fmt(t['end_at'])}", photos))
    cover = _rows(conn, [b["id"] for b in stats["best"][:1]])
    return render(library, originals, f"{y}-year-book", y, "The year in pictures", cover[0] if cover else None,
                  "\n".join(lines), sections)


def life_book(conn: sqlite3.Connection, originals: Path, library: Path) -> Path:
    """The whole story: a part per chapter, with its trips, biggest events and milestones."""
    from . import notes
    chapters = conn.execute("SELECT * FROM chapters ORDER BY start_at").fetchall()
    first = conn.execute(f"SELECT MIN(taken_at), MAX(taken_at), COUNT(*) FROM assets a WHERE {VISIBLE}").fetchone()
    if not first[2]:
        raise ValueError("No photos yet.")
    years = f"{first[0][:4]} – {first[1][:4]}" if first[0][:4] != first[1][:4] else first[0][:4]
    sections: list = []
    if not chapters:
        chapters = [{"id": 0, "title": "The story so far", "start_at": first[0][:10], "end_at": first[1][:10], "note": None, "photo_count": first[2]}]
    for ch in chapters:
        where = f"{VISIBLE} AND a.taken_at >= ? AND a.taken_at < date(?, '+1 day')"
        args = [ch["start_at"], ch["end_at"]]
        story = ask.get_story(conn, f"chapter:{ch['id']}") if ch["id"] else None
        intro = [f"{_fmt(ch['start_at'])} – {_fmt(ch['end_at'])}. {ch['photo_count']:,} photos."]
        if ch["note"]:
            intro.append(ch["note"])
        trips = conn.execute("SELECT * FROM trips WHERE start_at >= ? AND start_at < date(?, '+1 day') ORDER BY start_at", args).fetchall()
        if trips:
            intro.append("Trips: " + ", ".join(f"{t['title']} ({t['start_at'][:7]})" for t in trips) + ".")
        ms = conn.execute("SELECT date, title FROM milestones WHERE state != 'dismissed' AND date >= ? AND date <= ? ORDER BY date", args).fetchall()
        if ms:
            intro.append("Milestones: " + "; ".join(f"{m['title']} ({m['date']})" for m in ms[:12]) + ".")
        vn = conn.execute("SELECT COUNT(*) FROM voice_notes n JOIN assets a ON a.id = n.ref_id WHERE n.kind = 'photo' AND a.taken_at >= ? AND a.taken_at <= ?", args).fetchone()[0]
        if vn:
            intro.append(f"{vn} voice note{'s' if vn != 1 else ''} recorded in this chapter (listen in the app).")
        text = (story["text"] + "\n\n" if story else "") + "\n".join(intro)
        sections.append((f"{ch['title']}", _best(conn, where, args, 6), text))
        for t in trips:
            tstory = ask.get_story(conn, f"trip:{t['id']}")
            sections.append((f"Trip: {t['title']} · {_fmt(t['start_at'])} – {_fmt(t['end_at'])}", _best(conn, f"{VISIBLE} AND a.trip_id = ?", [t["id"]]),
                             tstory["text"] if tstory else ""))
        evs = conn.execute("SELECT id, title, start_at FROM events WHERE start_at >= ? AND start_at < date(?, '+1 day') AND trip_id IS NULL "
                           "ORDER BY photo_count DESC LIMIT 6", args).fetchall()
        for e in sorted(evs, key=lambda e: e["start_at"]):
            sections.append((f"{e['title']} · {_fmt(e['start_at'])}", _best(conn, f"{VISIBLE} AND a.event_id = ?", [e["id"]])))
    cover_ids = quality.best_of(conn, VISIBLE, [], 1)
    cover = _rows(conn, cover_ids)[0] if cover_ids else None
    return render(library, originals, f"life-book-{first[1][:4]}", "The story so far", f"{years} · {first[2]:,} photos", cover, "", sections)


def album_book(conn: sqlite3.Connection, originals: Path, library: Path, album_id: int) -> Path:
    from . import albums, browse
    a = albums.get(conn, album_id)
    if not a:
        raise ValueError("Unknown album")
    params = {k: v for k, v in albums.params_for(conn, album_id).items() if k != "q"}
    where, args = browse._filters(params, alias="a.")
    ids = quality.best_of(conn, f"{where} AND a.private = 0", args, 60)
    photos = _rows(conn, ids)
    if not photos:
        raise ValueError("The album has no photos.")
    sections = [(a["title"], photos[i:i + PER_PAGE]) for i in range(0, len(photos), PER_PAGE)]
    cover = _rows(conn, [a["cover_id"]])[0] if a["cover_id"] else photos[0]
    return render(library, originals, f"{a['title'][:40]}-album-book", a["title"], f"{len(ids)} photos", cover, "", sections)


def make(conn: sqlite3.Connection, originals: Path, library: Path, kind: str, ident: str) -> Path:
    if kind == "album":
        return album_book(conn, originals, library, int(ident))
    if kind == "life":
        return life_book(conn, originals, library)
    if kind == "trip":
        return trip_book(conn, originals, library, int(ident))
    if kind == "event":
        return event_book(conn, originals, library, int(ident))
    if kind == "year":
        return year_book(conn, originals, library, ident)
    raise ValueError("kind must be life, trip, event or year")
