import json
import os
import threading
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from image_magic import catalog, faces
from image_magic.server import App, make_server
from image_magic.settings import Settings


def test_similarity_transform_recovers_known_mapping():
    src = np.array([[10, 10], [50, 12], [30, 35], [15, 60], [48, 58]], np.float32)
    theta, s, t = 0.3, 1.7, np.array([5.0, -3.0])
    R = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    dst = (s * src @ R.T + t).astype(np.float32)
    M = faces.similarity_transform(src, dst)
    out = (M @ np.hstack([src, np.ones((5, 1))]).T).T
    assert np.allclose(out, dst, atol=1e-3)


def test_align_face_puts_landmarks_on_template():
    im = Image.new("RGB", (400, 300), (40, 60, 80))
    kps = faces.ARCFACE_DST * 2 + np.array([50, 30], np.float32)   # scaled & shifted template
    for x, y in kps:                      # 5x5 red dots so they survive the 2x downscale
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                im.putpixel((int(x) + dx, int(y) + dy), (255, 0, 0))
    out = faces.align_face(im, kps)
    assert out.size == (112, 112)
    px = np.asarray(out).astype(int)
    for x, y in faces.ARCFACE_DST:
        patch = px[int(y) - 1:int(y) + 2, int(x) - 1:int(x) + 2]
        assert (patch[..., 0] - patch[..., 2]).max() > 100, "red dot should land on the template point"


def test_nms_removes_overlaps():
    dets = np.array([[0, 0, 100, 100, 0.9], [5, 5, 105, 105, 0.8], [200, 200, 300, 300, 0.7]], np.float32)
    assert faces._nms(dets, 0.4) == [0, 2]


class FakeFaceModel:
    """Finds one 'face' per solid-coloured square; embeds by colour."""
    device = "CPU"

    def detect(self, im):
        arr = np.asarray(im)
        out = []
        for color in [(220, 20, 20), (20, 200, 20), (20, 20, 220)]:
            mask = np.all(np.abs(arr.astype(int) - color) < 30, axis=2)
            if mask.sum() < 100:
                continue
            ys, xs = np.where(mask)
            x1, y1, x2, y2 = xs.min(), ys.min(), xs.max(), ys.max()
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            kps = np.array([[cx - 10, cy - 10], [cx + 10, cy - 10], [cx, cy], [cx - 8, cy + 12], [cx + 8, cy + 12]], np.float32)
            out.append({"box": [float(x1), float(y1), float(x2), float(y2)], "score": 0.9, "kps": kps})
        return out

    def embed(self, aligned):
        vecs = []
        for a in aligned:
            c = np.asarray(a.convert("RGB")).reshape(-1, 3).mean(0)
            v = np.full(512, 0.01, np.float32)
            v[int(np.argmax(c))] = 1.0
            vecs.append(v / np.linalg.norm(v))
        return np.stack(vecs)


