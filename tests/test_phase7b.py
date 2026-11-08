"""Phase 7: stories for events and years (and in the books), share as one zip file."""
import zipfile

import pytest

from datetime import datetime, timedelta

from image_magic import ask, books, catalog, events, share
from test_ask import StoryClient, library  # noqa: F401
from test_places_browse import HOME, make_photo


def _with_event(conn, lib):
    """The ask fixture has no photos close enough in time to form an event; add an afternoon of them."""
    for i in range(6):
        make_photo(lib / "originals" / f"party/IMG_{i}.JPG", datetime(2019, 6, 8, 14) + timedelta(minutes=15 * i), HOME)
    catalog.scan(conn, lib / "originals")
    events.rebuild(conn)


def test_event_and_year_story_context(library):  # noqa: F811
    conn, lib = library
    _with_event(conn, lib)
    ev = conn.execute("SELECT id, title FROM events ORDER BY photo_count DESC LIMIT 1").fetchone()
    ctx = ask.story_context(conn, "event", ev["id"])
    assert ctx["title"] == ev["title"] and ctx["what"].startswith("event") and ctx["days"]
    y = ask.story_context(conn, "year", 2019)
    assert y["what"] == "year" and y["title"] == "2019" and y["photos"] > 100
    assert [t["title"] for t in y["trips"]] == ["Jordan"] and "Jordan" in y["countries"]
    with pytest.raises(ValueError):
        ask.story_context(conn, "year", 1990)
    with pytest.raises(ValueError):
        ask.story_context(conn, "event", 99999)
    # written the same way as trips, saved under event:/year: keys
    text = ask.write_story(StoryClient(), y)
    ask.save_story(conn, "year:2019", text, ask.STORY_MODEL, edited=False)
    ask.save_story(conn, f"event:{ev['id']}", "The day at the cliffs.", "you", edited=True)
    assert ask.get_story(conn, "year:2019")["text"].startswith("You spent")


def test_books_carry_event_and_year_stories(library):  # noqa: F811
    conn, lib = library
    _with_event(conn, lib)
    ev = conn.execute("SELECT id FROM events ORDER BY photo_count DESC LIMIT 1").fetchone()[0]
    ask.save_story(conn, f"event:{ev}", "The day at the cliffs.", "you", edited=True)
    ask.save_story(conn, "year:2019", "A year of red rock and home.", "you", edited=True)
    for kind, ident in (("event", str(ev)), ("year", "2019"), ("life", "all")):
        f = books.make(conn, lib / "originals", lib, kind, ident)
        assert f.is_file() and f.stat().st_size > 1000, kind
        data = f.read_bytes()
        assert b"/Type /Page" in data


def test_share_zip(library):  # noqa: F811
    conn, lib = library
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    folder = share.export(conn, lib / "originals", lib, {"trip": trip}, "Jordan zip")
    z = share.zip_folder(folder)
    assert z.name == "share-jordan-zip.zip" and z.parent == folder.parent
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        assert "share-jordan-zip/index.html" in names and "share-jordan-zip/README.txt" in names
        assert sum(n.endswith(".jpg") for n in names) == 12
        assert zf.getinfo("share-jordan-zip/images/0001.jpg").compress_type == zipfile.ZIP_STORED
        assert zf.testzip() is None
