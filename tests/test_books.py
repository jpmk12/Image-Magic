"""Trip, year and event books."""
import pytest

from image_magic import ask, books, catalog
from test_events import _lib as events_lib
from test_ask import library  # noqa: F401


def test_trip_and_year_books(library):  # noqa: F811
    conn, lib = library
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    ask.save_story(conn, f"trip:{trip}", "Our week in Jordan.\nRed cliffs and tea.", "you", edited=True)
    f = books.trip_book(conn, lib / "originals", lib, trip)
    data = f.read_bytes()
    assert f.name == "Jordan-2019-trip-book.pdf" and data.startswith(b"%PDF") and b"/Count 8" in data   # cover + story + 6 days
    f2 = books.year_book(conn, lib / "originals", lib, "2019")
    assert f2.name == "2019-year-book.pdf" and f2.stat().st_size > 3000
    with pytest.raises(ValueError):
        books.year_book(conn, lib / "originals", lib, "1999")
    with pytest.raises(ValueError):
        books.make(conn, lib / "originals", lib, "nope", "1")


def test_event_book(tmp_path):
    conn = events_lib(tmp_path)
    eid = conn.execute("SELECT id FROM events ORDER BY photo_count DESC").fetchone()[0]
    f = books.make(conn, tmp_path / "lib" / "originals", tmp_path / "lib", "event", str(eid))
    assert f.suffix == ".pdf" and f.read_bytes().startswith(b"%PDF")


def test_life_book(library):  # noqa: F811
    conn, lib = library
    from image_magic import events
    events.rebuild(conn)
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    ask.save_story(conn, f"trip:{trip}", "Our week in Jordan.", "you", edited=True)
    f = books.make(conn, lib / "originals", lib, "life", "all")
    data = f.read_bytes()
    assert f.name.startswith("life-book-") and data.startswith(b"%PDF") and b"/Count" in data
    import re
    pages = int(re.search(rb"/Count (\d+)", data).group(1))
    assert pages >= 5          # cover, chapter text + photos, trip text + photos
