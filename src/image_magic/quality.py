"""Local photo quality: which photos are *good*, and which are the best of a burst.

Everything here is computed on the computer from the cached thumbnail, so it is
free and needs no network. Three signals go into a 0-100 score:

* sharpness: variance of the Laplacian (blurry photos score low)
* exposure: not too dark, not blown out
* contrast: a flat grey frame scores low

A burst is a run of photos taken seconds apart that look alike (perceptual
hash); only the best one of each burst is shown in "best of" views. AI
descriptions (when on) add their own 1-5 rating; the two are combined when
ranking.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from . import media

BURST_SECONDS = 8        # photos this close in time...
BURST_BITS = 12          # ...and this alike (dHash hamming distance) are one burst
GOOD = 55                # "best only" shows photos scoring at least this

MIGRATIONS = {
    "quality_score": "REAL",                         # 0-100, NULL = not scored yet
    "burst_id": "INTEGER",                           # id of the first photo in the burst
    "burst_best": "INTEGER NOT NULL DEFAULT 1",      # 1 = the keeper of its burst (or not in one)
}


def score_array(gray: np.ndarray) -> dict:
    """Sharpness, exposure and contrast for a grayscale float array (0-255)."""
    g = gray.astype(np.float32)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return {"sharpness": 0.0, "exposure": 0.0, "contrast": 0.0, "score": 0.0}
    lap = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1]
    var = float(lap.var())
    sharp = min(1.0, max(0.0, (math.log10(var + 1) - 1.0) / 1.8))
    dark = float((g < 16).mean())
    bright = float((g > 240).mean())
    mean = float(g.mean())
    expo = 1.0 - max(0.0, dark - 0.25) * 2.0 - max(0.0, bright - 0.15) * 2.5 - abs(mean - 118) / 300
    expo = min(1.0, max(0.0, expo))
    contrast = min(1.0, float(g.std()) / 60)
    score = 100 * (0.55 * sharp + 0.25 * expo + 0.20 * contrast)
    return {"sharpness": round(sharp, 3), "exposure": round(expo, 3), "contrast": round(contrast, 3),
            "score": round(score, 1), "laplacian_var": round(var, 1)}


def score_image(im: Image.Image) -> dict:
    im = im.convert("L")
    im.thumbnail((400, 400))
    return score_array(np.asarray(im))


def score_file(path: Path) -> dict:
    with Image.open(path) as im:
        return score_image(im)


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, path, sha256 FROM assets WHERE kind = 'photo' AND missing = 0 AND (quality_score IS NULL OR q_sharp IS NULL) ORDER BY id"
    ).fetchall()


def score_pending(conn: sqlite3.Connection, library: Path, originals: Path,
                  progress: Callable[[int, int], None] | None = None, cancelled=lambda: False) -> int:
    """Score every unscored photo (from its thumbnail), then regroup bursts. Returns the number scored."""
    from . import importer
    rows = pending(conn)
    n = 0
    for i, r in enumerate(rows, 1):
        if cancelled():
            break
        try:
            thumb = media.thumbnail(library, originals, r["path"], r["sha256"])
            with Image.open(thumb) as im:
                s = score_image(im)
                ph = importer.dhash(im)
        except Exception:
            s, ph = {"score": 0.0, "sharpness": 0.0, "exposure": 0.0, "contrast": 0.0}, None
        conn.execute("UPDATE assets SET quality_score = ?, q_sharp = ?, q_expo = ?, q_contrast = ?, phash = COALESCE(phash, ?) WHERE id = ?",
                     (s["score"], s["sharpness"], s["exposure"], s["contrast"], ph, r["id"]))
        n += 1
        if n % 200 == 0:
            conn.commit()
        if progress and i % 50 == 0:
            progress(i, len(rows))
    conn.commit()
    if n:
        group_bursts(conn)
    return n


def _ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


def group_bursts(conn: sqlite3.Connection) -> int:
    """Find runs of look-alike photos seconds apart; mark the best of each. Returns the number of bursts."""
    from . import importer
    rows = conn.execute(
        """SELECT id, taken_at, phash, quality_score FROM assets
           WHERE kind = 'photo' AND missing = 0 AND duplicate_of IS NULL AND taken_at IS NOT NULL
           ORDER BY taken_at, id""").fetchall()
    conn.execute("UPDATE assets SET burst_id = NULL, burst_best = 1 WHERE burst_id IS NOT NULL OR burst_best = 0")
    bursts: list[list[sqlite3.Row]] = []
    cur: list[sqlite3.Row] = []
    prev_t = None
    for r in rows:
        t = _ts(r["taken_at"])
        if cur and r["phash"] is not None and cur[-1]["phash"] is not None \
                and prev_t is not None and t - prev_t <= BURST_SECONDS \
                and importer.hamming(r["phash"], cur[-1]["phash"]) <= BURST_BITS:
            cur.append(r)
            prev_t = t
            continue
        prev_t = t
        if len(cur) > 1:
            bursts.append(cur)
        cur = [r]
    if len(cur) > 1:
        bursts.append(cur)
    for b in bursts:
        best = max(b, key=lambda r: (r["quality_score"] or 0, -r["id"]))
        first = b[0]["id"]
        for r in b:
            conn.execute("UPDATE assets SET burst_id = ?, burst_best = ? WHERE id = ?",
                         (first, 1 if r["id"] == best["id"] else 0, r["id"]))
    conn.commit()
    return len(bursts)


def counts(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {"scored": q("SELECT COUNT(*) FROM assets WHERE quality_score IS NOT NULL"),
            "pending": q("SELECT COUNT(*) FROM assets WHERE kind = 'photo' AND missing = 0 AND (quality_score IS NULL OR q_sharp IS NULL)"),
            "bursts": q("SELECT COUNT(DISTINCT burst_id) FROM assets WHERE burst_id IS NOT NULL"),
            "good": q(f"SELECT COUNT(*) FROM assets WHERE missing = 0 AND quality_score >= {GOOD} AND burst_best = 1")}


# A ranking expression usable in any query over `assets a`: local score, the AI rating when
# present, a bonus for named people, and a little for being on a trip.
RANK = """(COALESCE(a.quality_score, 50)
          + COALESCE((SELECT (d.quality - 3) * 10 FROM descriptions d WHERE d.asset_id = a.id AND d.status = 'ok'), 0)
          + COALESCE((SELECT MIN(12, COUNT(*) * 6) FROM faces f WHERE f.asset_id = a.id AND f.person_id IS NOT NULL), 0)
          + CASE WHEN a.trip_id IS NOT NULL THEN 3 ELSE 0 END)"""
KEEPER = "(a.burst_id IS NULL OR a.burst_best = 1)"


def best_of(conn: sqlite3.Connection, where: str, args: list, n: int) -> list[int]:
    """About n of the best photos matching `where` (over alias a), spread evenly through time."""
    rows = conn.execute(
        f"""SELECT a.id, a.taken_at, {RANK} AS rank FROM assets a
            WHERE {where} AND a.kind = 'photo' AND {KEEPER} ORDER BY a.taken_at, a.id""", args).fetchall()
    if not rows:
        return []
    slots = max(1, min(n, len(rows)))
    per = len(rows) / slots
    picked: list[int] = []
    for s in range(slots):
        chunk = rows[int(s * per): int((s + 1) * per)] or rows[-1:]
        best = max(chunk, key=lambda r: (r["rank"], r["taken_at"]))
        if not picked or picked[-1] != best["id"]:
            picked.append(best["id"])
    return picked


def detail(conn: sqlite3.Connection, asset_id: int) -> dict:
    r = conn.execute("SELECT quality_score, burst_id, burst_best FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not r:
        return {}
    out = {"score": r["quality_score"], "burst_best": bool(r["burst_best"]), "burst_size": 0}
    if r["burst_id"] is not None:
        out["burst_size"] = conn.execute("SELECT COUNT(*) FROM assets WHERE burst_id = ?", (r["burst_id"],)).fetchone()[0]
        out["burst_id"] = r["burst_id"]
    return out
