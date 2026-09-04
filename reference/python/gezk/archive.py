"""Container handling (spec §3): the mimetype magic, manifest parsing with the
format-version gate, entry safety, and verified extraction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

from .signature import verify_manifest

MIME_TYPE = "application/vnd.gezk+zip"
MANIFEST_KIND = "gezk-catalog"
FORMAT_VERSION = "0.5"
MIMETYPE_PATH = "mimetype"
MANIFEST_PATH = "manifest.json"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 16_384
_DRIVE = re.compile(r"^[A-Za-z]:")


class GezkError(Exception):
    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def _assert_safe_entry(info: zipfile.ZipInfo, seen_lower: set[str]) -> None:
    name = info.filename
    if name.endswith("/"):
        raise GezkError(f"directory entry: {name}", "unsafe-entry")
    parts = name.split("/")
    if "\\" in name or name.startswith("/") or ".." in parts or _DRIVE.match(name):
        raise GezkError(f"unsafe path: {name}", "unsafe-entry")
    if info.flag_bits & 0x1:
        raise GezkError(f"encrypted entry: {name}", "unsafe-entry")
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise GezkError(f"symlink entry: {name}", "unsafe-entry")
    lower = name.lower()
    if lower in seen_lower:
        raise GezkError(f"duplicate entry (case-insensitive): {name}", "unsafe-entry")
    seen_lower.add(lower)


def _check_mimetype(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if not infos or infos[0].filename != MIMETYPE_PATH:
        raise GezkError("archive does not start with the gezk mimetype entry", "mimetype")
    first = infos[0]
    if first.compress_type != zipfile.ZIP_STORED or first.file_size != len(MIME_TYPE):
        raise GezkError("the mimetype entry must be stored, not compressed", "mimetype")
    if zf.read(first) != MIME_TYPE.encode("ascii"):
        raise GezkError("not a gezk archive (wrong mimetype)", "mimetype")


def _parse_manifest(raw: bytes) -> dict:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        raise GezkError(f"invalid manifest: {err}", "manifest") from err
    if not isinstance(manifest, dict):
        raise GezkError("invalid manifest: not an object", "manifest")
    kind, version = manifest.get("kind"), manifest.get("formatVersion")
    if kind != MANIFEST_KIND or version != FORMAT_VERSION:
        raise GezkError(
            f"unsupported gezk format (kind {kind}, version {version}); this reader supports "
            f"{MANIFEST_KIND} {FORMAT_VERSION}",
            "format-version",
        )
    return manifest


def read_manifest(path: str | os.PathLike[str]) -> dict:
    with zipfile.ZipFile(path) as zf:
        _check_mimetype(zf)
        try:
            info = zf.getinfo(MANIFEST_PATH)
        except KeyError as err:
            raise GezkError("archive has no manifest.json", "manifest") from err
        if info.file_size > MAX_MANIFEST_BYTES:
            raise GezkError("manifest exceeds size limit", "limits")
        return _parse_manifest(zf.read(info))


def archive_sha256(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_and_extract(
    path: str | os.PathLike[str],
    dest: str | os.PathLike[str],
    anchors: list[dict] | None = None,
) -> dict:
    """Extract a catalog after reconciling entries against the manifest in
    both directions and verifying every declared file's size and SHA-256.

    File digests bind the *content* to the manifest; only the manifest
    signature binds the manifest — and therefore the catalog's identity and
    publisher — to a key. Pass `anchors` (a list of `{keyId, publicKeyPem}`)
    to require that signature; without it the manifest is taken on trust.
    """
    dest_root = Path(dest).resolve()
    with zipfile.ZipFile(path) as zf:
        _check_mimetype(zf)
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise GezkError("too many archive entries", "limits")
        manifest = _parse_manifest(zf.read(MANIFEST_PATH))
        if anchors is not None:
            ok, reason = verify_manifest(manifest, anchors)
            if not ok:
                raise GezkError(f"manifest signature not verified: {reason}", "signature")
        seen: set[str] = set()
        for info in infos:
            _assert_safe_entry(info, seen)
        declared = {f["path"]: f for f in manifest["files"]}
        names = {info.filename for info in infos}
        for name in names:
            if name in (MIMETYPE_PATH, MANIFEST_PATH):
                continue
            if name not in declared:
                raise GezkError(f"archive contains undeclared file: {name}", "undeclared-file")
        for name, file in declared.items():
            if name not in names:
                raise GezkError(f"archive is missing declared file: {name}", "missing-file")
            if zf.getinfo(name).file_size != file["sizeBytes"]:
                raise GezkError(f"declared size does not match ZIP metadata for {name}", "hash-mismatch")
        dest_root.mkdir(parents=True, exist_ok=True)
        for info in infos:
            target = (dest_root / info.filename).resolve()
            if dest_root not in target.parents and target != dest_root:
                raise GezkError(f"entry escapes destination: {info.filename}", "unsafe-entry")
            target.parent.mkdir(parents=True, exist_ok=True)
            expected = declared.get(info.filename)
            digest = hashlib.sha256()
            size = 0
            with zf.open(info) as src, open(target, "wb") as out:
                for block in iter(lambda: src.read(4 * 1024 * 1024), b""):
                    size += len(block)
                    if expected and size > expected["sizeBytes"]:
                        raise GezkError(f"size mismatch for {info.filename}", "hash-mismatch")
                    digest.update(block)
                    out.write(block)
            if expected:
                if size != expected["sizeBytes"]:
                    raise GezkError(f"size mismatch for {info.filename}", "hash-mismatch")
                if digest.hexdigest() != expected["sha256"]:
                    raise GezkError(f"sha256 mismatch for {info.filename}", "hash-mismatch")
        return manifest
