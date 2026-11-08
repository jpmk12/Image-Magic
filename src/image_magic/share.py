"""Sharing: export a self-contained story page with an OPSEC check first.

The export is a folder you can zip and send or host anywhere: index.html plus
downscaled JPEG copies with all metadata removed (no GPS, no camera, no dates
inside the files). Private photos are never included. Before exporting, the
check lists photos whose captions, keywords or places look sensitive so you
can leave them out.
"""

from __future__ import annotations

import html
import io
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

from . import ask, browse, describe, faces, notes

EXPORT_PX = 1600
SENSITIVE_TERMS = [
    "aircraft", "airplane", "jet", "c-17", "c17", "globemaster", "cargo plane", "military", "air force", "army",
    "navy", "marine", "uniform", "camouflage", "flight line", "flightline", "runway", "hangar", "cockpit",
    "tail number", "base housing", "air base", "airbase", "afb", "weapon", "rifle", "gun", "ammunition", "convoy",
    "deployment", "squadron", "tarmac", "helicopter", "drone", "id card", "badge", "passport", "license plate",
    "licence plate", "document", "receipt", "screenshot", "credit card", "address",
]
# With the strict filter (flying log) these are left out automatically, as is any photo with readable text.
STRICT_TERMS = {"tail number", "id card", "badge", "passport", "document", "address", "base housing", "license plate",
                "licence plate", "screenshot", "receipt", "credit card", "weapon", "rifle", "gun", "ammunition", "convoy",
                "deployment", "squadron"}


def _visible_ids(conn: sqlite3.Connection, params: dict) -> list[int]:
    params = {k: v for k, v in params.items() if v not in (None, "", 0)}
    params.pop("kind", None)
    params = browse.expand(conn, params)
    if params.get("q"):
        params.pop("q")                  # a text search can't be re-run here; share the filtered base
    res = browse.photos(conn, {**params, "kind": "photo"}, 0, 5000)
    ids = [i["id"] for i in res["items"]]
    private: set[int] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        private |= {r[0] for r in conn.execute(f"SELECT id FROM assets WHERE private = 1 AND id IN ({','.join('?' * len(chunk))})", chunk)}
    return [i for i in ids if i not in private]


