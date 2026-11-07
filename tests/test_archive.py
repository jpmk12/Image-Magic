"""The archive export: a static site of everything."""
from image_magic import albums, archive, ask, describe, events
from test_ask import library  # noqa: F401


def test_archive_site(library):  # noqa: F811
    conn, lib = library
    events.rebuild(conn)
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    ask.save_story(conn, f"trip:{trip}", "Our week in Jordan.", "you", edited=True)
    priv = conn.execute("SELECT id FROM assets WHERE path LIKE 'jo/%' ORDER BY id LIMIT 1").fetchone()[0]
    describe.set_private(conn, priv, True)
    albums.create(conn, "Picks", ids=[conn.execute("SELECT id FROM assets WHERE path LIKE 'home/%' LIMIT 1").fetchone()[0]])
    folder = archive.export(conn, lib / "originals", lib)
    index = (folder / "index.html").read_text(encoding="utf-8")
    assert "The story so far" in index and f"trip-{trip}.html" in index and "album-" in index and "chapter-" in index
    tp = (folder / f"trip-{trip}.html").read_text(encoding="utf-8")
    assert "Our week in Jordan." in tp and "Red sandstone" in tp and f"images/{priv}.jpg" not in tp
    imgs = list((folder / "images").glob("*.jpg"))
    assert imgs and not (folder / "images" / f"{priv}.jpg").exists()
    assert (folder / "README.txt").exists() and "year-" not in index
    # everything: a page per year with every photo, each image written once
    folder2 = archive.export(conn, lib / "originals", lib, everything=True)
    idx2 = (folder2 / "index.html").read_text(encoding="utf-8")
    assert "year-2019.html" in idx2
    total_visible = conn.execute("SELECT COUNT(*) FROM assets WHERE kind = 'photo' AND missing = 0 AND private = 0 AND duplicate_of IS NULL").fetchone()[0]
    assert len(list((folder2 / "images").glob("*.jpg"))) == total_visible
