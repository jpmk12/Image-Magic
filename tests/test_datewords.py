"""Plain-English dates and names in the search box."""
import json
import threading
import urllib.request
from datetime import date

from image_magic import browse, datewords, faces
from image_magic.server import App, make_server
from image_magic.settings import Settings
from test_ask import library  # noqa: F401

TODAY = date(2026, 11, 7)


def test_parse_dates_seasons_holidays(library):  # noqa: F811
    conn, lib = library
    p = datewords.parse(conn, "christmas 2019", TODAY)
    assert (p["from"], p["to"], p["q"]) == ("2019-12-20", "2019-12-27", "") and p["label"] == "Christmas 2019"
    p = datewords.parse(conn, "last summer at the beach", TODAY)
    assert (p["from"], p["to"], p["q"]) == ("2025-06-01", "2025-08-31", "beach")
    p = datewords.parse(conn, "winter 2019 snow", TODAY)
    assert (p["from"], p["to"], p["q"]) == ("2018-12-01", "2019-02-28", "snow")
    p = datewords.parse(conn, "March 2019 petra", TODAY)
    assert (p["from"], p["to"], p["q"]) == ("2019-03-01", "2019-03-31", "petra")
    p = datewords.parse(conn, "thanksgiving", TODAY)
    assert (p["from"], p["to"], p["q"]) == ("2019-11-27", "2019-12-01", "")       # every year in the library (just one here)
    p = datewords.parse(conn, "easter 2021", TODAY)
    assert p["from"] == "2021-04-02" and p["to"] == "2021-04-05"
    p = datewords.parse(conn, "2019", TODAY)
    assert (p["from"], p["to"]) == ("2019-01-01", "2019-12-31")
    assert datewords.parse(conn, "sunset over the desert", TODAY)["q"] == "sunset over desert"
    assert datewords.parse(conn, "sunset over the desert", TODAY).get("from") is None
    # date filters work in browse without any search model
    assert len(browse.ids(conn, {"from": "2019-03-10", "to": "2019-03-16"})) == 14     # the trip, its video, and a home photo in that week
    assert browse.ids(conn, {"days": [("2019-01-01", "2019-01-10"), ("2019-12-20", "2019-12-31")]})


def test_people_and_birthdays_and_moves(library):  # noqa: F811
    conn, lib = library
    conn.execute("INSERT INTO persons (id, name, birthday, created) VALUES (7, 'Emma', '2016-03-14', 'now')")
    conn.execute("INSERT INTO persons (id, name, created) VALUES (8, 'Jack', 'now')")
    conn.commit()
    p = datewords.parse(conn, "Emma's 3rd birthday", TODAY)
    assert p["person"] == 7 and (p["from"], p["to"]) == ("2019-03-11", "2019-03-17") and "3rd birthday" in p["label"]
    p = datewords.parse(conn, "emma and jack at the beach", TODAY)
    assert (p["person"], p["person2"], p["q"]) == (7, 8, "beach")
    p = datewords.parse(conn, "Emma's birthday", TODAY)
    assert p["birthday_md"] == "03-14"
    conn.execute("INSERT INTO chapters (title, start_at, end_at, photo_count) VALUES ('Dover', '2019-06-01', '2019-12-31', 10)")
    conn.commit()
    p = datewords.parse(conn, "the week we moved to dover", TODAY)
    assert (p["from"], p["to"]) == ("2019-05-25", "2019-06-08") and p["q"] == ""


def test_date_search_api_needs_no_model(library):  # noqa: F811
    conn, lib = library
    Settings(apple_id="x", library_path=str(lib)).save()
    app = App(); srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/photos?q=march+2019", headers={"X-Token": app.token})).read())
        assert r["total"] >= 12 and r["understood"] == "March 2019" and r["search"] == ""
    finally:
        srv.shutdown()
