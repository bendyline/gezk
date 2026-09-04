"""The bit+int8 vector encoding (spec §6.2), computed exactly as the reference
implementation does: unit vectors are float32, rounding is half toward
positive infinity, and the sign bits pack LSB-first."""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence


def round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def to_float32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(v) * float(v) for v in vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("degenerate vector (zero norm)")
    return [to_float32(float(v) / norm) for v in vector]


def quantize_int8(vector: Sequence[float]) -> bytes:
    out = bytearray()
    for v in vector:
        q = round_half_up(float(v) * 127)
        q = 127 if q > 127 else -127 if q < -127 else q
        out.append(q & 0xFF)
    return bytes(out)


def quantize_bits(vector: Sequence[float]) -> bytes:
    out = bytearray((len(vector) + 7) // 8)
    for i, v in enumerate(vector):
        if float(v) > 0:
            out[i >> 3] |= 1 << (i & 7)
    return bytes(out)


def int8_values(blob: bytes) -> list[int]:
    return [b - 256 if b > 127 else b for b in blob]


def rerank_score(query: Sequence[float], passage_int8: bytes) -> float:
    total = 0.0
    for q, v in zip(query, int8_values(passage_int8)):
        total += float(q) * (v / 127)
    return total


def hamming(a: bytes, b: bytes) -> int:
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).bit_count()


def hamming_top_k(rows: bytes, bytes_per_row: int, query: bytes, k: int) -> list[tuple[int, int]]:
    """The k nearest rows as (chunk_id, distance), ascending distance, ties by
    chunk id — row i holds chunk id i + 1 (spec §5.3, §9)."""
    if len(query) != bytes_per_row:
        raise ValueError(f"query has {len(query)} bytes, rows have {bytes_per_row}")
    count = len(rows) // bytes_per_row
    limit = min(k, count)
    if limit <= 0:
        return []
    scored = []
    for i in range(count):
        row = rows[i * bytes_per_row : (i + 1) * bytes_per_row]
        scored.append((hamming(row, query), i + 1))
    scored.sort()
    return [(chunk_id, distance) for distance, chunk_id in scored[:limit]]
