# gezk — reference reader (Python)

A small, dependency-light reader for `.gezk` knowledge catalogs: verify an
archive, browse its table of contents, read documents, run full-text and
two-stage semantic search, and format `knowledge://` citations. Standard
library plus `brotli` (document bodies); Ed25519 signature verification
needs the `signing` extra (`cryptography`).

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
gezk verify conformance/fixtures/conformance-0.5.gezk --deep --anchors anchors.json
```

That key proves signature handling, never provenance — its private half is
published in the generator.

```python
from gezk import Catalog, verify_and_extract

anchors = [{"keyId": "…", "publicKeyPem": "-----BEGIN PUBLIC KEY-----\n…"}]
manifest = verify_and_extract("physics-en-2026.9.1.gezk", "physics", anchors)
cat = Catalog("physics")
for hit in cat.search_documents("newton laws"):
    print(hit.document_id, hit.title)
```

Semantic search takes a unit query vector you produce with the catalog's
embedding profile (the manifest names the Hugging Face model and revision);
`gezk.hashembed` implements the deterministic stand-in the conformance kit
uses. This package is held to `conformance/` in CI.
