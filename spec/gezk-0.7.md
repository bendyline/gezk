# gezk 0.7 — Knowledge Catalog Format

Status: **0.7, draft** (2026-10-04). Licensed CC BY 4.0 (see `LICENSE.md`).

A gezk catalog is a versioned, read-only body of reference documents shipped
as a single file, with everything a reader needs to browse, search and cite
it offline: the documents themselves, a table of contents, a full-text index
and quantized chunk embeddings. The format is deliberately built from parts
that already exist everywhere — ZIP, SQLite (with FTS5), JSON, brotli,
Ed25519 — so that any language can implement a reader in a few hundred
lines without a vector-database extension.

The key words MUST, MUST NOT, SHOULD and MAY are to be interpreted as in
RFC 2119.

**Changes since 0.6:**

- Index schema 4 adds shared TOC references in the router table
  `topic_documents`: one canonical document can appear under several topics
  without duplicating its body, chunks, embeddings or citations (§5.2).
- Every placement has its own optional ordinal. Direct topic counts count
  placements; subtree counts and paginated listings deduplicate documents
  before counting and pagination (§5.2, Appendix A).
- Compiler input adds optional `tocReferences`, a list of additional
  root-to-leaf paths and ordinals (§5.2.1). The primary path remains the
  document’s canonical metadata and deterministic shard assignment input.
- The conformance kit includes references across sibling topics and between
  an ancestor and its descendant, and retains both 0.5 and 0.6 fixtures (§13).
- 0.6 body, asset, chunk, vector, archive and citation encodings are unchanged.

## 1. Versioning and conformance

- `formatVersion` (a string, `"0.7"`) governs the container layout and the
  manifest; `indexSchemaVersion` (an integer, `4`) governs the SQLite DDL.
  Both are recorded in the manifest and echoed inside every database. The
  two move together: `"0.5"` pairs with `2`, `"0.6"` with `3`, and
  `"0.7"` with `4`; a manifest that pairs them otherwise is corrupt.
- **`0.x` is preliminary.** A `0.(x+1)` release MAY change the format
  incompatibly. A reader supports an explicit set of versions and MUST
  refuse any other with a typed, human-readable reason rather than guess.
  A reader MUST NOT rewrite, repair or migrate a catalog: a catalog is a
  publisher's immutable, possibly signed, artifact.
- A 0.7 reader SHOULD also read 0.5 (index schema 2) and 0.6 (schema 3)
  catalogs. These use `documents.topic_id` for their single placement and
  have no `topic_documents` table. 0.5 files documents at root topics;
  0.6 files them at leaves. Missing 0.6 columns read as absent in 0.5 and
  `assets/` entries are refused there. Readers that only implement 0.5 or
  0.6 MUST refuse 0.7 rather than silently omit shared placements.
- An implementation conforms to 0.7 when it reproduces every entry of the
  conformance kit (`conformance/vectors.json`), reads the fixture catalog
  as the kit describes, and reads every fixture the kit lists under
  `legacy` the same way. The kit is generated from the reference
  implementation; readers in other languages test against it in CI.

## 2. Identifiers

| Name | Grammar | Notes |
| --- | --- | --- |
| `publisherId`, `catalogId`, `topicId` | `[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?` (DNS-label style) | Catalog ids are unique **per publisher**. |
| `version` | one portable path segment: `[A-Za-z0-9](?:[A-Za-z0-9._+-]{0,126}[A-Za-z0-9])?`, never a Windows reserved device name | An identity, not a path. Publishers SHOULD use semver-compatible values (`2026.9.1`). |
| `documentId` | 1–256 Unicode scalars, NFC-normalized, no C0/C1 controls, no leading or trailing whitespace | A Markdown adapter uses the relative path without extension; Wikipedia catalogs use the decimal page id. |
| `chunk_uid` | exactly 32 lowercase hex characters | Content-derived, see §7.3. |

## 3. Container

A catalog is a ZIP archive (ZIP64 when required) with the extension
`.gezk` and the media type `application/vnd.gezk+zip`.

### 3.1 Entries

| Path | Required | Content |
| --- | --- | --- |
| `mimetype` | yes, **first entry, stored** | exactly `application/vnd.gezk+zip` (no trailing newline) |
| `manifest.json` | yes | the manifest, §4 |
| `README.md` | yes | human-facing description and provenance |
| `LICENSES/catalog.txt` | yes (the path named by `license.noticePath`) | the license notice for the content |
| `LICENSES/source-notices.json` | optional | per-source attribution, `schemas/source-notices.schema.json` |
| `index/router.db` | yes | the router database, §5.2 |
| `index/shards/NNN.db` | when the catalog is sharded | shard databases, §5.3; `NNN` is the zero-padded shard id |
| `assets/…` | optional | images referenced by document bodies, §3.4 |
| `sources/…` | optional | original source files a small author-built catalog chose to keep |

