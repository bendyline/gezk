"""knowledge:// references (spec §11)."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, TypedDict
from urllib.parse import quote, unquote

ID_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
PREFIX = "knowledge://"
MAX_ENCODED_DOCUMENT_ID = 512
_CHUNK = re.compile(r"^chunk=([0-9a-f]{32})$")
_LINE = re.compile(r"^line=(\d+)(?:-(\d+))?$")
_SAFE = "-_.!~*'()"


class ParsedUri(TypedDict, total=False):
    publisherId: str
    catalogId: str
    documentId: str
    fragment: dict


def valid_document_id(value: str) -> bool:
    if not 1 <= len(value) <= 256:
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    if value != value.strip():
        return False
    return all(not (ord(ch) <= 0x1F or 0x7F <= ord(ch) <= 0x9F) for ch in value)


def format_uri(publisher_id: str, catalog_id: str, document_id: str, fragment: Optional[dict] = None) -> str:
    encoded = "/".join(quote(seg, safe=_SAFE) for seg in document_id.split("/"))
    out = f"{PREFIX}{publisher_id}/{catalog_id}/{encoded}"
    if fragment:
        if "chunk" in fragment:
            out += f"#chunk={fragment['chunk']}"
        else:
            out += f"#line={fragment['lineStart']}"
            if fragment.get("lineEnd") is not None:
                out += f"-{fragment['lineEnd']}"
    return out


def parse_uri(raw: str) -> Optional[ParsedUri]:
    if not raw.startswith(PREFIX):
        return None
    rest = raw[len(PREFIX) :]
    body, _, fragment_raw = rest.partition("#")
    has_fragment = "#" in rest
    first = body.find("/")
    if first <= 0:
        return None
    second = body.find("/", first + 1)
    if second <= first + 1:
        return None
    publisher_id, catalog_id, encoded = body[:first], body[first + 1 : second], body[second + 1 :]
    if not ID_PATTERN.match(publisher_id) or not ID_PATTERN.match(catalog_id):
        return None
    if not encoded or len(encoded) > MAX_ENCODED_DOCUMENT_ID:
        return None
    segments = encoded.split("/")
    if any(seg == "" for seg in segments):
        return None
    try:
        document_id = "/".join(unquote(seg, errors="strict") for seg in segments)
    except UnicodeDecodeError:
        return None
    if not valid_document_id(document_id):
        return None
    parsed: ParsedUri = {"publisherId": publisher_id, "catalogId": catalog_id, "documentId": document_id}
    if not has_fragment:
        return parsed
    chunk = _CHUNK.match(fragment_raw)
    if chunk:
        parsed["fragment"] = {"chunk": chunk.group(1)}
        return parsed
    line = _LINE.match(fragment_raw)
    if line:
        fragment = {"lineStart": int(line.group(1))}
        if line.group(2) is not None:
            fragment["lineEnd"] = int(line.group(2))
        parsed["fragment"] = fragment
        return parsed
    return None
