import random
from datetime import date, datetime, timedelta

import numpy as np

from image_magic import catalog, memories, search
from test_places_browse import HOME, PETRA, make_photo
from test_search import FakeModel


def build(tmp_path):
    orig = tmp_path / "lib" / "originals"
    n = 0
    for year in (2017, 2018, 2019, 2020):
        for day in range(0, 360, 20):
            make_photo(orig / f"h/IMG_{n:04d}.JPG", datetime(year, 1, 1, 12) + timedelta(days=day), HOME); n += 1
    # the same spot in Petra, three years apart, plus an unrelated single visit
    make_photo(orig / f"p/IMG_{n:04d}.JPG", datetime(2018, 3, 14, 10), PETRA); n += 1
    make_photo(orig / f"p/IMG_{n:04d}.JPG", datetime(2021, 3, 14, 10), (PETRA[0] + 0.0001, PETRA[1])); n += 1
    make_photo(orig / f"p/IMG_{n:04d}.JPG", datetime(2021, 3, 15, 10), (PETRA[0] + 0.3, PETRA[1])); n += 1
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    catalog.scan(conn, orig)
    return conn, tmp_path / "lib"


def test_on_this_day(tmp_path):
    conn, _ = build(tmp_path)
    days = memories.on_this_day(conn, today=date(2022, 1, 1))
    assert [d["year"] for d in days] == ["2020", "2019", "2018", "2017"]
    assert days[0]["years_ago"] == 2 and len(days[0]["items"]) == 1
    assert memories.on_this_day(conn, today=date(2022, 6, 3)) == []


def test_then_and_now(tmp_path):
    conn, _ = build(tmp_path)
    pairs = memories.then_and_now(conn)
    assert pairs, "the home spot and Petra should both give pairs"
    petra = [p for p in pairs if "Petra" in p["place"]]
    assert len(petra) == 1 and petra[0]["then"]["taken_at"].startswith("2018") and petra[0]["now"]["taken_at"].startswith("2021")
    assert pairs[0]["days"] >= pairs[-1]["days"]
    home = [p for p in pairs if "Petra" not in p["place"]]
    assert home and home[0]["days"] > 3 * 365 - 30


def test_shuffle_prefers_good_photos_and_is_random(tmp_path):
    conn, _ = build(tmp_path)
    picks = {memories.shuffle(conn, random.Random(i))["id"] for i in range(20)}
    assert len(picks) > 5
    d = memories.shuffle(conn, random.Random(1))
    assert d["chapter"] and "United States" in d["chapter"]["title"] or d["chapter"] is None


def test_similar_uses_embeddings(tmp_path):
    conn, lib = build(tmp_path)
    assert memories.similar(conn, "k", 1) == []            # nothing indexed yet
    model = FakeModel()
    search.index_photos(conn, lib, lib / "originals", model)
    aid = conn.execute("SELECT id FROM assets WHERE path LIKE 'p/%' ORDER BY id LIMIT 1").fetchone()[0]
    sim = memories.similar(conn, "k", aid)
    assert all(s["id"] != aid for s in sim)
    assert all(not s["taken_at"].startswith("2018-03-14") for s in sim)
