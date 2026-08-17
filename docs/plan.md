# Chirograph — Implementation Plan

High-level checklist for the Chirograph format, library, and (deferred) registry.
Detail lives in the design docs — this file links rather than repeats.

- **Format:** [`chiro-format-spec.md`](chiro-format-spec.md)
- **Registry design:** [`manifest-registry-design.md`](manifest-registry-design.md)
- **Sibling project (data access):** [`archivefs`](https://github.com/swarbricklab/archivefs)

## Scope

Chirograph is the **manifest format + reference library/CLI**, and (later) the
**registry framework + reference registry**. It is *data-access-agnostic*:
`archivefs` consumes Chirograph, not the other way round. See the repo-topology
section of the archivefs plan for why these are separate repos.

## Milestone 0 — format frozen (mostly done)

- [x] Draft `.chiro.json` spec (v0.2) — mirrors DVC `.dir` (`relpath`/`md5`/`size`)
- [x] Two-path model (`relpath` = workspace, `source_path` = deposit location)
- [x] Three-layer hashing model (per-file / body_hash / whole-file-in-`.dvc`)
- [x] Provenance semantics (`self-deposited` vs `observed-download`)
- [ ] **Freeze canonicalisation with test vectors** — the fragility point; a
      near-miss reimplementation produces non-matching hashes silently
      (spec §4, §10). *Blocks any second implementation.*
- [ ] Decide hash-algorithm policy: md5-only for v1, or allow sha256? (spec §10)
- [ ] Decide ETag handling for observed HTTP/S3 sources (spec §6.1, §10)
- [ ] Publish a JSON Schema for `.chiro.json` (validation + editor support)

## Milestone 1 — reference library + CLI (`chirograph` / `chiro`)

The **writer** is the highest-value first artifact — usable immediately for the
imminent Visium/Xenium deposit, before any `archivefs` backend exists.

- [ ] `chiro write` — build a manifest from a directory tree (walk, hash, emit)
- [ ] `chiro write --from-dvc-cache` — transcribe existing DVC md5s (near-free; no
      recompute)
- [ ] `chiro verify` — check bytes against a manifest (Layers 1 & 2)
- [ ] `chiro capture` — write an `observed-download` manifest for a fetched dataset
- [ ] Canonicalisation implementation matching the frozen test vectors exactly
      (NFC normalisation, sort, `separators=(",",":")`, sorted keys)
- [ ] Path-safety validation on read (reject `..`/absolute in `relpath`/`source_path`)
- [ ] Library API (import surface) that `archivefs` can call for verification

## Milestone 2 — registry framework (DEFERRED)

Gated on the adoption path: start with **in-repo manifests**; build the registry
only when cross-project dedup or public discovery is concrete
(registry design §3b). Lands in a **separate** `chirograph-registry` repo, not here.

- [ ] Sharded content-addressed storage layout (`manifests/ab/c123….chiro.json`)
- [ ] `chiro://` resolver: ordered registry list, hash self-validation, local cache
      (registry design §2)
- [ ] CI verification templates: Tier-1 (free) → Tier-2a (API) → Tier-2b
      (download-and-hash), cheapest-first (registry design §6, §8)
- [ ] Recorded verification level per manifest (`tier1`/`tier2a`/`tier2b-*`)
- [ ] `qxub`-dispatched verification backend for Tier-2b at scale
      (registry design §7)
- [ ] Self-healing drift-report intake + CI auto-assessment
      (registry design §7b)
- [ ] Spam defences: cheap-checks-first, single-owner-default, rate-limit, optional
      signing/ORCID (registry design §8)

## Milestone 3 — reference registry instance (DEFERRED)

The actual **manifest data** + CI config, in a **separate** `<lab>-manifests` repo
(data churns; must not pollute code history). Instantiated from the Milestone-2
framework.

## Cross-cutting open items

- [ ] Canonicalisation test vectors (also blocks M0/M1) — the single most important
      correctness artifact.
- [ ] Auto-discovery filename convention per repo (spec §6) — low priority given
      registry-first resolution demotes auto-discovery to a courtesy.
- [ ] JSON-Lines variant — deferred until a deposit's file count demands it.
