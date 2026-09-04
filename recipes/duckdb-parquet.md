# DuckDB over the Parquet companion

The companion mirrors the archive's tables: `documents-NNN.parquet` and
`chunks-NNN.parquet` per shard, plus `topics.parquet`. Column names are the
gezk DDL names; `embedding` is the int8 vector exactly as the archive stores
it and `embedding_bit` the sign-bit vector (spec §6).

```sql
-- Straight from the Hub (needs the httpfs extension, installed on demand).
SELECT title, heading_text, text
FROM 'hf://datasets/Bendyline/wikipedia-physics/parquet/2026.9.1/chunks-*.parquet'
WHERE text ILIKE '%superconduct%'
LIMIT 10;

-- The rules travel with the file.
SELECT key, value
FROM parquet_kv_metadata('chunks-000.parquet');
```

## Dequantise and score

```sql
-- x = q / 127 turns an int8 row back into the unit vector the profile produced.
WITH q AS (
  SELECT list_transform(embedding, x -> x / 127.0) AS v, chunk_uid, text
  FROM 'chunks-*.parquet'
)
SELECT chunk_uid, text, list_cosine_similarity(v, $query_vector) AS cosine
FROM q
ORDER BY cosine DESC
LIMIT 24;
```

`$query_vector` is the catalog's embedding profile applied to your query
(the profile's `model`, `queryInstruction`, `pooling` and `normalized` fields
say exactly how). The reference reader does not embed: it takes a query
vector you produce, so use whatever runtime already loads the profile's
model.
Comparing an int8 catalog vector against a float query vector reproduces the
archive's stage-2 score.

## Stage 1 on the bit vectors

```sql
-- Hamming distance between sign-bit vectors: popcount of the XOR.
SELECT chunk_uid,
       bit_count(embedding_bit::BIT # $query_bits::BIT) AS hamming
FROM 'chunks-000.parquet'
ORDER BY hamming
LIMIT 256;
```

`$query_bits` packs `x > 0` of the query vector LSB-first into
`ceil(dimensions / 8)` bytes, the same packing the archive uses.
