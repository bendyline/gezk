# Building a Lance dataset from the Parquet

gezk keeps SQLite as its container (see the format's decision record in the
gezel repo, ADR 0012) — but a Lance dataset with a vector index is a few
lines away when remote query or lakehouse tooling is what you need.

```python
import duckdb, lance, pyarrow as pa

con = duckdb.connect()
table = con.sql("""
  SELECT chunk_uid, document_id, title, text,
         list_transform(embedding, x -> x / 127.0)::FLOAT[384] AS vector
  FROM 'parquet/2026.9.1/chunks-*.parquet'
""").arrow()

ds = lance.write_dataset(table, "wikipedia-physics.lance", mode="overwrite")
ds.create_index("vector", index_type="IVF_PQ", metric="cosine", num_partitions=256)
ds.to_table(nearest={"column": "vector", "q": query_vector, "k": 24})
```

Replace `384` with the profile's `dimensions`. The dequantised vectors are
within int8 quantisation error of the model's output; for exact float32
vectors, re-embed the `text` column with the profile's model and instruction.
