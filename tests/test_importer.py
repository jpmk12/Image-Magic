import threading
import time
from datetime import datetime, timedelta

from PIL import Image

from PIL import ImageDraw

from image_magic import catalog, importer
from image_magic.jobs import JobRunner
from image_magic.settings import Settings
from test_places_browse import HOME, dms


def make_photo(path, when, where=HOME, seed=0, size=(600, 400)):
    """A textured picture (gradient + circles placed by seed) with EXIF date and GPS."""
    import random
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(int(80 + 150 * y / h), int(120 + 100 * (1 - y / h)), 160))
    for _ in range(4):
        x, y, r = rng.randint(0, w), rng.randint(0, h), rng.randint(30, 120)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(rng.randint(0, 90), rng.randint(0, 90), rng.randint(0, 90)))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = when.strftime("%Y:%m:%d %H:%M:%S")
    lat, lon = where
    g = exif.get_ifd(0x8825)
    g[1], g[2], g[3], g[4] = ("N" if lat >= 0 else "S"), dms(lat), ("E" if lon >= 0 else "W"), dms(lon)
    im.save(path, "JPEG", exif=exif, quality=90)


def _lib(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i in range(5):
        make_photo(orig / f"2019/IMG_{i:04d}.JPG", datetime(2019, 3, 1) + timedelta(days=i), seed=i)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    return conn, lib, orig


def test_dhash_is_stable_under_resize_and_distinct_for_different_pictures(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    src = orig / "2019/IMG_0000.JPG"
    with Image.open(src) as im:
        a = importer.dhash(im)
        b = importer.dhash(im.resize((300, 200)))
    assert importer.hamming(a, b) <= 2
    with Image.open(orig / "2019/IMG_0003.JPG") as im:
        c = importer.dhash(im)
    assert importer.hamming(a, c) > importer.NEAR_BITS
    assert not importer.informative(0) and not importer.informative(2**64 - 1) and importer.informative(a)


def test_check_classifies_new_exact_and_near(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    desk = tmp_path / "desktop"
    desk.mkdir()
    # exact copy, a resized re-save (look-alike), a brand new photo, a note, and a duplicate within the folder
    (desk / "copy.JPG").write_bytes((orig / "2019/IMG_0001.JPG").read_bytes())
    with Image.open(orig / "2019/IMG_0002.JPG") as im:
        im.resize((300, 200)).save(desk / "resized.jpg", "JPEG", quality=70)
    make_photo(desk / "new.JPG", datetime(2015, 6, 1), seed=42)
    (desk / "notes.txt").write_text("hi")
    (desk / "sub").mkdir(); (desk / "sub" / "new-again.JPG").write_bytes((desk / "new.JPG").read_bytes())
    assert importer.fill_hashes(conn, lib, orig) == 5
    plan = importer.check_folder(conn, desk, lib, orig)
    s = plan.summary()
    assert s["new"] == 1 and s["exact"] == 2 and s["near"] == 1 and s["skipped"] == 1
    assert plan.near[0]["path"] == "resized.jpg" and plan.near[0]["bits"] <= importer.NEAR_BITS
    assert {e["path"] for e in plan.exact} == {"copy.JPG", "sub/new-again.JPG"}

    r = importer.run_import(conn, plan, lib, orig, include_near=False)
    assert r["copied"] == 1 and r["catalogued_new"] == 1
    dest = list((orig / "imported" / "2015" / "2015-06").glob("*.JPG"))
    assert len(dest) == 1 and (desk / "new.JPG").exists()          # original left in place
    # importing again finds nothing new
    assert importer.check_folder(conn, desk, lib, orig).summary()["new"] == 0
    # the look-alike can be imported on request
    plan2 = importer.check_folder(conn, desk, lib, orig)
    r2 = importer.run_import(conn, plan2, lib, orig, include_near=True)
    assert r2["copied"] == 1


def test_refuses_folder_inside_library(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        importer.check_folder(conn, orig / "2019", lib, orig)
    with pytest.raises(ValueError):
        importer.check_folder(conn, tmp_path / "nope", lib, orig)


def test_import_jobs(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    conn.close()
    desk = tmp_path / "desktop"; desk.mkdir()
    make_photo(desk / "new.JPG", datetime(2015, 6, 1), seed=42)
    settings = Settings(apple_id="x", library_path=str(lib))
    runner = JobRunner(lambda: settings)
    runner.start("import_check", payload={"folder": str(desk)})
    for _ in range(100):
        if runner.state.state == "done":
            break
        time.sleep(0.05)
    assert runner.state.state == "done" and runner.state.result["new"] == 1
    runner.start("import", payload={"include_near": False})
    for _ in range(100):
        if runner.state.state == "done":
            break
        time.sleep(0.05)
    assert runner.state.result["copied"] == 1 and runner.state.result["total"] == 6
