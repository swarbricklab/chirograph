# Chirograph — Implementation Plan

High-level checklist for the Chirograph format, library, and (deferred) registry.
Detail lives in the design docs — this file links rather than repeats.

- **Format:** [`chiro-format-spec.md`](chiro-format-spec.md)
- **Registry design:** [`manifest-registry-design.md`](manifest-registry-design.md)
- **Decisions taken while implementing:** [`decisions.md`](decisions.md)
- **Ideas & extensions (not built):** [`ideas.md`](ideas.md)
- **Sibling project (data access):** [`archivefs`](https://github.com/swarbricklab/archivefs)

## Scope

Chirograph is the **manifest format + reference library/CLI**, and (later) the
**registry framework + reference registry**. It is *data-access-agnostic*:
`archivefs` consumes Chirograph, not the other way round. See the repo-topology
section of the archivefs plan for why these are separate repos.

## Milestone 0 — format frozen ✅

- [x] Draft `.chiro.json` spec (v0.2) — mirrors DVC `.dir` (`relpath`/`md5`/`size`)
- [x] Two-path model (`relpath` = workspace, `source_path` = deposit location)
- [x] Three-layer hashing model (per-file / body_hash / whole-file-in-`.dvc`)
- [x] Provenance semantics (`self-deposited` vs `observed-download`)
- [x] **Freeze canonicalisation with test vectors** — 22 canonicalisation cases,
      32 path-safety cases, 14 whole-manifest cases; shipped in the package and
      runnable with `chiro test-vectors --check` (spec §11). Each case carries the
      literal canonical *string*, not just its hash, so a mismatching
      reimplementation can diff the string. Independently re-derived with coreutils
      by `tools/check_vectors_independently.sh`, so the values are not merely
      self-consistent with the code that produced them.
- [x] Decide hash-algorithm policy → **md5 default, sha256 permitted, nothing
      else** (decisions D-03)
- [x] Decide ETag handling for observed HTTP/S3 sources → **`hash_algorithm:
      "etag"` rejected in v1; per-entry non-authoritative `etag` field added
      instead** (spec §6.1, decisions D-04)
- [x] Publish a JSON Schema for `.chiro.json` — `chiro schema`; note it cannot
      express the integrity constraints, only the shape

> ⚠️ **Two spec claims were disproved during implementation** and corrected in
> spec v0.3: a DVC `.dir` carries **no `size`**, and DVC serialises it with
> Python's *default* `json.dumps` separators and `ensure_ascii=True` — so
> `body_hash` is **not** a DVC `.dir` hash. Verified against dvc 3.66.1. The
> canonical form was deliberately **not** changed to chase DVC's; the equivalence
> is now a separately derived value (`chiro dvc-dir`), tested byte-for-byte against
> real `dvc add` output. See decisions **D-01** and **D-02** — these are the two
> items most worth reviewing.

## Milestone 1 — reference library + CLI (`chirograph` / `chiro`) ✅

The **writer** is the highest-value first artifact — usable immediately for the
imminent Visium/Xenium deposit, before any `archivefs` backend exists.

- [x] `chiro write` — build a manifest from a directory tree (walk, hash, emit),
      with parallel hashing, deterministic output, and everything-not-included
      reported rather than dropped (decisions D-11)
- [x] `chiro write --from-dvc-cache` — transcribe existing DVC md5s. Near-free as
      promised: no re-hashing, sizes recovered by `stat` of the cache object (or
      the workspace) since a `.dir` records none. Refuses DVC 2.x-era
      `md5-dos2unix` hashes, which are not the md5 of the bytes on disk
      (decisions D-02, D-13)
- [x] `chiro verify` — check bytes against a manifest (Layers 1 & 2), with
      deterministic sampling, `--source-layout`, and a report that reads
      `observed-download` mismatches as probable upstream drift rather than
      corruption (decisions D-12)
- [x] `chiro capture` — write an `observed-download` manifest for a fetched
      dataset; requires `source_url` and defaults `retrieved`, the two fields whose
      absence makes such a manifest meaningless
- [x] Canonicalisation implementation matching the frozen test vectors exactly
      (NFC normalisation, sort, `separators=(",",":")`, sorted keys)
- [x] Path-safety validation on read (reject `..`/absolute in
      `relpath`/`source_path`) plus a materialisation lint for the safe-but-lossy
      cases — case-only collisions, Windows-reserved names (decisions D-10)
- [x] Library API (import surface) that `archivefs` can call for verification —
      `load()`, `Manifest.fetch_plan()`, `verify_entry()`; **zero required runtime
      dependencies** so consumers inherit nothing (decisions D-08)

Beyond the checklist, because each was near-free once the core existed:
`chiro validate` (Tier-1 gate), `chiro info`, `chiro ls`, `chiro diff` (the basis
of a drift report), `chiro ref` (`chiro://` syntax + registry shard path),
`chiro remap`, `chiro dvc-dir`, `chiro schema`, `chiro test-vectors`.

### Verification status

490 tests, 92% statement coverage; `ruff` and `mypy --strict` clean; conformance
vectors 68/68 and independently re-derived with coreutils. Also checked from a
clean zero-dependency wheel install and on the Python 3.10 floor. Notably:

- **DVC interop is tested against the real `dvc` CLI**, not a hand-built fake
  cache — the whole point is to check our *understanding* of DVC's format against
  DVC itself. `chiro dvc-dir` reproduces `dvc add`'s `.dir` hash byte-for-byte,
  including for non-ASCII trees (where the `ensure_ascii` difference bites).
- The end-to-end deposit workflow (reorganise → publish → verify → detect drift →
  diff) runs as a single test.

**Five adversarial review passes** were run over the implementation, each required to
reproduce every finding before reporting it. They found **41 defects**, all fixed
with regression tests — including a TOCTOU in `chiro ref`, `fetch_plan()` returning
non-URLs for `geo://` and `s3://` (the spec's own examples), `chiro remap` silently
destroying paths, and a `chiro validate` that accepted a UTF-16 manifest. Rounds 1–4
each introduced regressions that the next round caught — twice in the very function
just rewritten — which is the clearest evidence in this repo that a passing test suite
is not the same thing as correct code. The findings, including the
several places where a review disproved a claim in my own commit messages, are in
[`decisions.md`](decisions.md).

That history is the honest summary of how much of this was got right first time:
the tests passing was never the same thing as the code being correct.

## Milestone 2 — registry framework (DEFERRED)

Gated on the adoption path: start with **in-repo manifests**; build the registry
only when cross-project dedup or public discovery is concrete
(registry design §3b). Lands in a **separate** `chirograph-registry` repo, not here.

Two pieces landed here because they are *format*, not registry (decisions D-15):
`chiro://` reference syntax with self-validation, and the sharded path function —
a consumer needs the latter to build a plain HTTPS GET with no registry client at
all (registry design §5).

- [x] Sharded content-addressed storage layout (`manifests/ab/c123….chiro.json`) —
      `ChiroRef.shard_path`, pure function of the reference
- [ ] `chiro://` resolver: ordered registry list, hash self-validation, local cache
      (registry design §2). *Self-validation is implemented* (`ChiroRef.check_bytes`,
      `load_and_check`); the ordered list, network fetch and cache are not.
- [ ] CI verification templates: Tier-1 (free) → Tier-2a (API) → Tier-2b
      (download-and-hash), cheapest-first (registry design §6, §8). *Note the CLI
      already is the gate:* `chiro validate` is Tier-1 and `chiro verify` is
      Tier-2b, both with CI-shaped exit codes (decisions D-16, `ideas.md` §3.1).
- [x] Recorded verification level per manifest (`tier1`/`tier2a`/`tier2b-*`) —
      `VerificationReport.verification_level` already reports the design note's
      vocabulary; *storing* it per manifest is registry work
- [ ] `qxub`-dispatched verification backend for Tier-2b at scale
      (registry design §7) — `hash_files()` is the single choke point a backend
      would slot behind (`ideas.md` §2.3)
- [ ] Self-healing drift-report intake + CI auto-assessment
      (registry design §7b) — `chiro diff` already produces the content; the
      report *format* is the missing piece (`ideas.md` §2.1)
- [ ] Spam defences: cheap-checks-first, single-owner-default, rate-limit, optional
      signing/ORCID (registry design §8). *Cheap-checks-first is already the
      layering:* `validate` reads no data, `verify --layer 2` reads no data,
      `verify --sample N` reads a bounded amount.

**Before building the framework:** a public Git repo + `ChiroRef.shard_path` +
`chiro validate` in a workflow file is a functioning single-owner registry today.
Trying that first will reveal what the framework actually needs
(`ideas.md` §3.3).

## Milestone 3 — reference registry instance (DEFERRED)

The actual **manifest data** + CI config, in a **separate** `<lab>-manifests` repo
(data churns; must not pollute code history). Instantiated from the Milestone-2
framework.

## Cross-cutting open items

- [x] Canonicalisation test vectors — frozen, published, independently verified
- [ ] Auto-discovery filename convention per repo (spec §6). The *convention* is
      implemented (`Manifest.default_filename()`, accession-derived); which names
      GEO/BIA/Zenodo actually permit is an empirical question needing those
      repositories. Still low priority — registry-first resolution demotes
      auto-discovery to a courtesy.
- [ ] JSON-Lines variant — still deferred until a deposit's file count demands it.
      One observation for whoever designs it: the canonical form is already
      per-entry, so a JSONL body's `body_hash` can be defined over the same byte
      stream, keeping **one** canonicalisation rule rather than two
      (`ideas.md` §1.5).
- [ ] **Decide D-09** — whether registry addressing uses the whole-file hash (as
      registry design §2 specifies) or the reformat-invariant canonical-document
      hash. Both implemented; only the registry can choose.
