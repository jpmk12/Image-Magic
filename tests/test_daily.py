"""Memory of the day and the week ahead."""
from datetime import date

from image_magic import browse, catalog, daily, kids
from test_places_browse import library  # noqa: F401


def test_memory_and_week(library):  # noqa: F811
    conn, _, _ = library
    m = daily.memory_of_day(conn, date(2026, 3, 10))
    assert m and m["kind_of"] == "on_this_day" and m["headline"].startswith("7 years ago today") and m["taken_at"].startswith("2019-03-10")
    m2 = daily.memory_of_day(conn, date(2026, 12, 31))
    assert m2 and m2["kind_of"] == "shuffle" and m2["headline"].startswith("From ")
    assert daily.memory_of_day(conn, date(2026, 12, 31))["id"] == m2["id"]      # the same pick all day
    w = daily.week_ahead(conn, date(2026, 3, 8))
    assert w["days"] and any(d["date"] == "2026-03-10" for d in w["days"]) and w["trips"][0]["title"] == "Jordan"
    conn.execute("INSERT INTO persons (id, name, birthday, created) VALUES (9, 'Emma', '2016-03-12', 'now')"); conn.commit()
    w = daily.week_ahead(conn, date(2026, 3, 8))
    assert w["birthdays"] == [{"id": 9, "name": "Emma", "date": "2026-03-12", "turns": 10}]
    assert len(browse.ids(conn, {"days_md": "03-10"})) >= 2
    assert daily.notify("t", "b") in (True, False)