The `mimetype` entry is the container's magic: because it is the first entry
and stored, the bytes `application/vnd.gezk+zip` sit at a fixed offset (30 +
8 = byte 38) of every catalog, after the local file header and the entry
name. A reader MUST verify the first entry is `mimetype`, stored, with
exactly this content, before trusting anything else in the archive.

### 3.2 Writer requirements

- Every entry MUST be STORED (ZIP compression method 0). SQLite pages that
  already carry brotli-compressed bodies gain little from deflate, byte
  identity of a build then does not depend on a zlib implementation, and
  content-addressed hosting can deduplicate unchanged pages across releases.
- Entries after `mimetype` MUST appear sorted by path, comparing UTF-8
  bytes. Names are UTF-8, forward-slashed, relative, without `.` or `..`
  segments, drive letters or backslashes.
- Timestamps MUST be `2000-01-01T00:00:00Z`; the external attributes MUST
  encode mode `0100644`; no extra fields except ZIP64 where required.
- Identical inputs and an identical toolchain MUST produce a byte-identical
  archive (§10).

### 3.3 Reader requirements and limits

A reader MUST reject: directory entries, encrypted entries, symlink entries,
absolute or traversing paths, case-insensitively duplicated names, and any
entry not declared in `manifest.files` other than `mimetype` and
`manifest.json` — and, symmetrically, a declared file that is missing. A
declared file's size MUST match the ZIP metadata before extraction and its
SHA-256 MUST match after. Readers SHOULD enforce sanity limits; the
reference limits are 16,384 entries, a 16 MiB manifest, 8 GiB per entry,
32 GiB total and a 400:1 compression ratio. Assets have their own limits
(§3.4).

### 3.4 Assets

A catalog MAY ship images under `assets/`. An asset path matches
`assets/(<segment>/)*<name>.<ext>` where segments and names use
`[A-Za-z0-9._-]` and never begin with a dot, at most 15 directory segments
of at most 128 characters each, the whole path at most 512 characters, and
`<ext>` (case-insensitive) is one of `png`, `jpg`, `jpeg`, `gif`, `webp`,
`svg`; the media type follows from the extension. Limits: 8 MiB per asset,
256 MiB and 8,192 assets per catalog. Every asset is listed in
`manifest.files` like any other entry and counted in `counts.assets`.

A document references an asset by its archive path
(`![alt](assets/diagrams/flow.png)`); a reader resolves such a target
against the catalog, never against the network, and a body MUST NOT
reference an asset the archive does not ship.

An asset's leading bytes MUST match its extension (PNG, JPEG, GIF and WebP
magic numbers; an SVG is UTF-8 text whose first element after any BOM,
XML declaration, comments and DOCTYPE is `<svg`). An SVG MUST be inert:
readers MUST reject one that contains a `script` or `foreignObject`
element, an event-handler attribute, an entity declaration, a
`javascript:` or `data:text/html` reference, a CSS `@import`, or any
`href` or CSS `url()` that leaves the file other than embedded raster
data (`data:image/png|jpeg|gif|webp`). Readers that serve assets over HTTP
SHOULD send `X-Content-Type-Options: nosniff` and, for SVG, a sandboxing
`Content-Security-Policy`.

## 4. The manifest

`manifest.json` is UTF-8 JSON validated by `schemas/catalog-manifest.schema.json`.

```jsonc
{
  "kind": "gezk-catalog",
  "formatVersion": "0.7",
  "indexSchemaVersion": 4,
  "id": "wikipedia-physics",
  "version": "2026.9.1",
  "name": "Wikipedia: Physics",
  "description": "Physics reference articles from the English Wikipedia.",
  "language": "en",
  "publisher": { "id": "bendyline", "name": "Bendyline", "url": "https://bendyline.com" },
  "createdAt": "2026-09-01T00:00:00.000Z",
  "sourceSnapshot": { "name": "enwiki", "date": "2026-09-01", "taxonomyVersion": "2026.9.1+…" },
  "license": { "name": "CC BY-SA 4.0", "spdx": "CC-BY-SA-4.0", "noticePath": "LICENSES/catalog.txt", "attributionRequired": true },
  "embedding": { /* §6.1 */ },
  "chunking":  { /* §7.1 */ },
  "topics": [
    { "id": "physics", "name": "Physics", "sortKey": "00" },
    { "id": "mechanics", "name": "Mechanics", "parentId": "physics", "sortKey": "2147483649" }
  ],
  "router": {
    "shardTargetChunks": 200000,
    "shards": [ { "id": 0, "path": "index/shards/000.db", "chunks": 199481, "documents": 57210, "centroids": 16, "sha256": "…" } ],
    "totalCentroids": 16
  },
  "counts": { "documents": 57210, "chunks": 199481, "shards": 1, "assets": 0 },
  "files": [ { "path": "index/router.db", "sizeBytes": 123, "sha256": "…" }, … ],
  "requires": { "formatVersion": "0.7", "features": [] },
  "smokeQueries": [ { "query": "Newton's laws of motion", "expectedDocumentIds": ["…"] } ],
  "toolchain": { "name": "@bendyline/gezel-knowledge", "version": "1.1.0" },
  "signature": { "algorithm": "ed25519", "keyId": "…", "canonicalization": "rfc8785", "value": "…" }
}
```

