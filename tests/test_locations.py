"""Locations from sidecars, videos and nearby photos; dates for videos; favourites."""
import struct
from datetime import datetime, timedelta

from image_magic import browse, catalog, locations, metadata
from test_places_browse import HOME, PETRA, make_photo

SIDECAR = """<?xml version="1.0"?><x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="icloudpd 1.33.0+abc">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Petra at dawn</dc:title>
<dc:subject><rdf:Seq><rdf:li>travel</rdf:li><rdf:li>jordan</rdf:li></rdf:Seq></dc:subject></rdf:Description>
<rdf:Description rdf:about="" xmlns:exif="http://ns.adobe.com/exif/1.0/"><exif:GPSLatitude>{lat}</exif:GPSLatitude><exif:GPSLongitude>{lon}</exif:GPSLongitude></rdf:Description>
<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"><xmp:CreateDate>2019-03-10T06:30:00+02:00</xmp:CreateDate><xmp:Rating>{rating}</xmp:Rating></rdf:Description>
<rdf:Description rdf:about="" xmlns:tiff="http://ns.adobe.com/tiff/1.0/">{make}</rdf:Description>
</rdf:RDF></x:xmpmeta>"""


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def make_mov(path, lat=39.13, lon=-75.47, created=datetime(2019, 3, 14, 15, 0), apple_keys=False):
    """A minimal QuickTime file: ftyp + moov(mvhd, udta/©xyz or meta/keys+ilst)."""
    secs = int((created - datetime(1904, 1, 1)).total_seconds())
    mvhd = _box(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">II", secs, secs) + b"\x00" * 92)
    iso = f"{lat:+.4f}{lon:+.4f}/".encode()
    if apple_keys:
        name = b"com.apple.quicktime.location.ISO6709"
        keys = _box(b"keys", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">I4s", 8 + len(name), b"mdta") + name)
        data = _box(b"data", b"\x00\x00\x00\x01\x00\x00\x00\x00" + iso)
        ilst = _box(b"ilst", _box(struct.pack(">I", 1), data))
        meta = _box(b"meta", b"\x00\x00\x00\x00" + keys + ilst)
        moov = _box(b"moov", mvhd + meta)
    else:
        xyz = _box(b"\xa9xyz", struct.pack(">HH", len(iso), 0x15C7) + iso)
        moov = _box(b"moov", mvhd + _box(b"udta", xyz))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_box(b"ftyp", b"qt  \x00\x00\x00\x00qt  ") + moov + _box(b"mdat", b"\x00" * 64))


def test_sidecar_parsing(tmp_path):
    p = tmp_path / "IMG_1.HEIC"; p.write_bytes(b"x")
    (tmp_path / "IMG_1.HEIC.xmp").write_text(SIDECAR.format(lat="30.3285", lon="35.4444", rating=5, make="<tiff:Make>Screenshot</tiff:Make>"))
    m = metadata.read_sidecar(p)
    assert (m.lat, m.lon) == (30.3285, 35.4444) and m.favorite and m.screenshot and not m.rejected
    assert m.taken_at == "2019-03-10T06:30:00+02:00" and m.title == "Petra at dawn" and m.keywords == ["travel", "jordan"]
    # Adobe's degree,minute format and rejected ratings
    (tmp_path / "IMG_1.HEIC.xmp").write_text(SIDECAR.format(lat="39,7.8N", lon="75,28,12W", rating=-1, make=""))
    m = metadata.read_sidecar(p)
    assert abs(m.lat - 39.13) < 1e-6 and abs(m.lon - (-75.47)) < 1e-6 and m.rejected and not m.favorite
    assert metadata.read_sidecar(tmp_path / "nope.jpg") is None
    (tmp_path / "IMG_1.HEIC.xmp").write_text("not xml")
    assert metadata.read_sidecar(p) is None


