import json
import threading
import urllib.request

import pytest

from image_magic import browse, catalog, curate, places, search
from image_magic.server import App, make_server
from image_magic.settings import Settings
from test_places_browse import library  # noqa: F401
from test_search import FakeModel


def _jordan(conn):
    return next(t for t in browse.trips(conn) if t["title"] == "Jordan")


def test_manual_trip_assignment_survives_rebuild(library):  # noqa: F811
    conn, _, lib = library
    jordan = _jordan(conn)
    home = conn.execute("SELECT id FROM assets WHERE path LIKE 'home/%' ORDER BY taken_at LIMIT 1").fetchone()[0]
    curate.set_trip(conn, [home], jordan["id"])
    assert conn.execute("SELECT trip_id, trip_manual FROM assets WHERE id = ?", (home,)).fetchone()[:] == (jordan["id"], 1)
    t = _jordan(conn)
    assert t["photo_count"] == 14 and t["start_at"].startswith("2019-01-05")   # range grew to include it
    places.rebuild_trips(conn)                                                   # the automatic pass
    t2 = _jordan(conn)
    assert t2["id"] == jordan["id"] and t2["photo_count"] == 14                 # id stable, decision kept
    # "not a trip" also sticks
    petra = conn.execute("SELECT id FROM assets WHERE path LIKE 'jo/%' ORDER BY taken_at LIMIT 1").fetchone()[0]
    curate.set_trip(conn, [petra], None)
    places.rebuild_trips(conn)
    assert conn.execute("SELECT trip_id FROM assets WHERE id = ?", (petra,)).fetchone()[0] is None
    assert _jordan(conn)["photo_count"] == 13


def test_new_trip_and_rename_survive_rebuild(library):  # noqa: F811
    conn, _, _ = library
    short = [r[0] for r in conn.execute("SELECT id FROM assets WHERE path LIKE 'short/%'")]   # 3 Petra photos, too few to auto-detect
    tid = curate.set_trip(conn, short, None, new_title="Petra weekend")
    curate.rename_trip(conn, _jordan(conn)["id"], "Jordan with the kids")
    places.rebuild_trips(conn)
    titles = {t["title"]: t for t in browse.trips(conn)}
    assert titles["Petra weekend"]["photo_count"] == 3 and titles["Petra weekend"]["id"] == tid
    assert "Jordan with the kids" in titles
    with pytest.raises(ValueError):
        curate.rename_trip(conn, tid, "  ")


def test_suggest_by_time_and_looks(library):  # noqa: F811
    conn, _, lib = library
    jordan = _jordan(conn)
    s = curate.suggest_for_trip(conn, "k", jordan["id"])
    # home photos within 10 days of the trip are suggested, with the gap explained, but
    # they are 9,000 km away so they are not pre-selected
    assert s["items"] and all("the trip" in i["reasons"][0] for i in s["items"])
    assert all("km away" in i["reasons"][0] and not i["strong"] for i in s["items"])
    # a photo you add by hand from another month must not stretch the window
    far = conn.execute("SELECT id FROM assets WHERE path LIKE 'home/%' ORDER BY taken_at DESC LIMIT 1").fetchone()[0]
    curate.set_trip(conn, [far], jordan["id"])
    s3 = curate.suggest_for_trip(conn, "k", jordan["id"])
    assert len(s3["items"]) <= len(s["items"]) + 2
    assert all(i["id"] not in {r[0] for r in conn.execute("SELECT id FROM assets WHERE trip_id = ?", (jordan["id"],))} for i in s["items"])
    # with embeddings, look-alikes are found too (fake model: colour-based)
    search.index_photos(conn, lib, lib / "originals", FakeModel())
    seed = conn.execute("SELECT id FROM assets WHERE trip_id = ? AND kind = 'photo' LIMIT 1", (jordan["id"],)).fetchone()[0]
    s2 = curate.suggest_for_trip(conn, "k", jordan["id"], seed_id=seed)
    assert any(any("looks like" in r for r in i["reasons"]) for i in s2["items"])
    with pytest.raises(ValueError):
        curate.suggest_for_trip(conn, "k", 999)


def test_caption_edit(library):  # noqa: F811
    conn, _, _ = library
    aid = conn.execute("SELECT id FROM assets WHERE kind = 'photo' LIMIT 1").fetchone()[0]
    d = curate.set_caption(conn, aid, "  Our tent at Wadi Rum  ", ["Wadi Rum", "camping", ""])
    assert d["caption"] == "Our tent at Wadi Rum" and d["keywords"] == ["wadi rum", "camping"] and d["status"] == "ok"
    d = curate.set_caption(conn, aid, "Changed", None)
    assert d["caption"] == "Changed" and d["keywords"] == []


def test_curate_api(library):  # noqa: F811
    conn, _, lib = library
    Settings(apple_id="me@example.com", library_path=str(lib)).save()
    app = App()
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    h = {"X-Token": app.token, "Content-Type": "application/json"}

    def post(path, body):
        return json.loads(urllib.request.urlopen(urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=h)).read())

    def get(path):
        return json.loads(urllib.request.urlopen(urllib.request.Request(base + path, headers=h)).read())
    try:
        jordan = _jordan(conn)
        home = conn.execute("SELECT id FROM assets WHERE path LIKE 'home/%' ORDER BY taken_at LIMIT 1").fetchone()[0]
        assert post("/api/curate/trip", {"ids": [home], "trip": jordan["id"]})["trip"] == jordan["id"]
        assert get(f"/api/photo/{home}")["trip_title"] == "Jordan"
        new = post("/api/curate/trip", {"ids": [home], "trip": {"new": "Road trip"}})["trip"]
        assert get(f"/api/photo/{home}")["trip_title"] == "Road trip"
        post("/api/curate/trip", {"ids": [home], "trip": "none"})
        assert get(f"/api/photo/{home}")["trip_title"] is None
        assert get(f"/api/curate/suggest?trip={jordan['id']}&seed={home}")["items"]
        assert post("/api/curate/caption", {"id": home, "caption": "Snow day", "keywords": ["snow"]})["caption"] == "Snow day"
        post("/api/curate/trip-rename", {"id": jordan["id"], "title": "Jordan 2019"})
        assert get(f"/api/trips")[1]["title"] == "Jordan 2019"
    finally:
        srv.shutdown()
