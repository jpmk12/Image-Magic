"""Phase 7: holidays through the years, quick (incremental) cataloging, map time-lapse data."""
import os
import time
from datetime import datetime

from image_magic import browse, catalog, holidays
from test_places_browse import HOME, PETRA, make_photo


def _lib(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for y in (2019, 2020, 2021):
        for d in (24, 25):
            make_photo(orig / f"{y}/{y}-12/xmas{d}.jpg", datetime(y, 12, d, 10), HOME)
        make_photo(orig / f"{y}/{y}-10/hallow.jpg", datetime(y, 10, 31, 18), HOME)
    make_photo(orig / "2020/2020-06/petra.jpg", datetime(2020, 6, 1, 12), PETRA)
    make_photo(orig / "2020/2020-07/plain.jpg", datetime(2020, 7, 10, 12))
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    return lib, orig, conn


def test_holiday_overview_replay_and_filter(tmp_path):
    lib, orig, conn = _lib(tmp_path)
    ov = {h["key"]: h for h in holidays.overview(conn)}
    assert [h["year"] for h in ov["christmas"]["years"]] == [2019, 2020, 2021]
    assert ov["christmas"]["total"] == 6 and all(y["count"] == 2 and y["cover"] for y in ov["christmas"]["years"])
    assert [y["year"] for y in ov["halloween"]["years"]] == [2019, 2020, 2021]
    assert "easter" not in ov and "thanksgiving" not in ov          # nothing from those days
    # the replay: best of each year, oldest first
    ids = holidays.best_ids(conn, "christmas", per_year=1)
    years = [conn.execute("SELECT substr(taken_at, 1, 4) FROM assets WHERE id = ?", (i,)).fetchone()[0] for i in ids]
    assert years == ["2019", "2020", "2021"]
    # the gallery filter understands holiday=
    assert len(browse.ids(conn, {"holiday": "christmas"})) == 6
    assert browse.ids(conn, {"holiday": "easter"}) == set()
    # a birthday becomes an occasion once a person has one
    conn.execute("INSERT INTO persons (name, birthday) VALUES ('Emma', '2018-10-31')")
    conn.commit()
    ov = {h["key"]: h for h in holidays.overview(conn)}
    pid = conn.execute("SELECT id FROM persons WHERE name = 'Emma'").fetchone()[0]
    bd = ov[f"birthday:{pid}"]
    assert bd["title"] == "Emma's birthday" and [y["year"] for y in bd["years"]] == [2019, 2020, 2021]
    assert len(browse.ids(conn, {"holiday": f"birthday:{pid}"})) == 3


def test_holiday_book(tmp_path):
    lib, orig, conn = _lib(tmp_path)
    from image_magic import books
    f = books.make(conn, orig, lib, "holiday", "christmas")
    assert f.is_file() and f.suffix == ".pdf" and "Christmas" in f.name


def test_quick_scan_skips_unchanged_folders(tmp_path):
    lib, orig, conn = _lib(tmp_path)
    total = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM dir_stamps").fetchone()[0] > 3
    # nothing changed: everything counts as unchanged without touching the files
    r = catalog.scan(conn, orig, quick=True)
    assert (r.new, r.updated, r.unchanged, r.missing) == (0, 0, total, 0)
    # a new file in a new month folder, and one removed from an old one, are both seen
    time.sleep(0.02)
    make_photo(orig / "2022/2022-01/new.jpg", datetime(2022, 1, 1, 9), HOME)
    (orig / "2020/2020-07/plain.jpg").unlink()
    os.utime(orig / "2020/2020-07", None)                      # a removal changes the folder's mtime on every OS
    r = catalog.scan(conn, orig, quick=True)
    assert (r.new, r.missing) == (1, 1)
    assert conn.execute("SELECT missing FROM assets WHERE path = '2020/2020-07/plain.jpg'").fetchone()[0] == 1
    # a full scan still works and refreshes the stamps
    r = catalog.scan(conn, orig)
    assert (r.new, r.missing, r.unchanged) == (0, 1, total)
    # a file replaced in place (same folder entry count) is caught by the full scan, not the quick one
    time.sleep(0.02)
    make_photo(orig / "2019/2019-10/hallow.jpg", datetime(2019, 10, 31, 19), PETRA, color=(1, 2, 3))
    stamp = conn.execute("SELECT mtime FROM dir_stamps WHERE path = '2019/2019-10'").fetchone()[0]
    os.utime(orig / "2019/2019-10", (stamp, stamp))             # pretend the folder itself did not change
    assert catalog.scan(conn, orig, quick=True).updated == 0
    assert catalog.scan(conn, orig).updated == 1


def test_map_months(tmp_path):
    lib, orig, conn = _lib(tmp_path)
    months = browse.map_months(conn, {})
    assert [m["m"] for m in months] == ["2019-10", "2019-12", "2020-06", "2020-10", "2020-12", "2021-10", "2021-12"]
    dec = next(m for m in months if m["m"] == "2019-12")
    assert len(dec["pts"]) == 1 and dec["pts"][0][2] == 2 and abs(dec["pts"][0][0] - HOME[0]) < 0.05
    assert [m["m"] for m in browse.map_months(conn, {"year": "2020"})] == ["2020-06", "2020-10", "2020-12"]
