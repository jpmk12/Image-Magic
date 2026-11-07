"""Place stories and the world summary."""
import pytest

from image_magic import placestory
from test_places_browse import library  # noqa: F401


def test_world_and_place(library):  # noqa: F811
    conn, _, _ = library
    w = placestory.world(conn)
    codes = [c["code"] for c in w["countries"]]
    assert codes[0] == "US" and set(codes) == {"US", "JO", "JP"} and w["total_countries"] == 3
    jo = next(c for c in w["countries"] if c["code"] == "JO")
    assert jo["year_list"] == ["2019"] and jo["cities"] == 2 and jo["home_km"] and jo["home_km"] > 9000
    assert w["farthest"]["place"].endswith("Japan") and w["farthest"]["km"] > 10000
    assert w["by_year"]["2019"] and w["returned_to"][0]["country_code"] in ("JO", "JP")
    p = placestory.place(conn, "JO")
    assert p["country"] == "Jordan" and p["photos"] == 15 and p["years"] == ["2019"]   # photos only, not the trip video
    assert [c["name"] for c in p["cities"]][:2] == ["Petra", "Amman"] and p["trips"][0]["title"] == "Jordan" and p["trips"][0]["here"] == 12
    assert len(p["best"]) >= 5 and p["centre"]
    pc = placestory.place(conn, "JO", "Amman")
    assert pc["photos"] == 5 and pc["city"] == "Amman"
    us = placestory.place(conn, "US")
    assert us["lived"] and us["lived"][0]["title"].startswith("Dover")
    with pytest.raises(ValueError):
        placestory.place(conn, "ZZ")
