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
- **Immutable, content-addressed references** (`chiro://<hash>`) for cases where the source repository
  offers no versioning — the manifest freezes a dataset snapshot even if the upstream
  mutates.
- **Provenance** distinguishing "this is what I published" (`self-deposited`) from
  "this is what I downloaded on this date" (`observed-download`).

## Install

```bash
pip install chirograph                 # no runtime dependencies at all
pip install 'chirograph[dvc]'          # + reading .dvc / dvc.lock files
pip install 'chirograph[schema]'       # + JSON Schema validation
```

The core has **zero required dependencies**: it is the reference implementation of a
file format, and a consumer of a checksum-verification library has the best possible
reason to want its supply chain to be nothing.

For development, `environment.yaml` builds a conda environment with the extras and
tooling:

```bash
conda env create -f environment.yaml && conda activate chirograph
pytest && ruff check . && mypy
```

## Use

Describe a directory you are about to deposit:

```bash
chiro write ./deposit --accession GSE000000 -o tmp.chiro.json
D=$(chiro ref tmp.chiro.json | sed 's|^chiro://[a-z0-9]*:||')
mv tmp.chiro.json "$D.chiro.json"        # manifests are named by their own digest
```

Capture a dataset you downloaded, reorganising a flat deposit into a tidy workspace
tree as you go (one rule does the whole worked example in docs/howto-reference-data.md):

```bash
chiro capture ./deposit \
    --source-url 'geo://GSE000000' \
    --accession GSE000000 \
    --relpath-from-source '^GSE000000_([^_]+)_(.*)$=>\1/\2' \
    -o tmp.chiro.json
```

Transcribe what DVC has already hashed, without re-reading a byte:

```bash
chiro write --from-dvc-cache data.dvc -o tmp.chiro.json
```

Check bytes against a manifest, or just the manifest's own integrity:

```bash
chiro verify   "$D.chiro.json" --root ./deposit --source-layout
chiro validate "$D.chiro.json"                 # reads no data — a registry's Tier-1 gate
chiro verify   "$D.chiro.json" --sample 100    # bounded-cost spot check
```

Detect that an upstream record has changed under a stable accession:

```bash
chiro capture ./deposit --source-url geo://GSE000000 -o tmp.chiro.json
chiro diff <first-digest>.chiro.json tmp.chiro.json
```

A manifest never written to disk can be checked by streaming it:

```bash
curl -sL https://example.org/<digest>.chiro.json | chiro validate /dev/stdin
chiro validate <(curl -sL https://example.org/<digest>.chiro.json)
```

`chiro --help` lists the rest: `info`, `ls`, `ref`, `remap`, `dvc-dir`, `schema`,
`test-vectors`.

**Exit codes** are part of the contract, so this can run as a CI gate: `0` success,
`1` a *finding* (bytes or manifest disagree), `2` the tool could not run.

### As a library

```python
import chirograph

manifest = chirograph.load(manifest_path)  # validates schema + Layer 2
for item in manifest.fetch_plan():  # fetch resolution; see docs/chiro-format-rationale.md §11
    ...  # fetch item.url, place at item.relpath, check against item.md5

# Check bytes. `use_source_layout=True` looks for files where they sit in the
# deposit; the default looks for them in the workspace layout.
report = chirograph.verify_tree(manifest, "deposit/", use_source_layout=True)
assert report.ok, report.explain()
```

`fetch_plan()` is the intended integration point for a data-access layer such as
`archivefs`, so it does not have to re-derive the two-path model (or rediscover the
`urljoin` trailing-slash trap).

## The `.chiro.json` format

A Chirograph manifest is a JSON file that mirrors DVC's `.dir` format (`relpath` /
`md5` entries, plus `size`) with a header block adding provenance, a two-path model
(where a file lives in the repository vs. where it materialises in your workspace),
and a body-integrity hash for standalone use. See
[`docs/chiro-format-spec.md`](docs/chiro-format-spec.md).

Canonicalisation is the fragility point — a reimplementation that canonicalises
*almost* identically produces non-matching hashes silently — so it is **frozen and
pinned by published conformance vectors** that ship inside the package:

```bash
chiro test-vectors --check     # check an implementation against the freeze
chiro test-vectors             # the vectors themselves, for a reimplementation
```

Each vector carries the exact canonical *string*, not just its hash, so a mismatch
can be diffed rather than guessed at.

## Status

**Format frozen; reference library and CLI implemented.** The registry layer is
deliberately deferred — an in-repo manifest already does most of the job, and the
registry is an optimisation over that baseline rather than a prerequisite.

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
- **`chiro://` resolution and config:** [`docs/resolution-and-config.md`](docs/resolution-and-config.md) *(design, largely unbuilt)*
- **Manifest registry design:** [`docs/manifest-registry-design.md`](docs/manifest-registry-design.md) *(design, deferred)*
- **Implementation plan / status:** [`docs/plan.md`](docs/plan.md)
- **Decisions taken while implementing:** [`docs/decisions.md`](docs/decisions.md) — dated record; its spec section numbers refer to spec v0.3, before the split
- **Ideas & extensions (not built):** [`docs/ideas.md`](docs/ideas.md)

> Two claims in the spec about DVC `.dir` equivalence were disproved by checking them
> against a real DVC installation, and are corrected in spec v0.3. `body_hash` is
> **not** a DVC `.dir` hash; the equivalence is a separately derived value
> (`chiro dvc-dir`), tested byte-for-byte against real `dvc add` output. See
> [decisions D-01 and D-02](docs/decisions.md).

## Related projects

- **[archivefs](https://github.com/swarbricklab/archivefs)** — a set of read-only
  fsspec filesystems for scientific data archives (EGA, Zenodo, GEO, …), consumable
  by DVC. `archivefs` *optionally* reads Chirograph manifests to gain download-free
  checksums and friendly workspace layout, but works without them. Chirograph is the
  manifest format; `archivefs` is the data-access layer.