def make_photo(path, squares):
    """squares: list of (color, x, y, size)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (640, 480), (128, 128, 128))
    for color, x, y, size in squares:
        for dx in range(size):
            for dy in range(size):
                im.putpixel((x + dx, y + dy), color)
    im.putpixel((0, 0), (hash(str(path)) % 200, 0, 0))   # make files unique
    im.save(path, "PNG")


RED, GREEN, BLUE = (220, 20, 20), (20, 200, 20), (20, 20, 220)


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    make_photo(orig / "a.png", [(RED, 100, 100, 80)])
    make_photo(orig / "b.png", [(RED, 300, 150, 90), (GREEN, 50, 50, 80)])
    make_photo(orig / "c.png", [(GREEN, 200, 200, 85)])
    make_photo(orig / "d.png", [(BLUE, 200, 200, 85)])          # only one blue face: no group
    make_photo(orig / "e.png", [(RED, 10, 10, 60), (GREEN, 400, 300, 70)])
    make_photo(orig / "f.png", [])                                # no faces
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    return conn, lib


def test_scan_and_group(library):
    conn, lib = library
    model = FakeFaceModel()
    assert faces.scan_photos(conn, lib / "originals", model) == 6
    assert faces.scan_photos(conn, lib / "originals", model) == 0        # incremental
    c = faces.counts(conn)
    assert c["scanned"] == 6 and c["faces"] == 7
    g = faces.group_faces(conn)
    assert g["people"] == 2                                              # red, green; blue too rare
    ppl = faces.people(conn)
    assert sorted(p["photo_count"] for p in ppl) == [3, 3]
    assert all(p["cover_face"] for p in ppl)
    assert faces.group_faces(conn)["people"] == 2                        # stable on re-run


def test_user_actions(library):
    conn, lib = library
    faces.scan_photos(conn, lib / "originals", FakeFaceModel())
    faces.group_faces(conn)
    red, green = faces.people(conn)
    faces.rename_person(conn, red["id"], "Emma")
    assert faces.people(conn)[0]["name"] == "Emma"
    # a locked (named) face stays put even if regrouped
    faces.group_faces(conn)
    assert conn.execute("SELECT COUNT(*) FROM faces WHERE person_id = ?", (red["id"],)).fetchone()[0] == 3
    # move one face out, then merge groups
    fid = conn.execute("SELECT id FROM faces WHERE person_id = ? LIMIT 1", (green["id"],)).fetchone()[0]
    faces.move_face(conn, fid, None)
    assert conn.execute("SELECT person_id FROM faces WHERE id = ?", (fid,)).fetchone()[0] is None
    faces.merge_people(conn, green["id"], red["id"])
    ppl = faces.people(conn, include_hidden=True)
    assert len(ppl) == 1 and ppl[0]["name"] == "Emma" and ppl[0]["photo_count"] == 4
    faces.hide_person(conn, red["id"], True)
    assert faces.people(conn) == [] and len(faces.people(conn, include_hidden=True)) == 1


def test_people_api(library, monkeypatch):
    conn, lib = library
    faces.scan_photos(conn, lib / "originals", FakeFaceModel())
    faces.group_faces(conn)
    Settings(apple_id="me@example.com", library_path=str(lib), people=True).save()
    app = App()
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    h = {"X-Token": app.token}

    def get(path):
        return json.loads(urllib.request.urlopen(urllib.request.Request(base + path, headers=h)).read())

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={**h, "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())
    try:
        ppl = get("/api/people")
        assert len(ppl) == 2
        post("/api/people/rename", {"id": ppl[0]["id"], "name": "Emma"})
        assert get("/api/people")[0]["name"] == "Emma"
        photos = get(f"/api/photos?person={ppl[0]['id']}")
        assert photos["total"] == 3
        detail = get(f"/api/photo/{photos['items'][0]['id']}")
        assert any(f["name"] == "Emma" for f in detail["faces"])
        crop = urllib.request.urlopen(urllib.request.Request(
            f"{base}/media/face/{ppl[0]['cover_face']}?t={app.token}")).read()
        assert crop[:2] == b"\xff\xd8"
        st = get("/api/status")
        assert st["people"]["named"] == 1 and st["people_on"] is True
    finally:
        srv.shutdown()


@pytest.mark.skipif(not os.environ.get("IMAGE_MAGIC_TEST_FACE_DIR"),
                    reason="set IMAGE_MAGIC_TEST_FACE_DIR (models) and IMAGE_MAGIC_TEST_FACE_PICS")
def test_real_models_group_same_person(monkeypatch, tmp_path):
    monkeypatch.setattr(faces, "model_dir", lambda: Path(os.environ["IMAGE_MAGIC_TEST_FACE_DIR"]))
    pics = Path(os.environ["IMAGE_MAGIC_TEST_FACE_PICS"])
    lib = tmp_path / "lib"
    orig = lib / "originals"
    orig.mkdir(parents=True)
    for f in pics.iterdir():
        (orig / f.name).write_bytes(f.read_bytes())
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    model = faces.FaceModel()
    faces.scan_photos(conn, orig, model)
    faces.group_faces(conn)
    groups = {}
    for r in conn.execute("SELECT f.person_id, a.path FROM faces f JOIN assets a ON a.id = f.asset_id"):
        groups.setdefault(r["person_id"], set()).add(r["path"])
    obama = [g for g in groups.values() if "obama.jpg" in g]
    assert len(obama) == 1 and {"obama2.jpg", "two_people.jpg", "obama-240p.jpg"} <= obama[0]
    biden = [g for g in groups.values() if "biden.jpg" in g]
    assert len(biden) == 1 and "two_people.jpg" in biden[0] and "obama.jpg" not in biden[0]


def test_gpu_failure_falls_back_to_cpu(tmp_path, monkeypatch):
    """A DirectML-style failure mid-run switches both sessions to the CPU and the run completes."""
    import numpy as np
    from PIL import Image

    class FlakySession:
        def __init__(self, provider, fail_batches_over=1):
            self.provider, self.limit = provider, fail_batches_over
        def get_providers(self):
            return [self.provider]
        def get_outputs(self):
            return [type("O", (), {"name": f"o{i}"})() for i in range(9)]
        def run(self, outputs, inputs):
            x = inputs["input.1"]
            if self.provider == "DmlExecutionProvider" and x.shape[0] > self.limit:
                raise RuntimeError("[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : BatchNormalization ... The parameter is incorrect.")
            if x.shape[-1] == 112:
                return [np.ones((x.shape[0], 512), np.float32)]
            return [np.zeros((1,), np.float32)] * 9

    made = []
    def fake_session(path_name, test_input, providers):
        made.append(providers[0]); return FlakySession(providers[0])
    monkeypatch.setattr(faces.FaceModel, "_session", staticmethod(fake_session))
    monkeypatch.setattr(faces, "_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    m = faces.FaceModel()
    assert m.device == "GPU (DirectML)" and not m.fell_back
    # on DirectML faces are embedded one at a time, so a batch of 3 works on the GPU
    v = m.embed([Image.new("RGB", (112, 112))] * 3)
    assert v.shape == (3, 512) and not m.fell_back
    # a GPU error on the detector switches to the CPU and the call still returns
    m.det = FlakySession("DmlExecutionProvider", fail_batches_over=0)
    assert m.detect(Image.new("RGB", (200, 100))) == [] and m.fell_back and m.device == "CPU"
    assert made[-2:] == ["CPUExecutionProvider", "CPUExecutionProvider"]
