"""The gezk conformance kit, exercised against the reference Python reader."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from gezk import (
    FORMAT_GENERATIONS,
    Catalog,
    GezkError,
    asset_references,
    canonicalize,
    chunk_uid,
    content_hash,
    format_uri,
    is_asset_path,
    key_id,
    parse_uri,
    quantize_bits,
    quantize_int8,
    read_manifest,
    sniff_asset_type,
    svg_inertness_problem,
    verify_and_extract,
    verify_manifest,
)
from gezk.assets import asset_extension, asset_kind
from gezk.hashembed import hash_embed_unit
from gezk.quantize import hamming_top_k

KIT = Path(__file__).resolve().parents[3] / "conformance"
VECTORS = json.loads((KIT / "vectors.json").read_text("utf-8"))
FIXTURE = KIT / VECTORS["fixture"]["path"]
LEGACY = VECTORS.get("legacy", [])
ANCHORS = [{"keyId": VECTORS["signature"]["keyId"], "publicKeyPem": VECTORS["signature"]["publicKeyPem"]}]


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    root = tmp_path_factory.mktemp("fixture") / "catalog"
    verify_and_extract(FIXTURE, root, ANCHORS)
    cat = Catalog(root)
    yield cat
    cat.close()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assert_fixture_facts(cat: Catalog, facts: dict) -> None:
    """The facts every generation's fixture records: identity, counts, a
    clean deep validation, the full-text queries, the body round trip and
    the semantic probe."""
    m = cat.manifest
    assert m["id"] == facts["catalogId"] and m["publisher"]["id"] == facts["publisherId"]
    assert m["version"] == facts["version"]
    assert m["formatVersion"] == facts.get("formatVersion", VECTORS["formatVersion"])
    assert (m["counts"]["documents"], m["counts"]["chunks"], m["counts"]["shards"]) == (
        facts["documents"],
        facts["chunks"],
        facts["shards"],
    )
    assert [c for c in cat.validate(deep=True) if not c[1]] == []
    for q in facts["ftsQueries"]:
        assert q["expectedDocumentId"] in [h.document_id for h in cat.search_documents(q["query"], 5)]
    doc = cat.get_document(facts["documentRoundTrip"]["documentId"])
    assert doc is not None
    assert _sha256(doc["markdown"]) == facts["documentRoundTrip"]["markdownSha256"]
    probe = facts["semanticProbe"]
    hits = cat.search_semantic(hash_embed_unit(probe["embedInput"]), final_k=5)
    assert hits and hits[0].chunk_uid == probe["chunkUid"]
    assert hits[0].document_id == probe["documentId"]


def _rewrite_manifest(source: Path, dest: Path, mutate) -> Path:
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(dest, "w") as out:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "manifest.json":
                m = json.loads(data)
                mutate(m)
                data = json.dumps(m).encode("utf-8")
            out.writestr(info, data, compress_type=zipfile.ZIP_STORED)
    return dest


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
    assert manifest["indexSchemaVersion"] == FORMAT_GENERATIONS[VECTORS["formatVersion"]]
    sig = VECTORS["signature"]
    assert key_id(sig["publicKeyPem"]) == sig["keyId"]
    assert verify_manifest(manifest, ANCHORS) == (True, "ok")
    tampered = dict(manifest, **{sig["tamperedField"]: "tampered"})
    assert verify_manifest(tampered, ANCHORS)[0] is False


def test_fixture_reads_and_searches(catalog):
    _assert_fixture_facts(catalog, VECTORS["fixture"])


def test_nested_topic_rollup(catalog):
    facts = VECTORS["fixture"]["nestedTopic"]
    topics = {t["id"]: t for t in catalog.topics()}
    child, parent = topics[facts["id"]], topics[facts["parentId"]]
    assert child["parent_id"] == facts["parentId"]
    assert child["document_count"] == facts["directDocuments"]
    assert parent["document_count"] == facts["parentDirectDocuments"]
    assert parent["total_document_count"] == facts["parentTotalDocuments"]
    assert catalog.document_count(facts["parentId"]) == facts["parentTotalDocuments"]
    assert catalog.document_count(facts["parentId"], descendants=False) == facts["parentDirectDocuments"]

    subtree = {facts["parentId"]}
    for topic in catalog.topics():
        walk, seen = topic, []
        while walk is not None and walk["id"] not in seen:
            seen.append(walk["id"])
            walk = topics.get(walk["parent_id"]) if walk["parent_id"] else None
        if facts["parentId"] in seen:
            subtree.add(topic["id"])
    listed = catalog.documents(facts["parentId"], limit=1000)
    assert len(listed) == facts["parentTotalDocuments"]
    assert {d["topic_id"] for d in listed} <= subtree
    own = catalog.documents(facts["parentId"], limit=1000, descendants=False)
    assert len(own) == facts["parentDirectDocuments"]
    assert all(d["topic_id"] == facts["parentId"] for d in own)


def test_ordered_listing(catalog):
    facts = VECTORS["fixture"]["orderedListing"]
    first = catalog.documents(facts["topicId"], limit=len(facts["firstDocumentIds"]))
    assert [d["id"] for d in first] == facts["firstDocumentIds"]
    ordinals = [d["ordinal"] for d in catalog.documents(facts["topicId"], limit=1000)]
    ranked = [o for o in ordinals if o is not None]
    assert ordinals[: len(ranked)] == sorted(ranked)
    assert all(o is None for o in ordinals[len(ranked) :])


def test_meta_sample(catalog):
    facts = VECTORS["fixture"]["metaSample"]
    doc = catalog.get_document(facts["documentId"])
    assert doc is not None and doc["meta"] == facts["meta"]
    listed = next(d for d in catalog.documents(limit=1000) if d["id"] == facts["documentId"])
    assert listed["meta"] == facts["meta"]


def test_assets(catalog):
    facts = VECTORS["fixture"]
    keys = ("path", "contentType", "sizeBytes", "sha256")
    assert [tuple(a[k] for k in keys) for a in catalog.assets()] == [tuple(a[k] for k in keys) for a in facts["assets"]]
    assert catalog.manifest["counts"]["assets"] == len(facts["assets"])
    for expected in facts["assets"]:
        asset = catalog.read_asset(expected["path"])
        assert asset is not None
        assert hashlib.sha256(asset["bytes"]).hexdigest() == expected["sha256"]
        assert sniff_asset_type(asset["bytes"]) == asset_kind(asset_extension(expected["path"]))
    assert catalog.read_asset("assets/does-not-exist.png") is None
    ref = facts["assetDocument"]
    doc = catalog.get_document(ref["documentId"])
    assert doc is not None and ref["path"] in asset_references(doc["markdown"])


def test_asset_rules():
    assert is_asset_path("assets/diagrams/flow.PNG")
    assert not is_asset_path("assets/../escape.png")
    assert not is_asset_path("assets/.hidden.png")
    assert not is_asset_path("assets/notes.txt")
    assert sniff_asset_type(b"\x89PNG\r\n\x1a\n" + b"\0" * 16) == "png"
    assert sniff_asset_type(b"RIFF\0\0\0\0WEBPVP8 ") == "webp"
    assert sniff_asset_type(b"\xef\xbb\xbf<?xml version=\"1.0\"?><!-- c --><svg xmlns=\"http://www.w3.org/2000/svg\"/>") == "svg"
    assert sniff_asset_type(b"<html><svg/></html>") is None
    inert = b"<svg xmlns=\"http://www.w3.org/2000/svg\"><use href=\"#a\"/><image href=\"data:image/png;base64,AA==\"/></svg>"
    assert svg_inertness_problem(inert) is None
    assert svg_inertness_problem(b"<svg><script>1</script></svg>") == "contains a <script> element"
    assert svg_inertness_problem(b"<svg onload=\"x()\"/>") == "contains an event-handler attribute"
    assert svg_inertness_problem(b"<svg><image href=\"https://example.com/x.png\"/></svg>") == "contains a https: reference in an href"
    assert svg_inertness_problem(b"<svg><style>a{background:url(http://x/y)}</style></svg>") == "contains a http: reference in a CSS url()"
    assert svg_inertness_problem(b"\xff\xfe") == "not valid UTF-8"


@pytest.mark.parametrize("facts", [pytest.param(entry, id=f"gezk-{entry['formatVersion']}") for entry in LEGACY])
def test_legacy_fixture_reads_under_its_generation_rules(facts, tmp_path):
    """Older fixtures retain their own version-specific browse and asset rules."""
    path = KIT / facts["path"]
    data = path.read_bytes()
    assert len(data) == facts["sizeBytes"] and hashlib.sha256(data).hexdigest() == facts["sha256"]
    manifest = read_manifest(path)
    assert manifest["formatVersion"] == facts["formatVersion"]
    assert manifest["indexSchemaVersion"] == FORMAT_GENERATIONS[facts["formatVersion"]]
    assert verify_manifest(manifest, ANCHORS) == (True, "ok")
    verify_and_extract(path, tmp_path / "legacy", ANCHORS)
    cat = Catalog(tmp_path / "legacy")
    try:
        assert cat.schema_version == FORMAT_GENERATIONS[facts["formatVersion"]]
        _assert_fixture_facts(cat, facts)
        docs = cat.documents(limit=1000)
        assert len(docs) == facts["documents"]
        if facts["formatVersion"] == "0.5":
            assert cat.assets() == [] and cat.read_asset("assets/mark.png") is None
            assert all(t["total_document_count"] == t["document_count"] for t in cat.topics())
            assert all(d["ordinal"] is None and d["meta"] is None for d in docs)
        else:
            nested = facts["nestedTopic"]
            assert cat.document_count(nested["parentId"]) == nested["parentTotalDocuments"]
            assert cat.document_count(nested["id"], descendants=False) == nested["directDocuments"]
            order = facts["orderedListing"]
            assert [d["id"] for d in cat.documents(order["topicId"], limit=5)] == order["firstDocumentIds"]
            assert cat.get_document(facts["metaSample"]["documentId"])["meta"] == facts["metaSample"]["meta"]
            assert cat.assets() == facts["assets"]
    finally:
        cat.close()


def test_rejects_legacy_generation(tmp_path):
    def mutate(m):
        m["kind"], m["formatVersion"] = "gezel-knowledge-catalog", 1

    legacy = _rewrite_manifest(FIXTURE, tmp_path / "legacy.gezk", mutate)
    with pytest.raises(GezkError) as err:
        read_manifest(legacy)
    assert err.value.reason == "format-version"


def test_rejects_a_manifest_that_pairs_version_and_schema_wrongly(tmp_path):
    def mutate(m):
        m["indexSchemaVersion"] = 2

    mismatched = _rewrite_manifest(FIXTURE, tmp_path / "mismatched.gezk", mutate)
    with pytest.raises(GezkError) as err:
        read_manifest(mismatched)
    assert err.value.reason == "manifest"


def _forged(fixture: Path, dest: Path) -> Path:
    """The fixture with a rewritten publisher and name. `manifest.json` is not
    among its own declared files, so every file digest still reconciles."""

    def mutate(m):
        m["name"] = "Totally Legit Catalog"
        m["publisher"] = {"id": "somebodyelse", "name": "Somebody Else"}

    return _rewrite_manifest(fixture, dest, mutate)


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
    forged = _forged(FIXTURE, tmp_path / "forged.gezk")

    assert verify_manifest(read_manifest(forged), ANCHORS) == (False, "bad-signature")

    with pytest.raises(GezkError) as err:
        verify_and_extract(forged, tmp_path / "out", ANCHORS)
    assert err.value.reason == "signature"

    assert verify_and_extract(FIXTURE, tmp_path / "genuine", ANCHORS)["publisher"]["id"] == "bendyline"


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


def test_cli_toc_shows_the_rollup(capsys):
    from gezk.cli import main

    facts = VECTORS["fixture"]["nestedTopic"]
    assert main(["toc", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert f"[{facts['parentId']}]  {facts['parentDirectDocuments']} documents ({facts['parentTotalDocuments']} in subtree)" in out
    assert f"  [{facts['id']}]  {facts['directDocuments']} documents" in out


def test_module_entry_point_matches_the_console_script():
    """`python -m gezk` is what recipes/README.md tells readers to run, and it
    needs a `__main__.py` the console script does not."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "gezk", "inspect", str(FIXTURE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert VECTORS["fixture"]["catalogId"] in proc.stdout
    assert f"{len(VECTORS['fixture']['assets'])} assets" in proc.stdout


