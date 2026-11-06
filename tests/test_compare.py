"""Comparison pages and the this-day-every-year pins."""
from datetime import date, datetime, timedelta

import pytest

from image_magic import catalog, compare, memories
from test_faces import RED, GREEN, FakeFaceModel, make_photo as make_face_photo
from test_places_browse import HOME, PETRA, TOKYO, make_photo


def test_compare_years_trips_and_people(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(8):
        make_photo(orig / f"h{i}.jpg", datetime(2017, 7, 1 + i, 12), HOME)
        make_photo(orig / f"j{i}.jpg", datetime(2023, 7, 1 + i, 12), HOME)
    for i in range(6):
        make_photo(orig / f"jo{i}.jpg", datetime(2019, 3, 1 + i, 12), PETRA)
        make_photo(orig / f"jp{i}.jpg", datetime(2022, 4, 1 + i, 12), TOKYO)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    r = compare.compare(conn, {"kind": "year", "year": "2017"}, {"kind": "year", "year": "2023"})
    assert r["a"]["count"] == 8 and r["b"]["count"] == 8 and len(r["pairs"]) == 8
    assert r["a"]["first"] == "2017-07-01" and r["a"]["label"] == "2017"
    trips = {t["title"]: t["id"] for t in conn.execute("SELECT id, title FROM trips")}
    r = compare.compare(conn, {"kind": "trip", "id": trips["Jordan"]}, {"kind": "trip", "id": trips["Japan"]})
    assert r["a"]["places"] == ["Petra"] and r["b"]["count"] == 6
    assert compare.side(conn, {"kind": "month", "month": "2019-03"})["count"] == 6
    with pytest.raises(ValueError):
        compare.side(conn, {"kind": "trip", "id": 999})
    with pytest.raises(ValueError):
        compare.side(conn, {"kind": "nope"})
    # this day, every year: the nearest photo within ±3 days per year
    pins = memories.this_day_pins(conn, date(2026, 7, 3))
    assert [p["year"] for p in pins] == ["2017", "2023"] and pins[0]["days_off"] == 0
    assert memories.this_day_pins(conn, date(2026, 12, 25)) == []


def test_compare_person_at_age(tmp_path):
    from image_magic import faces, kids
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(4):
        make_face_photo(orig / f"a{i}.png", [(RED, 100 + i, 100, 90)])
        make_face_photo(orig / f"g{i}.png", [(GREEN, 200 + i, 200, 85)])
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    # give the photos dates: red in 2021, green in 2023
    for r in conn.execute("SELECT id, path FROM assets").fetchall():
        y = 2021 if r["path"].startswith("a") else 2023
        conn.execute("UPDATE assets SET taken_at = ? WHERE id = ?", (f"{y}-06-{10 + int(r['path'][1])}T12:00:00", r["id"]))
    conn.commit()
    faces.scan_photos(conn, orig, FakeFaceModel())
    faces.group_faces(conn)
    red, green = sorted(faces.people(conn), key=lambda p: -p["photo_count"])[:2]
    faces.rename_person(conn, red["id"], "Emma"); faces.rename_person(conn, green["id"], "Jack")
    with pytest.raises(ValueError, match="birthday"):
        compare.side(conn, {"kind": "person", "id": red["id"], "age": 3})
    for p in (red, green):      # birthday three years before that person's photos
        y = conn.execute("SELECT MIN(substr(a.taken_at, 1, 4)) FROM faces f JOIN assets a ON a.id = f.asset_id WHERE f.person_id = ?", (p["id"],)).fetchone()[0]
        kids.set_birthday(conn, p["id"], f"{int(y) - 3}-01-01")
    r = compare.compare(conn, {"kind": "person", "id": red["id"], "age": 3}, {"kind": "person", "id": green["id"], "age": 3})
    assert r["a"]["label"] == "Emma at 3" and r["b"]["label"] == "Jack at 3"
    assert r["a"]["count"] >= 1 and r["b"]["count"] >= 1 and r["pairs"]
    assert compare.side(conn, {"kind": "person", "id": red["id"], "age": 1})["count"] == 0
    assert compare.side(conn, {"kind": "person", "id": red["id"]})["count"] >= 1
