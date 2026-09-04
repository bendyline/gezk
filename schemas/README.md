# gezk 0.5 JSON Schemas

Generated from the `@bendyline/gezk` Zod definitions by
`pnpm --filter @bendyline/gezk export-schemas` in the gezel repository —
do not edit by hand. Refinements that JSON Schema cannot express (the
Windows reserved-name rule on versions, NFC normalization of document ids)
are enforced by conforming readers on top of these schemas.

| File | Validates |
| --- | --- |
| `catalog-manifest.schema.json` | `manifest.json` inside a `.gezk` |
| `registry-index.schema.json` | A publisher's registry `index.json` |
| `source-notices.schema.json` | `LICENSES/source-notices.json` |
| `embedding-profile.schema.json` | The `embedding` block of a manifest |
| `chunking-profile.schema.json` | The `chunking` block of a manifest |
| `catalog-document.schema.json` | One normalized document fed to a compiler |
