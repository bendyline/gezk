# gezk — an open format for knowledge catalogs

A `.gezk` file is a portable, read-only **knowledge catalog**: a body of
reference documents with full-text and vector indexes, shipped as one file
that any reader can search, cite and browse offline. Gezel uses it as the
RAG substrate for local models; it needs nothing beyond stock SQLite to
read, so it is not tied to gezel or to any vector-database extension.

**Status: version 0.5 (preliminary).** The format is `0.x` until 1.0: a minor
release may change it incompatibly, and a reader supports exactly the
versions it names. Catalogs published under 0.5 stay readable by 0.5
readers forever.

## What is inside a catalog

```
physics-en-2026.9.1.gezk           a ZIP whose first entry is the stored magic
├── mimetype                       "application/vnd.gezk+zip"
├── manifest.json                  identity, license, profiles, shards, file digests, signature
├── README.md                      what the catalog is, provenance
├── LICENSES/catalog.txt           the license notice for the content
├── LICENSES/source-notices.json   per-source attribution (optional)
└── index/
    ├── router.db                  topics, document directory, full bodies, routing centroids
    └── shards/000.db …            chunks, FTS5, sign-bit + int8 vectors (plain BLOB tables)
```

Every entry is stored uncompressed. Document bodies are Markdown, brotli-
compressed inside SQLite. Chunk vectors use a two-stage encoding — 384 sign
bits for a hamming pre-filter and int8 for a cosine rerank — that an
implementation reproduces with a few dozen lines of code.

## In this repository

| Path | What |
| --- | --- |
| [`spec/gezk-0.5.md`](spec/gezk-0.5.md) | The specification (CC BY 4.0) |
| [`schemas/`](schemas/) | JSON Schemas for the manifest, registry, notices and profiles (generated) |
| [`conformance/`](conformance/) | Test vectors and a signed fixture catalog every implementation must reproduce (generated) |
| [`reference/python/`](reference/python/) | A reference reader in Python (standard library + `brotli`) with a small CLI |
| [`recipes/`](recipes/) | Working with catalogs from other tools |

## Implementations

- **TypeScript** — [`@bendyline/gezk`](https://www.npmjs.com/package/@bendyline/gezk)
  (format definitions) and [`@bendyline/gezel-knowledge`](https://www.npmjs.com/package/@bendyline/gezel-knowledge)
  (compiler, verified archive reader, retrieval), maintained in the
  [gezel](https://github.com/bendyline/gezel) repository. The schemas and the
  conformance kit here are generated from it.
- **Python** — the reference reader in `reference/python/`.

## Publishing catalogs

A catalog is just a file, so any host will do. The arrangement Bendyline
publishes to — and the one the recipes assume — is one Hugging Face dataset
repository per catalog, holding the archive alongside a Parquet companion of
the same documents, chunks and embeddings. The specification's distribution
section describes it; no catalogs are published under 0.5 yet.