def test_unverifiable_signature_fails_when_a_check_was_asked_for(tmp_path, monkeypatch):
    """Anchors mean the caller asked for verification. If `cryptography` is
    missing the answer is FAIL, never a pass with a warning."""
    import gezk.cli as cli

    def no_crypto(*_args, **_kwargs):
        raise RuntimeError("signature verification needs the 'cryptography' package")

    monkeypatch.setattr(cli, "verify_manifest", no_crypto)
    anchors_file = tmp_path / "anchors.json"
    anchors_file.write_text(json.dumps([VECTORS["signature"]]))

    assert cli.main(["verify", str(FIXTURE), "--anchors", str(anchors_file)]) == 1
    # Without anchors nothing was asked for, so the same install still passes.
    assert cli.main(["verify", str(FIXTURE)]) == 0


def test_shared_toc_references(catalog):
    shared = VECTORS["fixture"]["sharedToc"]
    document_id = shared["documentId"]
    canonical = catalog.get_document(document_id)
    assert canonical["topic_id"] == shared["primaryTopicId"]
    assert catalog.document_count() == VECTORS["fixture"]["documents"]
    assert sum(t["document_count"] for t in catalog.topics()) == VECTORS["fixture"]["documents"] + 2
    for topic_id, ordinal in zip(shared["referenceTopicIds"], [-5, -4]):
        rows = catalog.documents(topic_id, limit=1000, descendants=False)
        doc = next(d for d in rows if d["id"] == document_id)
        assert doc["topic_id"] == topic_id and doc["ordinal"] == ordinal
        assert doc["title"] == canonical["title"]
    # A primary placement at craft and a reference below craft count once.
    rows = catalog.documents("craft", limit=1000)
    assert len(rows) == catalog.document_count("craft") == 27
    assert len({d["id"] for d in rows}) == len(rows)
    chosen = next(d for d in rows if d["id"] == document_id)
    assert (chosen["topic_id"], chosen["ordinal"]) == ("metals", -4)
    pages = [d for offset in range(0, len(rows), 3)
             for d in catalog.documents("craft", limit=3, offset=offset)]
    assert pages == rows
    assert catalog.document_count("unknown") == 0 and catalog.documents("unknown") == []
    assert sum(d["id"] == document_id for d in catalog.documents(limit=1000)) == 1
    assert sum(h.document_id == document_id for h in catalog.search_documents(canonical["title"], 40)) == 1
    assert catalog.get_document(document_id) == canonical


