import json
import threading
import urllib.request
from types import SimpleNamespace

import pytest

from image_magic import ask, catalog, describe
from image_magic.server import App, make_server
from image_magic.settings import Settings
from test_places_browse import HOME, PETRA, make_photo
from datetime import datetime, timedelta


@pytest.fixture
def library(tmp_path):
    orig = tmp_path / "lib" / "originals"
    n = 0
    for day in range(0, 300, 3):
        make_photo(orig / f"home/IMG_{n:04d}.JPG", datetime(2019, 1, 5, 12) + timedelta(days=day), HOME); n += 1
    for i in range(12):
        make_photo(orig / f"jo/IMG_{n:04d}.JPG", datetime(2019, 3, 10, 9) + timedelta(hours=12 * i), PETRA); n += 1
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    catalog.scan(conn, orig)
    describe.ensure_schema(conn)
    for r in conn.execute("SELECT id FROM assets WHERE path LIKE 'jo/%' LIMIT 3").fetchall():
        conn.execute("INSERT INTO descriptions (asset_id, model, caption, keywords, created) VALUES (?, 'm', "
                     "'Red sandstone cliffs at Petra.', '[\"petra\",\"desert\",\"cliffs\"]', 'now')", (r[0],))
    conn.commit()
    return conn, tmp_path / "lib"


def test_story_context_is_text_only_and_compact(library):
    conn, _ = library
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    ctx = ask.story_context(conn, "trip", trip)
    assert ctx["title"] == "Jordan" and ctx["what"] == "trip"
    assert [d["places"][0] for d in ctx["days"]][0].startswith("Petra")
    assert len(ctx["sample_captions"]) == 3
    s = json.dumps(ctx)
    assert "base64" not in s and len(s) < 20000
    ch = conn.execute("SELECT id FROM chapters").fetchone()[0]
    assert ask.story_context(conn, "chapter", ch)["what"] == "chapter of life"
    with pytest.raises(ValueError):
        ask.story_context(conn, "trip", 999)


class StoryClient:
    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create)); self.calls = []
    def create(self, **kw):
        self.calls.append(kw)
        assert kw["fallbacks"] == "default" and "image" not in json.dumps(kw["messages"])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="thinking", thinking=""),
                               SimpleNamespace(type="text", text="You spent a week among the red cliffs of Petra.")])


def test_write_and_edit_story(library):
    conn, _ = library
    trip = conn.execute("SELECT id FROM trips").fetchone()[0]
    text = ask.write_story(StoryClient(), ask.story_context(conn, "trip", trip))
    assert text.startswith("You spent")
    ask.save_story(conn, f"trip:{trip}", text, ask.STORY_MODEL, edited=False)
    assert ask.get_story(conn, f"trip:{trip}")["edited"] == 0
    ask.save_story(conn, f"trip:{trip}", "My own words.", "you", edited=True)
    assert ask.get_story(conn, f"trip:{trip}")["text"] == "My own words."


class AskClient:
    """Scripted Claude: summary -> search -> show -> answer."""
    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create)); self.n = 0; self.seen = []
    def create(self, **kw):
        self.n += 1
        last = kw["messages"][-1]
        if isinstance(last["content"], list):
            self.seen.append([json.loads(c["content"]) if not c.get("is_error") else c["content"] for c in last["content"]])
        tu = lambda name, inp: SimpleNamespace(type="tool_use", id=f"t{self.n}", name=name, input=inp)  # noqa: E731
        if self.n == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[tu("library_summary", {})])
        if self.n == 2:
            return SimpleNamespace(stop_reason="tool_use", content=[tu("search_photos", {
                "query": "cliffs", "year": "", "month": "", "day": "", "country": "JO", "city": "", "person": "",
                "trip_id": 0, "chapter_id": 0, "limit": 5})])
        if self.n == 3:
            ids = [p["id"] for p in self.seen[-1][0]["photos"]]
            return SimpleNamespace(stop_reason="tool_use", content=[tu("show_photos", {"ids": ids})])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="You were in Petra, Jordan, in March 2019.")])


def test_ask_uses_tools_and_returns_photos(library, monkeypatch):
    conn, lib = library
    settings = Settings(apple_id="x", library_path=str(lib))
    monkeypatch.setattr(ask.search, "model_ready", lambda: False, raising=False) if hasattr(ask, "search") else None
    from image_magic.server import find_photos
    asker = ask.Asker(conn, settings, find_photos)
    client = AskClient()
    r = asker.ask(client, "When was I in Jordan?")
    assert r["answer"].startswith("You were in Petra")
    assert len(r["photos"]) == 3                      # the three captioned Petra photos
    summary = client.seen[0][0]
    assert summary["trips"][0]["title"] == "Jordan" and summary["years"][0]["year"] == "2019"
    assert r["history"][-1]["content"].startswith("You were")
    # unknown person is reported, not crashed
    assert "No person named" in asker.run_tool("search_photos", {"person": "Nobody", "query": ""})


def test_ask_api_requires_key(library):
    conn, lib = library
    Settings(apple_id="me@example.com", library_path=str(lib)).save()
    app = App()
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    h = {"X-Token": app.token, "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(base + "/api/ask", data=json.dumps({"question": "hi"}).encode(), headers=h)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400 and b"API key" in e.value.read()
        trip = conn.execute("SELECT id FROM trips").fetchone()[0]
        req = urllib.request.Request(base + "/api/story/save", data=json.dumps({"kind": "trip", "id": trip, "text": "Mine"}).encode(), headers=h)
        assert json.loads(urllib.request.urlopen(req).read())["text"] == "Mine"
        got = json.loads(urllib.request.urlopen(urllib.request.Request(f"{base}/api/story?kind=trip&id={trip}", headers=h)).read())
        assert got["edited"] == 1
    finally:
        srv.shutdown()
