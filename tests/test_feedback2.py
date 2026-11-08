"""User feedback round: address-tolerant place search, remembered trip rejections, tickable face cards, clutter categories."""
from datetime import datetime, timedelta

import pytest
from PIL import Image

from image_magic import catalog, cleanup, curate, inbox, places, quality
from test_importer import make_photo as textured
from test_places_browse import HOME, PETRA, make_photo


def test_place_search_understands_addresses_states_and_coordinates():
    top = lambda q: [(r["name"], r["region"]) for r in places.search(q)[:1]]  # noqa: E731
    assert top("14400 State Hwy Z, St Robert, MO 65584, United States") == [("Saint Robert", "Missouri")]
    assert top("St Robert, MO") == [("Saint Robert", "Missouri")]
    assert top("st robert") == [("Saint Robert", "Missouri")]
    assert top("Dover, DE") == [("Dover", "Delaware")]                 # DE is Delaware in an address, not Germany
    assert top("Paris, TX") == [("Paris", "Texas")]
    assert top("Ft Walton Beach") == [("Fort Walton Beach", "Florida")]
    assert top("Petra, Jordan") == [("Petra", "Ma'an")]
    r = places.search("37.83, -92.18")
    assert len(r) == 1 and abs(r[0]["lat"] - 37.83) < 1e-6 and "Saint Robert" in r[0]["region"]
    assert places.search("x") == [] and places.search("") == []
    p = places.parse_query("14400 State Hwy Z, St Robert, MO 65584, United States")
    assert p["names"] == ["saint robert"] and p["region"] == "missouri" and p["country"] == "US"


