"""Validate the conformance fixture's manifest (and its embedded profiles)
against the published JSON Schemas."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from gezk import read_manifest

ROOT = Path(__file__).resolve().parents[3]
KIT = ROOT / "conformance"
SCHEMAS = ROOT / "schemas"


def main() -> int:
    vectors = json.loads((KIT / "vectors.json").read_text("utf-8"))
    manifest = read_manifest(KIT / vectors["fixture"]["path"])
    checks = [
        ("catalog-manifest.schema.json", manifest),
        ("embedding-profile.schema.json", manifest["embedding"]),
        ("chunking-profile.schema.json", manifest["chunking"]),
    ]
    failures = 0
    for name, instance in checks:
        schema = json.loads((SCHEMAS / name).read_text("utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(instance))
        for error in errors:
            failures += 1
            print(f"{name}: {'/'.join(str(p) for p in error.absolute_path)}: {error.message}")
        print(f"{name}: {'ok' if not errors else f'{len(errors)} error(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
