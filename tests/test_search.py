import json
import os
import threading
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from image_magic import catalog, search
from image_magic.clip_tokenizer import Tokenizer
from image_magic.jobs import JobRunner
from image_magic.server import App, make_server
from image_magic.settings import Settings


def test_tokenizer_matches_openai_clip():
    t = Tokenizer()
    # Reference ids from OpenAI's tokenizer.
    assert t.encode("a photo of a cat") == [320, 1125, 539, 320, 2368]
    ids, mask = t(["a photo of a cat"])
    assert ids[0][:7] == [49406, 320, 1125, 539, 320, 2368, 49407]
    assert len(ids[0]) == 77 and sum(mask[0]) == 7
    long_ids, _ = t(["word " * 200])
    assert len(long_ids[0]) == 77 and long_ids[0][-1] == t.eot


def test_preprocess_shape_and_crop():
    arr = search.preprocess(Image.new("RGB", (640, 480), (255, 0, 0)))
    assert arr.shape == (3, 224, 224) and arr.dtype == np.float32
    assert arr[0].mean() > 1.5 and arr[1].mean() < -1.5   # red after CLIP normalization


class FakeModel:
    """Maps a photo's dominant colour, and colour words, to fixed vectors."""
    device = "CPU"
    COLORS = {"red": 0, "green": 1, "blue": 2}

    def _vec(self, idx):
        v = np.full(512, 0.01, dtype=np.float32)
        v[idx] = 1.0
        return v / np.linalg.norm(v)

    def encode_images(self, images):
        out = []
        for im in images:
            r, g, b = [np.asarray(im.convert("RGB"))[..., c].mean() for c in range(3)]
            out.append(self._vec(int(np.argmax([r, g, b]))))
        return np.stack(out)

    def encode_text(self, texts):
        out = []
        for t in texts:
            idx = next((i for w, i in self.COLORS.items() if w in t), 10)
            out.append(self._vec(idx))
        return np.stack(out)


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    for i, color in enumerate(["red"] * 3 + ["green"] * 2 + ["blue"]):
        p = orig / f"2020/IMG_{i}.PNG"
        p.parent.mkdir(parents=True, exist_ok=True)
        im = Image.new("RGB", (64, 48), {"red": (220, 20, 20), "green": (20, 200, 20), "blue": (20, 20, 220)}[color])
        im.putpixel((0, 0), (i, i, i))   # make each file unique (identical files are duplicates)
        im.save(p)
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    return conn, lib


def test_index_and_search(library):
    conn, lib = library
    model = FakeModel()
    assert search.counts(conn)["indexed"] == 0
    assert search.index_photos(conn, lib, lib / "originals", model) == 6
    assert search.index_photos(conn, lib, lib / "originals", model) == 0      # incremental
    assert search.counts(conn)["indexed"] == 6
    reds = search.search(conn, "k", model, "red things")
    assert len(reds) == 3 and all(s > 0.9 for _, s in reds)
    assert len(search.search(conn, "k", model, "green")) == 2
    assert search.search(conn, "k", model, "submarine") == []                 # nothing matches
    one = {reds[0][0]}
    assert [i for i, _ in search.search(conn, "k", model, "red", allowed_ids=one)] == [reds[0][0]]


def test_search_api(library, monkeypatch):
    conn, lib = library
    model = FakeModel()
    search.index_photos(conn, lib, lib / "originals", model)
    Settings(apple_id="me@example.com", library_path=str(lib), smart_search=True).save()
    monkeypatch.setattr(search, "model_ready", lambda: True)
    monkeypatch.setattr(search.Model, "get", classmethod(lambda cls: model))
    app = App()
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        req = urllib.request.Request(base + "/api/photos?q=green", headers={"X-Token": app.token})
        res = json.loads(urllib.request.urlopen(req).read())
        assert res["total"] == 2 and res["search"] == "green" and res["items"][0]["score"] > 0.9
        req = urllib.request.Request(base + "/api/status", headers={"X-Token": app.token})
        st = json.loads(urllib.request.urlopen(req).read())
        assert st["search"]["indexed"] == 6 and st["smart_search"] is True
    finally:
        srv.shutdown()


def test_download_verifies_hash(tmp_path, monkeypatch):
    """A corrupted download must be rejected and deleted."""
    import http.server
    payload = b"not the real model"

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/m.onnx"
    monkeypatch.setattr(search, "MODEL_FILES", {"m.onnx": (url, len(payload), "0" * 64)})
    with pytest.raises(RuntimeError, match="integrity"):
        search.download_model()
    assert not any(search.model_dir().iterdir())
    srv.shutdown()


@pytest.mark.skipif(not os.environ.get("IMAGE_MAGIC_TEST_MODEL_DIR"),
                    reason="set IMAGE_MAGIC_TEST_MODEL_DIR to the folder with the real CLIP model")
def test_real_model_ranks_sample_photos(monkeypatch):
    monkeypatch.setattr(search, "model_dir", lambda: Path(os.environ["IMAGE_MAGIC_TEST_MODEL_DIR"]))
    imgs = Path(os.environ["IMAGE_MAGIC_TEST_IMAGES"])
    files = sorted(imgs.iterdir())
    model = search.Model()
    vecs = model.encode_images([Image.open(f) for f in files])
    for query, expected in [("cat", "chelsea"), ("cup of coffee", "coffee"), ("horse", "horse"),
                            ("rocket launch", "rocket"), ("astronaut", "astronaut")]:
        best = files[int(np.argmax(vecs @ search.query_vector(model, query)))].stem
        assert best == expected, (query, best)