def check(conn: sqlite3.Connection, params: dict, strict: bool = False) -> dict:
    """What would be shared, and which photos look sensitive. Strict: list what is removed automatically."""
    describe.ensure_schema(conn)
    if params.get("flying"):
        from . import flying
        flying.refresh_if_stale(conn)
        strict = True
    ids = _visible_ids(conn, params)
    flagged = []
    removed = []
    descs: dict[int, sqlite3.Row] = {}
    assets: dict[int, sqlite3.Row] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        descs.update({r["asset_id"]: r for r in conn.execute(
            f"SELECT asset_id, caption, keywords, text FROM descriptions WHERE status = 'ok' AND asset_id IN ({ph})", chunk)})
        assets.update({r["id"]: r for r in conn.execute(f"SELECT id, city, taken_at FROM assets WHERE id IN ({ph})", chunk)})
    for aid in ids:
        a = assets[aid]
        dr = descs.get(aid)
        d = {"caption": dr["caption"], "keywords": json.loads(dr["keywords"] or "[]"), "text": dr["text"]} if dr else {}
        text = " ".join([d.get("caption") or "", " ".join(d.get("keywords") or []),
                         d.get("text") or "", a["city"] or ""]).lower()
        hits = sorted({t for t in SENSITIVE_TERMS if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text)})
        # Strict looks at what is in the photo, not at the place name (places are never exported in strict mode).
        content = text[: len(text) - len((a["city"] or "").lower()) - 1] if a["city"] else text
        content_hits = {t for t in STRICT_TERMS if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", content)}
        if strict and ((d.get("text") or "").strip() or content_hits):
            removed.append({"id": aid, "taken_at": a["taken_at"], "reasons": sorted(content_hits) or ["readable text"],
                            "caption": (d or {}).get("caption", "")})
        elif hits:
            flagged.append({"id": aid, "taken_at": a["taken_at"], "reasons": hits,
                            "caption": (d or {}).get("caption", "")})
    return {"total": len(ids) - len(removed), "flagged": flagged, "removed": removed, "strict": strict,
            "no_descriptions": not conn.execute("SELECT 1 FROM descriptions LIMIT 1").fetchone()}


def _export_image(src: Path, dest: Path) -> tuple[int, int]:
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((EXPORT_PX, EXPORT_PX))
        im.save(dest, "JPEG", quality=85, optimize=True)   # no exif: nothing from the original survives
        return im.size


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower() or "story"


def export(conn: sqlite3.Connection, originals: Path, library: Path, params: dict, title: str,
           exclude: list[int] | None = None, story_key: str | None = None, voice: bool = False,
           strict: bool = False) -> Path:
    """Write exports/share-<slug>/ with index.html and images/. Returns the folder.

    Strict (always on for the flying log): photos with readable text or strict-term hits are
    left out, and the page carries no captions, places or people, only the year.
    """
    exclude = set(exclude or [])
    if params.get("flying") or strict:
        strict = True
        exclude |= {r["id"] for r in check(conn, params, strict=True)["removed"]}
    ids = [i for i in _visible_ids(conn, params) if i not in exclude]
    if not ids:
        raise ValueError("Nothing to share after your exclusions.")
    base = library / "exports" / f"share-{_slug(title)}"
    folder = base
    if folder.exists():
        try:
            shutil.rmtree(folder)
        except OSError:   # a file is open elsewhere (Windows): use a fresh folder instead
            n = 2
            while (base.parent / f"{base.name}-{n}").exists():
                n += 1
            folder = base.parent / f"{base.name}-{n}"
    (folder / "images").mkdir(parents=True)
    items = []
    people_names = {}
    order: dict[int, str] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        order.update({r[0]: r[1] or "" for r in conn.execute(f"SELECT id, taken_at FROM assets WHERE id IN ({','.join('?' * len(chunk))})", chunk)})
    for n, aid in enumerate(sorted(ids, key=lambda i: order.get(i, "")), 1):
        a = conn.execute("SELECT path, taken_at, city, country FROM assets WHERE id = ?", (aid,)).fetchone()
        name = f"images/{n:04d}.jpg"
        try:
            w, h = _export_image(originals / a["path"], folder / name)
        except Exception:
            continue
        d = describe.for_photo(conn, aid)
        names = [f["name"] for f in faces.faces_in_photo(conn, aid) if f.get("name") and not f.get("hidden")]
        audio = ""
        if voice:
            vn = notes.list_for(conn, "photo", aid)
            found = notes.path_of(conn, library, vn[-1]["id"]) if vn else None
            if found:
                audio = f"images/{n:04d}{found[0].suffix}"
                shutil.copyfile(found[0], folder / audio)
        if strict:
            items.append({"src": name, "w": w, "h": h, "date": (a["taken_at"] or "")[:4], "place": "", "caption": "", "people": [], "audio": ""})
            continue
        items.append({"src": name, "w": w, "h": h, "date": (a["taken_at"] or "")[:10],
                      "place": ", ".join(x for x in [a["city"], a["country"]] if x),
                      "caption": (d or {}).get("caption", "") if d and d.get("status") == "ok" else "",
                      "people": names, "audio": audio})
    story = ask.get_story(conn, story_key) if story_key else None
    (folder / "index.html").write_text(_page(title, story["text"] if story else "", items), encoding="utf-8")
    (folder / "README.txt").write_text(
        f"{title}\n\nMade with Image Magic on {datetime.now(timezone.utc).date()}.\n"
        "These images are downscaled copies with all metadata (including location) removed.\n"
        "Zip this folder to send it, or copy it to any web host.\n", encoding="utf-8")
    return folder


def _page(title: str, story: str, items: list[dict]) -> str:
    esc = html.escape
    tiles = "\n".join(
        f'<figure><img src="{it["src"]}" loading="lazy" alt="" width="{it["w"]}" height="{it["h"]}">'
        f'<figcaption>{esc(" · ".join(x for x in [it["date"], it["place"]] if x))}'
        f'{("<br>" + esc(it["caption"])) if it["caption"] else ""}'
        f'{("<br><small>" + esc(", ".join(it["people"])) + "</small>") if it["people"] else ""}'
        f'{("<br><audio controls preload=\"none\" src=\"" + it["audio"] + "\"></audio>") if it.get("audio") else ""}</figcaption></figure>'
        for it in items)
    paras = "".join(f"<p>{esc(p.strip())}</p>" for p in story.split("\n") if p.strip())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
  body {{ margin: 0; background: #111; color: #eee; font: 16px/1.6 system-ui, sans-serif; }}
  header {{ padding: 48px 24px 24px; max-width: 760px; margin: 0 auto; }}
  h1 {{ font-size: 34px; margin: 0 0 8px; }}
  .story p {{ font-size: 18px; }}
  main {{ columns: 3 320px; column-gap: 10px; padding: 0 10px 40px; }}
  figure {{ margin: 0 0 10px; break-inside: avoid; }}
  img {{ width: 100%; height: auto; display: block; border-radius: 8px; cursor: zoom-in; }}
  figcaption {{ font-size: 13px; color: #aaa; padding: 6px 2px; }}
  .lb {{ position: fixed; inset: 0; background: rgba(0,0,0,.95); display: none; place-items: center; }}
  .lb img {{ max-width: 100vw; max-height: 100vh; cursor: zoom-out; }}
  footer {{ color: #777; font-size: 12px; text-align: center; padding: 20px; }}
</style></head>
<body>
<header><h1>{esc(title)}</h1><div class="story">{paras}</div><p style="color:#aaa">{len(items)} photos</p></header>
<main>
{tiles}
</main>
<div class="lb" id="lb" onclick="this.style.display='none'"><img id="lbi" alt=""></div>
<footer>Shared from a personal photo library. Please don't repost.</footer>
<script>
document.querySelectorAll('main img').forEach(i => i.onclick = () => {{ document.getElementById('lbi').src = i.src; document.getElementById('lb').style.display = 'grid'; }});
</script>
</body></html>
"""
