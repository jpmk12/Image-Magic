"""Clean-up candidates and the safe trash."""
from datetime import datetime, timedelta

from PIL import Image, ImageFilter

from image_magic import browse, catalog, cleanup, quality
from test_importer import make_photo as textured
from test_places_browse import HOME, make_photo


def _lib(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2019, 5, 1, 12)
    for i in range(4):
        textured(orig / f"2019/good{i}.jpg", t + timedelta(days=i), seed=i)
    with Image.open(orig / "2019/good0.jpg") as im:
        exif = im.getexif()
        im.filter(ImageFilter.GaussianBlur(8)).save(orig / "2019/blur.jpg", "JPEG", exif=exif)
    Image.new("RGB", (600, 400), (3, 3, 3)).save(orig / "2019/pocket.jpg", "JPEG")
    (orig / "2019/copy.jpg").write_bytes((orig / "2019/good1.jpg").read_bytes())
    make_photo(orig / "2019/shot.png", t, HOME, fmt="PNG")
    (orig / "2019/shot.png.xmp").write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                                            '<rdf:Description rdf:about="" xmlns:tiff="http://ns.adobe.com/tiff/1.0/"><tiff:Make>Screenshot</tiff:Make></rdf:Description></rdf:RDF></x:xmpmeta>')
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    quality.score_pending(conn, lib, orig)
    return conn, lib, orig


def test_candidates_are_grouped(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    c = cleanup.candidates(conn, lib, orig)
    paths = lambda key: {conn.execute("SELECT path FROM assets WHERE id = ?", (i["id"],)).fetchone()[0] for i in c["groups"][key]}  # noqa: E731
    assert "2019/blur.jpg" in paths("blurry") and "2019/pocket.jpg" in paths("black")
    assert paths("duplicates") in ({"2019/copy.jpg"}, {"2019/good1.jpg"}) and paths("screenshots") == {"2019/shot.png"}   # the first-seen copy is the original
    assert not any("good" in p for k in cleanup.GROUP_ORDER if k != "duplicates" for p in paths(k))
    assert c["counts"]["blurry"] >= 1 and c["bytes"] > 0 and c["trash"]["photos"] == 0


def test_trash_restore_empty_and_retrash(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    blur = conn.execute("SELECT id FROM assets WHERE path = '2019/blur.jpg'").fetchone()[0]
    shot = conn.execute("SELECT id FROM assets WHERE path = '2019/shot.png'").fetchone()[0]
    r = cleanup.trash(conn, lib, orig, [blur, shot])
    assert r["trashed"] == 2 and r["failed"] == []
    assert not (orig / "2019/blur.jpg").exists() and (lib / "trash/2019/blur.jpg").exists()
    assert (lib / "trash/2019/shot.png.xmp").exists() and not (orig / "2019/shot.png.xmp").exists()   # sidecar travels along
    assert conn.execute("SELECT missing, trashed_at FROM assets WHERE id = ?", (blur,)).fetchone()[0] == 1
    assert blur not in {i["id"] for i in browse.photos(conn, {})["items"]}
    st = catalog.stats(conn)
    assert st["trashed"] == 2 and st["missing"] == 0                     # trashed is not "missing"
    # a scan leaves trashed rows alone
    catalog.scan(conn, orig)
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE trashed_at IS NOT NULL").fetchone()[0] == 2
    # restore one
    assert cleanup.restore(conn, lib, orig, [shot]) == 1
    assert (orig / "2019/shot.png").exists() and (orig / "2019/shot.png.xmp").exists()
    assert conn.execute("SELECT missing, trashed_at FROM assets WHERE id = ?", (shot,)).fetchone()[:] == (0, None)
    # iCloud sends the blurry one again: the scan puts it straight back in the trash
    (orig / "2019/blur.jpg").write_bytes((lib / "trash/2019/blur.jpg").read_bytes())
    res = catalog.scan(conn, orig)
    assert res.retrashed == 1 and not (orig / "2019/blur.jpg").exists() and res.new == 0
    # empty: files gone from the laptop, decision remembered
    e = cleanup.empty(conn, lib)
    assert e["deleted"] >= 1 and e["bytes"] > 0 and not list((lib / "trash").rglob("*.jpg"))
    assert cleanup.trash_summary(conn) == {"photos": 1, "bytes": cleanup.trash_summary(conn)["bytes"], "restorable": False}
    (orig / "2019/blur.jpg").write_bytes(b"\xff\xd8 re-sent")
    catalog.scan(conn, orig)
    assert not (orig / "2019/blur.jpg").exists()
