"""Phase 7: Google Photos Takeout import (json sidecars, attribution) and a second iCloud account."""
import json
import time
from datetime import datetime

from PIL import Image

from image_magic import catalog, importer, locations, metadata, sync
from image_magic.jobs import JobRunner
from image_magic.settings import Settings
from test_importer import _lib, make_photo
from test_places_browse import HOME


def _takeout(folder, name, when: datetime, where=None, description="", people=(), favorited=False, sidecar_name=None, with_exif=True):
    """A Takeout photo: a JPEG (with or without EXIF) and its json sidecar."""
    folder.mkdir(parents=True, exist_ok=True)
    if with_exif:
        make_photo(folder / name, when, where=None, seed=hash(name) % 1000)
    else:
        Image.new("RGB", (600, 400), (10, 90, 200)).save(folder / name, "JPEG")
    data = {"title": name, "description": description, "photoTakenTime": {"timestamp": str(int(when.timestamp()))},
            "geoData": {"latitude": where[0] if where else 0.0, "longitude": where[1] if where else 0.0, "altitude": 0.0},
            "favorited": favorited, "people": [{"name": p} for p in people]}
    (folder / (sidecar_name or name + ".json")).write_text(json.dumps(data))


def test_google_sidecar_lookup_and_fields(tmp_path):
    t = tmp_path / "Takeout" / "Google Photos" / "Photos from 2016"
    _takeout(t, "IMG_1.jpg", datetime(2016, 5, 4, 15, 30), HOME, "Sam at the beach", ["Sam"], favorited=True)
    _takeout(t, "IMG_2.jpg", datetime(2016, 5, 5, 9), sidecar_name="IMG_2.jpg.supplemental-metadata.json")
    _takeout(t, "IMG_3(1).jpg", datetime(2016, 5, 6, 9), sidecar_name="IMG_3.jpg(1).json")
    _takeout(t, "a_very_long_file_name_that_takeout_will_truncate_somewhere.jpg", datetime(2016, 5, 7, 9), HOME,
             sidecar_name="a_very_long_file_name_that_takeout_will_trunc.json")
    make_photo(t / "lonely.jpg", datetime(2016, 5, 8, 9), where=None, seed=5)
    m = metadata.read_google_sidecar(t / "IMG_1.jpg")
    assert m and (round(m.lat, 2), round(m.lon, 2)) == (round(HOME[0], 2), round(HOME[1], 2))
    assert m.taken_at.startswith("2016-05-04T15:30") and m.favorite and m.title == "Sam at the beach" and m.keywords == ["Sam"]
    assert metadata.read_google_sidecar(t / "IMG_2.jpg").taken_at.startswith("2016-05-05")
    assert metadata.read_google_sidecar(t / "IMG_3(1).jpg").taken_at.startswith("2016-05-06")
    assert metadata.read_google_sidecar(t / "a_very_long_file_name_that_takeout_will_truncate_somewhere.jpg").lat is not None   # found by title
    assert metadata.read_google_sidecar(t / "lonely.jpg") is None
    m2 = metadata.read_google_sidecar(t / "IMG_2.jpg")
    assert m2.lat is None                                     # 0,0 means no location


