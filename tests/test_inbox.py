"""The curator inbox: suggestions, accept/skip, undo, auto-apply."""
from datetime import datetime, timedelta

from image_magic import catalog, inbox, quality
from test_importer import make_photo as textured
from test_places_browse import HOME, PETRA, make_photo


def _lib(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(30):
        textured(orig / f"h{i}.jpg", datetime(2019, 1, 1, 12) + timedelta(days=i * 4), HOME, seed=i)
    for i in range(8):
        textured(orig / f"jo{i}.jpg", datetime(2019, 3, 10, 9) + timedelta(hours=6 * i), PETRA, seed=100 + i)
    (orig / "dup.jpg").write_bytes((orig / "h0.jpg").read_bytes())
    for i in range(4):                                                   # the day after the trip, no GPS, captions say Petra
        textured(orig / f"nogps{i}.jpg", datetime(2019, 3, 13, 9 + i, 30), None, seed=200 + i)
    for i in range(5):                                                   # unlocated, 25 min after located home photos (just past the automatic window)
        textured(orig / f"near{i}.jpg", datetime(2019, 1, 1, 12, 25) + timedelta(days=i * 4), None, seed=300 + i)
    for i in range(6):                                                   # blurry clutter
        from PIL import Image, ImageFilter
        textured(orig / f"blur{i}.jpg", datetime(2019, 6, 1 + i, 12), seed=i)
        with Image.open(orig / f"blur{i}.jpg") as im:
            ex = im.getexif(); im.filter(ImageFilter.GaussianBlur(9)).save(orig / f"blur{i}.jpg", "JPEG", exif=ex)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    quality.score_pending(conn, lib, orig)
    for r in conn.execute("SELECT id FROM assets WHERE path LIKE 'nogps%'").fetchall():
        conn.execute("INSERT INTO descriptions (asset_id, model, caption, keywords, created) VALUES (?, 'm', "
                     "'Red cliffs above Petra, Jordan.', '[\"petra\",\"jordan\",\"cliffs\"]', 'now')", (r[0],))
    conn.commit()
    return conn, lib, orig


def test_suggestions_accept_skip_undo(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    ov = inbox.overview(conn, str(lib / "library.db"), lib, orig)
    kinds = {c["kind"] for c in ov["cards"]}
    assert {"trip", "location", "clutter"} <= kinds, kinds
    assert ov["pending"] >= 3 and ov["high"] >= 1 and all(c["count"] == len(c["ids"]) for c in ov["cards"])
    trip = next(c for c in ov["cards"] if c["kind"] == "trip" and c["action"]["do"] == "trip_add")
    assert trip["count"] == 4 and "part of" in trip["title"]
    # accept the trip card: the photos join; undo puts them back exactly
    inbox.apply(conn, lib, orig, trip)
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE path LIKE 'nogps%' AND trip_id IS NOT NULL").fetchone()[0] == 4
    assert trip["key"] not in {c["key"] for c in inbox.suggestions(conn, str(lib / "library.db"), lib, orig)}
    assert inbox.undo_last(conn, lib, orig) == trip["title"]
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE path LIKE 'nogps%' AND trip_id IS NULL AND trip_manual = 0").fetchone()[0] == 4
    # the card is back after undo; skipping hides it for good
    again = {c["key"]: c for c in inbox.suggestions(conn, str(lib / "library.db"), lib, orig)}
    assert trip["key"] in again
    inbox.skip(conn, again[trip["key"]])
    assert trip["key"] not in {c["key"] for c in inbox.suggestions(conn, str(lib / "library.db"), lib, orig)}
    # location card fills in, undo clears
    loc = next(c for c in inbox.suggestions(conn, str(lib / "library.db"), lib, orig) if c["kind"] == "location")
    assert loc["count"] == 5 and loc["conf"] >= 0.8
    inbox.apply(conn, lib, orig, loc)
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE path LIKE 'near%' AND location_source = 'manual'").fetchone()[0] == 5
    inbox.undo_last(conn, lib, orig)
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE path LIKE 'near%' AND location_source = 'manual'").fetchone()[0] == 0


def test_auto_apply_only_safe_kinds(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    logs = []
    n = inbox.auto_apply(conn, str(lib / "library.db"), lib, orig, log=logs.append)
    assert n >= 1 and all("Inbox" in l for l in logs)
    # only the exact copy went to the trash and locations were filled; blurry frames and the trip card wait for a person
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE trashed_at IS NOT NULL").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE path LIKE 'near%' AND location_source = 'manual'").fetchone()[0] == 5
    left = inbox.suggestions(conn, str(lib / "library.db"), lib, orig)
    assert any(c["kind"] == "trip" for c in left) and any(c["kind"] == "clutter" for c in left)
    assert not any(c["kind"] == "location" for c in left)
    ov = inbox.overview(conn, str(lib / "library.db"), lib, orig)
    assert ov["auto_recent"] and ov["last"]["decision"] == "auto"


def test_partial_acceptance_and_card_detail(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    cards = inbox.suggestions(conn, str(lib / "library.db"), lib, orig)
    trip = next(c for c in cards if c["kind"] == "trip" and c["action"]["do"] == "trip_add")
    keep = trip["ids"][:2]
    r = inbox.apply(conn, lib, orig, inbox.restrict(trip, keep))
    assert r["summary"] == "2 photos added to the trip"
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE path LIKE 'nogps%' AND trip_id IS NOT NULL").fetchone()[0] == 2
    import pytest
    with pytest.raises(ValueError):
        inbox.restrict(trip, [-1])
    clutter = next(c for c in cards if c["kind"] == "clutter")
    assert inbox.summary_of(clutter).endswith("moved to the trash")
