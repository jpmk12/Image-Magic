"""The burst picker."""
from datetime import datetime, timedelta

from PIL import Image, ImageFilter

from image_magic import catalog, cleanup, quality
from test_importer import make_photo as textured


def test_bursts_list_keep_and_trash(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t0 = datetime(2019, 6, 1, 12, 0, 0)
    textured(orig / "b1.jpg", t0, seed=7)
    textured(orig / "b2.jpg", t0 + timedelta(seconds=2), seed=7)
    textured(orig / "b3.jpg", t0 + timedelta(seconds=4), seed=7)
    with Image.open(orig / "b3.jpg") as im:
        ex = im.getexif(); im.filter(ImageFilter.GaussianBlur(6)).save(orig / "b3.jpg", "JPEG", exif=ex)
    textured(orig / "c.jpg", t0 + timedelta(days=1), seed=9)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    quality.score_pending(conn, lib, orig)
    bs = cleanup.bursts(conn)
    assert len(bs) == 1 and bs[0]["n"] == 3 and bs[0]["secs"] == 4
    b = bs[0]
    assert sum(f["keeper"] for f in b["frames"]) == 1 and any(f["blur"] for f in b["frames"])
    blurry = next(f for f in b["frames"] if f["blur"])
    # choose a different keeper and trash the rest
    other = next(f for f in b["frames"] if not f["keeper"] and not f["blur"])
    r = cleanup.keep_frame(conn, lib, orig, b["burst_id"], other["id"], trash_rest=True)
    assert r["trashed"] == 2
    assert conn.execute("SELECT burst_best, trashed_at FROM assets WHERE id = ?", (other["id"],)).fetchone()[:] == (1, None)
    assert conn.execute("SELECT trashed_at FROM assets WHERE id = ?", (blurry["id"],)).fetchone()[0] is not None
    assert cleanup.bursts(conn) == []                       # reviewed
    import pytest
    with pytest.raises(ValueError):
        cleanup.keep_frame(conn, lib, orig, 999999, None, False)
