# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project aims to follow
[Semantic Versioning](https://semver.org/) for the **package**, which is distinct from
the **`.chiro.json` format version** (`schema_version`) it implements.

## [Unreleased]

## [0.2.1] — 2026-10-26

A packaging and documentation release that prepares chirograph for its first
publication to PyPI and conda-forge. **No library API or `.chiro.json` format change:**
every `0.2.0` manifest and caller is unaffected.

### Packaging

- PEP 639 license metadata (`license = "MIT"`, `license-files`), a complete set of trove
  classifiers (Python 3.10–3.13, OS Independent, `Typing :: Typed`), and project URLs
  (Issues, Changelog, Documentation). A `py.typed` marker ships in the package.
- A conda-forge recipe (`conda-recipe/meta.yaml`) and a `RELEASING.md` documenting the
  PyPI (Trusted Publishing) and conda-forge path.

### Documentation

- Refreshed the docs to match the implemented state — "the tool is more mature than the
  docs implied". Corrected stale *unbuilt / aspirational* markers: the schema `2.0`
  **container model** (shipped in v0.2.0, normative in the spec §9) and the
  `chiro://`-dependency **`dvc pull`** path (implemented in archivefs, which now
  registers `chiro`) are described as built; genuinely-deferred items (network
  **registry** resolution of a bare `chiro://<hash>`, `chiro freeze`) stay marked.
- Documented that the `md5`-**or-`sha256`** choice makes sha256-only archives
  first-class, and that `archivefs`' new **Mendeley Data** and **cBioPortal** backends
  (sha256, with free-form `concept` CURIEs) pin without any Chirograph change.
- Trimmed the README and moved the internal design notes (`ideas.md`, `plan.md`) under
  `docs/design/`, keeping the top-level docs surface to the spec, rationale, decisions,
  and how-tos.

### Fixed

- The source-tree version fallback (`version.py` `_FALLBACK`) still read `0.1.0` after
  the 0.2.0 release; corrected, and `tests/test_version.py` now guards it against drift.

### Tests

- Added a regression test pinning the "new backend" guarantee: a `sha256` manifest with
  a non-`doi:` concept CURIE (`cbioportal:…`, `mendeley:…`) and a git-ref
  `record_accession` round-trips and validates.

## [0.2.0] — 2026-09-07

A backward-compatible release: the library reads and writes `.chiro.json` schema
versions `1.0`, `1.1`, and now `2.0`. Nothing in the library API changed for existing
callers; every `1.x` manifest is byte-for-byte unchanged.

### Format (`schema_version`)

- **`2.0`** — an optional top-level `containers` block describing archive containers
  (`.zip`, `.tar`, `.tar.gz`, …) and their members, for member-granular provenance and
  download-free confirmation of ZIP members (`docs/container-model.md`, spec §9). Members
  link to a container by `source_path` under its `source_root`; a per-member `crc32`
  (ZIP) is the cheap cross-check. A manifest is `2.0` **iff** it carries containers, so
  every `1.x` manifest is byte-for-byte unchanged; `body_hash` then covers the
  `{containers, files}` document. A container's `format` selects the confirm strategy,
  and an unrecognised format is treated as opaque rather than rejected.
- New public API: the `Container` dataclass; `chiro info` reports `containers`.

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

[Unreleased]: https://github.com/Swarbricklab/chirograph/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/Swarbricklab/chirograph/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Swarbricklab/chirograph/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Swarbricklab/chirograph/releases/tag/v0.1.0
