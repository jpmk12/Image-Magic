"""Smart search: find photos by describing them ("C-17 on the flight line").

Uses OpenAI's CLIP ViT-B/16 model (MIT License) in ONNX format, run locally with
ONNX Runtime: on the GPU through DirectML on Windows when available, otherwise
on the CPU. Photos never leave the computer.

The model (~600 MB) is downloaded once into the settings folder and checked
against pinned SHA-256 hashes before use.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps

from . import media
from .settings import settings_dir

MODEL_NAME = "clip-vit-b-16"
_BASE = ("https://clip-as-service.s3.us-east-2.amazonaws.com/"
         "models-436c69702d61732d53657276696365/onnx/ViT-B-16/")
MODEL_FILES = {
    # name: (url, size, sha256)
    "textual.onnx": (_BASE + "textual.onnx", 254120156,
                     "52d329a1c978d0d2644bc81bcae8f77102523853f1a4c24cfc8305f49f5d212e"),
    "visual.onnx": (_BASE + "visual.onnx", 344894533,
                    "e2bb83fb39c0a93d69e1f6a087f0a4cbd2a6501d2686358d6be65c5e50f0cf25"),
}
DOWNLOAD_BYTES = sum(size for _, size, _ in MODEL_FILES.values())

_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
_PX = 224
BATCH = 16

SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(id),
    model    TEXT NOT NULL,
    vec      BLOB NOT NULL       -- 512 x float16, L2-normalized
);
"""


def model_dir() -> Path:
    return settings_dir() / "models" / MODEL_NAME


def model_ready() -> bool:
    d = model_dir()
    return all((d / name).is_file() and (d / name).stat().st_size == size
               for name, (_, size, _) in MODEL_FILES.items())


def download_model(progress: Callable[[int, int], None] | None = None,
                   cancelled: Callable[[], bool] = lambda: False) -> None:
    """Download and verify the model files. Resumes partial downloads."""
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    done_before = 0
    for name, (url, size, sha) in MODEL_FILES.items():
        final = d / name
        if final.is_file() and final.stat().st_size == size:
            done_before += size
            continue
        part = d / (name + ".part")
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": "ImageMagic"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        with urllib.request.urlopen(req, timeout=60) as resp:
            if have and resp.status != 206:   # server ignored Range: start over
                have = 0
            with part.open("ab" if have else "wb") as f:
                while chunk := resp.read(1 << 20):
                    if cancelled():
                        return
                    f.write(chunk)
                    have += len(chunk)
                    if progress:
                        progress(done_before + have, DOWNLOAD_BYTES)
        h = hashlib.sha256()
        with part.open("rb") as f:
            while block := f.read(1 << 20):
                h.update(block)
        if h.hexdigest() != sha:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{name} failed its integrity check; it was deleted. Please try again.")
        part.replace(final)
        done_before += size


def _providers() -> list[str]:
    import onnxruntime as ort
    available = ort.get_available_providers()
    preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    return [p for p in preferred if p in available] or available


@dataclass
class Device:
    name: str     # "GPU (DirectML)", "GPU (CUDA)", "CPU"


class Model:
    """Lazily loaded text and image encoders. Thread-safe for inference."""

    _instance: "Model | None" = None
    _guard = threading.Lock()

    def __init__(self) -> None:
        import onnxruntime as ort
        from .clip_tokenizer import Tokenizer

        d = model_dir()
        providers = _providers()
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        if "DmlExecutionProvider" in providers:
            # Required by DirectML: no memory pattern, sequential execution.
            opts.enable_mem_pattern = False
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        try:
            self.visual = ort.InferenceSession(str(d / "visual.onnx"), opts, providers=providers)
            # Fail fast here (not halfway through indexing) if the GPU can't run the model.
            self.visual.run(None, {"pixel_values": np.zeros((1, 3, _PX, _PX), dtype=np.float32)})
        except Exception:
            cpu = ort.SessionOptions()
            cpu.log_severity_level = 3
            self.visual = ort.InferenceSession(str(d / "visual.onnx"), cpu, providers=["CPUExecutionProvider"])
        text_opts = ort.SessionOptions()
        text_opts.log_severity_level = 3
        # Text queries are tiny; CPU is fast enough and avoids GPU start-up cost.
        self.textual = ort.InferenceSession(str(d / "textual.onnx"), text_opts, providers=["CPUExecutionProvider"])
        used = self.visual.get_providers()[0]
        self.device = {"CUDAExecutionProvider": "GPU (CUDA)", "DmlExecutionProvider": "GPU (DirectML)"}.get(used, "CPU")
        self.tokenizer = Tokenizer()
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "Model":
        with cls._guard:
            if cls._instance is None:
                cls._instance = Model()
            return cls._instance

    def encode_images(self, images: list[Image.Image]) -> np.ndarray:
        batch = np.stack([preprocess(im) for im in images])
        with self._lock:
            out = self.visual.run(None, {"pixel_values": batch})[0]
        return _normalize(out)

    def encode_text(self, texts: list[str]) -> np.ndarray:
        ids, mask = self.tokenizer(texts)
        with self._lock:
            out = self.textual.run(None, {"input_ids": np.array(ids, dtype=np.int32),
                                          "attention_mask": np.array(mask, dtype=np.int32)})[0]
        return _normalize(out)


def _normalize(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-8)


