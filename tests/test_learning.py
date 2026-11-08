"""Learning from your decisions: the log, the tuned numbers, and the passes that read them."""
from datetime import datetime, timedelta

from image_magic import catalog, confidence, curate, inbox, learning, places
from test_importer import make_photo as textured
from test_places_browse import HOME, PETRA, make_photo


def test_defaults_until_there_is_evidence(tmp_path):
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    assert learning.get(conn, "trip.gap_days") == 3.0 and learning.get(conn, "face.join") == 0.45
    assert learning.get(conn, "auto.location.min") == 0.9
    assert learning.learn(conn) == {}
    for _ in range(2):
        learning.record(conn, "trip_merge", "done", 6.0)
    assert learning.learn(conn) == {}                       # two merges are not a pattern
    assert learning.status(conn)["decisions"] == 2


def test_trip_rules_follow_merges_and_dissolves(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    # two away spells 5 days apart (the built-in 3-day gap makes them two trips), each 6 photos
    for i in range(6):
        make_photo(orig / f"a{i}.jpg", datetime(2019, 3, 10, 9) + timedelta(hours=6 * i), PETRA)
        make_photo(orig / f"b{i}.jpg", datetime(2019, 3, 16, 9) + timedelta(hours=6 * i), PETRA)
    for i in range(20):                                        # home before and after, not in between (coming home ends a trip anyway)
        make_photo(orig / f"h{i}.jpg", datetime(2019, 1, 1 if i < 10 else 4, 1, 12) + timedelta(days=(i % 10) * 5), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    trips = conn.execute("SELECT id, start_at FROM trips ORDER BY start_at").fetchall()
    assert len(trips) == 2
    # you merge them: the gap (about 4.5 days) is remembered
    curate.merge_trips(conn, trips[1]["id"], trips[0]["id"])
    row = conn.execute("SELECT value FROM decision_log WHERE kind = 'trip_merge'").fetchone()
    assert 4 < row[0] < 5.5
    # after three such merges the gap rule allows ~5 days, so a fresh pass keeps them one trip
    for _ in range(2):
        learning.record(conn, "trip_merge", "done", 4.6)
    learned = learning.learn(conn)
    assert 5 <= learned["trip.gap_days"]["value"] <= 5.5 and learned["trip.gap_days"]["evidence"] == 3
    conn.execute("UPDATE assets SET trip_manual = 0"); conn.execute("DELETE FROM trips"); conn.commit()
    places.rebuild_trips(conn)
    assert conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 1
    # dissolving small trips raises the minimum size, but only after five of them (it removes trips)
    for n in (5, 6, 5):
        learning.record(conn, "trip_delete", "done", float(n))
    assert "trip.min_photos" not in learning.learn(conn)
    for n in (6, 5):
        learning.record(conn, "trip_delete", "done", float(n))
    learned = learning.learn(conn)
    assert learned["trip.min_photos"]["value"] == 6
    conn.execute("UPDATE assets SET trip_manual = 0"); conn.execute("DELETE FROM trips"); conn.commit()
    learning.record(conn, "trip_merge", "done", 0.1)         # noise below a day is ignored by the gap rule
    learning.learn(conn)
    places.rebuild_trips(conn)
    assert conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 1   # 12 photos ≥ 6
    for n in (5, 6, 5, 7, 7, 8, 8, 9, 9, 12, 12, 12):
        learning.record(conn, "trip_delete", "done", float(n))
    learned = learning.learn(conn)
    assert learned["trip.min_photos"]["value"] <= 8           # never beyond the safe range
    # a real "not a trip" is logged with the photo count
    tid = conn.execute("SELECT id FROM trips").fetchone()[0]
    curate.delete_trip(conn, tid)
    assert conn.execute("SELECT value FROM decision_log WHERE kind = 'trip_delete' ORDER BY id DESC LIMIT 1").fetchone()[0] == 12
    learning.forget(conn)
    assert learning.status(conn) == {"decisions": 0, "learned": []}
    assert learning.get(conn, "trip.min_photos") == 5.0


def test_face_join_moves_within_bounds(tmp_path):
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    for s in (0.41, 0.40, 0.43):                               # people you merged were this far apart
        learning.record(conn, "people_merge", "done", s)
    assert "face.join" not in learning.learn(conn)             # three is not enough to loosen face grouping
    for s in (0.42, 0.41):
        learning.record(conn, "people_merge", "done", s)
    v = learning.learn(conn)["face.join"]["value"]
    assert 0.40 <= v < 0.45
    for s in (0.47, 0.48, 0.5, 0.46, 0.49):                   # faces you dismissed were this close
        learning.record(conn, "review:face", "dismissed", s)
    v2 = learning.learn(conn)["face.join"]["value"]
    assert v2 > 0.49 and v2 <= 0.55
    for s in (0.2, 0.1, 0.1):
        learning.record(conn, "people_merge", "done", s)       # even wild merges cannot push below the floor
    assert learning.learn(conn)["face.join"]["value"] >= 0.40


def test_inbox_decisions_are_logged_and_calibrate(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(30):
        textured(orig / f"h{i}.jpg", datetime(2019, 1, 1, 12) + timedelta(days=i * 4), HOME, seed=i)
    for i in range(5):
        textured(orig / f"near{i}.jpg", datetime(2019, 1, 1, 12, 25) + timedelta(days=i * 4), None, seed=300 + i)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    cards = inbox.suggestions(conn, str(lib / "library.db"), lib, orig)
    loc = next(c for c in cards if c["kind"] == "location")
    assert loc["conf"] == loc["raw_conf"]                      # nothing learned yet: shown as computed
    inbox.skip(conn, loc)
    row = conn.execute("SELECT kind, outcome, value FROM decision_log").fetchone()
    assert (row["kind"], row["outcome"], row["value"]) == ("inbox:location", "skipped", loc["raw_conf"])
    # a run of skips makes the app cautious about that kind: shown confidence drops, auto threshold rises
    for i in range(6):
        learning.record(conn, "inbox:location", "skipped", 0.9)
    learned = learning.learn(conn)
    assert learned["auto.location.min"]["value"] == 0.97 and learned["calib.location"]["value"] == 0
    assert learning.shown_confidence(conn, "location", 0.93) < 0.93
    assert learning.get(conn, "auto.clutter.min") == 0.9      # other kinds untouched
    # a run of agreement at high confidence lets the app apply that kind from 85%
    learning.forget(conn)
    for i in range(12):
        learning.record(conn, "inbox:location", "accepted", 0.86 + i / 200)
    learned = learning.learn(conn)
    assert learned["auto.location.min"]["value"] == 0.85
    # review-queue decisions are logged too
    photo = conn.execute("SELECT id FROM assets LIMIT 1").fetchone()[0]
    confidence.decide(conn, f"trip:{photo}", "confirmed")
    assert conn.execute("SELECT COUNT(*) FROM decision_log WHERE kind = 'review:trip' AND outcome = 'confirmed'").fetchone()[0] == 1
