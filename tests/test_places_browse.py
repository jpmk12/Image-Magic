import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest
from PIL import Image

from image_magic import browse, catalog, places
from image_magic.server import App, make_server
from image_magic.settings import Settings

HOME = (39.13, -75.47)      # Dover AFB, Delaware
PETRA = (30.3285, 35.4444)
AMMAN = (31.95, 35.93)
TOKYO = (35.68, 139.76)


def dms(value):
    v = abs(value)
    d = int(v); m = int((v - d) * 60); s = round(((v - d) * 60 - m) * 60, 2)
    return (float(d), float(m), s)


def make_photo(path, when: datetime, where=None, fmt="JPEG", color=(120, 80, 40)):
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = when.strftime("%Y:%m:%d %H:%M:%S")
    if where:
        lat, lon = where
        g = exif.get_ifd(0x8825)
        g[1], g[2] = ("N" if lat >= 0 else "S"), dms(lat)
        g[3], g[4] = ("E" if lon >= 0 else "W"), dms(lon)
    Image.new("RGB", (60, 40), color).save(path, fmt, exif=exif)


def test_lookup_names_places():
    p = places.lookup(*PETRA)
    assert (p.city, p.country, p.country_code) == ("Petra", "Jordan", "JO")
    assert places.lookup(*HOME).country == "United States"
    sea = places.lookup(35.0, -40.0)
    assert sea.country is None and sea.city is None


def test_lookup_across_antimeridian():
    assert places.lookup(-16.8, 179.99).country == "Fiji"


@pytest.fixture
def library(tmp_path):
    """A year at home, a Jordan trip, a short Tokyo trip, and a video during the Jordan trip."""
    orig = tmp_path / "lib" / "originals"
    t = datetime(2019, 1, 5, 12)
    n = 0
    for day in range(0, 300, 3):           # ~100 home photos over the year
        make_photo(orig / f"home/IMG_{n:04d}.JPG", t + timedelta(days=day), HOME); n += 1
    jordan = datetime(2019, 3, 10, 9)
    for i in range(12):                    # Jordan trip: Amman then Petra over 6 days
        where = AMMAN if i < 5 else PETRA
        make_photo(orig / f"jo/IMG_{n:04d}.HEIC", jordan + timedelta(hours=12 * i), where, fmt="HEIF"); n += 1
    vid = orig / "jo/IMG_9000.MOV"
    vid.write_bytes(b"video during the trip")
    ts = (jordan + timedelta(days=2)).timestamp()
    import os; os.utime(vid, (ts, ts))
    tokyo = datetime(2019, 8, 1, 10)
    for i in range(6):
        make_photo(orig / f"jp/IMG_{n:04d}.JPG", tokyo + timedelta(hours=8 * i), TOKYO); n += 1
    for i in range(3):                     # too few photos to count as a trip
        make_photo(orig / f"short/IMG_{n:04d}.JPG", datetime(2019, 10, 1) + timedelta(hours=i), PETRA); n += 1
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    result = catalog.scan(conn, orig)
    return conn, result, tmp_path / "lib"


def test_trips_found(library):
    conn, result, _ = library
    assert result.trips == 2
    trips = browse.trips(conn)
    assert [t["title"] for t in trips] == ["Japan", "Jordan"]
    jordan = trips[1]
    assert "Petra" in jordan["subtitle"] and "Amman" in jordan["subtitle"]
    # 12 photos + the GPS-less video taken during the trip
    assert jordan["photo_count"] == 13
    assert jordan["start_at"].startswith("2019-03-10")


def test_filters_and_facets(library):
    conn, _, _ = library
    jo = browse.photos(conn, {"country": "JO"})
    assert jo["total"] == 15                     # 12 trip + 3 later Petra photos
    petra = browse.photos(conn, {"country": "JO", "city": "Petra"})
    assert petra["total"] == 10
    f = browse.facets(conn)
    assert f["years"] == [{"year": "2019", "n": 122}]
    codes = [c["code"] for c in f["countries"]]
    assert codes[0] == "US" and "JO" in codes and "JP" in codes
    trip_id = browse.trips(conn)[1]["id"]
    in_trip = browse.photos(conn, {"trip": trip_id})
    assert in_trip["total"] == 13
    assert any(i["kind"] == "video" for i in in_trip["items"])
    assert len(browse.map_points(conn, {"country": "JP"})) == 6


def test_places_only_looked_up_once(library):
    conn, _, _ = library
    assert places.update_places(conn) == 0


@pytest.fixture
def server(library):
    conn, _, lib = library
    Settings(apple_id="me@example.com", library_path=str(lib)).save()
    app = App()
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{srv.server_address[1]}", conn
    srv.shutdown()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, dict(r.headers), r.read()


def test_api_photos_thumbs_and_view(server):
    app, base, conn = server
    h = {"X-Token": app.token}
    _, _, body = get(base + "/api/photos?country=JO&limit=5", h)
    data = json.loads(body)
    assert data["total"] == 15 and len(data["items"]) == 5
    heic = conn.execute("SELECT id FROM assets WHERE path LIKE '%.HEIC' LIMIT 1").fetchone()[0]

    # media URLs authenticate with ?t= because <img> can't send headers
    status, headers, body = get(f"{base}/media/thumb/{heic}?t={app.token}")
    assert status == 200 and headers["Content-Type"] == "image/jpeg" and body[:2] == b"\xff\xd8"
    status, headers, body = get(f"{base}/media/view/{heic}?t={app.token}")
    assert headers["Content-Type"] == "image/jpeg"   # HEIC converted for the browser
    with pytest.raises(urllib.error.HTTPError) as e:
        get(f"{base}/media/thumb/{heic}")
    assert e.value.code == 403

    detail = json.loads(get(f"{base}/api/photo/{heic}", h)[2])
    assert detail["country"] == "Jordan" and detail["trip_title"] == "Jordan"

    trips = json.loads(get(base + "/api/trips", h)[2])
    assert len(trips) == 2
    facets = json.loads(get(base + "/api/facets", h)[2])
    assert facets["countries"][0]["code"] == "US"


def test_video_range_requests(server):
    app, base, conn = server
    vid = conn.execute("SELECT id FROM assets WHERE kind = 'video'").fetchone()[0]
    status, headers, body = get(f"{base}/media/view/{vid}?t={app.token}", {"Range": "bytes=6-10"})
    assert status == 206 and body == b"durin"
    assert headers["Content-Range"] == "bytes 6-10/21"
    status, _, body = get(f"{base}/media/view/{vid}?t={app.token}")
    assert status == 200 and body == b"video during the trip"


def test_vendor_static_files(server):
    _, base, _ = server
    status, headers, body = get(base + "/static/vendor/leaflet.js")
    assert status == 200 and headers["Content-Type"] == "text/javascript" and b"Leaflet" in body[:500]
    assert get(base + "/static/vendor/images/marker-icon.png")[0] == 200
    for bad in ("/static/vendor/../index.html", "/static/vendor/%2e%2e/server.py",
                "/static/vendor/LICENSE-leaflet.txt", "/static/vendor/"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base + bad)
        assert e.value.code in (403, 404)
