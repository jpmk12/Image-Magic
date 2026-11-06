import json
import threading
import urllib.request
from datetime import datetime, timedelta

import pytest

from image_magic import catalog, chapters
from image_magic.server import App, make_server
from image_magic.settings import Settings
from test_places_browse import HOME, PETRA, TOKYO, make_photo

RAMSTEIN = (49.437, 7.6)


@pytest.fixture
def life(tmp_path):
    """Two years in Delaware (with a Jordan trip), a move to Germany for a year, Tokyo trip."""
    orig = tmp_path / "lib" / "originals"
    n = 0
    t = datetime(2018, 1, 10, 12)
    while t < datetime(2020, 1, 1):
        make_photo(orig / f"a/IMG_{n:04d}.JPG", t, HOME); n += 1
        t += timedelta(days=5)
    for i in range(12):
        make_photo(orig / f"jo/IMG_{n:04d}.JPG", datetime(2019, 3, 10, 9) + timedelta(hours=12 * i), PETRA); n += 1
    t = datetime(2020, 1, 5, 12)
    while t < datetime(2021, 1, 1):
        make_photo(orig / f"de/IMG_{n:04d}.JPG", t, RAMSTEIN); n += 1
        t += timedelta(days=5)
    for i in range(6):
        make_photo(orig / f"jp/IMG_{n:04d}.JPG", datetime(2020, 8, 1, 10) + timedelta(hours=8 * i), TOKYO); n += 1
    for i in range(20):   # one very busy day in 2018
        make_photo(orig / f"busy/IMG_{n:04d}.JPG", datetime(2018, 7, 4, 10) + timedelta(minutes=15 * i), HOME); n += 1
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    catalog.scan(conn, orig)
    return conn, tmp_path / "lib"


def test_chapters_follow_home_moves(life):
    conn, _ = life
    chs = conn.execute("SELECT title, start_at, end_at, photo_count FROM chapters ORDER BY start_at").fetchall()
    assert len(chs) == 2
    assert "United States" in chs[0]["title"] and chs[0]["start_at"] == "2018-01-01"
    assert "Germany" in chs[1]["title"] and chs[1]["start_at"] == "2020-01-01"
    # the Jordan trip must not have broken the Delaware chapter
    assert chs[0]["end_at"] == "2019-12-31"
    assert chs[0]["photo_count"] > 100


def test_milestones(life):
    conn, _ = life
    ms = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM milestones")}
    assert ms["first-photo"]["date"] == "2018-01-10"
    assert ms["move:2020-01"]["title"].startswith("Moved to") and "Germany" in ms["move:2020-01"]["title"]
    assert "Jordan" in ms["trip-year:2019"]["title"] and "Japan" in ms["trip-year:2020"]["title"]
    assert ms["busy-day:2018"]["date"] == "2018-07-04" and "20 photos" in ms["busy-day:2018"]["detail"]
    chapters.set_milestone_state(conn, ms["busy-day:2018"]["id"], "dismissed")
    chapters.rebuild(conn)                                   # rebuild keeps your decision
    assert conn.execute("SELECT state FROM milestones WHERE key = 'busy-day:2018'").fetchone()[0] == "dismissed"
    assert all(m["key"] != "busy-day:2018" for m in chapters.timeline(conn)["milestones"])


def test_renamed_chapter_survives_rebuild(life):
    conn, _ = life
    cid = conn.execute("SELECT id FROM chapters ORDER BY start_at LIMIT 1").fetchone()[0]
    chapters.update_chapter(conn, cid, {"title": "The Dover years"})
    chapters.rebuild(conn)
    titles = [r[0] for r in conn.execute("SELECT title FROM chapters ORDER BY start_at")]
    assert titles[0] == "The Dover years" and len(titles) == 2
    with pytest.raises(ValueError):
        chapters.update_chapter(conn, cid, {"title": "   "})


def test_timeline_api_and_filters(life):
    conn, lib = life
    Settings(apple_id="me@example.com", library_path=str(lib)).save()
    app = App()
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    h = {"X-Token": app.token}
    get = lambda p: json.loads(urllib.request.urlopen(urllib.request.Request(base + p, headers=h)).read())  # noqa: E731
    try:
        t = get("/api/timeline")
        assert len(t["chapters"]) == 2 and t["months"]["2018-07"] >= 20
        assert any(m["kind"] == "move" for m in t["milestones"])
        cid = t["chapters"][-1]["id"]   # oldest = Delaware
        assert get(f"/api/photos?chapter={cid}")["total"] == t["chapters"][-1]["photo_count"]
        assert get("/api/photos?month=2018-07")["total"] == t["months"]["2018-07"]
        assert get("/api/photos?day=2018-07-04")["total"] == 20
    finally:
        srv.shutdown()
