import os
from pathlib import Path

import pytest
from PIL import Image

from image_magic import catalog, cli, sync
from image_magic.metadata import read_photo_meta


def make_photo(path: Path, *, when="2019:03:14 10:30:00", gps=None, color="red", fmt="JPEG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (64, 48), color)
    exif = Image.Exif()
    exif[271] = "Apple"
    exif[272] = "iPhone 15 Pro"
    exif.get_ifd(0x8769)[36867] = when
    if gps:
        (lat, lat_ref), (lon, lon_ref) = gps
        g = exif.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = lat_ref, lat, lon_ref, lon
    img.save(path, fmt, exif=exif)
    return path


# Petra, Jordan: 30.3285 N, 35.4444 E
PETRA = (((30.0, 19.0, 42.6), "N"), ((35.0, 26.0, 39.84), "E"))


def test_reads_exif_date_gps_camera(tmp_path):
    meta = read_photo_meta(make_photo(tmp_path / "a.jpg", gps=PETRA))
    assert meta.taken_at == "2019-03-14T10:30:00"
    assert meta.lat == pytest.approx(30.3285, abs=1e-4)
    assert meta.lon == pytest.approx(35.4444, abs=1e-4)
    assert (meta.camera_make, meta.camera_model) == ("Apple", "iPhone 15 Pro")
    assert (meta.width, meta.height) == (64, 48)


def test_reads_heic(tmp_path):
    meta = read_photo_meta(make_photo(tmp_path / "a.heic", gps=PETRA, fmt="HEIF"))
    assert meta.taken_at == "2019-03-14T10:30:00"
    assert meta.lat == pytest.approx(30.3285, abs=1e-4)


def test_west_south_are_negative(tmp_path):
    gps = (((33.0, 51.0, 0.0), "S"), ((151.0, 12.0, 0.0), "W"))
    meta = read_photo_meta(make_photo(tmp_path / "a.jpg", gps=gps))
    assert meta.lat < 0 and meta.lon < 0


def test_corrupt_file_does_not_crash(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    meta = read_photo_meta(bad)
    assert meta.taken_at is None and meta.width is None


@pytest.fixture
def library(tmp_path):
    orig = tmp_path / "originals"
    make_photo(orig / "2019/2019-03/IMG_0001.HEIC", gps=PETRA, fmt="HEIF")
    (orig / "2019/2019-03/IMG_0001_HEVC.MOV").write_bytes(b"live video bytes")
    make_photo(orig / "2019/2019-03/IMG_0002.JPG", color="blue")
    (orig / "2020/2020-07").mkdir(parents=True)
    (orig / "2020/2020-07/IMG_0100.MOV").write_bytes(b"a normal video")
    (orig / "2019/2019-03/IMG_0001.HEIC.xmp").write_text("<xmp/>")
    conn = catalog.connect(tmp_path / "library.db")
    return conn, orig


def test_scan_catalogs_and_pairs_live_photos(library):
    conn, orig = library
    r = catalog.scan(conn, orig)
    assert (r.new, r.missing, r.skipped) == (4, 0, [])
    kinds = dict(conn.execute("SELECT path, kind FROM assets").fetchall())
    assert kinds["2019/2019-03/IMG_0001_HEVC.MOV"] == "live_video"
    assert kinds["2020/2020-07/IMG_0100.MOV"] == "video"
    live = conn.execute(
        "SELECT p.path FROM assets v JOIN assets p ON v.live_pair_id = p.id"
    ).fetchone()[0]
    assert live == "2019/2019-03/IMG_0001.HEIC"


def test_rescan_is_incremental_and_detects_missing(library):
    conn, orig = library
    catalog.scan(conn, orig)
    r = catalog.scan(conn, orig)
    assert (r.new, r.updated, r.unchanged) == (0, 0, 4)

    (orig / "2019/2019-03/IMG_0002.JPG").unlink()
    make_photo(orig / "2021/2021-01/IMG_0200.JPG", color="green")
    r = catalog.scan(conn, orig)
    assert (r.new, r.missing) == (1, 1)
    assert catalog.stats(conn)["missing"] == 1


def test_duplicates_flagged(library):
    conn, orig = library
    dup = orig / "2019/2019-03/IMG_0002 (1).JPG"
    dup.write_bytes((orig / "2019/2019-03/IMG_0002.JPG").read_bytes())
    catalog.scan(conn, orig)
    assert catalog.stats(conn)["duplicates"] == 1


def test_verify_detects_corruption(library):
    conn, orig = library
    catalog.scan(conn, orig)
    target = orig / "2020/2020-07/IMG_0100.MOV"
    st = target.stat()
    target.write_bytes(b"a normal videX")  # same size, different content
    os.utime(target, (st.st_atime, st.st_mtime))
    r = catalog.verify(conn, orig)
    assert r.changed == ["2020/2020-07/IMG_0100.MOV"]
    assert r.ok == 3


def test_video_falls_back_to_file_date(library):
    conn, orig = library
    catalog.scan(conn, orig)
    taken = conn.execute(
        "SELECT taken_at FROM assets WHERE path = '2020/2020-07/IMG_0100.MOV'"
    ).fetchone()[0]
    assert taken is not None


def test_sync_command_shape(tmp_path):
    cmd = sync.build_command("icloudpd", "me@example.com", tmp_path, recent=10, adp=True)
    assert cmd[:3] == ["icloudpd", "--username", "me@example.com"]
    assert cmd[cmd.index("--folder-structure") + 1] == "{:%Y/%Y-%m}"
    assert ["--size", "original"] == cmd[cmd.index("--size"):cmd.index("--size") + 2]
    assert "--adp-web-access" in cmd and "--recent" in cmd


def test_cli_init_scan_status(tmp_path, capsys):
    lib = tmp_path / "lib"
    assert cli.main(["--library", str(lib), "init", "--apple-id", "me@example.com"]) == 0
    make_photo(lib / "originals/2019/2019-03/IMG_0001.JPG", gps=PETRA)
    assert cli.main(["--library", str(lib), "scan"]) == 0
    assert cli.main(["--library", str(lib), "verify"]) == 0
    assert cli.main(["--library", str(lib), "status"]) == 0
    out = capsys.readouterr().out
    assert "1 new" in out and "1 OK" in out and "With GPS:     1" in out