def preprocess(im: Image.Image) -> np.ndarray:
    """CLIP preprocessing: resize short side to 224 (bicubic), center-crop, normalize."""
    im = ImageOps.exif_transpose(im).convert("RGB")
    w, h = im.size
    scale = _PX / min(w, h)
    im = im.resize((max(_PX, round(w * scale)), max(_PX, round(h * scale))), Image.BICUBIC)
    w, h = im.size
    left, top = (w - _PX) // 2, (h - _PX) // 2
    im = im.crop((left, top, left + _PX, top + _PX))
    arr = (np.asarray(im, dtype=np.float32) / 255.0 - _MEAN) / _STD
    return arr.transpose(2, 0, 1)


# ---- catalog integration --------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    ensure_schema(conn)
    return conn.execute(
        """SELECT a.id, a.path, a.sha256 FROM assets a
           LEFT JOIN embeddings e ON e.asset_id = a.id AND e.model = ?
           WHERE e.asset_id IS NULL AND a.kind = 'photo' AND a.missing = 0
             AND a.duplicate_of IS NULL
           ORDER BY a.taken_at DESC""",
        (MODEL_NAME,),
    ).fetchall()


def counts(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    total = conn.execute("SELECT COUNT(*) FROM assets WHERE kind = 'photo' AND missing = 0 "
                         "AND duplicate_of IS NULL").fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM embeddings e JOIN assets a ON a.id = e.asset_id "
                        "WHERE e.model = ? AND a.missing = 0", (MODEL_NAME,)).fetchone()[0]
    return {"indexed": done, "photos": total, "model_ready": model_ready()}


def index_photos(conn: sqlite3.Connection, library: Path, originals: Path, model,
                 progress: Callable[[int, int], None] | None = None,
                 cancelled: Callable[[], bool] = lambda: False) -> int:
    """Compute embeddings for photos that don't have one yet. Returns how many."""
    rows = pending(conn)
    done = 0
    for start in range(0, len(rows), BATCH):
        if cancelled():
            break
        chunk = rows[start:start + BATCH]
        images, ids = [], []
        for r in chunk:
            try:
                # Thumbnails are small and already rotated; much faster than full HEIC.
                thumb = media.thumbnail(library, originals, r["path"], r["sha256"])
                with Image.open(thumb) as im:
                    images.append(im.copy())
                ids.append(r["id"])
            except Exception:
                continue  # unreadable file: skip, it'll be retried next time
        if images:
            vecs = model.encode_images(images).astype(np.float16)
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (asset_id, model, vec) VALUES (?, ?, ?)",
                [(i, MODEL_NAME, v.tobytes()) for i, v in zip(ids, vecs)],
            )
            conn.commit()
            done += len(images)
        if progress:
            progress(min(start + BATCH, len(rows)), len(rows))
    _Index.invalidate()
    return done


class _Index:
    """All embeddings in memory (16k photos x 512 x 2 bytes = 16 MB)."""
    _cache: dict[str, tuple[int, np.ndarray, np.ndarray]] = {}
    _lock = threading.Lock()

    @classmethod
    def invalidate(cls) -> None:
        with cls._lock:
            cls._cache.clear()

    @classmethod
    def load(cls, conn: sqlite3.Connection, db_key: str) -> tuple[np.ndarray, np.ndarray]:
        ensure_schema(conn)
        n = conn.execute("SELECT COUNT(*) FROM embeddings WHERE model = ?", (MODEL_NAME,)).fetchone()[0]
        with cls._lock:
            hit = cls._cache.get(db_key)
            if hit and hit[0] == n:
                return hit[1], hit[2]
        rows = conn.execute("SELECT asset_id, vec FROM embeddings WHERE model = ?", (MODEL_NAME,)).fetchall()
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        mat = (np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), -1)
               if rows else np.zeros((0, 512), dtype=np.float16))
        with cls._lock:
            cls._cache[db_key] = (n, ids, mat)
        return ids, mat


# Phrasings averaged together; CLIP matches "a photo of X" better than bare "X".
TEMPLATES = ["a photo of {}.", "a photo of the {}.", "{}"]
# Measured on sample photos: true matches score ~0.26-0.31, unrelated photos
# up to ~0.25, and a concept with no matching photo tops out around 0.16-0.22.
MIN_SCORE = 0.22       # below this, matches are mostly noise for ViT-B/16
RELATIVE_CUTOFF = 0.06  # also drop results far below the best match


def query_vector(model, text: str) -> np.ndarray:
    vecs = model.encode_text([t.format(text) for t in TEMPLATES])
    return _normalize(vecs.mean(axis=0, keepdims=True))[0]


def search(conn: sqlite3.Connection, db_key: str, model, text: str,
           allowed_ids: set[int] | None = None, limit: int = 500) -> list[tuple[int, float]]:
    ids, mat = _Index.load(conn, db_key)
    if not len(ids):
        return []
    q = query_vector(model, text).astype(np.float32)
    scores = mat.astype(np.float32) @ q
    order = np.argsort(-scores)
    best = float(scores[order[0]])
    out = []
    for i in order:
        s = float(scores[i])
        if s < MIN_SCORE or s < best - RELATIVE_CUTOFF:
            break
        aid = int(ids[i])
        if allowed_ids is None or aid in allowed_ids:
            out.append((aid, round(s, 4)))
            if len(out) >= limit:
                break
    return out
