"""An extracted catalog: browse, read, search (spec §5, §8, §9)."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Optional

import brotli

from .archive import GezkError
from .quantize import hamming_top_k, quantize_bits, rerank_score

APPLICATION_ID = 0x47455A4B
INDEX_SCHEMA_VERSION = 2
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
ROUTER_DB_PATH = "index/router.db"
SMOKE_QUERY_TOP_N = 10
_TOKEN = re.compile(r"\w+", re.UNICODE)


def sanitize_fts_query(query: str) -> Optional[str]:
    tokens: list[str] = []
    for token in _TOKEN.findall(unicodedata.normalize("NFKC", query))[:16]:
        if token not in tokens:
            tokens.append(token)
    if not tokens:
        return None
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def rerank_k(final_k: int = 24, chunk_count: int = 2**31) -> int:
    return min(512, max(128, 8 * final_k), chunk_count)


@dataclass
class DocumentHit:
    document_id: str
    title: str
    rank: int


@dataclass
class ChunkHit:
    chunk_uid: str
    document_id: str
    title: str
    heading_path: list[str]
    line_start: int
    line_end: int
    text: str
    shard_id: int
    cosine: Optional[float] = None
    source: str = "fts"


def open_catalog_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    if app_id != APPLICATION_ID:
        conn.close()
        raise GezkError(f"not a .gezk catalog database (application_id {app_id})", "not-a-catalog")
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if user_version != INDEX_SCHEMA_VERSION:
        conn.close()
        raise GezkError(f"unsupported index schema version {user_version}", "schema-version")
    return conn


class Catalog:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "manifest.json").read_text("utf-8"))
        self.router = open_catalog_db(self.root / ROUTER_DB_PATH)
        self.meta = {row["key"]: row["value"] for row in self.router.execute("SELECT key, value FROM meta")}
        self.shards = [
            (row["id"], row["path"], row["chunk_count"])
            for row in self.router.execute("SELECT id, path, chunk_count FROM shards ORDER BY id")
        ]
        self._conns: dict[str, sqlite3.Connection] = {ROUTER_DB_PATH: self.router}
        self._bits: dict[str, bytes] = {}
        self.dimensions = int(self.manifest["embedding"]["dimensions"])

    def close(self) -> None:
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()
        self._bits.clear()

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if self.root.resolve() not in target.parents:
            raise GezkError(f"path escapes catalog root: {path}", "corrupt")
        return target

    def _shard_db(self, path: str) -> sqlite3.Connection:
        conn = self._conns.get(path)
        if conn is None:
            conn = open_catalog_db(self._resolve(path))
            self._conns[path] = conn
        return conn

    # ── browsing ──────────────────────────────────────────────────────────
    def topics(self) -> list[dict]:
        rows = self.router.execute(
            "SELECT id, parent_id, name, description, sort_key, document_count FROM topics ORDER BY sort_key"
        )
        return [dict(row) for row in rows]

    def documents(self, topic_id: Optional[str] = None, limit: int = 50, offset: int = 0) -> list[dict]:
        where = "WHERE topic_id = ?" if topic_id else ""
        params: list = [topic_id] if topic_id else []
        rows = self.router.execute(
            f"SELECT id, title, slug, summary, language, topic_id, source_url, source_revision, "
            f"source_updated_at, attribution_json FROM documents {where} ORDER BY slug, id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return [dict(row) for row in rows]

    def get_document(self, document_id: str) -> Optional[dict]:
        row = self.router.execute(
            "SELECT id, title, slug, summary, language, topic_id, source_url, source_revision, "
            "source_updated_at, attribution_json, body_codec, body_blob FROM documents WHERE id = ?",
            [document_id],
        ).fetchone()
        if row is None:
            return None
        blob = row["body_blob"]
        if len(blob) > MAX_DOCUMENT_BYTES + 1024:
            raise GezkError(f"document body exceeds the stored-size limit: {document_id}", "corrupt")
        if row["body_codec"] == "br":
            body = brotli.decompress(blob)
        elif row["body_codec"] == "none":
            body = bytes(blob)
        else:
            raise GezkError(f"unknown document body codec for {document_id}", "corrupt")
        if len(body) > MAX_DOCUMENT_BYTES:
            raise GezkError(f"document body exceeds the size limit: {document_id}", "corrupt")
        doc = {k: row[k] for k in row.keys() if k not in ("body_codec", "body_blob")}
        doc["markdown"] = body.decode("utf-8")
        return doc

    # ── search ────────────────────────────────────────────────────────────
    def search_documents(self, query: str, limit: int = 10) -> list[DocumentHit]:
        match = sanitize_fts_query(query)
        if not match:
            return []
        rows = self.router.execute(
            "SELECT f.document_id, d.title FROM fts_documents f JOIN documents d ON d.id = f.document_id "
            "WHERE fts_documents MATCH ? ORDER BY f.rank LIMIT ?",
            [match, limit],
        )
        return [DocumentHit(row["document_id"], row["title"], i) for i, row in enumerate(rows)]

    def search_chunks(self, query: str, shard_ids: Optional[Sequence[int]] = None, limit_per_shard: int = 12) -> list[ChunkHit]:
        match = sanitize_fts_query(query)
        if not match:
            return []
        hits: list[ChunkHit] = []
        for shard_id, path, _ in self.shards:
            if shard_ids is not None and shard_id not in shard_ids:
                continue
            db = self._shard_db(path)
            rows = db.execute(
                "SELECT c.chunk_uid, c.document_id, c.title, c.heading_path, c.line_start, c.line_end, c.text "
                "FROM fts_chunks f JOIN chunks c ON c.id = f.rowid WHERE fts_chunks MATCH ? ORDER BY f.rank LIMIT ?",
                [match, limit_per_shard],
            )
            for row in rows:
                hits.append(
                    ChunkHit(row["chunk_uid"], row["document_id"], row["title"], json.loads(row["heading_path"]),
                             row["line_start"], row["line_end"], row["text"], shard_id)
                )
        return hits

    def shard_bits(self, path: str) -> bytes:
        cached = self._bits.get(path)
        if cached is not None:
            return cached
        db = self._shard_db(path)
        bytes_per_row = ceil(self.dimensions / 8)
        count, lo, hi = db.execute("SELECT COUNT(*), MIN(chunk_id), MAX(chunk_id) FROM chunk_vectors_bit").fetchone()
        if count and (lo != 1 or hi != count):
            raise GezkError(f"chunk ids are not dense in {path}", "corrupt")
        out = bytearray(count * bytes_per_row)
        for chunk_id, v in db.execute("SELECT chunk_id, v FROM chunk_vectors_bit ORDER BY chunk_id"):
            if len(v) != bytes_per_row:
                raise GezkError(f"bit vector width {len(v)} != {bytes_per_row} in {path}", "corrupt")
            start = (chunk_id - 1) * bytes_per_row
            out[start : start + bytes_per_row] = v
        self._bits[path] = bytes(out)
        return self._bits[path]

    def score_shards(self, query: Sequence[float]) -> dict[int, float]:
        import struct

        best: dict[int, float] = {}
        for shard_id, blob in self.router.execute("SELECT shard_id, embedding FROM route_centroids"):
            centroid = struct.unpack(f"<{len(blob) // 4}f", blob)
            dot = sum(c * q for c, q in zip(centroid, query))
            if dot > best.get(shard_id, float("-inf")):
                best[shard_id] = dot
        return best

    def route_shards(self, query: Sequence[float], budget: int = 6) -> list[int]:
        if len(self.shards) <= budget:
            return [shard_id for shard_id, _, _ in self.shards]
        scores = self.score_shards(query)
        return [shard_id for shard_id, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:budget]]

    def search_semantic(self, query: Sequence[float], final_k: int = 24, shard_budget: int = 6) -> list[ChunkHit]:
        query_bits = quantize_bits(query)
        hits: list[ChunkHit] = []
        for shard_id in self.route_shards(query, shard_budget):
            path, chunk_count = next((p, c) for sid, p, c in self.shards if sid == shard_id)
            db = self._shard_db(path)
            candidates = hamming_top_k(self.shard_bits(path), ceil(self.dimensions / 8), query_bits, rerank_k(final_k, chunk_count))
            scored = []
            for chunk_id, _ in candidates:
                row = db.execute("SELECT v FROM chunk_vectors_int8 WHERE chunk_id = ?", [chunk_id]).fetchone()
                if row is not None:
                    scored.append((rerank_score(query, row["v"]), chunk_id))
            scored.sort(key=lambda s: -s[0])
            for cosine, chunk_id in scored[:final_k]:
                row = db.execute(
                    "SELECT chunk_uid, document_id, title, heading_path, line_start, line_end, text FROM chunks WHERE id = ?",
                    [chunk_id],
                ).fetchone()
                if row is not None:
                    hits.append(ChunkHit(row["chunk_uid"], row["document_id"], row["title"], json.loads(row["heading_path"]),
                                         row["line_start"], row["line_end"], row["text"], shard_id, cosine, "vector"))
        hits.sort(key=lambda h: -(h.cosine or 0))
        return hits

    def self_knn_smoke(self, shard_id: int) -> bool:
        path = next((p for sid, p, _ in self.shards if sid == shard_id), None)
        if path is None:
            return False
        bytes_per_row = ceil(self.dimensions / 8)
        bits = self.shard_bits(path)
        if not bits:
            return False
        nearest = hamming_top_k(bits, bytes_per_row, bits[:bytes_per_row], 1)
        return bool(nearest) and nearest[0] == (1, 0)

    # ── validation ────────────────────────────────────────────────────────
    def validate(self, deep: bool = False) -> list[tuple[str, bool, str]]:
        checks: list[tuple[str, bool, str]] = []
        m = self.manifest
        checks.append(("meta-echo", self.meta.get("catalog_id") == m["id"] and self.meta.get("catalog_version") == m["version"], ""))
        checks.append(("meta-profile", self.meta.get("embedding_profile_id") == m["embedding"]["id"], ""))
        topics = self.topics()
        checks.append(("toc-present", len(topics) >= 1, f"{len(topics)} topics"))
        checks.append(("license-notice", any(f["path"] == m["license"]["noticePath"] for f in m["files"]), ""))
        total_docs = self.router.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        checks.append(("counts-documents", total_docs == m["counts"]["documents"] and sum(t["document_count"] for t in topics) == total_docs, f"{total_docs}"))
        checks.append(("counts-shards", len(self.shards) == m["counts"]["shards"], f"{len(self.shards)}"))
        checks.append(("counts-chunks", sum(c for _, _, c in self.shards) == m["counts"]["chunks"], ""))
        if deep:
            checks.append(("quick-check:index/router.db", self.router.execute("PRAGMA quick_check").fetchone()[0] == "ok", ""))
            for shard_id, path, chunk_count in self.shards:
                db = self._shard_db(path)
                checks.append((f"quick-check:{path}", db.execute("PRAGMA quick_check").fetchone()[0] == "ok", ""))
                n_chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                n_bits = db.execute("SELECT COUNT(*) FROM chunk_vectors_bit").fetchone()[0]
                n_int8 = db.execute("SELECT COUNT(*) FROM chunk_vectors_int8").fetchone()[0]
                checks.append((f"vectors-aligned:{path}", n_chunks == chunk_count == n_bits == n_int8, f"{n_chunks}/{n_bits}/{n_int8}/{chunk_count}"))
                bad_bit = db.execute("SELECT COUNT(*) FROM chunk_vectors_bit WHERE length(v) != ?", [ceil(self.dimensions / 8)]).fetchone()[0]
                bad_int8 = db.execute("SELECT COUNT(*) FROM chunk_vectors_int8 WHERE length(v) != ?", [self.dimensions]).fetchone()[0]
                checks.append((f"vector-widths:{path}", bad_bit == 0 and bad_int8 == 0, f"{bad_bit}/{bad_int8}"))
                lo, hi = db.execute("SELECT MIN(id), MAX(id) FROM chunks").fetchone()
                checks.append((f"chunk-ids-dense:{path}", n_chunks == 0 or (lo == 1 and hi == n_chunks), f"{lo}..{hi}"))
                checks.append((f"self-knn:{shard_id}", self.self_knn_smoke(shard_id), ""))
            for smoke in m.get("smokeQueries", []):
                top = [h.document_id for h in self.search_documents(smoke["query"], SMOKE_QUERY_TOP_N)]
                missing = [d for d in smoke["expectedDocumentIds"] if d not in top]
                checks.append((f"smoke:{smoke['query']}", not missing, ", ".join(missing)))
        return checks
