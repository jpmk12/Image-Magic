"""People over time and pairs who appear together."""
from image_magic import browse, catalog, chapters, faces, peopletime
from test_faces import RED, GREEN, FakeFaceModel, make_photo as make_face_photo


def test_overview_pairs_and_milestones(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(3):
        make_face_photo(orig / f"a{i}.png", [(RED, 100 + i, 100, 90)])
        make_face_photo(orig / f"g{i}.png", [(GREEN, 200 + i, 200, 85)])
    for i in range(3):                                                       # both in the same photo
        make_face_photo(orig / f"both{i}.png", [(RED, 100 + i, 100, 90), (GREEN, 400 + i, 300, 85)])
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    for r in conn.execute("SELECT id, path FROM assets").fetchall():
        y = 2020 if r["path"].startswith("both") else 2019
        conn.execute("UPDATE assets SET taken_at = ? WHERE id = ?", (f"{y}-06-{10 + int(r['path'][-5])}T12:00:00", r["id"]))
    conn.commit()
    faces.scan_photos(conn, orig, FakeFaceModel())
    faces.group_faces(conn)
    ps = sorted(faces.people(conn), key=lambda p: -p["photo_count"])[:2]
    faces.rename_person(conn, ps[0]["id"], "Emma"); faces.rename_person(conn, ps[1]["id"], "Jack")
    o = peopletime.overview(conn)
    assert o["years"] == ["2019", "2020"] and {p["name"] for p in o["people"]} == {"Emma", "Jack"}
    emma = next(p for p in o["people"] if p["name"] == "Emma")
    assert emma["years"] == {"2019": 3, "2020": 3} and emma["first"] == "2019" and emma["last"] == "2020"
    assert len(o["pairs"]) == 1 and o["pairs"][0]["photos"] == 3 and o["pairs"][0]["first_at"] == "2020-06-10"
    assert set(o["pairs"][0]["names"]) == {"Emma", "Jack"}
    both = browse.ids(conn, {"person": o["pairs"][0]["a"], "person2": o["pairs"][0]["b"]})
    assert len(both) == 3
    chapters.rebuild_milestones(conn)
    m = conn.execute("SELECT title, date FROM milestones WHERE kind = 'together'").fetchone()
    assert m and m["title"].startswith("First photo of") and m["date"] == "2020-06-10"
