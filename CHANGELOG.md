# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project aims to follow
[Semantic Versioning](https://semver.org/) for the **package**, which is distinct from
the **`.chiro.json` format version** (`schema_version`) it implements.

## [Unreleased]

## [0.1.0] — 2026-09-06

First tagged release: Milestones 0 & 1 — the frozen `.chiro.json` format, the reference
library, and the CLI.

### Format (`schema_version`)

- **`1.0`** — the frozen core: three-layer hashing (per-file `md5`, `body_hash` over the
  canonicalised `files` array, whole-file hash held externally), the two-path model
  (`relpath` vs `source_path`), and provenance semantics (`self-deposited` vs
  `observed-download`).
- **`1.1`** — three optional, advisory-only staleness fields: `concept` and
  `record_version` (header) and `file_version` (entry). A manifest declares `1.1` only
  when it carries one of these, so every `1.0` manifest is byte-identical.
- Canonicalisation is frozen and pinned by **70 conformance vectors** that ship in the
  package, each carrying the literal canonical string and independently re-derivable
  with coreutils (`chiro test-vectors`).

### Library

- Zero required runtime dependencies. `dvc` (reading `.dvc` / `dvc.lock`) and `schema`
  (JSON Schema validation) are optional extras.
- Public API for a data-access layer such as `archivefs`: `load()`,
  `Manifest.fetch_plan()`, `verify_tree()`, and building a manifest from checksum tuples
  or a fetched directory.

### CLI (`chiro`)

- `write`, `capture`, `verify`, `validate`, `info`, `ls`, `diff`, `ref`, `remap`,
  `dvc-dir`, `schema`, `test-vectors`.
- Manifests are named `<digest>.chiro.json` by default (spec §1.1); an `-o` path ending
  in `.json` is used verbatim (the human-readable deposit-copy escape hatch).
- Output-policy guards: refuses to clobber an existing manifest (`--force`) or to write
  an empty one (`--allow-empty`).
- `write` / `capture` accept `--concept` and `--record-version` (each makes the manifest
  `1.1`).
- Exit codes are a contract: `0` success, `1` a finding, `2` the tool could not run.

### Interop

- DVC `.dir` equivalence is a derived value (`chiro dvc-dir`), tested byte-for-byte
  against the real `dvc` CLI — it is **not** `body_hash`.

### Notes

- Not yet on PyPI; install from the tag
  (`pip install "chirograph @ git+https://github.com/Swarbricklab/chirograph@v0.1.0"`).

[Unreleased]: https://github.com/Swarbricklab/chirograph/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Swarbricklab/chirograph/releases/tag/v0.1.0
