"""People: find faces, turn each into a face signature, and group them by person.

Models (InsightFace "buffalo_l" pack, MIT-licensed code; models for non-commercial
research use): SCRFD-10G for detection with 5 landmarks, and ArcFace ResNet-50
(w600k_r50) for 512-d face embeddings. Both run locally with ONNX Runtime; photos
never leave the computer.

Only the two needed models are kept from the ~290 MB pack download, which is
verified against a pinned SHA-256 before anything is extracted.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps

from . import metadata  # noqa: F401  (HEIC opener)
from .settings import settings_dir

MODEL_NAME = "buffalo_l"
PACK_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
PACK_SIZE = 288621354
PACK_SHA256 = "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f"
MODEL_FILES = {
    "det_10g.onnx": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "w600k_r50.onnx": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
}

DET_SIZE = 640            # detector input (letterboxed square)
WORK_PX = 1600            # photos are downscaled to this before detection
DET_THRESHOLD = 0.5
NMS_THRESHOLD = 0.4
MIN_FACE_PX = 24          # smaller faces (in the WORK_PX image) give unreliable signatures
STRIDES = (8, 16, 32)
ANCHORS_PER_CELL = 2
EMBED_BATCH = 32

# Where the 5 landmarks go in a 112x112 aligned face (ArcFace standard template).
ARCFACE_DST = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

# Grouping thresholds (cosine similarity of ArcFace embeddings).
JOIN_THRESHOLD = 0.45     # a new face joins a person if this close to that person's centre
MERGE_THRESHOLD = 0.55    # two unnamed groups this close are merged
MIN_GROUP = 2             # a person needs at least this many faces to be shown


SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id         INTEGER PRIMARY KEY,
    name       TEXT,                 -- NULL until you name them
    hidden     INTEGER NOT NULL DEFAULT 0,
    cover_face INTEGER,
    created    TEXT
);
CREATE TABLE IF NOT EXISTS faces (
    id         INTEGER PRIMARY KEY,
    asset_id   INTEGER NOT NULL REFERENCES assets(id),
    x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,  -- fractions of the image
    score      REAL NOT NULL,
    kps        TEXT NOT NULL,        -- 5 landmarks as fractions, JSON
    size_px    INTEGER NOT NULL,     -- face width in the detection image
    vec        BLOB,                 -- 512 x float16, normalized; NULL if face too small
    person_id  INTEGER REFERENCES persons(id),
    locked     INTEGER NOT NULL DEFAULT 0   -- 1 = you confirmed/changed this assignment
);
CREATE INDEX IF NOT EXISTS idx_faces_asset ON faces(asset_id);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);
CREATE TABLE IF NOT EXISTS face_scans (
    asset_id   INTEGER PRIMARY KEY REFERENCES assets(id),
    model      TEXT NOT NULL,
    n_faces    INTEGER NOT NULL
);
"""


# ---- model files -----------------------------------------------------------

def model_dir() -> Path:
    return settings_dir() / "models" / MODEL_NAME


