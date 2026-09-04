"""Content-derived identifiers (spec §7.3)."""

from __future__ import annotations

import hashlib


def chunk_uid(document_id: str, ordinal: int, text: str) -> str:
    inner = hashlib.sha256(text.encode("utf-8")).digest()
    outer = hashlib.sha256()
    outer.update(document_id.encode("utf-8"))
    outer.update(b"\x00")
    outer.update(str(ordinal).encode("ascii"))
    outer.update(b"\x00")
    outer.update(inner)
    return outer.hexdigest()[:32]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
