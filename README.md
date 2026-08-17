# Chirograph

**Portable, content-addressed manifests for scientific datasets.**

A *chirograph* was a medieval document written in duplicate with a word inscribed
across the join, then cut through that word so that authenticity could be proven by
matching the two halves — content-addressed verification, centuries early. A
Chirograph manifest does the same for data: it describes a dataset's files, their
checksums, and their provenance, so that any copy can be verified against it, and any
drift from it detected.

## What it's for

Many scientific data repositories don't expose retrievable per-file checksums, and
some (e.g. GEO) don't version their records at all — a stable accession can mutate in
place. Chirograph adds the missing rigor as a thin layer *on top of* whichever
repository the data must live in:

- **Retrievable checksums** where the repository withholds them.
- **Immutable, content-addressed references** (`chiro://<hash>`) where the repository
  offers no versioning — the manifest freezes a dataset snapshot even if the upstream
  mutates.
- **Provenance** distinguishing "this is what I published" (`self-deposited`) from
  "this is what I downloaded on date D" (`observed-download`).

## The `.chiro.json` format

A Chirograph manifest is a JSON file that mirrors DVC's `.dir` format (`relpath` /
`md5` / `size` entries) with a header block adding provenance, a two-path model
(where a file lives in the repository vs. where it materialises in your workspace),
and a body-integrity hash for standalone use. See
[`docs/chiro-format-spec.md`](docs/chiro-format-spec.md).

## Status

Design stage. This repo holds the format spec, the design of the manifest registry
layer, and the implementation plan. The reference library/CLI (generate, verify,
reconcile) is not yet built.

- **Format spec:** [`docs/chiro-format-spec.md`](docs/chiro-format-spec.md)
- **Manifest registry design:** [`docs/manifest-registry-design.md`](docs/manifest-registry-design.md)
- **Implementation plan / checklist:** [`docs/plan.md`](docs/plan.md)

## Related projects

- **[archivefs](https://github.com/swarbricklab/archivefs)** — a set of read-only
  fsspec filesystems for scientific data archives (EGA, Zenodo, GEO, …), consumable
  by DVC. `archivefs` *optionally* reads Chirograph manifests to gain download-free
  checksums and friendly workspace layout, but works without them. Chirograph is the
  manifest format; `archivefs` is the data-access layer.

## Naming

Retired working names: `xdir` (invisible etymology, collided with directory-listing
tools), `tessera` and `bulla` (both taken — `bulla` by a conceptually adjacent
agent-receipt tool). The technical token is `chiro`: files `.chiro.json`, references
`chiro://…`, CLI `chirograph` (alias `chiro`).