Field notes:

- `kind` MUST be `gezk-catalog`; `formatVersion` MUST be `"0.7"`;
  `indexSchemaVersion` MUST be `4`.
- `createdAt` is an input to the build, never the wall clock (§10).
- `license.noticePath` names an archive entry that MUST be listed in `files`.
  `spdx` is the SPDX identifier when one exists.
- `topics` is the shipped table of contents and MUST have at least one
  entry. `parentId`, when present, names another entry; `sortKey`, when
  present, equals the topic's `sort_key` in the router; ids are unique.
  Every segment of every document's topic path MUST be one of them.
- `counts.assets` is the number of `assets/` entries (§3.4); absent means
  none, as in 0.5.
- `router.shards[].path` is either `index/shards/NNN.db` or
  `index/router.db` when the single shard is embedded in the router (§8.3).
- `files` lists every entry except `mimetype` and `manifest.json`, with its
  byte size and SHA-256.
- `requires.formatVersion` is what a reader must implement to open the
  catalog; `features` reserves room for optional extensions and MUST be
  empty or absent in 0.7.
- `smokeQueries` are full-text queries over the document directory that the
  publisher verified at build time; a reader MAY re-run them after install.
- `toolchain` is provenance, never a compatibility gate.
- `signature`, when present, covers the manifest as described in §9.

## 5. Databases

### 5.1 Common rules

Every database is an SQLite file written with `PRAGMA page_size = 8192`,
`PRAGMA application_id = 0x47455A4B` (`GEZK`), `PRAGMA user_version = 4`
(the index schema version) and sealed with `VACUUM INTO` under
`journal_mode = DELETE`, so no journal or WAL sidecar exists. Readers open
databases read-only and immutable, MUST check `application_id` and
`user_version`, and MUST NOT need any extension: FTS5 is the only virtual
table module used.

Every database has a `meta(key, value)` table echoing `format_version`,
`index_schema_version`, `catalog_id`, `catalog_version`,
`embedding_profile_id`, `chunking_profile_id`, `shard_id` and `chunk_count`;
the router additionally stores `embedding_profile_json`,
`chunking_profile_json`, `created_at` and, optionally, `toolchain_json`. A
reader SHOULD cross-check the echoes against the manifest — a shard from
another catalog copied into place is caught here.

### 5.2 `index/router.db`

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;

CREATE TABLE topics(
  id TEXT PRIMARY KEY, parent_id TEXT, name TEXT NOT NULL,
  description TEXT, sort_key TEXT NOT NULL, document_count INTEGER NOT NULL
);

CREATE TABLE documents(
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL, slug TEXT NOT NULL, summary TEXT,
  language TEXT NOT NULL, topic_id TEXT NOT NULL REFERENCES topics(id),
  ordinal INTEGER,
  shard_id INTEGER NOT NULL,
  chunk_count INTEGER NOT NULL,
  source_url TEXT, source_revision TEXT, source_updated_at TEXT,
  attribution_json TEXT,
  meta_json TEXT,
  body_codec TEXT NOT NULL CHECK (body_codec IN ('none','br')),
  body_blob BLOB NOT NULL
);
CREATE INDEX documents_topic ON documents(topic_id, ordinal, slug);
CREATE INDEX documents_shard ON documents(shard_id);

CREATE TABLE topic_documents(
  topic_id TEXT NOT NULL REFERENCES topics(id),
  document_id TEXT NOT NULL REFERENCES documents(id),
  ordinal INTEGER CHECK (ordinal IS NULL OR
    (typeof(ordinal) = 'integer' AND ordinal BETWEEN -2147483648 AND 2147483647)),
  PRIMARY KEY (topic_id, document_id)
) WITHOUT ROWID;
CREATE INDEX topic_documents_document ON topic_documents(document_id);
CREATE INDEX topic_documents_order ON topic_documents(topic_id, ordinal, document_id);

CREATE TABLE aliases(alias TEXT NOT NULL, document_id TEXT NOT NULL,
  PRIMARY KEY (alias, document_id)) WITHOUT ROWID;

