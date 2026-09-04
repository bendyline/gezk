"""The gezk conformance kit, exercised against the reference Python reader."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gezk import (
    Catalog,
    GezkError,
    canonicalize,
    chunk_uid,
    content_hash,
    format_uri,
    key_id,
    parse_uri,
    quantize_bits,
    quantize_int8,
    read_manifest,
    verify_and_extract,
    verify_manifest,
)
from gezk.hashembed import hash_embed_unit
from gezk.quantize import hamming_top_k

KIT = Path(__file__).resolve().parents[3] / "conformance"
VECTORS = json.loads((KIT / "vectors.json").read_text("utf-8"))
FIXTURE = KIT / VECTORS["fixture"]["path"]


def test_chunk_ids():
    for case in VECTORS["chunkUid"]:
        assert chunk_uid(case["documentId"], case["ordinal"], case["text"]) == case["expected"]
    for case in VECTORS["contentHash"]:
        assert content_hash(case["text"]) == case["expected"]


def test_quantization():
    for case in VECTORS["quantization"]:
        assert [b - 256 if b > 127 else b for b in quantize_int8(case["input"])] == case["int8"]
        assert list(quantize_bits(case["input"])) == case["bits"]


def test_canonical_json():
    for case in VECTORS["jcs"]:
        assert canonicalize(case["input"]) == case["canonical"]


def test_uri():
    for case in VECTORS["uri"]:
        assert parse_uri(case["uri"]) == case["parsed"]
    fmt = VECTORS["uriFormat"]
    i = fmt["input"]
    assert format_uri(i["publisherId"], i["catalogId"], i["documentId"], i.get("fragment")) == fmt["expected"]


def test_hamming_selection():
    h = VECTORS["hamming"]
    hits = hamming_top_k(bytes(h["rows"]), h["bytesPerRow"], bytes(h["query"]), h["k"])
    assert [{"chunkId": c, "distance": d} for c, d in hits] == h["expected"]


def test_fixture_digest_and_signature():
    data = FIXTURE.read_bytes()
    assert len(data) == VECTORS["fixture"]["sizeBytes"]
    assert hashlib.sha256(data).hexdigest() == VECTORS["fixture"]["sha256"]
    manifest = read_manifest(FIXTURE)
    assert manifest["formatVersion"] == VECTORS["formatVersion"]
    sig = VECTORS["signature"]
    assert key_id(sig["publicKeyPem"]) == sig["keyId"]
    anchors = [{"keyId": sig["keyId"], "publicKeyPem": sig["publicKeyPem"]}]
    assert verify_manifest(manifest, anchors) == (True, "ok")
    tampered = dict(manifest, **{sig["tamperedField"]: "tampered"})
    assert verify_manifest(tampered, anchors)[0] is False


def test_fixture_reads_and_searches(tmp_path):
    manifest = verify_and_extract(FIXTURE, tmp_path / "catalog")
    f = VECTORS["fixture"]
    assert manifest["id"] == f["catalogId"] and manifest["publisher"]["id"] == f["publisherId"]
    assert manifest["counts"] == {"documents": f["documents"], "chunks": f["chunks"], "shards": f["shards"]}
    cat = Catalog(tmp_path / "catalog")
    try:
        failed = [c for c in cat.validate(deep=True) if not c[1]]
        assert failed == []
        for q in f["ftsQueries"]:
            assert q["expectedDocumentId"] in [h.document_id for h in cat.search_documents(q["query"], 5)]
        doc = cat.get_document(f["documentRoundTrip"]["documentId"])
        assert doc is not None
        assert hashlib.sha256(doc["markdown"].encode("utf-8")).hexdigest() == f["documentRoundTrip"]["markdownSha256"]
        probe = f["semanticProbe"]
        hits = cat.search_semantic(hash_embed_unit(probe["embedInput"]), final_k=5)
        assert hits and hits[0].chunk_uid == probe["chunkUid"]
        assert hits[0].document_id == probe["documentId"]
    finally:
        cat.close()


def test_rejects_legacy_generation(tmp_path):
    import zipfile

    legacy = tmp_path / "legacy.gezk"
    with zipfile.ZipFile(FIXTURE) as src, zipfile.ZipFile(legacy, "w") as out:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "manifest.json":
                m = json.loads(data)
                m["kind"], m["formatVersion"] = "gezel-knowledge-catalog", 1
                data = json.dumps(m).encode("utf-8")
            out.writestr(info, data, compress_type=zipfile.ZIP_STORED)
    with pytest.raises(Exception) as err:
        read_manifest(legacy)
    assert getattr(err.value, "reason", None) == "format-version"


def _forged(fixture: Path, dest: Path) -> Path:
    """The fixture with a rewritten publisher and name. `manifest.json` is not
    among its own declared files, so every file digest still reconciles."""
    import zipfile

    with zipfile.ZipFile(fixture) as src, zipfile.ZipFile(dest, "w") as out:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "manifest.json":
                m = json.loads(data)
                m["name"] = "Totally Legit Catalog"
                m["publisher"] = {"id": "somebodyelse", "name": "Somebody Else"}
                data = json.dumps(m).encode("utf-8")
            out.writestr(info, data, compress_type=zipfile.ZIP_STORED)
    return dest


def test_rewritten_manifest_survives_every_structural_check(tmp_path):
    """Digests bind the content to the manifest; only the signature binds the
    manifest. Without anchors a forged identity is undetectable — which is why
    the CLI reports an unchecked signature instead of staying silent."""
    forged = _forged(FIXTURE, tmp_path / "forged.gezk")
    manifest = verify_and_extract(forged, tmp_path / "forged-out")
    assert manifest["publisher"]["id"] == "somebodyelse"
    cat = Catalog(tmp_path / "forged-out")
    try:
        assert [c for c in cat.validate(deep=True) if not c[1]] == []
    finally:
        cat.close()


def test_anchors_reject_a_rewritten_manifest(tmp_path):
    sig = VECTORS["signature"]
    anchors = [{"keyId": sig["keyId"], "publicKeyPem": sig["publicKeyPem"]}]
    forged = _forged(FIXTURE, tmp_path / "forged.gezk")

    assert verify_manifest(read_manifest(forged), anchors) == (False, "bad-signature")

    with pytest.raises(GezkError) as err:
        verify_and_extract(forged, tmp_path / "out", anchors)
    assert err.value.reason == "signature"

    assert verify_and_extract(FIXTURE, tmp_path / "genuine", anchors)["publisher"]["id"] == "bendyline"


def test_cli_verify_fails_a_rewritten_manifest_under_anchors(tmp_path):
    from gezk.cli import main

    sig = VECTORS["signature"]
    anchors_file = tmp_path / "anchors.json"
    anchors_file.write_text(json.dumps([{"keyId": sig["keyId"], "publicKeyPem": sig["publicKeyPem"]}]))
    forged = _forged(FIXTURE, tmp_path / "forged.gezk")

    assert main(["verify", str(forged), "--anchors", str(anchors_file)]) == 1
    assert main(["verify", str(FIXTURE), "--anchors", str(anchors_file)]) == 0

    key_file = tmp_path / "key.pem"
    key_file.write_text(sig["publicKeyPem"])
    assert main(["verify", str(FIXTURE), "--key", str(key_file)]) == 0
    assert main(["verify", str(forged), "--key", str(key_file)]) == 1
