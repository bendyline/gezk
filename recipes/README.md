# Recipes

Short ways to use a published gezk catalog outside gezel. Every recipe starts
from the Parquet companion a release ships beside the archive
(`parquet/<version>/` in the catalog's Hugging Face dataset repo), so none of
them needs a gezk reader.

`Bendyline/wikipedia-physics` stands in for a catalog repository throughout;
substitute the one you are reading. The queries are written against the
companion's schema, described in §14 of the specification. In the 0.7 draft,
Parquet document topic paths describe primary placements only. Shared TOC
memberships remain in the archive's `topic_documents` table; use the archive
reader when you need the complete TOC.

- [DuckDB over the Parquet companion](duckdb-parquet.md)
- [Loading with the `datasets` library](datasets-library.md)
- [Building a Lance dataset from the Parquet](lance-from-parquet.md)
- [Reading the archive itself in Python](../reference/python/README.md) — the
  reference reader (`python -m gezk inspect|verify|search`).