CREATE TABLE shards(
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL,
  chunk_count INTEGER NOT NULL, document_count INTEGER NOT NULL,
  topic_ids_json TEXT NOT NULL, centroid_count INTEGER NOT NULL,
  bytes INTEGER NOT NULL
);

CREATE TABLE route_centroids(
  id INTEGER PRIMARY KEY,
  shard_id INTEGER NOT NULL REFERENCES shards(id),
  embedding BLOB NOT NULL,
  weight INTEGER NOT NULL
);
CREATE INDEX route_centroids_shard ON route_centroids(shard_id);

CREATE VIRTUAL TABLE fts_documents USING fts5(
  title, summary, aliases, document_id UNINDEXED,
  tokenize = 'unicode61 remove_diacritics 2', prefix = '2 3'
);
```

- `documents.body_blob` holds the NFC-normalized, LF-terminated Markdown
  body. `body_codec` is `br` (brotli, quality 5, text mode) for bodies of
  512 bytes or more and `none` below that. A decompressed body MUST NOT
  exceed 16 MiB; readers MUST cap decompression accordingly.
- `documents.topic_id` is the leaf of the document’s **primary** topic
  path. Every segment of every primary or additional path MUST be a
  declared topic and each MUST be the `parent_id` of the next, starting
  at a root (`parent_id IS NULL`). Topic trees MUST be forests (no cycles)
  of depth at most 16. References add document placements, never topic edges.
- `topic_documents` MUST contain every primary placement and every
  additional TOC reference. Each pair `(topic_id, document_id)` MUST occur
  exactly once and reference existing rows. Each document MUST have a
  matching primary row whose `ordinal` equals `documents.ordinal`,
  including NULL. Other rows MAY have independent signed 32-bit ordinals.
- `topics.document_count` MUST equal the number of rows in
  `topic_documents` for that topic. The sum counts placements and MAY
  exceed `counts.documents`, which MUST equal the canonical row count
  in `documents`. The document’s title, body, source attribution, FTS row,
  chunks, embeddings and citation identity are stored only once.
- A reader that lists a topic SHOULD include its descendants. It MUST
  count distinct document IDs in the selected scope and deduplicate before
  sorting and pagination. Subtree counts MUST NOT sum direct counts:
  an article placed at both a parent and a child counts once in that subtree.
- When several placements of a document qualify, choose the first by
  `(ordinal IS NULL), ordinal, topics.sort_key, topic_id` (ascending,
  bytewise for strings). Order the unique documents by that placement’s
  `(ordinal IS NULL), ordinal, documents.slug, documents.id`. Scoped
  listings report the chosen placement’s topic and ordinal. Unscoped
  listings and direct document reads report primary metadata. Search and
  `knowledge://` references always identify the canonical document.
- Readers validating schema 4 MUST reject a missing placement table,
  undeclared targets, duplicate pairs, invalid ordinals, missing or mismatched
  primary rows, and incorrect per-topic or canonical document counts.
- `topics.sort_key` orders siblings bytewise (`ORDER BY sort_key, id`).
  Producers that order topics numerically SHOULD encode the number as
  `zero-padded(order + 2^31)`, ten ASCII digits, so byte order equals
  numeric order, negatives included; digit-keyed siblings therefore sort
  before name-keyed ones.
- `documents.ordinal`, when not NULL, is a signed 32-bit listing position
  for the primary placement. Unscoped readers list `ORDER BY
  (ordinal IS NULL), ordinal, slug, id`: ordered documents first, the rest
  by slug. Scoped readers use the selected placement’s ordinal as above.
- `attribution_json` is a JSON object of free-form string pairs.
- `meta_json`, when not NULL, is a JSON object of producer-defined keys, at
  most 16 KiB of UTF-8, opaque to readers; writers SHOULD emit the RFC 8785
  canonical form. Keys beginning with `gezk.` are reserved for future
  versions of this specification.
- `shards.bytes` is the sealed size of an external shard file and `0` for
  a shard embedded in the router, whose size only the manifest knows.
- `route_centroids.embedding` is a little-endian float32 vector of the
  profile's dimension, L2-normalized.

### 5.2.1 Compiler input (informative)

The normalized document schema adds optional `tocReferences`:

```json
{
  "id": "84283796",
  "title": "Hurricane Polo",
  "topicPath": ["news", "2026-09-28"],
  "ordinal": 9,
  "tocReferences": [
    { "topicPath": ["news", "2026-09-22"], "ordinal": 12 }
  ]
}
```

This fragment omits the other document fields, including the body supplied
once. Each reference has a nonempty root-to-leaf path and optional signed
32-bit ordinal. All paths follow the topic forest. The reference compiler
rejects repeated leaf topics, including a reference to the primary leaf;
it emits one `topic_documents` row per placement. Reference input order
does not change the archive. The body and canonical title are shared; no
per-placement title or redirect document is introduced.

