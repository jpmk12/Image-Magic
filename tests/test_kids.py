import json
import threading
import urllib.request
from datetime import datetime, timedelta

import pytest
from PIL import Image

from image_magic import catalog, chapters, faces, kids
from image_magic.server import App, make_server
from image_magic.settings import Settings
from test_faces import RED, GREEN, FakeFaceModel


def make_photo(path, when, squares):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (640, 480), (128, 128, 128))
    for color, x, y, size in squares:
        for dx in range(size):
            for dy in range(size):
                im.putpixel((x + dx, y + dy), color)
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = when.strftime("%Y:%m:%d %H:%M:%S")
    im.putpixel((0, 0), (hash(str(path)) % 200, 0, 0))
    im.save(path, "JPEG", exif=exif, quality=95)


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2017, 3, 14, 12)
    n = 0
    while t < datetime(2021, 1, 1):          # a "red" child every ~6 weeks for 4 years
        make_photo(orig / f"IMG_{n:04d}.jpg", t, [(RED, 100 + n % 50, 100, 90)]); n += 1
        t += timedelta(days=42)
    make_photo(orig / "bday.jpg", datetime(2019, 3, 14, 15), [(RED, 300, 150, 90), (GREEN, 50, 50, 80)])
    make_photo(orig / "g1.jpg", datetime(2019, 5, 1), [(GREEN, 200, 200, 85)])
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    faces.scan_photos(conn, orig, FakeFaceModel())
    faces.group_faces(conn)
    red = next(p for p in faces.people(conn) if p["photo_count"] > 10)
    faces.rename_person(conn, red["id"], "Emma")
    return conn, lib, red["id"]


def test_age_label():
    assert kids.age_label("2017-03-14", "2017-03-20") == "newborn"
    assert kids.age_label("2017-03-14", "2017-09-13") == "5 months"
    assert kids.age_label("2017-03-14", "2018-03-13") == "11 months"
    assert kids.age_label("2017-03-14", "2019-03-14") == "2 years"
    assert kids.age_label(None, "2019-03-14") == ""
    assert kids.age_label("2017-03-14", "2016-01-01") == "before birth"


def test_growing_up_and_birthday(library):
    conn, lib, pid = library
    g = kids.growing_up(conn, pid)
    dates = [f["date"] for f in g["frames"]]
    assert dates == sorted(dates) and 20 <= len(dates) <= kids.MAX_FRAMES
    assert g["frames"][0]["age"] == ""
    kids.set_birthday(conn, pid, "2017-03-14")
    g = kids.growing_up(conn, pid)
    assert g["frames"][0]["age"] == "newborn" and g["frames"][-1]["age"].endswith("years")
    with pytest.raises(ValueError):
        kids.set_birthday(conn, pid, "not a date")
    chapters.rebuild_milestones(conn)
    ms = {r["key"]: r["title"] for r in conn.execute("SELECT key, title FROM milestones WHERE kind = 'birthday'")}
    assert ms[f"birthday:{pid}:2019"] == "Emma turns 2"


def test_gif_and_book(library):
    conn, lib, pid = library
    kids.set_birthday(conn, pid, "2017-03-14")
    gif = kids.make_gif(conn, lib / "originals", lib, pid)
    assert gif.name == "Emma-growing-up.gif"
    im = Image.open(gif)
    assert im.format == "GIF" and im.n_frames >= 20
    book = kids.make_book(conn, lib / "originals", lib, pid)
    assert book.name == "Emma-book.pdf" and book.stat().st_size > 20000
    data = book.read_bytes()
    assert data.startswith(b"%PDF") and b"/Count 6" in data or b"/Count" in data   # cover + growing + 4 years


def test_people_endpoints(library):
    conn, lib, pid = library
    Settings(apple_id="me@example.com", library_path=str(lib)).save()
    app = App()
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    h = {"X-Token": app.token, "Content-Type": "application/json"}
    get = lambda p: urllib.request.urlopen(urllib.request.Request(base + p, headers=h))  # noqa: E731
    try:
        urllib.request.urlopen(urllib.request.Request(base + "/api/people/birthday",
                               data=json.dumps({"id": pid, "birthday": "2017-03-14"}).encode(), headers=h))
        assert json.loads(get("/api/people").read())[0]["birthday"] == "2017-03-14"
        g = json.loads(get(f"/api/people/growing?id={pid}").read())
        assert g["frames"][0]["age"] == "newborn"
        f = json.loads(get(f"/api/people/book?id={pid}").read())["file"]
        r = get(f"/media/export/{f}?t={app.token}")
        assert r.headers["Content-Type"] == "application/pdf" and r.read()[:4] == b"%PDF"
        with pytest.raises(urllib.error.HTTPError):
            get("/media/export/..%2F..%2Flibrary.db?t=" + app.token)
    finally:
        srv.shutdown()