def test_takeout_import_keeps_metadata_and_attributes(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    t = tmp_path / "Takeout" / "Google Photos" / "Photos from 2016"
    _takeout(t, "IMG_1.jpg", datetime(2016, 5, 4, 15, 30), HOME, "Sam at the beach", ["Sam"], favorited=True)
    _takeout(t, "nodate.jpg", datetime(2016, 5, 5, 9), HOME, with_exif=False)       # the file itself carries nothing
    (t / "metadata.json").write_text(json.dumps({"albumData": {"title": "Photos from 2016"}}))   # Takeout's album file
    importer.fill_hashes(conn, lib, orig)
    plan = importer.check_folder(conn, t, lib, orig)
    s = plan.summary()
    assert s["new"] == 2 and s["sidecars"] == 2 and s["skipped"] == 0           # json files are not "other files"
    r = importer.run_import(conn, plan, lib, orig, contributor="Sam's Google")
    assert r["copied"] == 2 and r["sidecars"] == 2
    rows = {r["path"].split("/")[-1]: r for r in conn.execute(
        "SELECT path, lat, taken_at, location_source, favorite, title, contributor FROM assets WHERE path LIKE 'imported/%'")}
    assert set(rows) == {"IMG_1.jpg", "nodate.jpg"}
    a = rows["IMG_1.jpg"]
    assert a["location_source"] == "google" and abs(a["lat"] - HOME[0]) < 1e-3 and a["favorite"] == 1
    assert a["title"] == "Sam at the beach" and a["contributor"] == "Sam's Google"
    b = rows["nodate.jpg"]
    assert b["taken_at"].startswith("2016-05-05T09:00") and b["location_source"] == "google" and b["contributor"] == "Sam's Google"
    assert (orig / "imported/2016/2016-05/IMG_1.jpg.json").is_file()
    # the old photos were not renamed as anyone's
    assert conn.execute("SELECT COUNT(*) FROM assets WHERE contributor IS NULL").fetchone()[0] == 5
    from image_magic import family
    assert family.contributors(conn)[0] == {"name": "Sam's Google", "photos": 2, "last": family.contributors(conn)[0]["last"]}


def test_import_job_passes_the_name(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    conn.close()
    desk = tmp_path / "phone"; desk.mkdir()
    make_photo(desk / "new.JPG", datetime(2015, 6, 1), seed=42)
    settings = Settings(apple_id="x", library_path=str(lib))
    runner = JobRunner(lambda: settings)
    runner.start("import_check", payload={"folder": str(desk)})
    for _ in range(100):
        if runner.state.state == "done":
            break
        time.sleep(0.05)
    runner.start("import", payload={"include_near": False, "contributor": "Old Android"})
    for _ in range(100):
        if runner.state.state == "done":
            break
        time.sleep(0.05)
    conn = catalog.connect(lib / "library.db")
    assert conn.execute("SELECT contributor FROM assets WHERE path LIKE 'imported/%'").fetchone()[0] == "Old Android"


def test_second_account_settings_and_command():
    s = Settings.from_dict({"apple_id": "me@icloud.com", "apple_id_2": "them@icloud.com", "contributor_2": " Sam / <x> ", "library_path": "/lib"})
    assert s.contributor_2 == "Sam x"
    assert [a[0] for a in s.accounts] == ["me@icloud.com", "them@icloud.com"]
    assert s.accounts[1][1].parts[-2:] == ("accounts", "Sam x") and s.accounts[1][2] == "Sam x" and s.accounts[0][2] is None
    cmd = sync.build_command("icloudpd", s, mode="quick", account=2)
    from pathlib import Path
    assert cmd[cmd.index("--username") + 1] == "them@icloud.com" and Path(cmd[cmd.index("--directory") + 1]).parts[-2:] == ("accounts", "Sam x")
    sync.assert_never_deletes(cmd)
    # a name is derived when only the id is given; one account when the second is blank or the same
    assert Settings.from_dict({"apple_id_2": "them@icloud.com"}).contributor_2 == "them"
    # names that could escape the library, hide the folder, or collide with year folders are not accepted as-is
    from image_magic.settings import clean_name
    assert clean_name("../../x") == "x" and clean_name(".sam") == "sam" and clean_name("Sam.") == "Sam"
    assert clean_name("2019") == "" and clean_name("..") == "" and clean_name("imported") == "imported"
    assert Settings.from_dict({"apple_id_2": "../../x@icloud.com"}).contributor_2 == "x"
    assert Settings.from_dict({"apple_id_2": "2019@icloud.com"}).contributor_2 == "second account"
    assert Settings.from_dict({"apple_id_2": "them@icloud.com", "contributor_2": "sam_j"}).contributor_2 == "sam_j"
    assert len(Settings.from_dict({"apple_id": "a@b.c", "apple_id_2": "a@b.c"}).accounts) == 1
    assert len(Settings(apple_id="a@b.c").accounts) == 1


def test_second_account_folder_is_attributed(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    make_photo(orig / "2019/IMG_1.JPG", datetime(2019, 3, 1), seed=1)
    make_photo(orig / "Sam/2019/IMG_2.JPG", datetime(2019, 3, 2), seed=2)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    make_photo(orig / "SamXj/2019/IMG_3.JPG", datetime(2019, 3, 3), seed=3)
    make_photo(orig / "Sam_j/2019/IMG_4.JPG", datetime(2019, 3, 4), seed=4)
    catalog.scan(conn, orig)
    assert catalog.attribute_folder(conn, "Sam", "Sam") == 1
    rows = dict(conn.execute("SELECT path, contributor FROM assets").fetchall())
    assert rows["Sam/2019/IMG_2.JPG"] == "Sam" and rows["2019/IMG_1.JPG"] is None
    assert catalog.attribute_folder(conn, "Sam", "Sam") == 0                    # already named
    assert catalog.attribute_folder(conn, "Sam_j", "Sam J") == 1                 # '_' is not a wildcard
    assert dict(conn.execute("SELECT path, contributor FROM assets").fetchall())["SamXj/2019/IMG_3.JPG"] is None
