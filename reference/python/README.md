# gezk — reference reader (Python)

A small, dependency-light reader for `.gezk` knowledge catalogs: verify an
archive, browse its table of contents, read documents and their assets, run
full-text and two-stage semantic search, and format `knowledge://` citations.
Standard library plus `brotli` (document bodies); Ed25519 signature
verification needs the `signing` extra (`cryptography`). It reads draft gezk
0.7 catalogs and the 0.5 and 0.6 archives published before it (spec §1).

## Install from this repository

There is no PyPI release, by choice: this is a reference implementation held
to `conformance/`, versioned with the format rather than on its own cadence.
Install it from a checkout.

```bash
git clone https://github.com/bendyline/gezk.git
cd gezk
python -m pip install -e 'reference/python[signing]'
```

That puts a `gezk` command on your PATH. `python -m gezk` runs the same entry
point by import path, which is the safer form inside a script or when several
environments are in play. The `signing` extra adds `cryptography` for Ed25519
verification; without it everything but signature checking still works, and
asking for a check anyway fails rather than passing quietly.

```bash
gezk inspect physics-en-2026.9.1.gezk
gezk toc     physics-en-2026.9.1.gezk
gezk verify  physics-en-2026.9.1.gezk --deep --key publisher.pub.pem
gezk search  physics-en-2026.9.1.gezk "newton laws"
```

## Signatures are checked only against a key you name

File digests bind a catalog's *content* to its manifest. Only the manifest
signature binds the manifest — the catalog's id, version and publisher — to a
key, so a rewritten identity passes every structural check on its own. Pass
`--key` (a public key PEM) or `--anchors` (JSON `{keyId, publicKeyPem}`
objects) to `verify` or `extract`; both flags repeat. Without one, `verify`
reports the signature as `NOT CHECKED` rather than passing it silently.

The conformance kit carries its TEST key in `vectors.json`, so the fixture
verifies end to end without any other file:

```bash
python -c "import json,sys; json.dump([json.load(open('conformance/vectors.json'))['signature']], sys.stdout)" > anchors.json
gezk verify conformance/fixtures/conformance-0.7.gezk --deep --anchors anchors.json
gezk verify conformance/fixtures/conformance-0.6.gezk --deep --anchors anchors.json
gezk verify conformance/fixtures/conformance-0.5.gezk --deep --anchors anchors.json
```

That key proves signature handling, never provenance — its private half is
published in the generator.

```python
from gezk import Catalog, verify_and_extract

anchors = [{"keyId": "…", "publicKeyPem": "-----BEGIN PUBLIC KEY-----\n…"}]
manifest = verify_and_extract("physics-en-2026.9.1.gezk", "physics", anchors)
cat = Catalog("physics")
for topic in cat.topics():
    print(topic["id"], topic["document_count"], topic["total_document_count"])
for doc in cat.documents("mechanics"):
    print(doc["id"], doc["ordinal"], doc["meta"])
for hit in cat.search_documents("newton laws"):
    print(hit.document_id, hit.title)
png = cat.read_asset("assets/figures/pendulum.png")
```

`topics()` reports each topic's own documents and the total across its
subtree; `documents(topic_id)` lists the subtree by default (ordered
documents first, then by slug) and `descendants=False` narrows it to the
topic itself. In 0.7, an article may appear in several topics; subtree
counts and pages contain each canonical document once. Scoped rows return the
chosen placement’s `topic_id` and `ordinal`; `get_document(id)` and unscoped
listings return its primary placement. The body and citation stay the same.
Assets are the images a body references by archive path;
`read_asset` hands back the bytes the manifest declared, or `None`.

Semantic search takes a unit query vector you produce with the catalog's
embedding profile (the manifest names the Hugging Face model and revision);
`gezk.hashembed` implements the deterministic stand-in the conformance kit
uses. This package is held to `conformance/` in CI.


The 0.7 draft includes typed document subject/associated point locations, a
manifest spatial summary, and radius discovery with `sphere-6371000` distances.
See [the 0.7 specification](../../spec/gezk-0.7.md).

`catalog.nearby_documents({"latitude": 47.6062, "longitude": -122.3321, "radiusMeters": 50000})`
returns documents, total, nearest matching subject and unrounded metre distances.
`search_documents`, `search_chunks` and `search_semantic` accept the same radius
as the optional `spatial` argument and constrain candidates before their limits.