def _trip_lib(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(8):
        make_photo(orig / f"jo{i}.jpg", datetime(2019, 3, 10, 9) + timedelta(hours=6 * i), PETRA)
    for i in range(6):                                                  # just after the trip, unlocated: candidates
        make_photo(orig / f"after{i}.jpg", datetime(2019, 3, 13, 9 + i))
    for i in range(20):
        make_photo(orig / f"h{i}.jpg", datetime(2019, 1, 1, 12) + timedelta(days=i * 4), HOME)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    return conn, lib, orig, trip


def test_unticked_trip_photos_are_not_suggested_again(tmp_path):
    conn, lib, orig, trip = _trip_lib(tmp_path)
    cands = {i["id"] for i in curate.suggest_for_trip(conn, str(lib / "library.db"), trip)["items"]}
    after = {r[0] for r in conn.execute("SELECT id FROM assets WHERE path LIKE 'after%'")}
    assert after <= cands
    ids = sorted(after)
    card = {"key": "trip:test", "kind": "trip", "conf": 0.9, "ids": ids, "title": "t", "action": {"do": "trip_add", "trip": trip, "ids": ids}}
    # accept with two unticked: those two are remembered as "not this trip"
    inbox.apply(conn, lib, orig, inbox.restrict(card, ids[2:]))
    assert inbox.rejected_for_trip(conn, trip) == set(ids[:2])
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE trip_id = ? AND path LIKE 'after%'", (trip,)).fetchone()[0] == 4
    again = {i["id"] for i in curate.suggest_for_trip(conn, str(lib / "library.db"), trip)["items"]}
    assert not (again & set(ids[:2]))
    # skipping a trip card rejects all of its photos; the suggestion dialog's unticked ones too
    other = conn.execute("SELECT id FROM assets WHERE path = 'h19.jpg'").fetchone()[0]
    inbox.skip(conn, {"key": "trip:skip", "kind": "trip", "ids": [other], "action": {"do": "trip_add", "trip": trip, "ids": [other]}})
    inbox.reject_for_trip(conn, trip, [other])                           # idempotent
    assert other in inbox.rejected_for_trip(conn, trip)


def test_face_card_unticked_faces_leave_the_group(tmp_path):
    from image_magic import faces
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    faces.ensure_schema(conn)
    lib = tmp_path / "lib"
    for i in range(3):
        make_photo(lib / "originals" / f"p{i}.jpg", datetime(2020, 1, 1 + i), HOME)
    catalog.scan(conn, lib / "originals")
    aids = [r[0] for r in conn.execute("SELECT id FROM assets ORDER BY id")]
    conn.execute("INSERT INTO persons (name) VALUES ('Emma')"); emma = conn.execute("SELECT id FROM persons WHERE name = 'Emma'").fetchone()[0]
    conn.execute("INSERT INTO persons (name) VALUES (NULL)"); grp = conn.execute("SELECT id FROM persons WHERE name IS NULL").fetchone()[0]
    fids = []
    for aid in aids:
        cur = conn.execute("INSERT INTO faces (asset_id, person_id, x1, y1, x2, y2, score, kps, size_px) VALUES (?, ?, 0, 0, 0.2, 0.2, 0.9, '[]', 100)", (aid, grp))
        fids.append(cur.lastrowid)
    conn.commit()
    card = {"key": "face:test", "kind": "people", "conf": 0.8, "ids": aids, "faces": fids, "title": "t",
            "action": {"do": "face_merge", "from": grp, "into": emma}}
    r = inbox.restrict_faces(card, fids[1:])
    assert r["action"]["detach"] == [fids[0]]
    inbox.apply(conn, lib, lib / "originals", r)
    rows = {r[0]: r[1] for r in conn.execute("SELECT id, person_id FROM faces")}
    assert rows[fids[0]] is None and rows[fids[1]] == emma and rows[fids[2]] == emma
    assert inbox.restrict_faces(card, []) == card                       # no list = nothing unticked
    with pytest.raises(ValueError):
        inbox.restrict_faces(card, [999])                               # nothing left ticked


def test_clutter_categories(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2019, 5, 1, 12)
    for i in range(3):
        textured(orig / f"2019/good{i}.jpg", t + timedelta(days=i), seed=i)
    Image.new("RGB", (600, 400), (3, 3, 3)).save(orig / "2019/pocket.jpg", "JPEG")              # black
    Image.new("RGB", (600, 400), (120, 120, 120)).save(orig / "2019/wall.jpg", "JPEG")           # blank
    make_photo(orig / "2019/night.jpg", t + timedelta(days=9), HOME, color=(0, 0, 0))              # dim: a moon in a black sky
    with Image.open(orig / "2019/night.jpg") as im:
        from PIL import ImageDraw
        exif = im.getexif(); im = im.convert("RGB"); ImageDraw.Draw(im).ellipse((20, 10, 40, 30), fill=(250, 250, 240)); im.save(orig / "2019/night.jpg", "JPEG", exif=exif)
    Image.new("RGB", (300, 300), (200, 20, 20)).save(orig / "2019/meme.gif", "GIF")              # saved image
    Image.new("RGB", (300, 300), (20, 200, 20)).save(orig / "2019/sticker.png", "PNG")           # saved image (no camera)
    make_photo(orig / "2019/shot.png", t, HOME, fmt="PNG")                                        # screenshot (iCloud says so)
    (orig / "2019/shot.png.xmp").write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                                            '<rdf:Description rdf:about="" xmlns:tiff="http://ns.adobe.com/tiff/1.0/"><tiff:Make>Screenshot</tiff:Make></rdf:Description></rdf:RDF></x:xmpmeta>')
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    quality.score_pending(conn, lib, orig)
    c = cleanup.candidates(conn, lib, orig)
    paths = lambda key: {conn.execute("SELECT path FROM assets WHERE id = ?", (i["id"],)).fetchone()[0].split("/")[-1] for i in c["groups"][key]}  # noqa: E731
    assert set(c["groups"]) == set(cleanup.GROUP_ORDER)
    assert paths("screenshots") == {"shot.png"}
    assert paths("saved") >= {"meme.gif", "sticker.png"} and "shot.png" not in paths("saved")
    assert "pocket.jpg" in paths("black") and "wall.jpg" in paths("blank")
    assert "night.jpg" in paths("dim") and "night.jpg" not in paths("black")
    assert not any("good" in p for k in cleanup.GROUP_ORDER if k != "duplicates" for p in paths(k))
    # the inbox card only ever proposes the safe categories, never the dim photos or documents
    cards = inbox._clutter(conn, lib, orig)
    if cards:
        night = conn.execute("SELECT id FROM assets WHERE path = '2019/night.jpg'").fetchone()[0]
        assert night not in cards[0]["ids"]


