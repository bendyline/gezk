"""An extracted catalog: browse, read, search, validate (spec §5, §8, §9)."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Optional

import brotli

from .archive import GezkError
from .assets import (
    MAX_ASSET_BYTES,
    MAX_ASSET_COUNT,
    MAX_ASSETS_TOTAL_BYTES,
    asset_content_type,
    asset_extension,
    asset_kind,
    asset_references,
    is_asset_path,
    sniff_asset_type,
    svg_inertness_problem,
)
from .quantize import hamming_top_k, quantize_bits, rerank_score

APPLICATION_ID = 0x47455A4B
INDEX_SCHEMA_VERSION = 3
SUPPORTED_INDEX_SCHEMA_VERSIONS = (2, 3)
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_META_BYTES = 16 * 1024
MAX_TOPIC_DEPTH = 16
# The recursive walks stop here so a hostile parent_id cycle terminates; a
# valid catalog is never deeper than MAX_TOPIC_DEPTH.
TOPIC_WALK_MAX_DEPTH = 32
ROUTER_DB_PATH = "index/router.db"
SMOKE_QUERY_TOP_N = 10
_TOKEN = re.compile(r"\w+", re.UNICODE)
_DOCUMENT_COLUMNS = (
    "id, title, slug, summary, language, topic_id, source_url, source_revision, "
    "source_updated_at, attribution_json"
)


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
    if user_version not in SUPPORTED_INDEX_SCHEMA_VERSIONS:
        conn.close()
        raise GezkError(
            f"unsupported index schema version {user_version}; this reader supports "
            f"{', '.join(str(v) for v in SUPPORTED_INDEX_SCHEMA_VERSIONS)}",
            "schema-version",
        )
    return conn


def db_schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def topic_tree_problem(topics: Sequence[dict]) -> tuple[bool, str]:
    """The topic forest must be acyclic, every parent declared, and no deeper
    than MAX_TOPIC_DEPTH. Returns (ok, detail)."""
    by_id = {t["id"]: t for t in topics}
    for topic in topics:
        parent = topic["parent_id"]
        if parent is not None and parent not in by_id:
            return False, f"topic '{topic['id']}' names an undeclared parent '{parent}'"
    max_depth = 0
    for topic in topics:
        seen: set[str] = set()
        depth = 0
        current: Optional[dict] = topic
        while current is not None:
            if current["id"] in seen:
                return False, f"topic '{topic['id']}' sits in a parent cycle"
            seen.add(current["id"])
            depth += 1
            if depth > MAX_TOPIC_DEPTH:
                return False, f"topic '{topic['id']}' is deeper than {MAX_TOPIC_DEPTH}"
            parent = current["parent_id"]
            current = by_id.get(parent) if parent is not None else None
        max_depth = max(max_depth, depth)
    return True, f"{len(topics)} topics, max depth {max_depth}"


class Catalog:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "manifest.json").read_text("utf-8"))
        self.router = open_catalog_db(self.root / ROUTER_DB_PATH)
        self.schema_version = db_schema_version(self.router)
        self.meta = {row["key"]: row["value"] for row in self.router.execute("SELECT key, value FROM meta")}
        self.shards = [
            (row["id"], row["path"], row["chunk_count"])
            for row in self.router.execute("SELECT id, path, chunk_count FROM shards ORDER BY id")
        ]
        self._conns: dict[str, sqlite3.Connection] = {ROUTER_DB_PATH: self.router}
        self._bits: dict[str, bytes] = {}
        self._assets: Optional[dict[str, dict]] = None
        self.dimensions = int(self.manifest["embedding"]["dimensions"])

    def close(self) -> None:
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()
        self._bits.clear()

    @property
    def format_version(self) -> str:
        return str(self.meta.get("format_version", ""))

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if self.root.resolve() not in target.parents:
            raise GezkError(f"path escapes catalog root: {path}", "corrupt")
        return target

    def _shard_db(self, path: str) -> sqlite3.Connection:
        conn = self._conns.get(path)
        if conn is None:
            conn = open_catalog_db(self._resolve(path))
            if db_schema_version(conn) != self.schema_version:
                conn.close()
                raise GezkError(f"shard {path} does not share the router's index schema", "schema-version")
            self._conns[path] = conn
        return conn

    # ── browsing ──────────────────────────────────────────────────────────
    def topics(self) -> list[dict]:
        """Every topic with its direct `document_count` and a
        `total_document_count` rolled up over its subtree. On a 0.5 catalog
        every document sits at a root, so the two agree there."""
        rows = self.router.execute(
            f"""
            WITH RECURSIVE sub(root, id, depth) AS (
              SELECT id, id, 0 FROM topics
              UNION ALL
              SELECT sub.root, t.id, sub.depth + 1 FROM topics t JOIN sub ON t.parent_id = sub.id
              WHERE sub.depth < {TOPIC_WALK_MAX_DEPTH}
            ),
            rollup AS (
              SELECT sub.root AS id, SUM(t.document_count) AS total
              FROM sub JOIN topics t ON t.id = sub.id GROUP BY sub.root
            )
            SELECT t.id, t.parent_id, t.name, t.description, t.sort_key, t.document_count,
                   rollup.total AS total_document_count
            FROM topics t JOIN rollup ON rollup.id = t.id
            ORDER BY t.sort_key, t.id
            """
        )
        return [dict(row) for row in rows]

    def _document_columns(self) -> str:
        return _DOCUMENT_COLUMNS + (", ordinal, meta_json" if self.schema_version >= 3 else "")

    def _document_order(self) -> str:
        if self.schema_version >= 3:
            return "ORDER BY (ordinal IS NULL), ordinal, slug, id"
        return "ORDER BY slug, id"

    def _topic_scope(self, topic_id: Optional[str], descendants: bool) -> tuple[str, str, list]:
        if topic_id and descendants:
            scope = (
                f"WITH RECURSIVE sub(id, depth) AS (SELECT ?, 0 UNION ALL "
                f"SELECT t.id, sub.depth + 1 FROM topics t JOIN sub ON t.parent_id = sub.id "
                f"WHERE sub.depth < {TOPIC_WALK_MAX_DEPTH})"
            )
            return scope, "WHERE topic_id IN (SELECT id FROM sub)", [topic_id]
        if topic_id:
            return "", "WHERE topic_id = ?", [topic_id]
        return "", "", []

    def _document_from_row(self, row: sqlite3.Row) -> dict:
        doc = {k: row[k] for k in row.keys() if k not in ("body_codec", "body_blob", "meta_json")}
        if self.schema_version >= 3:
            raw = row["meta_json"]
            doc["meta"] = self._parse_meta(doc["id"], raw) if raw is not None else None
        else:
            doc["ordinal"] = None
            doc["meta"] = None
        return doc

    @staticmethod
    def _parse_meta(document_id: str, raw: str) -> dict:
        try:
            meta = json.loads(raw)
        except ValueError as err:
            raise GezkError(f"document metadata is not JSON: {document_id}", "corrupt") from err
        if not isinstance(meta, dict):
            raise GezkError(f"document metadata is not an object: {document_id}", "corrupt")
        return meta

    def document_count(self, topic_id: Optional[str] = None, descendants: bool = True) -> int:
        scope, where, params = self._topic_scope(topic_id, descendants)
        return int(self.router.execute(f"{scope} SELECT COUNT(*) FROM documents {where}", params).fetchone()[0])

    def documents(
        self,
        topic_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        descendants: bool = True,
    ) -> list[dict]:
        """A page of documents, by default including those filed under the
        topic's descendants (`descendants=False` lists only its own). Ordered
        documents (`ordinal`) come first, the rest by slug."""
        scope, where, params = self._topic_scope(topic_id, descendants)
        rows = self.router.execute(
            f"{scope} SELECT {self._document_columns()} FROM documents {where} "
            f"{self._document_order()} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return [self._document_from_row(row) for row in rows]

    def _decode_body(self, document_id: str, codec: str, blob: bytes) -> str:
        if len(blob) > MAX_DOCUMENT_BYTES + 1024:
            raise GezkError(f"document body exceeds the stored-size limit: {document_id}", "corrupt")
        if codec == "br":
            body = brotli.decompress(blob)
        elif codec == "none":
            body = bytes(blob)
        else:
            raise GezkError(f"unknown document body codec for {document_id}", "corrupt")
        if len(body) > MAX_DOCUMENT_BYTES:
            raise GezkError(f"document body exceeds the size limit: {document_id}", "corrupt")
        return body.decode("utf-8")

    def get_document(self, document_id: str) -> Optional[dict]:
        row = self.router.execute(
            f"SELECT {self._document_columns()}, body_codec, body_blob FROM documents WHERE id = ?",
            [document_id],
        ).fetchone()
        if row is None:
            return None
        doc = self._document_from_row(row)
        doc["markdown"] = self._decode_body(document_id, row["body_codec"], row["body_blob"])
        return doc

    def document_bodies(self) -> Iterator[tuple[str, str]]:
        """(id, markdown) for every document, in id order."""
        for row in self.router.execute("SELECT id, body_codec, body_blob FROM documents ORDER BY id"):
            yield row["id"], self._decode_body(row["id"], row["body_codec"], row["body_blob"])

    # ── assets ────────────────────────────────────────────────────────────
    def _asset_index(self) -> dict[str, dict]:
        """The manifest's `files` entries under `assets/`: the declaration is
        what authorizes serving a file, since extraction reconciled and hashed
        every declared entry."""
        if self._assets is None:
            index: dict[str, dict] = {}
            for file in self.manifest.get("files", []):
                path = file.get("path")
                if not isinstance(path, str) or not is_asset_path(path):
                    continue
                content_type = asset_content_type(path)
                if content_type is None:
                    continue
                index[path] = {
                    "path": path,
                    "contentType": content_type,
                    "sizeBytes": int(file["sizeBytes"]),
                    "sha256": file["sha256"],
                }
            self._assets = index
        return self._assets

    def assets(self) -> list[dict]:
        """Declared assets (`path`, `contentType`, `sizeBytes`, `sha256`), sorted by path."""
        return [self._asset_index()[path] for path in sorted(self._asset_index())]

    def read_asset(self, path: str) -> Optional[dict]:
        """One declared asset with its `bytes`, or None when the catalog
        declares no such asset."""
        info = self._asset_index().get(path)
        if info is None:
            return None
        if info["sizeBytes"] > MAX_ASSET_BYTES:
            raise GezkError(f"asset exceeds the size limit: {path}", "corrupt")
        data = self._resolve(path).read_bytes()
        if len(data) != info["sizeBytes"]:
            raise GezkError(f"asset size differs from the manifest: {path}", "corrupt")
        return {**info, "bytes": data}

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
        checks.append((
            "meta-format",
            self.format_version == m["formatVersion"] and self.schema_version == m["indexSchemaVersion"],
            f"router says format {self.format_version} / schema {self.schema_version}, "
            f"manifest says {m['formatVersion']} / {m['indexSchemaVersion']}",
        ))
        topics = self.topics()
        checks.append(("toc-present", len(topics) >= 1, f"{len(topics)} topics"))
        checks.append(("topics-tree", *topic_tree_problem(topics)))
        undeclared = self.router.execute(
            "SELECT COUNT(*) FROM documents WHERE topic_id NOT IN (SELECT id FROM topics)"
        ).fetchone()[0]
        checks.append(("documents-topic-declared", undeclared == 0, f"{undeclared} documents are filed under an undeclared topic"))
        asset_files = [f for f in m["files"] if f["path"].startswith("assets/")]
        if m["formatVersion"] == "0.5":
            checks.append(("assets-not-in-0.5", not asset_files, f"{len(asset_files)} assets/ entries in a 0.5 catalog"))
        else:
            bad_paths = [f["path"] for f in asset_files if not is_asset_path(f["path"])]
            checks.append(("assets-paths", not bad_paths, f"invalid asset paths: {', '.join(bad_paths)}"))
            oversize = [f for f in asset_files if f["sizeBytes"] > MAX_ASSET_BYTES]
            total_bytes = sum(f["sizeBytes"] for f in asset_files)
            checks.append((
                "assets-limits",
                len(asset_files) <= MAX_ASSET_COUNT and not oversize and total_bytes <= MAX_ASSETS_TOTAL_BYTES,
                f"{len(asset_files)} assets, {total_bytes} bytes, {len(oversize)} over the per-asset limit",
            ))
            declared_count = m["counts"].get("assets", 0)
            checks.append(("counts-assets", declared_count == len(asset_files), f"manifest counts {declared_count} assets, files declare {len(asset_files)}"))
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
            if self.schema_version >= 3:
                checks.extend(self._deep_checks_0_6(asset_files))
            for smoke in m.get("smokeQueries", []):
                top = [h.document_id for h in self.search_documents(smoke["query"], SMOKE_QUERY_TOP_N)]
                missing = [d for d in smoke["expectedDocumentIds"] if d not in top]
                checks.append((f"smoke:{smoke['query']}", not missing, ", ".join(missing)))
        return checks

    def _deep_checks_0_6(self, asset_files: list[dict]) -> list[tuple[str, bool, str]]:
        checks: list[tuple[str, bool, str]] = []
        invalid: list[str] = []
        oversize: list[str] = []
        for row in self.router.execute("SELECT id, meta_json FROM documents WHERE meta_json IS NOT NULL"):
            raw = row["meta_json"]
            if len(raw.encode("utf-8")) > MAX_DOCUMENT_META_BYTES:
                oversize.append(row["id"])
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                invalid.append(row["id"])
        detail = []
        if invalid:
            detail.append(f"not a JSON object: {', '.join(invalid)}")
        if oversize:
            detail.append(f"over {MAX_DOCUMENT_META_BYTES} bytes: {', '.join(oversize)}")
        checks.append(("document-meta-json", not invalid and not oversize, "; ".join(detail)))
        declared = {f["path"] for f in asset_files}
        for file in asset_files:
            ext = asset_extension(file["path"])
            if ext is None:
                continue
            data = self._resolve(file["path"]).read_bytes()
            kind = sniff_asset_type(data)
            checks.append((
                f"asset-type:{file['path']}",
                kind == asset_kind(ext),
                f"leading bytes say {kind or 'unknown'}, the extension says {asset_kind(ext)}",
            ))
            if ext == "svg":
                problem = svg_inertness_problem(data)
                checks.append((f"asset-svg-inert:{file['path']}", problem is None, problem or ""))
        if declared:
            missing: list[str] = []
            for document_id, markdown in self.document_bodies():
                for target in asset_references(markdown):
                    if target not in declared:
                        missing.append(f"{document_id} -> {target}")
                    if len(missing) >= 5:
                        break
                if len(missing) >= 5:
                    break
            checks.append(("document-asset-refs", not missing, f"undeclared asset references: {'; '.join(missing)}"))
        return checks
