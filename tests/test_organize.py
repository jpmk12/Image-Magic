"""Manual locations, place search, nearby suggestions, quick-select filters."""
from datetime import datetime, timedelta

import pytest

from image_magic import browse, catalog, locations, places
from test_places_browse import HOME, PETRA, make_photo


def test_place_search_is_offline_and_ranked():
    r = places.search("petr")
    assert r and r[0]["name"].lower().startswith("petr") and all("country" in x for x in r)
    assert any(x["name"] == "Petra" and x["country_code"] == "JO" for x in places.search("Petra"))
    assert places.search("x") == []


def test_manual_location_sticks_and_anchors_neighbours(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2019, 3, 12, 10)
    for i in range(3):
        make_photo(orig / f"n{i}.jpg", t + timedelta(minutes=10 * i))          # no GPS at all
    make_photo(orig / "far.jpg", t + timedelta(hours=5))
    make_photo(orig / "home.jpg", datetime(2019, 3, 20, 10), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    ids = {r["path"]: r["id"] for r in conn.execute("SELECT id, path FROM assets")}
    assert conn.execute("SELECT lat FROM assets WHERE id = ?", (ids["n0.jpg"],)).fetchone()[0] is None
    # nothing nearby within 20 minutes; with a two-hour window the neighbour (n0) would help once n0 is placed
    assert locations.nearby_suggestions(conn, [ids["far.jpg"]], 20)[0]["lat"] is None
    # set n0 by hand to Petra: n1 and n2 pick it up by inference, far.jpg sees it within 5 h
    assert locations.set_manual(conn, [ids["n0.jpg"]], *PETRA) == 1
    locations.infer_nearby(conn); places.update_places(conn)
    rows = {r["path"]: r for r in conn.execute("SELECT path, lat, city, location_source FROM assets")}
    assert rows["n0.jpg"]["location_source"] == "manual" and rows["n0.jpg"]["city"] == "Petra"
    assert rows["n1.jpg"]["location_source"] == "nearby" and rows["n1.jpg"]["city"] == "Petra"
    s = locations.nearby_suggestions(conn, [ids["far.jpg"]], 360)[0]
    assert s["place"].startswith("Petra") and s["minutes"] == 300 and not s["has"]
    # a scan does not undo the manual choice; overwrite is opt-in
    catalog.scan(conn, orig)
    assert conn.execute("SELECT location_source FROM assets WHERE id = ?", (ids["n0.jpg"],)).fetchone()[0] == "manual"
    assert locations.set_manual(conn, [ids["home.jpg"]], *PETRA) == 0                  # has a real location: untouched
    assert locations.set_manual(conn, [ids["home.jpg"]], *PETRA, overwrite=True) == 1
    with pytest.raises(ValueError):
        locations.set_manual(conn, [ids["n0.jpg"]], 95, 0)
    # clearing puts it back to inference
    assert locations.clear_manual(conn, [ids["n0.jpg"]]) == 1
    assert conn.execute("SELECT location_source FROM assets WHERE id = ?", (ids["n0.jpg"],)).fetchone()[0] in ("none", "nearby")
    # quick-select filters
    assert len(browse.ids(conn, {"nolocation": "1"})) >= 1 and len(browse.ids(conn, {"notrip": "1"})) == 5
    assert len(browse.ids(conn, {"noalbum": "1"})) == 5
