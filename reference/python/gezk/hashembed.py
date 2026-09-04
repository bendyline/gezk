"""`test-hash-embed@1`: the deterministic embedder of the conformance kit
(spec §13). Not a model — a reproducible stand-in for one."""

from __future__ import annotations

import hashlib

from .quantize import l2_normalize


def hash_embed(text: str, dimensions: int = 384) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    out: list[float] = []
    offset = 0
    for _ in range(dimensions):
        if offset >= len(digest):
            digest = hashlib.sha256(digest).digest()
            offset = 0
        b = digest[offset]
        signed = b - 256 if b > 127 else b
        out.append((signed + 0.5) / 128)
        offset += 1
    return out


def hash_embed_unit(text: str, dimensions: int = 384) -> list[float]:
    return l2_normalize(hash_embed(text, dimensions))
