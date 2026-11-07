"""Expression indexes and the short query cache."""
import threading
import json
import urllib.request

from image_magic import catalog
from image_magic.server import App, make_server
from image_magic.settings import Settings
from test_places_browse import library  # noqa: F401


def test_date_filters_use_indexes(library):  # noqa: F811
    conn, _, _ = library
    names = {r[1] for r in conn.execute("PRAGMA index_list(assets)")}
    assert {"idx_assets_year", "idx_assets_month", "idx_assets_day", "idx_assets_md"} <= names
    plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN SELECT id FROM assets WHERE substr(taken_at, 1, 4) = '2019'"))
    assert "idx_assets_year" in plan


def test_timeline_is_cached_until_the_catalog_changes(library, monkeypatch):  # noqa: F811
    conn, _, lib = library
    Settings(apple_id="x", library_path=str(lib)).save()
    app = App()
    from image_magic import chapters
    calls = []
    real = chapters.timeline
    monkeypatch.setattr(chapters, "timeline", lambda c: (calls.append(1), real(c))[1])
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    get = lambda: json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/timeline", headers={"X-Token": app.token})).read())  # noqa: E731
    try:
        a = get(); b = get()
        assert a == b and len(calls) == 1
        conn.execute("UPDATE chapters SET title = 'Renamed' WHERE id = (SELECT MIN(id) FROM chapters)"); conn.commit()
        c = get()
        assert len(calls) == 2 and c["chapters"][-1]["title"] == "Renamed" or any(ch["title"] == "Renamed" for ch in c["chapters"])
    finally:
        srv.shutdown()
