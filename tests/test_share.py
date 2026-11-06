import json

from PIL import Image

from image_magic import ask, catalog, describe, share
from test_ask import library  # noqa: F401  (Jordan trip with three captioned Petra photos)


def test_check_flags_sensitive_captions(library):  # noqa: F811
    conn, lib = library
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    aid = conn.execute("SELECT asset_id FROM descriptions LIMIT 1").fetchone()[0]
    conn.execute("UPDATE descriptions SET caption = 'A C-17 Globemaster on the flight line at dusk.', "
                 "keywords = '[\"aircraft\",\"military\"]' WHERE asset_id = ?", (aid,))
    conn.commit()
    r = share.check(conn, {"trip": trip})
    assert r["total"] == 12 and len(r["flagged"]) == 1
    f = r["flagged"][0]
    assert f["id"] == aid and {"c-17", "globemaster", "flight line", "aircraft", "military"} <= set(f["reasons"])
    # "address" must not match inside "addressed"
    conn.execute("UPDATE descriptions SET caption = 'She addressed the crowd.', keywords = '[]' WHERE asset_id = ?", (aid,))
    conn.commit()
    assert share.check(conn, {"trip": trip})["flagged"] == []


def test_export_strips_metadata_and_respects_exclusions(library):  # noqa: F811
    conn, lib = library
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    ids = [r[0] for r in conn.execute("SELECT id FROM assets WHERE trip_id = ? ORDER BY id", (trip,))]
    describe.set_private(conn, ids[0], True)
    ask.save_story(conn, f"trip:{trip}", "Our week in Jordan.", "you", edited=True)
    folder = share.export(conn, lib / "originals", lib, {"trip": trip}, "Jordan 2019",
                          exclude=[ids[1]], story_key=f"trip:{trip}")
    assert folder.name == "share-jordan-2019"
    images = sorted((folder / "images").glob("*.jpg"))
    assert len(images) == 10                      # 12 - 1 private - 1 excluded
    im = Image.open(images[0])
    assert not im.getexif() and max(im.size) <= share.EXPORT_PX
    page = (folder / "index.html").read_text(encoding="utf-8")
    assert "Our week in Jordan." in page and "Petra, Jordan" in page and page.count("<figure>") == 10
    assert "Red sandstone" in page              # captions travel with the photos
    assert (folder / "README.txt").exists()
    # re-export replaces the folder cleanly
    folder2 = share.export(conn, lib / "originals", lib, {"trip": trip}, "Jordan 2019")
    assert len(list((folder2 / "images").glob("*.jpg"))) == 11
