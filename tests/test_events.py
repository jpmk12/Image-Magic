"""Events: clusters of photos a few hours and a couple of km apart."""
from datetime import datetime, timedelta

from image_magic import browse, catalog, events
from test_places_browse import HOME, PETRA, make_photo

BEACH = (38.78, -75.08)   # Rehoboth Beach, ~40 km from HOME


def _lib(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2019, 7, 6, 10, 0)
    for i in range(6):                                   # a Saturday morning at the beach
        make_photo(orig / f"beach{i}.jpg", t + timedelta(minutes=20 * i), BEACH)
    for i in range(5):                                   # same day, evening at home
        make_photo(orig / f"home{i}.jpg", t.replace(hour=19) + timedelta(minutes=5 * i), HOME)
    make_photo(orig / "lone.jpg", t + timedelta(days=3), HOME)   # a single photo: no event
    for i in range(4):                                   # a cluster with no GPS at all
        make_photo(orig / f"nogps{i}.jpg", datetime(2019, 8, 1, 14, 0) + timedelta(minutes=i))
    for d in range(10):                                  # many days at home so home is known
        make_photo(orig / f"h{d}.jpg", datetime(2019, 5, 1) + timedelta(days=d), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    return conn


def test_events_are_found_titled_and_stable(tmp_path):
    conn = _lib(tmp_path)
    evs = events.events(conn)
    titles = {e["title"] for e in evs}
    beach = next(e for e in evs if e["photo_count"] == 6)
    assert beach["title"].startswith("Saturday morning in ")
    home = next(e for e in evs if e["photo_count"] == 5)
    assert home["title"].endswith("at home"), home["title"]
    assert any(e["photo_count"] == 4 for e in evs)       # no-GPS cluster still groups by time
    assert len(evs) == 3, titles
    assert "July 6, 2019" in beach["subtitle"] and "6 photos" in beach["subtitle"]
    # photos are tagged, the single photo is not
    assert len(browse.photos(conn, {"event": beach["id"]})["items"]) == 6
    assert conn.execute("SELECT event_id FROM assets WHERE path = 'lone.jpg'").fetchone()[0] is None
    # renaming survives a rebuild; ids are stable
    events.rename(conn, beach["id"], "Beach day with the kids")
    events.rebuild(conn)
    again = {e["id"]: e for e in events.events(conn)}
    assert again[beach["id"]]["title"] == "Beach day with the kids" and again[home["id"]]["photo_count"] == 5
    assert events.event_for(conn, conn.execute("SELECT id FROM assets WHERE path = 'beach0.jpg'").fetchone()[0])["id"] == beach["id"]
    assert events.events(conn, year="2018") == [] and len(events.events(conn, year="2019")) == 3


def test_far_apart_photos_split_into_separate_events(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2019, 3, 3, 9, 0)
    for i in range(4):
        make_photo(orig / f"a{i}.jpg", t + timedelta(minutes=i), PETRA)
    for i in range(4):
        make_photo(orig / f"b{i}.jpg", t + timedelta(minutes=10 + i), (PETRA[0] + 0.1, PETRA[1]))   # ~11 km away
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    assert len(events.events(conn)) == 2
