"""Per-person stories: growing-up time-lapse, birthdays, and a printable book.

Everything is built locally from the catalog. A birthday (optional, set on the
People tab) adds age labels and "turns N" milestones.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import faces, media

MAX_FRAMES = 40
FACE_PX = 320


def ensure_schema(conn: sqlite3.Connection) -> None:
    faces.ensure_schema(conn)


def set_birthday(conn: sqlite3.Connection, pid: int, birthday: str | None) -> None:
    ensure_schema(conn)
    if birthday:
        birthday = date.fromisoformat(birthday[:10]).isoformat()
    conn.execute("UPDATE persons SET birthday = ? WHERE id = ?", (birthday, pid))
    conn.commit()


def age_label(birthday: str | None, when: str | None) -> str:
    if not birthday or not when:
        return ""
    b, d = date.fromisoformat(birthday[:10]), date.fromisoformat(when[:10])
    if d < b:
        return "before birth"
    months = (d.year - b.year) * 12 + d.month - b.month - (1 if d.day < b.day else 0)
    if months < 1:
        return "newborn"
    if months < 24:
        return f"{months} month{'s' if months != 1 else ''}"
    years = months // 12
    return f"{years} year{'s' if years != 1 else ''}"


def _person(conn: sqlite3.Connection, pid: int) -> sqlite3.Row:
    ensure_schema(conn)
    p = conn.execute("SELECT * FROM persons WHERE id = ?", (pid,)).fetchone()
    if not p:
        raise ValueError("Unknown person")
    return p


def growing_up(conn: sqlite3.Connection, pid: int, max_frames: int = MAX_FRAMES) -> dict:
    """The best face of this person per period, oldest first."""
    p = _person(conn, pid)
    rows = conn.execute(
        """SELECT f.id AS face_id, f.asset_id, f.score, f.size_px, a.taken_at
           FROM faces f JOIN assets a ON a.id = f.asset_id
           WHERE f.person_id = ? AND a.missing = 0 AND a.taken_at IS NOT NULL AND a.private = 0
           ORDER BY a.taken_at""", (pid,)).fetchall()
    if not rows:
        return {"person": dict(p), "frames": []}
    first, last = date.fromisoformat(rows[0]["taken_at"][:10]), date.fromisoformat(rows[-1]["taken_at"][:10])
    span_months = max(1, (last.year - first.year) * 12 + last.month - first.month + 1)
    bucket = 1
    while span_months / bucket > max_frames:
        bucket *= 2 if bucket < 12 else 1
        if bucket >= 12 and span_months / bucket > max_frames:
            bucket += 12
    best: dict[int, sqlite3.Row] = {}
    for r in rows:
        d = date.fromisoformat(r["taken_at"][:10])
        key = ((d.year - first.year) * 12 + d.month - first.month) // bucket
        q = r["score"] * min(r["size_px"], 160)
        if key not in best or q > best[key]["score"] * min(best[key]["size_px"], 160):
            best[key] = r
    frames = [{"face_id": r["face_id"], "asset_id": r["asset_id"], "date": r["taken_at"][:10],
               "age": age_label(p["birthday"], r["taken_at"])} for _, r in sorted(best.items())]
    return {"person": dict(p), "frames": frames, "months_per_frame": bucket}


def _font(size: int):
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_gif(conn: sqlite3.Connection, originals: Path, library: Path, pid: int) -> Path:
    """An animated GIF of the growing-up frames with date/age labels."""
    g = growing_up(conn, pid)
    if not g["frames"]:
        raise ValueError("No dated photos of this person yet.")
    name = g["person"]["name"] or f"person-{pid}"
    out = library / "exports" / f"{_safe(name)}-growing-up.gif"
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    font = _font(22)
    for fr in g["frames"]:
        crop_path = faces.face_crop(conn, originals, library, fr["face_id"], size=FACE_PX)
        if not crop_path:
            continue
        im = Image.open(crop_path).convert("RGB")
        canvas = Image.new("RGB", (FACE_PX, FACE_PX + 44), (20, 22, 26))
        canvas.paste(im, (0, 0))
        label = fr["age"] or fr["date"]
        if fr["age"]:
            label = f"{fr['age']} · {fr['date'][:7]}"
        ImageDraw.Draw(canvas).text((12, FACE_PX + 10), label, fill=(240, 240, 240), font=font)
        frames.append(canvas)
    if not frames:
        raise ValueError("Couldn't read any of this person's photos.")
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=450, loop=0, optimize=False)
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "person"


def birthday_milestones(conn: sqlite3.Connection) -> list[dict]:
    """'Emma turns 3' for each birthday with photos (used by the timeline)."""
    ensure_schema(conn)
    out = []
    for p in conn.execute("SELECT id, name, birthday FROM persons WHERE birthday IS NOT NULL AND name IS NOT NULL "
                          "AND hidden = 0").fetchall():
        b = date.fromisoformat(p["birthday"])
        years = conn.execute("SELECT DISTINCT substr(a.taken_at, 1, 4) AS y FROM faces f JOIN assets a ON a.id = f.asset_id "
                             "WHERE f.person_id = ? AND a.missing = 0", (p["id"],)).fetchall()
        for (y,) in years:
            y = int(y)
            age = y - b.year
            if age < 1:
                continue
            day = date(y, b.month, min(b.day, 28)).isoformat()
            r = conn.execute(
                """SELECT a.id FROM assets a WHERE a.missing = 0 AND a.private = 0 AND a.kind = 'photo'
                   AND substr(a.taken_at, 1, 10) BETWEEN date(?, '-1 day') AND date(?, '+1 day')
                   ORDER BY (SELECT COUNT(*) FROM faces f WHERE f.asset_id = a.id AND f.person_id = ?) DESC LIMIT 1""",
                (day, day, p["id"])).fetchone()
            if not r:
                continue
            out.append({"key": f"birthday:{p['id']}:{y}", "date": day, "kind": "birthday",
                        "title": f"{p['name']} turns {age}", "asset_id": r["id"], "person_id": p["id"]})
    return out


# ---- the book ----------------------------------------------------------------

def _best_photos_for_year(conn: sqlite3.Connection, pid: int, year: str, n: int = 6) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT a.id, a.path, a.sha256, a.taken_at, a.city, a.country,
                  COALESCE(d.caption, '') AS caption, COALESCE(d.quality, 3) AS quality,
                  MAX(f.score * MIN(f.size_px, 160)) AS face_q
           FROM faces f JOIN assets a ON a.id = f.asset_id
           LEFT JOIN descriptions d ON d.asset_id = a.id
           WHERE f.person_id = ? AND a.missing = 0 AND a.private = 0 AND a.kind = 'photo'
             AND substr(a.taken_at, 1, 4) = ?
           GROUP BY a.id ORDER BY quality DESC, face_q DESC LIMIT ?""", (pid, year, n)).fetchall()


