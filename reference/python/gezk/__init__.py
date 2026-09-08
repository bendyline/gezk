"""Reference reader for gezk knowledge catalogs (0.6, and 0.5 archives)."""

from .archive import (
    FORMAT_GENERATIONS,
    FORMAT_VERSION,
    MIME_TYPE,
    SUPPORTED_FORMAT_VERSIONS,
    GezkError,
    archive_sha256,
    read_manifest,
    verify_and_extract,
)
from .assets import (
    asset_content_type,
    asset_references,
    is_asset_path,
    sniff_asset_type,
    svg_inertness_problem,
)
from .catalog import INDEX_SCHEMA_VERSION, SUPPORTED_INDEX_SCHEMA_VERSIONS, Catalog, ChunkHit, DocumentHit
from .hashembed import hash_embed
from .ids import chunk_uid, content_hash
from .jcs import canonicalize
from .quantize import hamming, l2_normalize, quantize_bits, quantize_int8, rerank_score
from .signature import key_id, verify_manifest
from .uri import format_uri, parse_uri

__all__ = [
    "Catalog",
    "ChunkHit",
    "DocumentHit",
    "FORMAT_GENERATIONS",
    "FORMAT_VERSION",
    "GezkError",
    "INDEX_SCHEMA_VERSION",
    "MIME_TYPE",
    "SUPPORTED_FORMAT_VERSIONS",
    "SUPPORTED_INDEX_SCHEMA_VERSIONS",
    "archive_sha256",
    "asset_content_type",
    "asset_references",
    "canonicalize",
    "chunk_uid",
    "content_hash",
    "format_uri",
    "hamming",
    "hash_embed",
    "is_asset_path",
    "key_id",
    "l2_normalize",
    "parse_uri",
    "quantize_bits",
    "quantize_int8",
    "read_manifest",
    "rerank_score",
    "sniff_asset_type",
    "svg_inertness_problem",
    "verify_and_extract",
    "verify_manifest",
]
