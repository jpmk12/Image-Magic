"""Trip suggestion signals: shot order, the trip's cast, travel days."""
import json
from datetime import datetime, timedelta

from image_magic import catalog, curate, describe
from test_places_browse import HOME, PETRA, make_photo


def _lib(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    # a Petra trip shot as IMG_0500..0507, with IMG_0504 missing its GPS (and its clock a year off)
    n = 500
    for i in range(8):
        when = datetime(2019, 3, 10, 9) + timedelta(hours=6 * i)
        if i == 4:
            make_photo(orig / f"jo/IMG_{n:04d}.JPG", datetime(2018, 3, 10, 0, 0))       # no GPS, clock a year behind
        else:
            make_photo(orig / f"jo/IMG_{n:04d}.JPG", when, PETRA)
        n += 1
    make_photo(orig / "jo/IMG_0509.JPG", datetime(2019, 3, 12, 9))                      # unlocated, right after the run
    make_photo(orig / "jo/IMG_0700.JPG", datetime(2019, 3, 12, 10))                     # unlocated, numbered far away
    make_photo(orig / "jo/DSC_0503.JPG", datetime(2019, 3, 12, 11))                     # other camera: prefix must not mix
    make_photo(orig / "jo/IMG_0480.JPG", datetime(2019, 3, 9, 8))                       # the day before: airport
    make_photo(orig / "jo/IMG_0481.JPG", datetime(2019, 3, 9, 9))                       # the day before: nothing special
    for i in range(20):
        make_photo(orig / f"home/h{i}.jpg", datetime(2019, 1, 1, 12) + timedelta(days=i * 4), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    describe.ensure_schema(conn)
    air = conn.execute("SELECT id FROM assets WHERE path = 'jo/IMG_0480.JPG'").fetchone()[0]
    conn.execute("INSERT INTO descriptions (asset_id, model, caption, keywords, created, status) VALUES (?, 'm', 'Waiting at the airport gate.', '[\"airport\",\"travel\"]', 'now', 'ok')", (air,))
    conn.commit()
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    return conn, lib, trip


def _by_path(conn, s):
    return {conn.execute("SELECT path FROM assets WHERE id = ?", (i["id"],)).fetchone()[0]: i for i in s["items"]}


def test_shot_order_and_travel_days(tmp_path):
    conn, lib, trip = _lib(tmp_path)
    assert curate.shot_number("2019/IMG_0504.HEIC") == ("img_", 504) and curate.shot_number("DSC_0503 (1).jpg") == ("dsc_", 503)
    assert curate.shot_number("photo.jpg") is None
    s = _by_path(conn, curate.suggest_for_trip(conn, str(lib / "library.db"), trip))
    # numbered between two trip photos: strong, whatever the clock said
    assert s["jo/IMG_0504.JPG"]["strong"] and any(r.startswith("shot between two photos of the trip (IMG_503 and IMG_505)") for r in s["jo/IMG_0504.JPG"]["reasons"])
    # numbered just after the run, during the trip's days: strong by time, no "shot between" claim
    assert s["jo/IMG_0509.JPG"]["strong"] and not any("shot between" in r for r in s["jo/IMG_0509.JPG"]["reasons"])
    assert not any("shot between" in r for r in s["jo/IMG_0700.JPG"]["reasons"])
    assert not any("shot between" in r for r in s["jo/DSC_0503.JPG"]["reasons"])
    # the airport photo the day before is a travel day and strong; the plain one is only "1 day from the trip"
    assert s["jo/IMG_0480.JPG"]["strong"] and any("a travel day: airport" in r for r in s["jo/IMG_0480.JPG"]["reasons"])
    assert s["jo/IMG_0480.JPG"]["score"] > s["jo/IMG_0481.JPG"]["score"] and not any("travel day" in r for r in s["jo/IMG_0481.JPG"]["reasons"])


def test_people_signal(tmp_path):
    from image_magic import faces
    conn, lib, trip = _lib(tmp_path)
    faces.ensure_schema(conn)
    conn.execute("INSERT INTO persons (name) VALUES ('Emma')"); emma = conn.execute("SELECT id FROM persons WHERE name = 'Emma'").fetchone()[0]
    conn.execute("INSERT INTO persons (name) VALUES ('Grandma')"); gran = conn.execute("SELECT id FROM persons WHERE name = 'Grandma'").fetchone()[0]
    def face(path, pid):
        aid = conn.execute("SELECT id FROM assets WHERE path = ?", (path,)).fetchone()[0]
        conn.execute("INSERT INTO faces (asset_id, person_id, x1, y1, x2, y2, score, kps, size_px) VALUES (?, ?, 0, 0, .2, .2, .9, '[]', 100)", (aid, pid))
    for p in ("jo/IMG_0500.JPG", "jo/IMG_0501.JPG", "jo/IMG_0502.JPG", "jo/IMG_0505.JPG"):
        face(p, emma)                                   # Emma is the trip's cast
    face("jo/IMG_0509.JPG", emma)                       # unlocated, during the trip, shows Emma
    face("jo/IMG_0700.JPG", gran)                       # unlocated, during the trip, shows only Grandma
    conn.commit()
    s = _by_path(conn, curate.suggest_for_trip(conn, str(lib / "library.db"), trip))
    assert any(r == "shows Emma, who is on this trip" for r in s["jo/IMG_0509.JPG"]["reasons"])
    assert any("Grandma, who is not in any photo of this trip" in r for r in s["jo/IMG_0700.JPG"]["reasons"])
    assert s["jo/IMG_0509.JPG"]["score"] > s["jo/IMG_0700.JPG"]["score"] and not s["jo/IMG_0700.JPG"]["strong"]