@pytest.mark.parametrize("sql,check", [
    ("DELETE FROM topic_documents WHERE document_id='doc-0000' AND topic_id='craft'", "toc-references"),
    ("UPDATE topic_documents SET ordinal=99 WHERE document_id='doc-0000' AND topic_id='craft'", "toc-references"),
    ("UPDATE topic_documents SET ordinal=2147483648 WHERE document_id='doc-0000' AND topic_id='nature'", "toc-references"),
    ("UPDATE topic_documents SET ordinal=1.5 WHERE document_id='doc-0000' AND topic_id='nature'", "toc-references"),
    ("UPDATE topic_documents SET topic_id='missing' WHERE document_id='doc-0000' AND topic_id='nature'", "toc-references"),
    ("INSERT INTO topic_documents VALUES ('nature', 'missing', NULL)", "toc-references"),
    ("UPDATE topics SET document_count=document_count+1 WHERE id='nature'", "toc-counts"),
    ("DROP TABLE topic_documents", "toc-references"),
    ("""CREATE TABLE broken AS SELECT * FROM topic_documents;
        INSERT INTO broken SELECT * FROM topic_documents WHERE document_id='doc-0000';
        DROP TABLE topic_documents; ALTER TABLE broken RENAME TO topic_documents;""", "toc-references"),
])
def test_invalid_shared_toc_is_rejected(tmp_path, sql, check):
    root = tmp_path / "invalid"
    verify_and_extract(FIXTURE, root, ANCHORS)
    with sqlite3.connect(root / "index/router.db") as db:
        db.executescript("PRAGMA ignore_check_constraints=ON;\n" + sql)
    cat = Catalog(root)
    try:
        failures = {name for name, ok, _ in cat.validate() if not ok}
        assert check in failures
    finally:
        cat.close()


def test_shared_toc_selection_ties_and_empty_topics(tmp_path):
    root = tmp_path / "ties"
    verify_and_extract(FIXTURE, root, ANCHORS)
    with sqlite3.connect(root / "index/router.db") as db:
        # Equal ordinals resolve by sort key, then by topic ID.
        db.execute("UPDATE topic_documents SET ordinal=7 WHERE document_id='doc-0000'")
        db.execute("UPDATE documents SET ordinal=7 WHERE id='doc-0000'")
        db.execute("UPDATE topics SET sort_key='same' WHERE id IN ('craft', 'metals')")
        db.execute("INSERT INTO topics VALUES ('empty', 'craft', 'Empty', NULL, 'last', 0)")
    cat = Catalog(root)
    try:
        shared = next(d for d in cat.documents("craft", limit=1000) if d["id"] == "doc-0000")
        assert shared["topic_id"] == "craft" and shared["ordinal"] == 7
        empty = next(t for t in cat.topics() if t["id"] == "empty")
        assert empty["document_count"] == empty["total_document_count"] == 0
        assert cat.document_count("empty") == 0 and cat.documents("empty") == []
    finally:
        cat.close()
