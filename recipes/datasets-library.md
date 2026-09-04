# Loading with the `datasets` library

Each catalog repo's dataset card declares three configs — `documents`
(default), `chunks` and `topics` — pointing at the newest Parquet companion.

```python
from datasets import load_dataset

documents = load_dataset("Bendyline/wikipedia-physics", "documents", split="train")
chunks = load_dataset("Bendyline/wikipedia-physics", "chunks", split="train")

print(documents[0]["title"], documents[0]["source_url"])
row = chunks[0]
vector = [q / 127 for q in row["embedding"]]        # int8 → unit vector
bits = row["embedding_bit"]                          # ceil(dim/8) bytes, LSB-first sign bits
```

Pin a release by revision so a re-run reads the same bytes:

```python
load_dataset("Bendyline/wikipedia-physics", "chunks", revision="<commit sha>")
```

The `release.json` beside each archive lists the commit, the archive sha256
and every Parquet file's sha256, so a downloaded companion can be checked
against what the publisher verified.
