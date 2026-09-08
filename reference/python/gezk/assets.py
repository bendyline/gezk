"""Assets (spec §3.4): which files may ship under `assets/`, their limits,
the media type an extension implies, magic-byte sniffing, and the inertness
an SVG must prove before a reader serves it."""

from __future__ import annotations

import re
from typing import Optional

ASSETS_PREFIX = "assets/"
ASSET_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_ASSETS_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ASSET_COUNT = 8_192
MAX_ASSET_PATH_LENGTH = 512

# `assets/(<dir>/)*<name>.<ext>`: up to 15 directory segments of at most 128
# characters, each `[A-Za-z0-9._-]` and never starting with a dot, so `.`,
# `..` and hidden files are impossible by construction.
ASSET_PATH = re.compile(
    r"^assets/(?:[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}/){0,15}"
    r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}\.(?:png|jpe?g|gif|webp|svg)$",
    re.IGNORECASE,
)
_ASSET_REFERENCE = re.compile(r"\]\(\s*<?(assets/[^)\s>]+)")


def is_asset_path(path: str) -> bool:
    return len(path) <= MAX_ASSET_PATH_LENGTH and ASSET_PATH.match(path) is not None


def asset_extension(path: str) -> Optional[str]:
    dot = path.rfind(".")
    if dot < 0:
        return None
    ext = path[dot + 1 :].lower()
    return ext if ext in ASSET_TYPES else None


def asset_content_type(path: str) -> Optional[str]:
    """The media type an asset path implies, from its extension."""
    ext = asset_extension(path)
    return ASSET_TYPES[ext] if ext else None


def asset_kind(ext: str) -> str:
    """The kind an extension declares, normalized (`jpg` and `jpeg` are one kind)."""
    return "jpeg" if ext == "jpg" else ext


def asset_references(markdown: str) -> list[str]:
    """`[…](assets/…)` and `![…](assets/…)` targets in a body, deduplicated."""
    found: list[str] = []
    for match in _ASSET_REFERENCE.finditer(markdown):
        target = match.group(1)
        if target not in found:
            found.append(target)
    return found


_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_GIF87 = b"GIF87a"
_GIF89 = b"GIF89a"


def sniff_asset_type(data: bytes) -> Optional[str]:
    """What the leading bytes say the file is. Rasters are recognized by
    their magic numbers; an SVG is UTF-8 text whose first element, after any
    BOM, whitespace, XML declaration, comments and DOCTYPE, is `<svg`."""
    if data.startswith(_PNG):
        return "png"
    if data.startswith(_JPEG):
        return "jpeg"
    if data.startswith(_GIF87) or data.startswith(_GIF89):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "svg" if _looks_like_svg(_decode_text(data)) else None


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return ""


_WHITESPACE = " \t\n\r\ufeff"
_SVG_START = re.compile(r"<svg[\s>/]", re.IGNORECASE)


def _looks_like_svg(text: str) -> bool:
    i, n = 0, len(text)
    while i < n:
        if text[i] in _WHITESPACE:
            i += 1
            continue
        if text.startswith("<?", i):
            end = text.find("?>", i)
            if end < 0:
                return False
            i = end + 2
            continue
        if text.startswith("<!--", i):
            end = text.find("-->", i)
            if end < 0:
                return False
            i = end + 3
            continue
        if text[i : i + 9].lower() == "<!doctype":
            bracket = text.find("[", i)
            close = text.find(">", i)
            if close < 0:
                return False
            if 0 <= bracket < close:
                end = text.find("]>", bracket)
                if end < 0:
                    return False
                i = end + 2
            else:
                i = close + 1
            continue
        return _SVG_START.match(text[i : i + 5]) is not None
    return False


_SVG_ACTIVE = [
    (re.compile(r"<script[\s>/]", re.IGNORECASE), "a <script> element"),
    (re.compile(r"<foreignobject[\s>/]", re.IGNORECASE), "a <foreignObject> element"),
    (re.compile(r"<!entity", re.IGNORECASE), "an entity declaration"),
    (re.compile(r"\son[a-z]+\s*=", re.IGNORECASE), "an event-handler attribute"),
    (re.compile(r"javascript\s*:", re.IGNORECASE), "a javascript: reference"),
    (re.compile(r"data\s*:\s*text/html", re.IGNORECASE), "a data:text/html reference"),
    (re.compile(r"@import", re.IGNORECASE), "a CSS @import"),
]
_HREF = re.compile(r"""(?:xlink:)?href\s*=\s*["']\s*([^"']*)["']""", re.IGNORECASE)
_CSS_URL = re.compile(r"""url\(\s*["']?\s*([^"')]*)""", re.IGNORECASE)
_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_EMBEDDED_RASTER = re.compile(r"^data:image/(?:png|jpeg|gif|webp)[;,]", re.IGNORECASE)


def _external_reference_problem(target: str) -> Optional[str]:
    value = target.strip()
    if value == "" or value.startswith("#"):
        return None
    scheme = _SCHEME.match(value)
    if scheme is None or _EMBEDDED_RASTER.match(value):
        return None
    return f"a {scheme.group(1)}: reference"


def svg_inertness_problem(data: bytes) -> Optional[str]:
    """Why an SVG is not inert, or None when it is. Scripts, event handlers,
    foreign content, entity declarations and any reference that leaves the
    file (other than embedded raster data) all disqualify it: a reader may
    hand the bytes to an image element, but it may also let a person open
    them directly, and an SVG that can run is a page, not a picture."""
    text = _decode_text(data)
    if text == "":
        return "not valid UTF-8"
    if not _looks_like_svg(text):
        return "does not start with an <svg> element"
    for pattern, why in _SVG_ACTIVE:
        if pattern.search(text):
            return f"contains {why}"
    for match in _HREF.finditer(text):
        problem = _external_reference_problem(match.group(1) or "")
        if problem:
            return f"contains {problem} in an href"
    for match in _CSS_URL.finditer(text):
        problem = _external_reference_problem(match.group(1) or "")
        if problem:
            return f"contains {problem} in a CSS url()"
    return None