### 5.3 Shard databases

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;

CREATE TABLE chunks(
  id INTEGER PRIMARY KEY,
  chunk_uid TEXT NOT NULL,
  document_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  title TEXT NOT NULL,
  heading_path TEXT NOT NULL,
  heading_text TEXT NOT NULL,
  line_start INTEGER NOT NULL, line_end INTEGER NOT NULL,
  token_count INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE UNIQUE INDEX chunks_uid ON chunks(chunk_uid);
CREATE INDEX chunks_document ON chunks(document_id, ordinal);

CREATE VIRTUAL TABLE fts_chunks USING fts5(
  title, heading_text, text,
  content = 'chunks', content_rowid = 'id',
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE chunk_vectors_bit(
  chunk_id INTEGER PRIMARY KEY,
  v BLOB NOT NULL
);

CREATE TABLE chunk_vectors_int8(
  chunk_id INTEGER PRIMARY KEY,
  v BLOB NOT NULL
);
```

- Chunk ids are shard-local and **dense from 1** in `(document_id, ordinal)`
  order; `chunks.id`, `chunk_vectors_bit.chunk_id`,
  `chunk_vectors_int8.chunk_id` and `fts_chunks.rowid` are the same number.
  A reader MAY rely on row `i` of the vector tables being chunk `i + 1`.
- `heading_path` is a JSON array of heading strings from the top of the
  document to the chunk's nearest heading (depth ≤ 6, the title excluded
  when it equals the H1); `heading_text` joins it with ` > `.
- `chunk_vectors_bit.v` is `ceil(dimensions / 8)` bytes of sign bits and
  `chunk_vectors_int8.v` is `dimensions` signed bytes, both as §6.2.
- The single shard of a small catalog is embedded in `router.db` (§8.3):
  the same tables exist there, `meta` shared.

## 6. Vectors

### 6.1 Embedding profile

The manifest's `embedding` object is the full vector-space identity of the
catalog. Two catalogs share a space only when their profiles are equal;
matching a model name or a dimension alone is unsafe.

```jsonc
{
  "id": "multilingual-e5-small@1",
  "model": {
    "repo": "Xenova/multilingual-e5-small",
    "revision": "761b726dd34fb83930e26aab4e9ac3899aa1fa78",
    "onnxFile": "onnx/model.onnx",
    "onnxDigest": "sha256:4aa845c27760e06e9a686b9d8b5d440eae4b6612cd09e5b522b716d3941f77ff"
  },
  "tokenizer": {
    "kind": "sentencepiece-xlmr",
    "file": "tokenizer.json",
    "digest": "sha256:0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"
  },
  "pooling": "mean",
  "normalized": true,
  "dimensions": 384,
  "maxTokens": 512,
  "queryInstruction": "query: ",
  "passageInstruction": "passage: ",
  "vectorEncoding": "bit+int8",
  "distance": { "stage1": "hamming", "stage2": "cosine" },
  "quantization": {
    "int8":   { "method": "symmetric-linear", "scale": 127 },
    "binary": { "method": "sign", "threshold": 0, "packing": "lsb-first" }
  }
}
```

- `model.repo` is a Hugging Face repository id and `model.revision` a commit
  hash or tag, so a reader can reproduce query vectors with the very weights
  that produced the passages.
- `model.onnxFile` is the repo-relative path of the ONNX graph the vectors
  were produced with; absent, it is `onnx/model.onnx`, the full-precision
  graph. The precision variant is part of the identity: fp16 or int8 weights
  move the floats, and with them the sign bits and int8 codes at the margins,
  so `onnx/model_fp16.onnx` is a different space from `onnx/model.onnx`.
- `model.onnxDigest` and `tokenizer.digest` are `sha256:` followed by 64
  lowercase hex digits — the sha256 of that file's bytes at `revision`
  (`tokenizer.file` defaults to `tokenizer.json`). On the Hub, an LFS file's
  object id is this value, so a pin can be checked against the repository's
  file metadata without a download. A reader SHOULD hash the files it
  actually loaded and treat a mismatch as a different vector space; a
  profile that declares no digest makes no claim.
- Two profiles describe one space when every field except `id`, `maxTokens`
  and the digests is equal, and every digest declared on both sides is
  equal. `id` is a label and `maxTokens` a compile-time bound.
- `queryInstruction` / `passageInstruction` are prefixed verbatim to the
  query text and to every embed input (§7.2); the trailing space is part of
  the value.
- `pooling` is `mean`, `cls` or `last`; `normalized: true` means passages
  are unit vectors, which the cosine rerank assumes.
- `vectorEncoding` names the on-disk encoding. 0.7 defines `bit+int8`. A
  reader MUST reject an encoding it does not implement.

Two profiles are published with the reference implementation:
`multilingual-e5-small@1` (above) and `bge-small-en-v1.5@1`
(`Xenova/bge-small-en-v1.5`, revision `ea104dacec62c0de699686887e3f920caeb4f3e3`,
`onnx/model.onnx` at
`sha256:828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35`,
`tokenizer.json` at
`sha256:d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66`,
query instruction `Represent this sentence for searching relevant passages: `,
empty passage instruction). The conformance kit uses `test-hash-embed@1`, a
deterministic stand-in defined in §13 that pins no files.

### 6.2 The `bit+int8` encoding

Let `x` be the unit passage vector of dimension `d`.

- **int8**: `q[i] = clamp(round(x[i] × 127), −127, 127)`, stored as `d`
  signed bytes. `round` is *round half toward positive infinity*
  (ECMAScript `Math.round`): `63.5 → 64` and `−63.5 → −63`. Dequantization is
  `x̂[i] = q[i] / 127`. `−128` never occurs.
- **bits**: `bit[i] = 1 if x[i] > 0 else 0` (an exact `0.0` is `0`), packed
  LSB-first — byte `j`, bit `k` holds dimension `8j + k` — into
  `ceil(d / 8)` bytes.
- **Rerank score** is `dot(q_float, x̂)` with the float32 unit query vector;
  passages were unit vectors, so this approximates cosine. The query is
  never quantized for the rerank.

Implementations MUST compute these formulas themselves rather than through
a vector library whose rounding may differ; the conformance vectors pin
the rounding rule.

## 7. Chunking

### 7.1 Profile

```jsonc
{ "id": "markdown-chunks@2", "unit": "tokens", "tokenizer": "profile",
  "target": 420, "overlap": 64, "contextHeader": { "max": 64 } }
```

`unit` names what `target`, `overlap` and `contextHeader.max` count:
`tokens` of the embedding profile's tokenizer (`tokenizer: "profile"`) or
`chars` (`tokenizer: "none"`). Counts exclude special tokens.

### 7.2 `markdown-chunks@2`

1. Normalize the body: CRLF → LF, Unicode NFC.
2. Split into blocks with line spans; ATX headings open sections and push
   onto a heading stack that becomes each chunk's `heading_path`.
3. Pack consecutive blocks of a section up to `target` units. A block that
   alone exceeds `target` is split at sentence boundaries, then at unit
   boundaries. Each chunk after the first of a section carries an overlap of
   trailing complete sentences or blocks of at most `overlap` units.
4. Each chunk records `ordinal` (0-based per document), `line_start`,
   `line_end`, `token_count`, `content_hash = SHA-256(text)` in hex, and
   `chunk_uid` (§7.3).
5. The **embed input** of a chunk is
   `passageInstruction + header + text`, where `header` is
   `title + "\n"` when the heading path is empty (or equals the title) and
   `title + "\n" + heading_path.join(" > ") + "\n"` otherwise, truncated to
   the title alone and then to nothing if it exceeds `contextHeader.max`
   units. With `target = 420`, `overlap = 64` and a 64-unit header, every
   embed input fits a 512-token window with room for special tokens.

### 7.3 `chunk_uid`

`chunk_uid` is the lowercase hex of the **first 16 bytes** of

```
SHA-256( utf8(documentId) || 0x00 || utf8(decimal ordinal) || 0x00 || SHA-256(utf8(text)) )
```

where the inner digest contributes its 32 raw bytes. It is content-derived
so identical inputs yield identical ids on any toolchain, and it is the
anchor of the `#chunk=` citation fragment (§11).

## 8. Sharding and routing

### 8.1 Sizing and assignment

A catalog's chunks are divided into shards of about
`router.shardTargetChunks` chunks (200,000 by default); a document's chunks
never split across shards. Within a shard, documents are processed in
`(topicPathKey, documentId)` order, where `topicPathKey` joins the
document's primary topic path with `/`. Additional TOC references do not
participate in shard assignment and MUST NOT duplicate chunks or vectors.

Which shard a document goes to is the producer's choice, provided the
choice is deterministic (§10). Readers MUST NOT assume that a shard covers a
contiguous range of the table of contents: routing (§8.4) uses only the
centroids (§8.2), and `shards.topic_ids_json` lists whatever leaf topics a
shard happens to hold. The reference compiler offers two assignments:

- **Topic fill** (the default). Documents in `(topicPathKey, documentId)`
  order fill shards greedily to `shardTargetChunks`, so shards come out as
  slices of the table of contents.
- **Semantic fill** (opt-in). The compiler embeds every chunk first,
  averages each document's unit chunk vectors into a unit document vector,
  and places documents in `ceil(chunks / shardTargetChunks)` shards by
  balanced k-means over those vectors. The centres are seeded with the
  k-means++ of §8.2 (seed = low 64 bits of
  `SHA-256(catalogId || "#shard-fill" || 0x00 || "0")`). The compiler then
  alternates two steps: a capacity-constrained assignment, and
  chunk-weighted recentring. Each shard's capacity is an even split plus 2%. The
  documents closest to one centre relative to the next choose first, and a
  document whose nearest centre is full takes the nearest one with room.
  Shards are numbered by their first document in `(topicPathKey,
  documentId)` order.

Topic fill separates documents that answer the same questions: an artist,
their albums and their songs sit in different slices. Semantic fill keeps
them together, so a routing budget of `S` shards reaches more of a query's
neighbours. On a 1.85M-chunk Wikipedia music catalog (10 shards, 155
queries, routed recall@8 against an exact scan), recall rose from 83% to
86% at `S = 6` and from 52% to 64% at `S = 3`. With the same corpus cut
into 37 shards, recall at `S = 6` rose from 36% to 56%. Neither assignment
makes a catalog of dozens of shards fully routable at `S = 6`; such
corpora are better published as several catalogs.

### 8.2 Centroids

Each shard carries `k = clamp(ceil(chunks / 12500), 1, 32)` routing
centroids: seeded k-means++ over a deterministic stride sample of at most
65,536 chunk vectors (seed = low 64 bits of
`SHA-256(catalogId || 0x00 || decimal shardId)`, at most 25 iterations),
L2-normalized, stored as float32 little-endian in `route_centroids` with
the number of sampled chunks assigned to each as `weight`.

### 8.3 The embedded shard

When a catalog has at most `shardTargetChunks` chunks it has one shard, and
the compiler embeds its tables in `router.db` instead of writing
`index/shards/000.db`. The `shards` row then reads
`path = 'index/router.db'` and `bytes = 0`. A reader resolves `shards.path`
uniformly, so the embedded case is the ordinary code path with a path that
happens to name the router.

### 8.4 Query routing (informative)

A reader with several shards scores every centroid against the unit query
vector (cosine), takes each shard's best score, and scans the top `S`
shards; the reference implementation uses `S = 3` for proactive retrieval
and `S = 6` for explicit search, sharing the budget across catalogs.
A shard the router does not reach contributes nothing, so once a catalog
has more shards than `S`, how its documents were assigned (§8.1) bounds
semantic recall.
Catalog-wide `fts_documents` search always runs regardless of routing, so
exact-title recall never depends on it.

## 9. Retrieval (informative, with normative encoding rules)

A reader implements:

- **Document search** over `fts_documents` (title, summary, aliases).
- **Chunk search** over `fts_chunks`.
- **Semantic search** in two stages per shard: (1) hamming distance between
  the query's sign bits and every row of `chunk_vectors_bit`, keeping the
  `K` nearest (the reference uses `K = min(512, max(128, 8 × finalK))`);
  (2) cosine rerank of those `K` through `chunk_vectors_int8`. Ties in stage
  1 are broken by ascending chunk id. The full sign-bit table of a 200,000-
  chunk shard is 9.6 MB, so an in-memory linear scan is the reference
  strategy and no index structure is part of the format.

The conformance kit's `hamming` vector pins stage-1 selection order.

## 10. Determinism and reproducibility

Identical inputs and an identical toolchain MUST yield a byte-identical
archive. Everything time- or random-shaped is an input (`createdAt`) or
seeded (k-means); processing order is sorted; chunk ids are content-derived;
databases are sealed with fixed pragmas and `VACUUM INTO`; the archive
stores entries in sorted order with fixed timestamps. Across toolchains
(different ONNX runtimes, SIMD paths), vectors may differ in the last bits,
so publishers SHOULD keep a content-hash → vector cache as the
reproducibility authority of a release.

## 11. `knowledge://` references

```
knowledge-uri   = "knowledge://" publisher-id "/" catalog-id "/" enc-document-id [ "#" fragment ]
enc-document-id = seg *( "/" seg )                 ; ≤ 512 characters encoded
seg             = 1*( unreserved / pct-encoded )
fragment        = ("chunk=" 32HEXDIG) / ("line=" 1*DIGIT ["-" 1*DIGIT])
```

Document ids are percent-encoded per path segment (every byte outside
`unreserved` ∪ `/`). The publisher is part of the authority because catalog
ids are unique only per publisher. A reader MUST reject an ill-formed
reference rather than repair it.

## 12. Signatures and registries

- A manifest MAY carry `signature`: Ed25519 over the RFC 8785 (JCS)
  canonical form of the manifest **without** its `signature` member, base64
  encoded; `keyId` is the first 16 hex characters of SHA-256 over the DER
  SubjectPublicKeyInfo of the signing key. A verifier holds trust anchors
  indexed by `keyId`; an unknown key is a failure, never a scan.
- `manifest.json` is not one of the manifest's own `files` entries, so the
  per-file digests of §3.3 bind a catalog's **content** to its manifest but
  cannot detect a rewritten manifest. Identity — publisher, catalog id,
  version, counts and profiles — is bound only by `signature`. A reader that
  validates a catalog without checking `signature` against a trust anchor
  MUST NOT present the result as verified provenance.
- The archive's own SHA-256 cannot live inside the archive; it belongs in
  whatever names the archive — a registry row, a package pin, a download
  manifest. Consumers MUST verify the archive digest they were promised
  before extracting.
- A publisher MAY publish a registry document (`schemas/registry-index.schema.json`,
  `kind: "gezk-registry"`) listing releases with `url`, `archiveBytes` and
  `contentDigest`, signed exactly like a manifest. A registry only locates
  bytes; content trust stays with the digest.

## 13. Conformance kit

`conformance/vectors.json` and `conformance/fixtures/conformance-0.7.gezk`
are generated from the reference implementation; `vectors.legacy` lists the
fixtures of earlier generations that still ship beside it
(`conformance-0.5.gezk` and `conformance-0.6.gezk`) with their own expectations. The fixture is signed with
a **test** key whose private half is published in the generator: it proves
signature handling, never provenance. Its documents were embedded with
`test-hash-embed@1`, a deterministic stand-in for a model that any
implementation can reproduce:

1. `h = SHA-256(utf8(text))`; consume bytes of `h`; when exhausted, set
   `h = SHA-256(h)` and continue.
2. Each byte `b`, read as a signed int8, yields `(b + 0.5) / 128`.
3. Take 384 values; the compiler L2-normalizes the result.

A reader passes when it reproduces every vector, verifies the fixture's
digest and signature, extracts and validates it, answers the recorded
full-text queries, round-trips the recorded document body, returns the
recorded chunk first for the semantic probe embedded with this embedder,
reproduces the recorded nested-topic rollup, ordered listing, metadata
sample and asset digest (introduced in 0.6), and the `sharedToc` placements
(0.7), including unique subtree counts, per-placement ordering and
deduplication before pagination. It does the same for every `legacy`
fixture under that generation's rules.

## 14. Distribution (informative)

Catalogs are files; any host will do. The reference publisher stores each
catalog as a Hugging Face dataset repository (one per catalog, commit-pinned
download URLs, the archive digest recorded wherever the catalog is listed)
next to a Parquet companion of the same documents, chunks and embeddings for
tools that would rather scan tables than open SQLite.

The existing companion exports one Parquet file per shard
(`documents-NNN`, `chunks-NNN`) plus `topics`. Document `topic_path`
and `ordinal` describe the primary placement; `meta` is canonical JSON.
The vector columns retain the int8 and sign-bit encodings from §6, with
dequantisation rules in file metadata. This companion does not currently
export `topic_documents` and is therefore not a lossless representation
of a shared TOC; use the archive’s router for all placements. No Parquet
consumer should infer complete membership from primary topic paths.

## Appendix A. Reader rollup (informative)

For schema 4, select all placements in the requested subtree, then choose
one per document before ordering and pagination. The depth bound terminates
a hostile cycle; validation still rejects cyclic topic graphs.

```sql
WITH RECURSIVE sub(id, depth) AS (
  SELECT ?, 0
  UNION ALL
  SELECT t.id, sub.depth + 1 FROM topics t JOIN sub ON t.parent_id = sub.id
  WHERE sub.depth < 32
), scoped AS (
  SELECT td.document_id, td.topic_id, td.ordinal, t.sort_key
  FROM topic_documents td JOIN topics t ON t.id = td.topic_id
  WHERE td.topic_id IN (SELECT id FROM sub)
), ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY document_id
    ORDER BY (ordinal IS NULL), ordinal, sort_key, topic_id) AS placement_rank
  FROM scoped
)
SELECT d.id, d.title, d.slug, p.topic_id, p.ordinal
FROM documents d JOIN ranked p ON p.document_id = d.id AND p.placement_rank = 1
ORDER BY (p.ordinal IS NULL), p.ordinal, d.slug, d.id LIMIT ? OFFSET ?;
```

Use `SELECT COUNT(DISTINCT document_id) FROM scoped` for the matching
count query. For direct listings, seed `sub` without its recursive term.
For all subtree totals, walk from every topic and count distinct document IDs
per walk root with a LEFT JOIN, so empty topics have total zero. For schemas
2 and 3, retain the single-placement queries against `documents.topic_id`
described in those specifications; those catalogs have no placement table.
