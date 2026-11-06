"""The home story: seasons at home and the same spot through the years."""
from datetime import datetime, timedelta

from image_magic import catalog, home
from test_places_browse import HOME, PETRA, make_photo

YARD = (HOME[0] + 0.0001, HOME[1] + 0.0001)


def test_home_story_has_seasons_and_spots(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    n = 0
    for year in (2018, 2019, 2020):
        for month in (1, 4, 7, 10, 12):
            for d in range(3):
                make_photo(orig / f"h{n}.jpg", datetime(year, month, 5 + d, 12), HOME); n += 1
            make_photo(orig / f"y{n}.jpg", datetime(year, month, 20, 15), YARD); n += 1      # the backyard
    for d in range(6):                                                                     # a trip: not at home
        make_photo(orig / f"t{d}.jpg", datetime(2019, 3, 1 + d, 12), PETRA)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    ch = conn.execute("SELECT id FROM chapters ORDER BY start_at").fetchone()[0]
    h = home.story(conn, ch)
    assert h["photos"] == n
    years = {y["year"]: y for y in h["years"]}
    assert set(years) >= {"2018", "2019", "2020", "2021"}         # December 2020 rolls into winter 2021
    assert [s["name"] for s in years["2019"]["seasons"]] == ["winter", "spring", "summer", "autumn"]
    assert all(1 <= len(s["best"]) <= 6 for s in years["2019"]["seasons"])
    assert h["spots"] and h["spots"][0]["years"] == 3 and len(h["spots"][0]["frames"]) == 3
    ids = home.replay_ids(conn, ch, "2019")
    assert 1 <= len(ids) <= 40
    assert all(conn.execute("SELECT trip_id FROM assets WHERE id = ?", (i,)).fetchone()[0] is None for i in ids)
    assert len(home.replay_ids(conn, ch, None)) > len(ids)