def test_video_atoms(tmp_path):
    make_mov(tmp_path / "a.mov")
    v = metadata.read_video_meta(tmp_path / "a.mov")
    assert (v.lat, v.lon) == (39.13, -75.47) and v.taken_at.startswith("2019-03-14T15:00:00")
    make_mov(tmp_path / "b.mov", lat=30.3285, lon=35.4444, apple_keys=True)
    v = metadata.read_video_meta(tmp_path / "b.mov")
    assert (v.lat, v.lon) == (30.3285, 35.4444)
    (tmp_path / "junk.mp4").write_bytes(b"\x00" * 100)
    assert metadata.read_video_meta(tmp_path / "junk.mp4").lat is None
    assert metadata._iso6709("+00.0000+000.0000/") is None


def test_scan_fills_locations_from_every_source(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2019, 7, 6, 10, 0)
    make_photo(orig / "gps.jpg", t, HOME)                                   # camera GPS
    make_photo(orig / "nogps_side.jpg", t + timedelta(hours=5))              # no GPS, iCloud knows
    (orig / "nogps_side.jpg.xmp").write_text(SIDECAR.format(lat="30.3285", lon="35.4444", rating=5, make=""))
    make_photo(orig / "nogps_near.jpg", t + timedelta(minutes=5))           # no GPS, 5 min after gps.jpg
    make_photo(orig / "nogps_far.jpg", t + timedelta(days=3))               # nothing nearby
    make_mov(orig / "clip.mov", created=datetime(2019, 7, 6, 12, 0))         # GPS and date inside the video
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    rows = {r["path"]: r for r in conn.execute("SELECT path, lat, lon, location_source, favorite, taken_at, city FROM assets")}
    assert rows["gps.jpg"]["location_source"] == "exif"
    assert rows["nogps_side.jpg"]["location_source"] == "icloud" and rows["nogps_side.jpg"]["city"] == "Petra" and rows["nogps_side.jpg"]["favorite"] == 1
    assert rows["nogps_near.jpg"]["location_source"] == "nearby" and abs(rows["nogps_near.jpg"]["lat"] - HOME[0]) < 1e-3
    assert rows["nogps_far.jpg"]["location_source"] == "none" and rows["nogps_far.jpg"]["lat"] is None
    assert rows["clip.mov"]["location_source"] == "video" and rows["clip.mov"]["taken_at"].startswith("2019-07-06T12:00")
    s = locations.summary(conn)
    assert s["located"] == 4 and s["by_source"] == {"exif": 1, "icloud": 1, "nearby": 1, "video": 1, "none": 1} and s["favorites"] == 1
    assert [i["id"] for i in browse.photos(conn, {"favorite": "1"})["items"]]
    # a second scan examines nothing again, and inference stays stable
    assert locations.read_sources(conn, orig) == {"exif": 0, "icloud": 0, "video": 0, "none": 0, "favorites": 0, "dates_fixed": 0}
    assert locations.infer_nearby(conn) == 1
    # interpolation between two neighbours that agree; the closer one wins when they disagree
    make_photo(orig / "p1.jpg", datetime(2020, 1, 1, 12, 0), HOME)
    make_photo(orig / "p2.jpg", datetime(2020, 1, 1, 12, 10), (HOME[0] + 0.01, HOME[1]))
    make_photo(orig / "mid.jpg", datetime(2020, 1, 1, 12, 5))
    make_photo(orig / "q1.jpg", datetime(2020, 2, 1, 12, 0), HOME)
    make_photo(orig / "q2.jpg", datetime(2020, 2, 1, 12, 10), PETRA)
    make_photo(orig / "qmid.jpg", datetime(2020, 2, 1, 12, 2))
    catalog.scan(conn, orig)
    rows = {r["path"]: r for r in conn.execute("SELECT path, lat, lon, location_source FROM assets")}
    assert abs(rows["mid.jpg"]["lat"] - (HOME[0] + 0.005)) < 1e-4
    assert abs(rows["qmid.jpg"]["lat"] - HOME[0]) < 1e-6
    # when the neighbour disappears, the inferred location goes too
    (orig / "p1.jpg").unlink(); (orig / "p2.jpg").unlink()
    catalog.scan(conn, orig)
    assert conn.execute("SELECT location_source, lat FROM assets WHERE path = 'mid.jpg'").fetchone()[:] == ("none", None)
