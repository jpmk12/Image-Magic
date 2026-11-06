"""Local quality rating and burst grouping."""
from datetime import datetime, timedelta

import numpy as np
from PIL import Image, ImageFilter

from image_magic import browse, catalog, quality, review
from test_importer import make_photo
from test_places_browse import HOME


def test_sharp_beats_blurry_and_flat_scores_low(tmp_path):
    make_photo(tmp_path / "sharp.jpg", datetime(2020, 1, 1), seed=1)
    with Image.open(tmp_path / "sharp.jpg") as im:
        sharp = quality.score_image(im)
        blurry = quality.score_image(im.filter(ImageFilter.GaussianBlur(6)))
    flat = quality.score_image(Image.new("RGB", (400, 300), (8, 8, 8)))
    assert sharp["score"] > blurry["score"] + 15
    assert flat["score"] < 25 and flat["exposure"] < 0.6
    assert quality.score_array(np.zeros((2, 2)))["score"] == 0


def test_scan_rates_photos_and_groups_bursts(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t0 = datetime(2019, 6, 1, 12, 0, 0)
    # a burst of three near-identical shots, two seconds apart, one of them blurred
    make_photo(orig / "b1.jpg", t0, seed=7)
    make_photo(orig / "b2.jpg", t0 + timedelta(seconds=2), seed=7)
    with Image.open(orig / "b1.jpg") as im:
        im.filter(ImageFilter.GaussianBlur(5)).save(orig / "b3.jpg", "JPEG")
    make_photo(orig / "b3.jpg", t0 + timedelta(seconds=4), seed=7)
    with Image.open(orig / "b3.jpg") as im:
        exif = im.getexif()
        im.filter(ImageFilter.GaussianBlur(5)).save(orig / "b3.jpg", "JPEG", exif=exif)
    # a different photo a minute later and another the next day
    make_photo(orig / "c.jpg", t0 + timedelta(minutes=1), seed=99)
    make_photo(orig / "d.jpg", t0 + timedelta(days=1), seed=3)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    assert quality.counts(conn)["pending"] == 5
    assert quality.score_pending(conn, lib, orig) == 5
    assert quality.score_pending(conn, lib, orig) == 0      # idempotent
    c = quality.counts(conn)
    assert c["scored"] == 5 and c["bursts"] == 1
    rows = {r["path"]: r for r in conn.execute("SELECT path, burst_id, burst_best, quality_score FROM assets")}
    assert rows["b1.jpg"]["burst_id"] == rows["b2.jpg"]["burst_id"] == rows["b3.jpg"]["burst_id"]
    assert rows["c.jpg"]["burst_id"] is None and rows["d.jpg"]["burst_id"] is None
    assert rows["b3.jpg"]["burst_best"] == 0                 # the blurred one is not the keeper
    assert sum(rows[p]["burst_best"] for p in ("b1.jpg", "b2.jpg")) == 1
    # "best only" shows one photo per burst and hides the blurry frame
    best = browse.photos(conn, {"best": "1"})["items"]
    assert 2 <= len(best) <= 3 and rows["b3.jpg"] is not None
    assert len({i["id"] for i in best} & {conn.execute("SELECT id FROM assets WHERE path = 'b3.jpg'").fetchone()[0]}) == 0
    # best-of spreads through time and never picks a non-keeper
    ids = quality.best_of(conn, "a.missing = 0 AND a.private = 0", [], 10)
    assert len(ids) >= 2 and conn.execute("SELECT burst_best FROM assets WHERE id = ?", (ids[0],)).fetchone()[0] == 1
    assert len(review.best_of_year(conn, "2019", 5)) >= 2
    d = quality.detail(conn, rows["b1.jpg"]["burst_id"])
    assert d["burst_size"] == 3 and d["score"] > 0
    assert len(browse.photos(conn, {"burst": rows["b1.jpg"]["burst_id"]})["items"]) == 3
