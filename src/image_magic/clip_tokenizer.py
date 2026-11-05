"""CLIP's byte-pair-encoding tokenizer, dependency-free.

Adapted from OpenAI CLIP's simple_tokenizer.py (MIT License, Copyright (c) 2021
OpenAI). Uses the bundled data/bpe_simple_vocab_16e6.txt.gz from the same project.
Differences: uses the standard library `re` instead of `regex`, and skips `ftfy`.
"""

from __future__ import annotations

import gzip
import html
import re
import unicodedata
from functools import lru_cache
from importlib import resources

CONTEXT_LENGTH = 77

# \p{L} -> [^\W\d_]  (letters), \p{N} -> \d, everything else that isn't space.
_PAT = re.compile(
    r"""<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|(?:[^\s\w]|_)+""",
    re.IGNORECASE,
)


@lru_cache()
def _bytes_to_unicode() -> dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) \
        + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs]))


def _pairs(word: tuple[str, ...]) -> set[tuple[str, str]]:
    return {(a, b) for a, b in zip(word, word[1:])}


class Tokenizer:
    def __init__(self) -> None:
        raw = (resources.files("image_magic") / "data" / "bpe_simple_vocab_16e6.txt.gz").read_bytes()
        merges = gzip.decompress(raw).decode("utf-8").split("\n")[1: 49152 - 256 - 2 + 1]
        merges = [tuple(m.split()) for m in merges]
        self.byte_encoder = _bytes_to_unicode()
        vocab = list(self.byte_encoder.values())
        vocab += [v + "</w>" for v in vocab]
        vocab += ["".join(m) for m in merges]
        vocab += ["<|startoftext|>", "<|endoftext|>"]
        self.encoder = {t: i for i, t in enumerate(vocab)}
        self.ranks = {m: i for i, m in enumerate(merges)}
        self.sot = self.encoder["<|startoftext|>"]
        self.eot = self.encoder["<|endoftext|>"]
        self._cache: dict[str, str] = {}

    def _bpe(self, token: str) -> str:
        if token in self._cache:
            return self._cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _pairs(word)
        if not pairs:
            return token + "</w>"
        while True:
            bigram = min(pairs, key=lambda p: self.ranks.get(p, float("inf")))
            if bigram not in self.ranks:
                break
            first, second = bigram
            new: list[str] = []
            i = 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                except ValueError:
                    new.extend(word[i:])
                    break
                new.extend(word[i:j])
                i = j
                if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
                    new.append(first + second)
                    i += 2
                else:
                    new.append(word[i])
                    i += 1
            word = tuple(new)
            if len(word) == 1:
                break
            pairs = _pairs(word)
        out = " ".join(word)
        self._cache[token] = out
        return out

    def encode(self, text: str) -> list[int]:
        text = unicodedata.normalize("NFC", html.unescape(html.unescape(text)))
        text = re.sub(r"\s+", " ", text).strip().lower()
        ids: list[int] = []
        for tok in _PAT.findall(text):
            tok = "".join(self.byte_encoder[b] for b in tok.encode("utf-8"))
            ids.extend(self.encoder[t] for t in self._bpe(tok).split(" "))
        return ids

    def __call__(self, texts: list[str]) -> tuple[list[list[int]], list[list[int]]]:
        """Return (input_ids, attention_mask), each len(texts) x 77."""
        ids_out, mask_out = [], []
        for t in texts:
            toks = [self.sot] + self.encode(t) + [self.eot]
            if len(toks) > CONTEXT_LENGTH:
                toks = toks[:CONTEXT_LENGTH]
                toks[-1] = self.eot
            pad = CONTEXT_LENGTH - len(toks)
            ids_out.append(toks + [0] * pad)
            mask_out.append([1] * len(toks) + [0] * pad)
        return ids_out, mask_out
