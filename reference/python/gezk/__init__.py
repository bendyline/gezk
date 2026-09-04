"""Reference reader for gezk 0.5 knowledge catalogs."""

from .archive import GezkError, archive_sha256, read_manifest, verify_and_extract
from .catalog import Catalog, ChunkHit, DocumentHit
from .hashembed import hash_embed
from .ids import chunk_uid, content_hash
from .jcs import canonicalize
from .quantize import hamming, l2_normalize, quantize_bits, quantize_int8, rerank_score
from .signature import key_id, verify_manifest
from .uri import format_uri, parse_uri

FORMAT_VERSION = "0.5"
INDEX_SCHEMA_VERSION = 2
MIME_TYPE = "application/vnd.gezk+zip"

__all__ = [
    "Catalog",
    "ChunkHit",
    "DocumentHit",
    "FORMAT_VERSION",
    "GezkError",
    "INDEX_SCHEMA_VERSION",
    "MIME_TYPE",
    "archive_sha256",
    "canonicalize",
    "chunk_uid",
    "content_hash",
    "format_uri",
    "hamming",
    "hash_embed",
    "key_id",
    "l2_normalize",
    "parse_uri",
    "quantize_bits",
    "quantize_int8",
    "read_manifest",
    "rerank_score",
    "verify_and_extract",
    "verify_manifest",
]
