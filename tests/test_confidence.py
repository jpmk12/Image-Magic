"""Confidence for automatic decisions, and the review queue."""
from datetime import datetime, timedelta

from image_magic import catalog, confidence, faces
from test_faces import RED, GREEN, FakeFaceModel, make_photo as make_face_photo
from test_places_browse import HOME, PETRA, make_photo


def test_trip_confidence_and_queue(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(30):
        make_photo(orig / f"h{i}.jpg", datetime(2019, 1, 1, 12) + timedelta(days=i * 4), HOME)
    for i in range(6):
        make_photo(orig / f"jo{i}.jpg", datetime(2019, 3, 10, 9) + timedelta(hours=12 * i), PETRA)
    make_photo(orig / "nogps.jpg", datetime(2019, 3, 11, 15))                       # joins the trip by date only
    make_photo(orig / "far.jpg", datetime(2019, 3, 12, 15), (PETRA[0] + 1.5, PETRA[1]))   # ~170 km from the rest
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    confs = confidence.trip_confidences(conn, trip)
    by_path = {r["path"]: r["id"] for r in conn.execute("SELECT id, path FROM assets")}
    assert confs[by_path["nogps.jpg"]] == 0.5 and confs[by_path["jo0.jpg"]] == 0.95 and confs[by_path["far.jpg"]] < 0.9
    assert confidence.trip_confidence(conn, by_path["h0.jpg"]) is None
    q = confidence.queue(conn)
    keys = {i["key"] for i in q["items"]}
    assert f"trip:{by_path['nogps.jpg']}" in keys and f"trip:{by_path['far.jpg']}" in keys
    assert q["items"][0]["confidence"] <= q["items"][-1]["confidence"]
    # confirm one (locks it), dismiss the other (leaves the trip); both leave the queue and survive a rebuild
    confidence.decide(conn, f"trip:{by_path['nogps.jpg']}", "confirmed")
    confidence.decide(conn, f"trip:{by_path['far.jpg']}", "dismissed")
    from image_magic import places
    places.rebuild_trips(conn)
    rows = {r["path"]: r for r in conn.execute("SELECT path, trip_id, trip_manual FROM assets")}
    assert rows["nogps.jpg"]["trip_id"] == trip and rows["nogps.jpg"]["trip_manual"] == 1
    assert rows["far.jpg"]["trip_id"] is None
    q2 = confidence.queue(conn)
    assert not {f"trip:{by_path['nogps.jpg']}", f"trip:{by_path['far.jpg']}"} & {i["key"] for i in q2["items"]}
    assert q2["reviewed"] == 2 and q2["reviewed_week"] == 2
    assert confidence.photo_confidence(conn, by_path["nogps.jpg"])["trip"] == 1.0
    # milestones that are new show up; keeping one removes it
    ms = [i for i in q2["items"] if i["kind"] == "milestone"]
    if ms:
        confidence.decide(conn, ms[0]["key"], "confirmed")
        assert conn.execute("SELECT state FROM milestones WHERE id = ?", (ms[0]["milestone_id"],)).fetchone()[0] == "kept"
    import pytest
    with pytest.raises(ValueError):
        confidence.decide(conn, "trip:1", "maybe")


def test_face_confidence(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(4):
        make_face_photo(orig / f"a{i}.png", [(RED, 100 + i, 100, 90)])
        make_face_photo(orig / f"g{i}.png", [(GREEN, 200 + i, 200, 85)])
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    faces.scan_photos(conn, orig, FakeFaceModel())
    faces.group_faces(conn)
    assert conn.execute("SELECT COUNT(*) FROM face_centres").fetchone()[0] == 2
    confs = confidence.face_confidences(conn)
    assert confs and all(0.9 <= c <= 1.0 for c in confs.values())      # tight fake groups: very sure
    fid = next(iter(confs))
    aid = conn.execute("SELECT asset_id FROM faces WHERE id = ?", (fid,)).fetchone()[0]
    assert confidence.photo_confidence(conn, aid)["faces"][fid] == confs[fid]
    confidence.decide(conn, f"face:{fid}", "confirmed")
    assert conn.execute("SELECT locked FROM faces WHERE id = ?", (fid,)).fetchone()[0] == 1
    assert confidence.face_confidences(conn, aid)[fid] == 1.0
    other = next(f for f in confs if f != fid)
    confidence.decide(conn, f"face:{other}", "dismissed")
    assert conn.execute("SELECT person_id, locked FROM faces WHERE id = ?", (other,)).fetchone()[:] == (None, 1)


def test_chapter_confidence(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(40):
        make_photo(orig / f"h{i}.jpg", datetime(2019, 1, 1, 12) + timedelta(days=i * 7), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    ch = conn.execute("SELECT id FROM chapters").fetchone()[0]
    assert confidence.chapter_confidence(conn, ch) == 1.0
    confidence.decide(conn, f"chapter:{ch}", "confirmed")
    assert conn.execute("SELECT auto FROM chapters WHERE id = ?", (ch,)).fetchone()[0] == 0
    assert confidence.chapter_confidence(conn, ch) == 1.0 and confidence.chapter_confidence(conn, 999) is None
