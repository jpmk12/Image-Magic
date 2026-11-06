from datetime import date

from image_magic import catalog, review
from test_memories import build


def test_year_stats(tmp_path):
    conn, _ = build(tmp_path)
    ys = review.years(conn)
    assert ys[0] == "2021" and "2017" in ys
    s = review.year_stats(conn, "2018")
    assert s["photos"] == 19 and s["videos"] == 0
    assert [c["code"] for c in s["countries"]] == ["US", "JO"]
    assert s["new_countries"] == ["Jordan"]           # first visit to Jordan was 2018
    assert s["km"] > 9000                             # Delaware -> Petra and back
    assert s["busiest_day"]["n"] >= 1 and sum(s["months"]) == 19
    assert 1 <= len(s["best"]) <= 12
    assert review.year_stats(conn, "2021")["new_countries"] == []


def test_best_of_year_spreads_across_the_year(tmp_path):
    conn, _ = build(tmp_path)
    best = review.best_of_year(conn, "2019", 6)
    months = [b["taken_at"][5:7] for b in best]
    assert len(best) == 6 and months == sorted(months) and len(set(months)) >= 4


def test_sun_times_known_values():
    # Dover, Delaware on the March equinox: sunrise ~07:05, sunset ~19:12 local (UTC-4 for EDT)
    s = review.sun_times(39.13, -75.47, date(2019, 3, 20), tz_hours=-4)
    h, m = map(int, s["sunrise"].split(":"))
    assert (h, abs(m - 5) <= 8) == (7, True)
    h, m = map(int, s["sunset"].split(":"))
    assert h == 19 and abs(m - 12) <= 8
    # polar night: no sunrise
    assert review.sun_times(78.2, 15.6, date(2019, 12, 21), tz_hours=1)["sunrise"] is None


def test_weather_cached_and_optional(tmp_path):
    conn, _ = build(tmp_path)
    calls = []

    def fake(lat, lon, day):
        calls.append(day)
        return {"daily": {"temperature_2m_max": [21.4], "temperature_2m_min": [9.0], "weather_code": [2], "precipitation_sum": [0.0]}}
    aid = conn.execute("SELECT id FROM assets WHERE path LIKE 'p/%' LIMIT 1").fetchone()[0]
    off = review.context(conn, aid, weather_online=False, fetch=fake)
    assert "sun" in off and "weather" not in off and calls == []
    on = review.context(conn, aid, weather_online=True, fetch=fake)
    assert on["weather"]["text"] == "partly cloudy" and on["weather"]["tmax_c"] == 21.4
    review.context(conn, aid, weather_online=True, fetch=fake)
    assert len(calls) == 1                            # second lookup served from cache

    def broken(lat, lon, day):
        raise OSError("offline")
    other = conn.execute("SELECT id FROM assets WHERE path LIKE 'h/%' LIMIT 1").fetchone()[0]
    assert review.context(conn, other, weather_online=True, fetch=broken).get("weather") is None