def model_ready() -> bool:
    d = model_dir()
    return all((d / n).is_file() for n in MODEL_FILES)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def download_model(progress: Callable[[int, int], None] | None = None,
                   cancelled: Callable[[], bool] = lambda: False) -> None:
    """Download the model pack (resumable), verify it, keep the two models we use."""
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    if model_ready():
        return
    part = d / "pack.zip.part"
    have = part.stat().st_size if part.exists() else 0
    if have < PACK_SIZE:
        req = urllib.request.Request(PACK_URL, headers={"User-Agent": "ImageMagic"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        with urllib.request.urlopen(req, timeout=60) as resp:
            if have and resp.status != 206:
                have = 0
            with part.open("ab" if have else "wb") as f:
                while chunk := resp.read(1 << 20):
                    if cancelled():
                        return
                    f.write(chunk)
                    have += len(chunk)
                    if progress:
                        progress(have, PACK_SIZE)
    if _sha256(part) != PACK_SHA256:
        part.unlink(missing_ok=True)
        raise RuntimeError("The face model download failed its integrity check and was deleted. Please try again.")
    with zipfile.ZipFile(part) as z:
        for name, sha in MODEL_FILES.items():
            info = z.getinfo(name)                       # raises if missing
            target = d / name
            with z.open(info) as src, target.open("wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            if _sha256(target) != sha:
                target.unlink(missing_ok=True)
                raise RuntimeError(f"{name} failed its integrity check.")
    part.unlink(missing_ok=True)


# ---- geometry ----------------------------------------------------------------

def similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares similarity (scale+rotation+translation) mapping src -> dst.

    Umeyama's method, as in scikit-image. Returns a 2x3 matrix M with
    dst ~= M @ [x, y, 1].
    """
    n = src.shape[0]
    mu_s, mu_d = src.mean(0), dst.mean(0)
    sc, dc = src - mu_s, dst - mu_d
    cov = dc.T @ sc / n
    U, S, Vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        d[1] = -1
    R = U @ np.diag(d) @ Vt
    var_s = (sc ** 2).sum() / n
    scale = (S * d).sum() / var_s
    t = mu_d - scale * (R @ mu_s)
    return np.hstack([scale * R, t[:, None]]).astype(np.float32)


def align_face(im: Image.Image, kps: np.ndarray, size: int = 112) -> Image.Image:
    """Crop and straighten a face so the eyes/nose/mouth land on the template."""
    M = similarity_transform(kps.astype(np.float32), ARCFACE_DST * (size / 112.0))
    inv = np.linalg.inv(np.vstack([M, [0, 0, 1]]))
    a, b, c, d, e, f = inv[0, 0], inv[0, 1], inv[0, 2], inv[1, 0], inv[1, 1], inv[1, 2]
    return im.transform((size, size), Image.AFFINE, (a, b, c, d, e, f), resample=Image.BILINEAR)


def _nms(dets: np.ndarray, thresh: float) -> list[int]:
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = np.argsort(-scores, kind="stable")
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1, yy1 = np.maximum(x1[i], x1[order[1:]]), np.maximum(y1[i], y1[order[1:]])
        xx2, yy2 = np.minimum(x2[i], x2[order[1:]]), np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


# ---- models ------------------------------------------------------------------

def _providers() -> list[str]:
    import onnxruntime as ort
    available = ort.get_available_providers()
    preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    return [p for p in preferred if p in available] or available


class FaceModel:
    _instance: "FaceModel | None" = None
    _guard = threading.Lock()

    def __init__(self) -> None:
        import onnxruntime as ort
        d = model_dir()
        providers = _providers()

        def session(name: str, test_input: dict):
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            if "DmlExecutionProvider" in providers:
                opts.enable_mem_pattern = False
                opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            try:
                s = ort.InferenceSession(str(d / name), opts, providers=providers)
                s.run(None, test_input)
                return s
            except Exception:
                cpu = ort.SessionOptions()
                cpu.log_severity_level = 3
                return ort.InferenceSession(str(d / name), cpu, providers=["CPUExecutionProvider"])

        self.det = session("det_10g.onnx", {"input.1": np.zeros((1, 3, DET_SIZE, DET_SIZE), np.float32)})
        self.rec = session("w600k_r50.onnx", {"input.1": np.zeros((1, 3, 112, 112), np.float32)})
        used = self.det.get_providers()[0]
        self.device = {"CUDAExecutionProvider": "GPU (CUDA)", "DmlExecutionProvider": "GPU (DirectML)"}.get(used, "CPU")
        self._det_out = [o.name for o in self.det.get_outputs()]
        self._centers: dict[int, np.ndarray] = {}
        for s in STRIDES:
            n = DET_SIZE // s
            c = np.stack(np.mgrid[:n, :n][::-1], axis=-1).astype(np.float32).reshape(-1, 2) * s
            self._centers[s] = np.repeat(c, ANCHORS_PER_CELL, axis=0)
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "FaceModel":
        with cls._guard:
            if cls._instance is None:
                cls._instance = FaceModel()
            return cls._instance

    def detect(self, im: Image.Image) -> list[dict]:
        """Faces in an RGB image: boxes/landmarks in that image's pixel coordinates."""
        w, h = im.size
        scale = DET_SIZE / max(w, h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        canvas = Image.new("RGB", (DET_SIZE, DET_SIZE))
        canvas.paste(im.resize((nw, nh), Image.BILINEAR), (0, 0))
        blob = ((np.asarray(canvas, dtype=np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        with self._lock:
            outs = self.det.run(self._det_out, {"input.1": blob})
        dets, kpss = [], []
        for i, s in enumerate(STRIDES):
            scores = outs[i].reshape(-1)
            keep = np.where(scores >= DET_THRESHOLD)[0]
            if not len(keep):
                continue
            c = self._centers[s][keep]
            bb = outs[i + 3][keep] * s
            kp = outs[i + 6][keep] * s
            boxes = np.stack([c[:, 0] - bb[:, 0], c[:, 1] - bb[:, 1], c[:, 0] + bb[:, 2], c[:, 1] + bb[:, 3]], 1)
            pts = np.stack([c[:, 0:1] + kp[:, 0::2], c[:, 1:2] + kp[:, 1::2]], axis=2)  # (n,5,2)
            dets.append(np.hstack([boxes, scores[keep, None]]))
            kpss.append(pts)
        if not dets:
            return []
        dets_a, kps_a = np.vstack(dets), np.vstack(kpss)
        order = np.argsort(-dets_a[:, 4], kind="stable")
        dets_a, kps_a = dets_a[order], kps_a[order]
        keep = _nms(dets_a, NMS_THRESHOLD)
        faces = []
        for i in keep:
            box = dets_a[i, :4] / scale
            faces.append({
                "box": [float(v) for v in box],
                "score": float(dets_a[i, 4]),
                "kps": (kps_a[i] / scale).astype(np.float32),
            })
        return faces

    def embed(self, aligned: list[Image.Image]) -> np.ndarray:
        arr = np.stack([(np.asarray(a.convert("RGB"), dtype=np.float32) - 127.5) / 127.5 for a in aligned])
        blob = arr.transpose(0, 3, 1, 2)
        with self._lock:
            out = self.rec.run(None, {"input.1": blob})[0].astype(np.float32)
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-8)


def load_for_faces(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((WORK_PX, WORK_PX))
        return im.copy()


# ---- catalog integration -------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> None:
    from .catalog import run_script
    run_script(conn, SCHEMA)


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    ensure_schema(conn)
    return conn.execute(
        """SELECT a.id, a.path FROM assets a
           LEFT JOIN face_scans s ON s.asset_id = a.id AND s.model = ?
           WHERE s.asset_id IS NULL AND a.kind = 'photo' AND a.missing = 0 AND a.duplicate_of IS NULL
           ORDER BY a.taken_at DESC""", (MODEL_NAME,)).fetchall()


def counts(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    photos = conn.execute("SELECT COUNT(*) FROM assets WHERE kind = 'photo' AND missing = 0 "
                          "AND duplicate_of IS NULL").fetchone()[0]
    scanned = conn.execute("SELECT COUNT(*) FROM face_scans s JOIN assets a ON a.id = s.asset_id "
                           "WHERE s.model = ? AND a.missing = 0", (MODEL_NAME,)).fetchone()[0]
    faces = conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
    people = conn.execute("SELECT COUNT(*) FROM persons WHERE hidden = 0").fetchone()[0]
    named = conn.execute("SELECT COUNT(*) FROM persons WHERE name IS NOT NULL AND hidden = 0").fetchone()[0]
    return {"scanned": scanned, "photos": photos, "faces": faces, "people": people,
            "named": named, "model_ready": model_ready()}


def scan_photos(conn: sqlite3.Connection, originals: Path, model: FaceModel,
                progress: Callable[[int, int], None] | None = None,
                cancelled: Callable[[], bool] = lambda: False) -> int:
    """Detect and embed faces in photos not yet scanned. Returns photos scanned."""
    rows = pending(conn)
    done = 0
    batch_faces: list[tuple[int, dict, Image.Image | None, tuple[int, int]]] = []

    def flush():
        to_embed = [(i, f) for i, (_, f, al, _) in enumerate(batch_faces) if al is not None]
        vecs = model.embed([batch_faces[i][2] for i, _ in to_embed]).astype(np.float16) if to_embed else None
        vec_by_index = {i: vecs[k] for k, (i, _) in enumerate(to_embed)} if vecs is not None else {}
        for i, (asset_id, f, _, (w, h)) in enumerate(batch_faces):
            x1, y1, x2, y2 = f["box"]
            kps = [[float(x) / w, float(y) / h] for x, y in f["kps"]]
            conn.execute(
                "INSERT INTO faces (asset_id, x1, y1, x2, y2, score, kps, size_px, vec) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (asset_id, max(0, x1 / w), max(0, y1 / h), min(1, x2 / w), min(1, y2 / h), f["score"],
                 json.dumps(kps), int(x2 - x1), vec_by_index[i].tobytes() if i in vec_by_index else None))
        batch_faces.clear()

    for n, row in enumerate(rows, 1):
        if cancelled():
            break
        try:
            im = load_for_faces(originals / row["path"])
            faces = model.detect(im)
        except Exception:
            # Unreadable file: remember that so it isn't retried forever.
            conn.execute("INSERT OR REPLACE INTO face_scans (asset_id, model, n_faces) VALUES (?, ?, -1)",
                         (row["id"], MODEL_NAME))
            done += 1
            continue
        for f in faces:
            size = f["box"][2] - f["box"][0]
            aligned = align_face(im, f["kps"]) if size >= MIN_FACE_PX else None
            batch_faces.append((row["id"], f, aligned, im.size))
        conn.execute("INSERT OR REPLACE INTO face_scans (asset_id, model, n_faces) VALUES (?, ?, ?)",
                     (row["id"], MODEL_NAME, len(faces)))
        done += 1
        if len(batch_faces) >= EMBED_BATCH:
            flush()
            conn.commit()
        if progress and n % 10 == 0:
            progress(n, len(rows))
    flush()
    conn.commit()
    return done


def _load_vectors(conn: sqlite3.Connection, where: str = "", args: tuple = ()) -> tuple[np.ndarray, np.ndarray, list]:
    rows = conn.execute(f"SELECT id, vec, person_id, locked, score, size_px FROM faces "
                        f"WHERE vec IS NOT NULL {where}", args).fetchall()
    ids = np.array([r["id"] for r in rows], dtype=np.int64)
    mat = (np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float16).reshape(len(rows), -1).astype(np.float32)
           if rows else np.zeros((0, 512), np.float32))
    return ids, mat, rows


def group_faces(conn: sqlite3.Connection) -> dict:
    """Assign every face with a signature to a person. Keeps your decisions.

    1. Faces you've locked (named/moved) stay put.
    2. Other faces join the closest existing person if similar enough.
    3. Leftover faces form new groups (greedy, in order of face quality).
    4. Very similar unnamed groups are merged.
    Returns counts.
    """
    ensure_schema(conn)
    ids, mat, rows = _load_vectors(conn)
    if not len(ids):
        return {"people": 0, "assigned": 0}
    person_of = {int(r["id"]): r["person_id"] for r in rows}
    locked = {int(r["id"]) for r in rows if r["locked"]}
    index = {int(i): k for k, i in enumerate(ids)}
    quality = {int(r["id"]): float(r["score"]) * min(1.0, r["size_px"] / 80.0) for r in rows}
    named = {r["id"] for r in conn.execute("SELECT id FROM persons WHERE name IS NOT NULL")}

    # Person centres from current members (locked members count double).
    members: dict[int, list[int]] = {}
    for fid, pid in person_of.items():
        if pid is not None:
            members.setdefault(pid, []).append(fid)

    def centre(fids: list[int]) -> np.ndarray:
        w = np.array([2.0 if f in locked else 1.0 for f in fids], np.float32)
        v = (mat[[index[f] for f in fids]] * w[:, None]).sum(0)
        return v / max(np.linalg.norm(v), 1e-8)

    centres = {pid: centre(f) for pid, f in members.items() if f}

    # 2. Unassigned faces join an existing person if close enough.
    free = [int(f) for f in ids if person_of[int(f)] is None]
    free.sort(key=lambda f: -quality[f])
    assigned = 0
    for fid in free:
        if not centres:
            break
        pids = list(centres)
        C = np.stack([centres[p] for p in pids])
        sims = C @ mat[index[fid]]
        k = int(np.argmax(sims))
        if sims[k] >= JOIN_THRESHOLD:
            pid = pids[k]
            person_of[fid] = pid
            members[pid].append(fid)
            centres[pid] = centre(members[pid])
            assigned += 1

    # 3. Leftovers form new groups. Running sums in a preallocated matrix keep this
    #    linear in the number of faces times the number of groups, with no copying.
    leftovers = [f for f in free if person_of[f] is None]
    new_groups: list[list[int]] = []
    sums = np.zeros((max(1, len(leftovers)), mat.shape[1]), dtype=np.float32)
    cents = np.zeros_like(sums)
    n_groups = 0
    for fid in leftovers:
        v = mat[index[fid]]
        if n_groups:
            sims = cents[:n_groups] @ v
            k = int(np.argmax(sims))
            if sims[k] >= JOIN_THRESHOLD:
                new_groups[k].append(fid)
                sums[k] += v
                cents[k] = sums[k] / max(np.linalg.norm(sums[k]), 1e-8)
                continue
        new_groups.append([fid])
        sums[n_groups] = v
        cents[n_groups] = v
        n_groups += 1
    for g in new_groups:
        if len(g) < MIN_GROUP:
            continue
        cur = conn.execute("INSERT INTO persons (name, created) VALUES (NULL, datetime('now'))")
        pid = cur.lastrowid
        members[pid] = g
        centres[pid] = centre(g)
        for fid in g:
            person_of[fid] = pid
            assigned += 1

    # 4. Merge near-identical unnamed groups (into named ones too). The similarity
    #    matrix is computed once; each merge updates one row/column.
    pids = list(centres)
    if len(pids) > 1:
        C = np.stack([centres[p] for p in pids]).astype(np.float32)
        S = C @ C.T
        np.fill_diagonal(S, -1)
        alive = np.ones(len(pids), dtype=bool)
        while True:
            S[~alive, :] = -1
            S[:, ~alive] = -1
            i, j = np.unravel_index(int(np.argmax(S)), S.shape)
            if S[i, j] < MERGE_THRESHOLD:
                break
            a, b = pids[i], pids[j]
            if a in named and b in named:
                S[i, j] = S[j, i] = -1
                continue
            keep, drop = (a, b) if (a in named or a < b) and b not in named else (b, a)
            ki, di = (i, j) if keep == a else (j, i)
            dropped = members.pop(drop)
            for fid in dropped:
                person_of[fid] = keep
            members[keep] = members.get(keep, []) + dropped
            centres[keep] = centre(members[keep])
            C[ki] = centres[keep]
            S[ki, :] = C @ C[ki]
            S[:, ki] = S[ki, :]
            S[ki, ki] = -1
            alive[di] = False
            centres.pop(drop)
            # Locked faces (your decisions) follow the merge too.
            conn.execute("UPDATE faces SET person_id = ? WHERE person_id = ?", (keep, drop))
            conn.execute("DELETE FROM persons WHERE id = ?", (drop,))

    conn.executemany("UPDATE faces SET person_id = ? WHERE id = ? AND locked = 0",
                     [(pid, fid) for fid, pid in person_of.items()])
    # Remove unnamed persons left with too few faces (unless you placed a face there); refresh covers.
    for pid, fids in members.items():
        if pid in named:
            continue
        has_locked = conn.execute("SELECT 1 FROM faces WHERE person_id = ? AND locked = 1 LIMIT 1", (pid,)).fetchone()
        if len(fids) < MIN_GROUP and not has_locked:
            conn.execute("UPDATE faces SET person_id = NULL WHERE person_id = ?", (pid,))
            conn.execute("DELETE FROM persons WHERE id = ?", (pid,))
    refresh_covers(conn)
    conn.commit()
    return {"people": conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0], "assigned": assigned}


def refresh_covers(conn: sqlite3.Connection) -> None:
    for p in conn.execute("SELECT id, cover_face FROM persons").fetchall():
        ok = p["cover_face"] and conn.execute(
            "SELECT 1 FROM faces WHERE id = ? AND person_id = ?", (p["cover_face"], p["id"])).fetchone()
        if not ok:
            best = conn.execute(
                "SELECT id FROM faces WHERE person_id = ? ORDER BY score * MIN(size_px, 160) DESC LIMIT 1",
                (p["id"],)).fetchone()
            conn.execute("UPDATE persons SET cover_face = ? WHERE id = ?", (best[0] if best else None, p["id"]))


# ---- user actions --------------------------------------------------------------

def people(conn: sqlite3.Connection, include_hidden: bool = False) -> list[dict]:
    ensure_schema(conn)
    rows = conn.execute(
        f"""SELECT p.id, p.name, p.hidden, p.cover_face, p.birthday,
                   (SELECT COUNT(DISTINCT f.asset_id) FROM faces f JOIN assets a ON a.id = f.asset_id
                     WHERE f.person_id = p.id AND a.missing = 0) AS photo_count
            FROM persons p {'' if include_hidden else 'WHERE p.hidden = 0'}
            ORDER BY p.name IS NULL, p.name, photo_count DESC""").fetchall()
    return [dict(r) for r in rows]


def rename_person(conn: sqlite3.Connection, pid: int, name: str | None) -> None:
    name = (name or "").strip() or None
    conn.execute("UPDATE persons SET name = ? WHERE id = ?", (name, pid))
    if name:
        conn.execute("UPDATE faces SET locked = 1 WHERE person_id = ?", (pid,))
    conn.commit()


def hide_person(conn: sqlite3.Connection, pid: int, hidden: bool) -> None:
    conn.execute("UPDATE persons SET hidden = ? WHERE id = ?", (1 if hidden else 0, pid))
    conn.commit()


def merge_people(conn: sqlite3.Connection, src: int, dst: int) -> None:
    """Everything in `src` becomes `dst` (keep dst's name unless it has none)."""
    if src == dst:
        return
    s = conn.execute("SELECT name FROM persons WHERE id = ?", (src,)).fetchone()
    d = conn.execute("SELECT name FROM persons WHERE id = ?", (dst,)).fetchone()
    if not s or not d:
        raise ValueError("Unknown person")
    conn.execute("UPDATE faces SET person_id = ?, locked = 1 WHERE person_id = ?", (dst, src))
    if not d["name"] and s["name"]:
        conn.execute("UPDATE persons SET name = ? WHERE id = ?", (s["name"], dst))
    conn.execute("DELETE FROM persons WHERE id = ?", (src,))
    refresh_covers(conn)
    conn.commit()


def move_face(conn: sqlite3.Connection, face_id: int, pid: int | None) -> None:
    """'Not this person': detach a face (pid None) or move it to another person."""
    conn.execute("UPDATE faces SET person_id = ?, locked = 1 WHERE id = ?", (pid, face_id))
    refresh_covers(conn)
    conn.commit()


def faces_in_photo(conn: sqlite3.Connection, asset_id: int) -> list[dict]:
    ensure_schema(conn)
    rows = conn.execute(
        """SELECT f.id, f.x1, f.y1, f.x2, f.y2, f.person_id, p.name, p.hidden
           FROM faces f LEFT JOIN persons p ON p.id = f.person_id
           WHERE f.asset_id = ? ORDER BY (f.x2 - f.x1) * (f.y2 - f.y1) DESC""", (asset_id,)).fetchall()
    return [dict(r) for r in rows]


def face_crop(conn: sqlite3.Connection, originals: Path, library: Path, face_id: int, size: int = 160) -> Path | None:
    """A square JPEG of one face, cached under derived/faces/."""
    row = conn.execute("SELECT f.x1, f.y1, f.x2, f.y2, a.path, a.sha256 FROM faces f "
                       "JOIN assets a ON a.id = f.asset_id WHERE f.id = ?", (face_id,)).fetchone()
    if not row:
        return None
    dest = library / "derived" / "faces" / (f"{face_id}.jpg" if size == 160 else f"{face_id}-{size}.jpg")
    from .media import _lock_for
    with _lock_for(str(dest)):
        if dest.exists():
            return dest
        im = load_for_faces(originals / row["path"])
        w, h = im.size
        cx, cy = (row["x1"] + row["x2"]) / 2 * w, (row["y1"] + row["y2"]) / 2 * h
        half = max(row["x2"] - row["x1"], row["y2"] - row["y1"]) * max(w, h) * 0.8
        box = (int(max(0, cx - half)), int(max(0, cy - half)), int(min(w, cx + half)), int(min(h, cy + half)))
        crop = im.crop(box).resize((size, size), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        crop.save(tmp, "JPEG", quality=85)
        tmp.replace(dest)
    return dest
