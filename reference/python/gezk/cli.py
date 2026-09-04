"""`gezk` command line: inspect, verify, extract, search."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from .archive import GezkError, archive_sha256, read_manifest, verify_and_extract
from .catalog import Catalog
from .signature import key_id, verify_manifest
from .uri import format_uri


def _load_anchors(args: argparse.Namespace) -> list[dict]:
    """Trust anchors from `--key` (a PEM public key, keyId derived) and
    `--anchors` (JSON: a list of `{keyId, publicKeyPem}` or an object with
    an `anchors` member)."""
    anchors: list[dict] = []
    for pem_path in args.key or []:
        pem = Path(pem_path).read_text("utf-8")
        anchors.append({"keyId": key_id(pem), "publicKeyPem": pem})
    for json_path in args.anchors or []:
        loaded = json.loads(Path(json_path).read_text("utf-8"))
        rows = loaded.get("anchors", []) if isinstance(loaded, dict) else loaded
        if not isinstance(rows, list):
            raise GezkError(f"{json_path}: expected a list of trust anchors", "anchors")
        for row in rows:
            if not isinstance(row, dict) or "publicKeyPem" not in row:
                raise GezkError(f"{json_path}: each anchor needs a publicKeyPem", "anchors")
            pem = row["publicKeyPem"]
            anchors.append({"keyId": row.get("keyId") or key_id(pem), "publicKeyPem": pem})
    return anchors


def _signature_check(manifest: dict, anchors: list[dict]) -> tuple[str, str, str]:
    """(name, status, detail) for the manifest signature. Without anchors the
    signature is reported as unchecked rather than silently skipped: the file
    digests cannot detect a rewritten manifest."""
    signature = manifest.get("signature")
    if not anchors:
        if not signature:
            return ("signature", "warn", "unsigned")
        return (
            "signature",
            "warn",
            f"key {signature.get('keyId', '?')} NOT CHECKED - pass --key or --anchors to verify it",
        )
    try:
        ok, reason = verify_manifest(manifest, anchors)
    except RuntimeError as err:
        return ("signature", "warn", f"not checked: {err}")
    detail = f"key {signature['keyId']}" if ok and signature else reason
    return ("signature", "ok" if ok else "FAIL", detail)


def _materialize(path: str, tmp: str) -> Path:
    p = Path(path)
    if p.is_dir():
        return p
    dest = Path(tmp) / "catalog"
    verify_and_extract(p, dest)
    return dest


def cmd_inspect(args: argparse.Namespace) -> int:
    manifest = read_manifest(args.archive)
    print(f"{manifest['id']}@{manifest['version']}  {manifest['name']}")
    print(f"  publisher  {manifest['publisher']['name']} ({manifest['publisher']['id']})")
    print(f"  format     gezk {manifest['formatVersion']} / index schema {manifest['indexSchemaVersion']}")
    print(f"  language   {manifest['language']}")
    print(f"  license    {manifest['license']['name']} (notice: {manifest['license']['noticePath']})")
    c = manifest["counts"]
    print(f"  counts     {c['documents']} documents, {c['chunks']} chunks, {c['shards']} shards")
    print(f"  embedding  {manifest['embedding']['id']} ({manifest['embedding']['model']['repo']}@{manifest['embedding']['model']['revision'][:12]})")
    print(f"  chunking   {manifest['chunking']['id']}")
    sig = manifest.get("signature")
    print(f"  signature  {'ed25519 key ' + sig['keyId'] if sig else 'unsigned'}")
    print(f"  sha256     {archive_sha256(args.archive)}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    anchors = _load_anchors(args)
    with tempfile.TemporaryDirectory(prefix="gezk-verify-") as tmp:
        root = _materialize(args.archive, tmp)
        cat = Catalog(root)
        try:
            checks = cat.validate(deep=args.deep)
            manifest = cat.manifest
        finally:
            cat.close()
    rows = [_signature_check(manifest, anchors)]
    rows += [(name, "ok" if passed else "FAIL", detail if not passed else "") for name, passed, detail in checks]
    for name, status, detail in rows:
        print(f"  {status:<4}  {name}{'  ' + detail if detail else ''}")
    return 1 if any(status == "FAIL" for _, status, _ in rows) else 0


def cmd_extract(args: argparse.Namespace) -> int:
    manifest = verify_and_extract(args.archive, args.dest, _load_anchors(args) or None)
    print(f"extracted {manifest['id']}@{manifest['version']} to {args.dest}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="gezk-search-") as tmp:
        root = _materialize(args.archive, tmp)
        cat = Catalog(root)
        try:
            publisher, catalog_id = cat.manifest["publisher"]["id"], cat.manifest["id"]
            docs = cat.search_documents(args.query, args.limit)
            if docs:
                print("Documents:")
                for hit in docs:
                    print(f"  {hit.title}  {format_uri(publisher, catalog_id, hit.document_id)}")
            chunks = cat.search_chunks(args.query, None, max(1, args.limit // max(1, len(cat.shards))))
            if chunks:
                print("Passages (full-text):")
                for hit in chunks[: args.limit]:
                    uri = format_uri(publisher, catalog_id, hit.document_id, {"chunk": hit.chunk_uid})
                    print(f"  {hit.title}  {uri}")
                    print(f"    {hit.text[:160].replace(chr(10), ' ')}")
            if not docs and not chunks:
                print(f"no matches for {args.query!r}")
        finally:
            cat.close()
    return 0


def _add_anchor_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--key",
        action="append",
        metavar="PEM",
        help="trust anchor: a public key PEM file; repeatable",
    )
    parser.add_argument(
        "--anchors",
        action="append",
        metavar="JSON",
        help="trust anchors as JSON ({keyId, publicKeyPem} objects); repeatable",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gezk", description="Reference reader for gezk knowledge catalogs.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect", help="print a catalog's manifest summary")
    p.add_argument("archive")
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("verify", help="extract and validate a catalog")
    p.add_argument("archive")
    p.add_argument("--deep", action="store_true")
    _add_anchor_args(p)
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("extract", help="verify and extract a catalog into a directory")
    p.add_argument("archive")
    p.add_argument("dest")
    _add_anchor_args(p)
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("search", help="full-text search over documents and passages")
    p.add_argument("archive")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_search)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except GezkError as err:
        print(f"error ({err.reason}): {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
