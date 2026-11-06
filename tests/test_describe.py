import json
import threading
import urllib.request
from types import SimpleNamespace

import pytest
from PIL import Image

from image_magic import catalog, describe
from image_magic.server import App, make_server
from image_magic.settings import Settings


class FakeClient:
    """Answers like Claude would: a JSON description of the dominant colour."""
    def __init__(self, refuse_on=None, fail_on=None):
        self.calls = []
        self.refuse_on, self.fail_on = refuse_on or set(), fail_on or set()
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        img = next(c for c in kw["messages"][0]["content"] if c["type"] == "image")
        import base64, io
        im = Image.open(io.BytesIO(base64.b64decode(img["source"]["data"])))
        assert im.size[0] <= describe.SEND_PX and "exif" not in im.info
        r, g, b = [int(x) for x in im.resize((1, 1)).getpixel((0, 0))]
        color = "red" if r > g and r > b else "green" if g > b else "blue"
        n = len(self.calls)
        usage = SimpleNamespace(input_tokens=1400, output_tokens=150)
        if color in self.refuse_on:
            return SimpleNamespace(stop_reason="refusal", content=[], usage=usage)
        if color in self.fail_on:
            raise ConnectionError("boom")
        text = json.dumps({"caption": f"A {color} square on a grey background.", "keywords": [color, "square", "test"],
                           "setting": "other", "people_count": 0, "text": "", "quality": 2})
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


def make_photo(path, color, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (1600, 1200), color)
    exif = Image.Exif(); exif[271] = "Apple"
    exif.get_ifd(0x8825)[2] = (30.0, 0.0, 0.0)          # GPS that must NOT be sent
    im.putpixel((0, 0), (n, n, n))
    im.save(path, "JPEG", exif=exif)


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    for i, c in enumerate([(220, 20, 20), (220, 30, 30), (20, 200, 20), (20, 20, 220)]):
        make_photo(lib / f"originals/IMG_{i}.JPG", c, i)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, lib / "originals")
    return conn, lib


def test_prepared_image_is_small_and_stripped(library):
    conn, lib = library
    jpeg = describe.prepare_image(lib / "originals/IMG_0.JPG")
    import io
    im = Image.open(io.BytesIO(jpeg))
    assert max(im.size) == describe.SEND_PX and not im.getexif()


def test_describe_run_and_search(library):
    conn, lib = library
    client = FakeClient(refuse_on={"blue"})
    r = describe.run(conn, lib / "originals", client, "claude-haiku-5-5")
    assert r == {"described": 3, "refused": 1, "errors": 0, "total": 4}
    assert describe.run(conn, lib / "originals", client, "claude-haiku-5-5")["total"] == 0   # incremental
    kw = client.calls[0]
    assert kw["model"] == "claude-haiku-5-5" and kw["output_config"]["format"]["type"] == "json_schema"
    c = describe.counts(conn)
    assert c["described"] == 3 and c["spent_usd"] > 0
    assert len(describe.text_search(conn, "red square")) == 2
    assert describe.text_search(conn, "blue") == []
    d = describe.for_photo(conn, conn.execute("SELECT id FROM assets WHERE path = 'IMG_2.JPG'").fetchone()[0])
    assert d["caption"].startswith("A green") and "green" in d["keywords"]


def test_private_photos_are_never_sent(library):
    conn, lib = library
    aid = conn.execute("SELECT id FROM assets WHERE path = 'IMG_0.JPG'").fetchone()[0]
    describe.set_private(conn, aid, True)
    client = FakeClient()
    describe.run(conn, lib / "originals", client, "claude-haiku-5-5")
    assert len(client.calls) == 3
    assert describe.for_photo(conn, aid) is None
    describe.set_private(conn, aid, False)
    assert len(describe.pending(conn)) == 1


def test_errors_are_counted_and_retried_next_time(library):
    conn, lib = library
    r = describe.run(conn, lib / "originals", FakeClient(fail_on={"green"}), "claude-haiku-5-5")
    assert r["errors"] == 1 and r["described"] == 3
    assert len(describe.pending(conn)) == 1


def test_api_key_storage(tmp_path):
    assert describe.get_api_key() == "" and describe.key_hint() == ""
    describe.set_api_key("sk-ant-api03-abcdefghijklmnop")
    assert describe.get_api_key() == "sk-ant-api03-abcdefghijklmnop"
    assert describe.key_hint() == "sk-ant-…mnop"
    describe.set_api_key("")
    assert describe.get_api_key() == ""


def test_settings_api_hides_key(library, monkeypatch):
    conn, lib = library
    Settings(apple_id="me@example.com", library_path=str(lib)).save()
    app = App()
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    h = {"X-Token": app.token, "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(base + "/api/settings", data=json.dumps(
            {"apple_id": "me@example.com", "library_path": str(lib), "anthropic_api_key": "sk-ant-api03-secretsecret1234",
             "ai_model": "claude-sonnet-5-5"}).encode(), headers=h)
        saved = json.loads(urllib.request.urlopen(req).read())
        assert "anthropic_api_key" not in saved and saved["ai_model"] == "claude-sonnet-5-5"
        got = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/settings", headers=h)).read())
        assert got["api_key_hint"] == "sk-ant-…1234" and "secretsecret" not in json.dumps(got)
        st = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/status", headers=h)).read())
        assert st["ai"]["has_key"] and st["ai"]["estimate_usd"] > 0
        # private toggle via API
        aid = conn.execute("SELECT id FROM assets LIMIT 1").fetchone()[0]
        urllib.request.urlopen(urllib.request.Request(base + "/api/private", data=json.dumps({"id": aid, "private": True}).encode(), headers=h))
        d = json.loads(urllib.request.urlopen(urllib.request.Request(f"{base}/api/photo/{aid}", headers=h)).read())
        assert d["private"] is True
    finally:
        srv.shutdown()