def make_book(conn: sqlite3.Connection, originals: Path, library: Path, pid: int) -> Path:
    """A printable PDF: cover, growing-up page, then a page per year."""
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as pdfcanvas

    from . import describe
    describe.ensure_schema(conn)
    p = _person(conn, pid)
    name = p["name"] or "Someone special"
    years = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(a.taken_at, 1, 4) FROM faces f JOIN assets a ON a.id = f.asset_id "
        "WHERE f.person_id = ? AND a.missing = 0 AND a.taken_at IS NOT NULL ORDER BY 1", (pid,))]
    if not years:
        raise ValueError("No dated photos of this person yet.")
    out = library / "exports" / f"{_safe(name)}-book.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = landscape(letter)
    c = pdfcanvas.Canvas(str(out), pagesize=(W, H))
    c.setTitle(f"{name}: the book")

    # Cover
    g = growing_up(conn, pid, max_frames=12)
    cover_face = g["frames"][len(g["frames"]) // 2]["face_id"] if g["frames"] else p["cover_face"]
    if cover_face:
        crop = faces.face_crop(conn, originals, library, cover_face, size=FACE_PX)
        if crop:
            c.drawImage(ImageReader(str(crop)), W / 2 - 110, H / 2 - 40, 220, 220)
    c.setFont("Helvetica-Bold", 40)
    c.drawCentredString(W / 2, H / 2 - 110, name)
    c.setFont("Helvetica", 18)
    c.drawCentredString(W / 2, H / 2 - 140, f"{years[0]} – {years[-1]}")
    c.showPage()

    # Growing up
    if g["frames"]:
        c.setFont("Helvetica-Bold", 26)
        c.drawString(40, H - 60, "Growing up")
        cols = min(6, len(g["frames"]))
        size = min(150, (W - 80) / cols - 12)
        for i, fr in enumerate(g["frames"]):
            crop = faces.face_crop(conn, originals, library, fr["face_id"], size=FACE_PX)
            if not crop:
                continue
            x = 40 + (i % cols) * (size + 12)
            y = H - 100 - (i // cols + 1) * (size + 34)
            if y < 20:
                break
            c.drawImage(ImageReader(str(crop)), x, y + 20, size, size)
            c.setFont("Helvetica", 9)
            c.drawCentredString(x + size / 2, y + 8, fr["age"] or fr["date"])
        c.showPage()

    # One page per year
    for y in years:
        photos = _best_photos_for_year(conn, pid, y)
        if not photos:
            continue
        c.setFont("Helvetica-Bold", 26)
        first = photos[0]["taken_at"]
        age = age_label(p["birthday"], f"{y}-07-01") if p["birthday"] else ""
        c.drawString(40, H - 60, y + (f"  ·  {age}" if age else ""))
        n = len(photos)
        cols = 3 if n > 2 else n
        rows = (n + cols - 1) // cols
        cw, ch = (W - 80) / cols, (H - 110) / rows
        for i, ph in enumerate(photos):
            x0 = 40 + (i % cols) * cw
            y0 = H - 80 - (i // cols + 1) * ch
            try:
                f, _ = media.viewable(library, originals, ph["path"], ph["sha256"])
                img = ImageReader(str(f))
                iw, ih = img.getSize()
                scale = min((cw - 16) / iw, (ch - 40) / ih)
                dw, dh = iw * scale, ih * scale
                c.drawImage(img, x0 + (cw - dw) / 2, y0 + 30 + (ch - 40 - dh) / 2, dw, dh)
            except Exception:
                continue
            c.setFont("Helvetica", 8.5)
            when = date.fromisoformat(ph["taken_at"][:10]).strftime("%b %d, %Y").replace(" 0", " ")
            place = ", ".join(x for x in [ph["city"], ph["country"]] if x)
            cap = ph["caption"][:90] + ("…" if len(ph["caption"]) > 90 else "")
            c.drawCentredString(x0 + cw / 2, y0 + 18, " · ".join(x for x in [when, place] if x))
            if cap:
                c.drawCentredString(x0 + cw / 2, y0 + 7, cap)
        c.showPage()
    c.save()
    return out
