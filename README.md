# Chirograph

**Portable, content-addressed manifests for scientific datasets.**

[![PyPI version](https://img.shields.io/pypi/v/chirograph.svg)](https://pypi.org/project/chirograph/)
[![Python versions](https://img.shields.io/pypi/pyversions/chirograph.svg)](https://pypi.org/project/chirograph/)
[![License: MIT](https://img.shields.io/pypi/l/chirograph.svg)](https://github.com/swarbricklab/chirograph/blob/main/LICENSE)
<!-- CI badge renders only on a public repo (GitHub 404s it anonymously while private):
[![CI](https://github.com/swarbricklab/chirograph/actions/workflows/ci.yml/badge.svg)](https://github.com/swarbricklab/chirograph/actions/workflows/ci.yml)
-->


A *chirograph* was a medieval document written in duplicate with a word inscribed
across the join, then cut through that word so that authenticity could be proven by
matching the two halves — content-addressed verification, centuries early. A
Chirograph manifest does the same for data: it records a dataset's files, their
checksums, and their provenance, so that any copy can be verified against it, and any
drift from it detected.

## What it's for

Many scientific data repositories don't expose retrievable per-file checksums, and
some (e.g. GEO) don't version their records at all — a stable accession can mutate in
place. Chirograph adds the missing rigor as a thin layer *on top of* whichever
repository the data must live in:

- **Retrievable checksums** where the repository withholds them.
- **Immutable, content-addressed references** (`chiro://<hash>`) where the source
  offers no versioning — the manifest freezes a snapshot even if the upstream mutates.
- **Provenance** distinguishing "this is what I published" (`self-deposited`) from
  "this is what I downloaded, on this date" (`observed-download`).

The core is the **reference implementation of a file format**, with **zero required
dependencies**: a consumer of a checksum-verification library has the best possible
reason to want its supply chain to be nothing.

## Install

```bash
pip install chirograph            # core (zero dependencies)
pip install chirograph[dvc]       # + reading .dvc / dvc.lock
pip install chirograph[schema]    # + JSON Schema validation
```

Until the first PyPI release, install from a tag instead:

```bash
pip install "chirograph @ git+https://github.com/Swarbricklab/chirograph@v0.2.1"
```

For development, `environment.yaml` builds a conda environment with the extras and
tooling (`conda env create -f environment.yaml`); then `pytest && ruff check . && mypy`.

## Use

Describe a directory before you deposit it. The manifest is named after its own
whole-file hash (spec §1.1), so the filename *is* the `chiro://` reference:

```bash
chiro write ./deposit --accession GSE000000 -o .chiro/manifests
```

Check bytes against a manifest, or just the manifest's own integrity:

```bash
chiro verify   .chiro/manifests/<digest>.chiro.json --root ./deposit --source-layout
chiro validate .chiro/manifests/<digest>.chiro.json   # reads no data — a registry's Tier-1 gate
```

Detect that an upstream record changed under a stable accession — each observation
gets its own digest name, so they coexist and can be diffed:

```bash
chiro capture ./deposit --source-url geo://GSE000000 -o .chiro/manifests
chiro diff .chiro/manifests/<first>.chiro.json .chiro/manifests/<second>.chiro.json
```

`chiro --help` lists the rest (`capture`, `info`, `ls`, `ref`, `remap`, `dvc-dir`,
`schema`, `test-vectors`); the how-tos below walk the full workflows, including
reorganising a flat deposit into a tidy workspace tree and transcribing what DVC has
already hashed. **Exit codes are part of the contract**, so this runs as a CI gate:
`0` success, `1` a *finding* (bytes or manifest disagree), `2` the tool could not run.

### As a library

```python
import chirograph

manifest = chirograph.load(manifest_path)  # validates schema + body integrity
for item in manifest.fetch_plan():  # fetch item.url → item.relpath, check item.md5
    ...

report = chirograph.verify_tree(manifest, "deposit/", use_source_layout=True)
assert report.ok, report.explain()
```

`fetch_plan()` is the intended integration point for a data-access layer such as
[archivefs](https://github.com/swarbricklab/archivefs), so it need not re-derive the
two-path model (or rediscover the `urljoin` trailing-slash trap).

## The `.chiro.json` format

A manifest mirrors DVC's `.dir` format (`relpath` / `md5` / `size` entries) with a
header adding provenance, a two-path model (where a file lives at the source vs. where
it materialises in your workspace), and a body-integrity hash for standalone use. The
per-file digest is **`md5` or `sha256`** (one `hash_algorithm` per manifest), so an
archive that publishes only sha256 — a git-lfs source, say — is pinned as faithfully as
an md5 one. Schema `2.0` adds an optional `containers` block for member-granular
provenance of archive files (ZIP / tar). See
[`docs/chiro-format-spec.md`](docs/chiro-format-spec.md).

Canonicalisation is the fragility point — a reimplementation that canonicalises
*almost* identically produces non-matching hashes silently — so it is **frozen and
pinned by published conformance vectors** that ship in the package. Each vector carries
the exact canonical *string*, not just its hash, so a mismatch can be diffed:

```bash
chiro test-vectors --check     # check an implementation against the freeze
chiro test-vectors             # the vectors themselves, for a reimplementation
```

## Status

**Format frozen; reference library and CLI implemented, through schema `2.0`.** All
three schema versions are read and written — `1.0` (core), `1.1` (staleness fields),
and `2.0` (the container block, shipped in v0.2.0) — with md5 *and* sha256 digests. What
remains deferred is the **registry** layer (network resolution of a `chiro://`
reference): an in-repo manifest already does most of the job, so the registry is an
optimisation over that baseline, not a prerequisite.

### How-to

1. [Include public reference data in a project](docs/howto-reference-data.md)
2. [Include data you are depositing](docs/howto-deposit-data.md) — and check the deposit matches your workspace
3. [Pull tracked data, and verify it](docs/howto-pull-and-verify.md) — including what a failure means
4. [Publish data and code together](docs/howto-publish.md)
5. [Detect that upstream data has changed](docs/howto-drift.md)
6. [Adopt Chirograph in a repo that already uses DVC](docs/howto-adopt-dvc.md)

### Reference

- **Format spec (normative):** [`docs/chiro-format-spec.md`](docs/chiro-format-spec.md)
- **Why the format is this way:** [`docs/chiro-format-rationale.md`](docs/chiro-format-rationale.md)
- **Container model (ZIP/tar members, schema 2.0):** [`docs/container-model.md`](docs/container-model.md) *(format implemented in 0.2.0)*
- **`chiro://` resolution and config:** [`docs/resolution-and-config.md`](docs/resolution-and-config.md) *(design; mostly aspirational)*
- **Updatability & `dvc update`:** [`docs/updatability-and-dvc-update.md`](docs/updatability-and-dvc-update.md) *(design, with measured findings)*
- **Drift-check & update design:** [`docs/design-check-and-update.md`](docs/design-check-and-update.md) *(design, unbuilt)*
- **Manifest registry design:** [`docs/manifest-registry-design.md`](docs/manifest-registry-design.md) *(design, deferred)*
- **Decisions taken while implementing:** [`docs/decisions.md`](docs/decisions.md) — dated record; its spec section numbers refer to spec v0.3, before the split
- **Implementation plan / status:** [`docs/design/plan.md`](docs/design/plan.md)
- **Ideas & extensions (not built):** [`docs/design/ideas.md`](docs/design/ideas.md)

> Two claims in the spec about DVC `.dir` equivalence were disproved by checking them
> against a real DVC installation, and are corrected in spec v0.3. `body_hash` is
> **not** a DVC `.dir` hash; the equivalence is a separately derived value
> (`chiro dvc-dir`), tested byte-for-byte against real `dvc add` output. See
> [decisions D-01 and D-02](docs/decisions.md).

## Related projects

- **[archivefs](https://github.com/swarbricklab/archivefs)** — read-only fsspec
  filesystems for scientific data archives (EGA, Zenodo, Mendeley Data, cBioPortal, GEO,
  BioImage Archive), consumable by DVC. It *optionally* reads Chirograph manifests to
  gain download-free checksums and a friendly workspace layout, but works without them,
  and is the intended caller of `fetch_plan()`. Its sha256-based backends (Mendeley,
  cBioPortal) pin to a `sha256` manifest; nothing in Chirograph is archive-specific — the
  `concept` CURIE is a free-form string (`mendeley:…`, `cbioportal:…`, `doi:…`,
  `geo:…`), so a new archive needs no change here.
