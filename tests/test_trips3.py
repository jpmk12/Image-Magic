"""Trips the GPS never saw, and stops inside a trip."""
from datetime import datetime, timedelta

from image_magic import catalog, curate, inbox, locations
from test_places_browse import HOME, PETRA, make_photo

AMMAN = (31.95, 35.93)


def test_unlocated_trip_candidates_and_create(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    # a located life at home, then four days of unlocated photos (an old camera on holiday), then home again
    for i in range(20):
        make_photo(orig / f"home/h{i}.jpg", datetime(2014, 5, 1, 12) + timedelta(days=i * 3), HOME)
    n = 0
    for day in range(4):
        for k in range(5):
            make_photo(orig / f"cam/DSC_{n:04d}.JPG", datetime(2014, 7, 10 + day, 9 + k)); n += 1
    make_photo(orig / "cam/DSC_9000.JPG", datetime(2014, 8, 1, 9))               # one lonely unlocated photo: not a trip
    make_photo(orig / "cam/DSC_9001.JPG", datetime(2014, 5, 4, 15))              # unlocated on a home day: skipped
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    cands = curate.unlocated_trip_candidates(conn)
    assert len(cands) == 1
    c = cands[0]
    assert c["days"] == 4 and len(c["ids"]) == 20 and c["start"] == "2014-07-10" and c["end"] == "2014-07-13"
    assert "Jul 10 – Jul 13, 2014" in c["span"] and 0.6 <= c["conf"] <= 0.9
    # the inbox offers it as a card; accepting with a place names the trip and places its photos
    card = next(k for k in inbox._new_trips(conn) if k["action"]["do"] == "trip_create")
    assert card["kind"] == "trip" and "20 photos look like a trip the GPS never saw" in card["title"]
    r = inbox.apply(conn, lib, orig, inbox.restrict(card, c["ids"][2:]), name="Petra, Jordan")
    assert "New trip made with 18 photos: Petra" == r["summary"]
    t = conn.execute("SELECT * FROM trips").fetchone()
    assert t["title"] == "Petra" and abs(t["lat"] - 30.33) < 0.1 and "Jordan" in t["subtitle"] and t["photo_count"] == 18
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE trip_id = ? AND location_source = 'manual'", (t["id"],)).fetchone()[0] == 18
    # the two unticked photos are never proposed as a new trip again; nothing is left to propose
    assert inbox.rejected_for_trip(conn, 0) == set(c["ids"][:2])
    assert curate.unlocated_trip_candidates(conn) == []
    # undo puts everything back
    assert inbox.undo_last(conn, lib, orig)
    assert conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE lat IS NOT NULL AND path LIKE 'cam/%'").fetchone()[0] == 0
    # skipping the card means "not a trip" for all of them
    cands = curate.unlocated_trip_candidates(conn)
    assert len(cands) == 1 and len(cands[0]["ids"]) == 18
    inbox.skip(conn, next(k for k in inbox._new_trips(conn)))
    assert curate.unlocated_trip_candidates(conn) == []


def test_create_trip_without_a_place(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(3):
        make_photo(orig / f"c{i}.jpg", datetime(2014, 7, 10 + i, 9))
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    ids = [r[0] for r in conn.execute("SELECT id FROM assets")]
    r = curate.create_trip(conn, ids, None, place="")
    assert r["title"].startswith("Trip, 2014-07-10") and r["place"] is None and r["located"] == []
    r2 = curate.create_trip(conn, ids, "Summer", place="nowhere-at-all-xyz")
    assert r2["title"] == "Summer" and r2["place"] is None


def test_stops(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2019, 3, 10, 9)
    for i in range(4):
        make_photo(orig / f"a{i}.jpg", t + timedelta(hours=6 * i), PETRA)
    make_photo(orig / "u1.jpg", t + timedelta(hours=25))                       # unlocated, between Petra and Amman: joins Petra
    for i in range(3):
        make_photo(orig / f"b{i}.jpg", t + timedelta(hours=30 + 6 * i), AMMAN)
    for i in range(2):
        make_photo(orig / f"c{i}.jpg", t + timedelta(hours=50 + 6 * i), PETRA)   # back to Petra: a third stop
    for i in range(20):
        make_photo(orig / f"home/h{i}.jpg", datetime(2019, 1, 1, 12) + timedelta(days=i * 4), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    s = curate.stops(conn, trip)
    assert [x["title"] for x in s] == ["Petra, Jordan", "Amman, Jordan", "Petra, Jordan"]
    assert [x["count"] for x in s] == [5, 3, 2] and [x["index"] for x in s] == [1, 2, 3]
    assert all(x["cover"] in x["ids"] for x in s)
    # a stop moved out becomes its own trip
    curate.set_trip(conn, s[1]["ids"], None, new_title="Amman day")
    left = curate.stops(conn, trip)
    assert [x["title"] for x in left] == ["Petra, Jordan"] and left[0]["count"] == 7      # the two Petra runs are now one stop
    assert conn.execute("SELECT photo_count FROM trips WHERE title = 'Amman day'").fetchone()[0] == 3