def test_coordinates_and_map_links_place_the_pin():
    want = (37.8281, -92.1777)
    for text in ("37.8281, -92.1777", "37.8281 -92.1777",
                 "https://www.google.com/maps/place/Saint+Robert,+MO/@37.8281,-92.1777,15z/data=!3m1!4b1!8m2!3d37.8281!4d-92.1777",
                 "https://maps.google.com/?q=37.8281,-92.1777", "https://www.google.com/maps/search/37.8281,+-92.1777",
                 "https://maps.apple.com/?ll=37.8281,-92.1777&q=Dropped%20Pin", "https://www.google.com/maps/dir/?api=1&destination=37.8281,-92.1777"):
        got = places.parse_coords(text)
        assert got and abs(got[0] - want[0]) < 1e-6 and abs(got[1] - want[1]) < 1e-6, text
    dms = places.parse_coords("37°49'41.2\"N 92°10'39.7\"W")
    assert dms and abs(dms[0] - 37.82811) < 1e-4 and abs(dms[1] + 92.17769) < 1e-4
    assert places.parse_coords("Saint Robert") is None and places.parse_coords("95.0, 10.0") is None
    assert places.parse_coords("https://maps.app.goo.gl/AbCdEf") is None          # a short link needs one online hop
    assert places.SHORT_LINK.match("https://maps.app.goo.gl/AbCdEf") and not places.SHORT_LINK.match("https://www.google.com/maps/@1,2,3z")
    r = places.search("https://maps.google.com/?q=37.8281,-92.1777")
    assert len(r) == 1 and "Saint Robert" in r[0]["region"]
    assert places.search("https://maps.app.goo.gl/AbCdEf") == []


def test_unticked_clutter_is_kept_and_not_listed_again(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(3):
        textured(orig / f"2019/good{i}.jpg", datetime(2019, 5, 1 + i, 12), seed=i)
    Image.new("RGB", (600, 400), (3, 3, 3)).save(orig / "2019/pocket.jpg", "JPEG")
    Image.new("RGB", (600, 400), (4, 4, 4)).save(orig / "2019/pocket2.jpg", "JPEG")
    Image.new("RGB", (600, 400), (120, 120, 120)).save(orig / "2019/wall.jpg", "JPEG")
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    quality.score_pending(conn, lib, orig)
    c = cleanup.candidates(conn, lib, orig)
    black = {i["id"] for i in c["groups"]["black"]}
    assert len(black) == 2 and c["kept"] == 0
    a, b = sorted(black)
    # you looked at the black frames and trashed one; the other was left unticked, so it is a keeper
    cleanup.trash(conn, lib, orig, [a]); cleanup.keep(conn, [b])
    c2 = cleanup.candidates(conn, lib, orig)
    assert {i["id"] for i in c2["groups"]["black"]} == set() and c2["kept"] == 1
    assert cleanup.kept(conn) == {b}
    # the inbox card does the same with photos you untick before accepting
    wall = conn.execute("SELECT id FROM assets WHERE path = '2019/wall.jpg'").fetchone()[0]
    card = {"key": "clutter:test", "kind": "clutter", "conf": 0.95, "ids": [wall], "title": "t", "action": {"do": "trash", "ids": [wall], "auto_ids": []}}
    from image_magic import inbox
    inbox.apply(conn, lib, orig, {**card, "all_ids": [wall, b], "action": {"do": "trash", "ids": [wall]}})
    assert b in cleanup.kept(conn)
    # and you can change your mind
    assert cleanup.forget_kept(conn) == 1
    assert cleanup.candidates(conn, lib, orig)["groups"]["black"] and cleanup.kept(conn) == set()
