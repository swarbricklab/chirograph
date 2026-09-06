# Resolving `chiro://` references, and configuration

> **Status: aspirational.** This document describes intended behaviour. Almost none
> of it is implemented. What exists today is marked **[built]**; everything else is
> design. The reference library currently ships the reference *syntax* and
> *self-validation* (`ChiroRef`, `load_and_check`) and the sharded path function —
> there is no resolver, no scope search, no cache and no configuration file.
>
> Treated as a design document, this is the place to argue with the model. Treated as
> documentation, it would be a lie.

The format spec ([`chiro-format-spec.md`](chiro-format-spec.md) §6) defines what a
`chiro://` reference *is*. It deliberately says nothing about how one is *found*,
because locating a manifest is a property of a deployment, not of the format. This
document covers the finding.

---

## 1. The two kinds of reference

There are two things a project might want to depend on, and conflating them is the
main hazard in this area.

**A version reference** names an exact manifest by the digest of its whole file
content. It is immutable: resolving it twice always yields the same bytes, or fails.

```
chiro://md5:6f174b49ded208801052240cf05798c8
```

**A concept reference** names a *dataset* rather than an observation of it. It is
mutable by design: resolving it means "give me the current manifest for this thing",
and the answer changes as new observations are recorded.

```
chiro-concept:geo/GSE12345
chiro-concept:doi/10.5281/zenodo.7654320
```

These use **different URL schemes** on purpose. Immutability is then a property of the
scheme itself, checkable by looking at the string, rather than something a consumer
has to remember to verify per-reference. The alternative — one scheme with two
interpretations — makes it possible to write a `.dvc` that looks pinned and isn't.

**[built]** `chiro://<algorithm>:<digest>` parsing, formatting and self-validation.
Everything about `chiro-concept:` is design; the parser rejects it today.

### Why the concept form exists at all

Zenodo distinguishes a *concept DOI* (always resolves to the latest version) from a
*version DOI* (immutable). That distinction is exactly right and almost no other
archive has it. Chirograph already supplies the version half — and supplies it more
strongly than Zenodo does, since a content digest is a proof where a version DOI is a
promise. The concept half is what's missing, and for most archives the accession
already *is* a perfectly good concept identifier.

So the concept reference is not a new identifier system. It is a spelling for
"whatever the accession currently points at", used only where mutability is the point:
tracking reference data during development, and answering "is there anything newer".

Concept references are spelled as CURIEs using
[bioregistry](https://bioregistry.io)/identifiers.org prefixes rather than
Chirograph-invented ones. Defining what `geo:` means would make this project an
identifier authority, with the governance obligations that implies, in exchange for
nothing.

---

## 2. Three-hop resolution

The intended dependency model puts the *manifest* in the dependency slot, not the
archive. A `.dvc` records a `chiro://` reference; the manifest records where the bytes
actually live.

```
1.  chiro://md5:abc…            →  resolve to manifest bytes, verify against digest
2.  manifest.source_url         →  where the bytes live  (geo://…, zenodo://…, https://…)
3.  ping that source            →  fetch bytes, or check whether anything changed
```

Two consequences worth being explicit about.

**The `.dvc` becomes archive-agnostic.** One dependency shape covers GEO, Zenodo, EGA
and BIA. Which archive it came from is a detail inside the manifest, where it can
change without touching the project.

**A pinned dependency cannot self-update, by construction.** If `deps[].path` holds an
immutable reference, there is nothing mutable in the `.dvc` for `dvc update` to
re-resolve — it will correctly report no change, forever. That is the desired
behaviour for a published project (see [`howto-publish.md`](howto-publish.md)) and the
wrong behaviour during development, which is what the concept form is for.

### How this differs from what archivefs does today

`archivefs` currently registers the `ega`, `zenodo`, `geo`, `bia` and `dbgap` fsspec
protocols, and *not* `chiro`. So the shipped architecture is two-hop: the archive URL
is the dependency, and the manifest is consulted alongside it as a sidecar
(`archivefs.chiro.discover_manifest`, `load_manifest`, `apply_manifest`).

Both models work. The three-hop model is better for publication, because it makes the
manifest the thing depended upon rather than an optional enhancement, and because it
removes the archive from the dependency declaration. Moving to it requires archivefs
to register a `chiro` protocol. Until then, the how-to documents describe the two-hop
flow, which is the one that runs.

---

## 3. Scopes

A reference is resolved by searching an ordered list of scopes, nearest first.

| Scope | What it is | Default location |
|---|---|---|
| **cache** | Previously-resolved manifests, keyed by digest. Not a registry — no names, no enumeration. | `$CHIROGRAPH_CACHE_HOME`, else `$XDG_CACHE_HOME/chirograph`, else `~/.cache/chirograph` |
| **project** | Manifests committed alongside the code they belong to. | `<repo>/.chiro/manifests/` |
| **user** | One person's collection, shared across their projects. | `$CHIROGRAPH_CONFIG_HOME`, else `$XDG_CONFIG_HOME/chirograph`, else `~/.config/chirograph` |
| **team** | A shared registry, typically a git repo or a plain HTTPS host. | configured, no default |

Note that **cache and registry are different things**, and XDG already separates
them. The cache answers "have I seen this digest before" and is pure optimisation —
deleting it must never change a result. A registry answers "what manifests exist" and
is a source of truth. Conflating them makes negative caching and enumeration
ill-defined.

### On this system specifically

`~/.cache` is on a quota-limited home filesystem on NCI, and users will not reliably
distinguish a few kilobytes of manifest cache from a data cache. `CHIROGRAPH_CACHE_HOME`
should be pointed at `/scratch` or `/g/data`:

```bash
export CHIROGRAPH_CACHE_HOME=/scratch/$PROJECT/$USER/.chiro-cache
```

`$HOME` is also shared across compute nodes, so array jobs will write the cache
concurrently. Content-addressing makes that safe by construction — two jobs resolving
the same reference write identical bytes — provided writes are atomic-rename, which
they are. The cache is deliberately not lock-protected.

---

## 4. Why the search order is safe — and where it isn't

**For version references, scope precedence is a performance question, not a trust
question.** Whichever scope answers, the returned bytes are verified against the
digest in the reference before being parsed. A scope that returns the wrong bytes is
detected, not trusted. So the order can be chosen purely for speed — nearest and
cheapest first — and a hostile or stale nearer scope can cause a *failure* but never a
wrong answer.

This is worth stating because it is unusual. In npm, pip or Maven, scope precedence is
a supply-chain attack surface, and the ordering is a security decision. Here it isn't.

**For concept references, precedence becomes a trust decision, and the order
inverts.** `chiro-concept:geo/GSE12345` can legitimately resolve to different
manifests in different scopes — that is what a mutable pointer means. Nearest-first
then means the *least* authoritative scope wins, so a stray file in a project can
silently shadow the curated team registry. The result is not corruption; it is quietly
using last year's observation and never being told.

So concept resolution has different rules:

- Resolve in **one explicitly declared scope**, not by search; or
- resolve in all scopes and **fail on divergence** rather than picking; and
- in either case, **record which scope answered** and what it resolved to.

The recommended default is to resolve concepts against the team scope only, and to
require an explicit flag to resolve them anywhere else.

---

## 5. Recording the resolution

Whatever a concept reference resolved to **must be written down** — into the `.dvc`,
the lockfile, or the manifest that results. Otherwise the same project resolves
differently on two machines and `dvc repro` is not reproducible.

This is the same conclusion as §2's: a project should always carry an immutable
reference somewhere, even when it was reached via a mutable one. In DVC's own
vocabulary that pairing is `rev` and `rev_lock` — with the caveat that `rev_lock` is
not a lock. It records the revision that was resolved at import time, and `dvc update`
moves it forward. The chirograph equivalent behaves the same way, and the naming
should not suggest otherwise.

---

## 6. Storage layout

A registry stores manifests content-addressed and sharded, so that a directory never
grows unmanageably and so that a plain HTTPS GET can reach one without a client:

```
manifests/6f/6f174b49ded208801052240cf05798c8.chiro.json
```

The shard directory is the first two characters of the digest; the filename is the
**full** digest. That is two characters of redundancy, in exchange for a file that
remains self-describing when copied out of the registry and saved somewhere else.
Git's convention — directory holds the first two characters, filename holds the
remainder — loses two characters of the digest the moment a file is moved.

**[built]** `ChiroRef.shard_path` computes this, though in the Git-style remainder
form; changing it to full-digest form is a pending decision.

### Content-addressed filenames as a general convention

Naming any manifest `<digest>.chiro.json` — not just inside a registry — buys three
things:

- It is verifiable with no tooling: `md5sum *.chiro.json` checks every name.
- Multiple observations of one accession coexist rather than colliding. This matters:
  capturing the same accession twice today produces the same filename twice, and the
  second write destroys the first — which is exactly the drift record you wanted.
- The name signals immutability, which discourages the reformatting that would
  invalidate it.

The cost is human legibility, which is why an accession-derived name remains
preferable for the one case where a human browses the files: a deposit that
non-Chirograph consumers will read.

---

## 7. Configuration

Configuration determines the *scope list*, not the cache.

```toml
# .chiro/config.toml  (project scope)   — aspirational, not implemented

[registries]
inherit = true            # false = ignore user and team scopes entirely

[[registries.source]]
name = "team"
url  = "https://manifests.example.org/"

[[registries.source]]
name = "lab-git"
url  = "git+https://git.example.org/lab/manifests.git"
```

**Merge semantics:** scalar keys override nearest-wins; the registry *list*
accumulates across scopes in order. Accumulation is the useful default — a project
wants its own registries *and* the team's — but a hermetic build needs to opt out,
hence `inherit = false` and an `--isolated` flag. This follows git's model, where
scalar config overrides and list-valued config accumulates.

**Environment overrides**, highest precedence first:

| Variable | Effect |
|---|---|
| `CHIROGRAPH_REGISTRIES` | Colon-separated list; replaces the configured list entirely |
| `CHIROGRAPH_CONFIG_HOME` | Overrides `$XDG_CONFIG_HOME/chirograph` |
| `CHIROGRAPH_CACHE_HOME` | Overrides `$XDG_CACHE_HOME/chirograph` |
| `CHIROGRAPH_OFFLINE` | Resolve from cache and project scope only; never touch the network |

`CHIROGRAPH_OFFLINE` matters more than it looks: HPC compute nodes frequently have no
outbound network, and a resolution step that silently requires one turns a working
pipeline into a queue-time failure.

---

## 8. Graceful degradation

Because version references are verified rather than trusted, an unreachable team
registry is invisible whenever a nearer scope holds the manifest. That is a real
operational property, not a consolation: it means a compute node with no network can
resolve everything a project needs, provided the manifests were committed.

Which is the strongest argument for **committing manifests into the project**. A
manifest is a few kilobytes. The registry is a convenience for finding manifests you
don't have yet; it should never be a runtime dependency of an analysis.

---

## 9. The enumeration gap

Content-addressed storage answers "give me this digest". It cannot answer "list every
manifest for `GSE12345`" — and that is precisely the question drift monitoring asks,
and precisely what content-addressed filenames make unanswerable without help.

So each registry needs an index, keyed by concept:

```json
// concepts/geo/GSE12345.json      — aspirational
{
  "concept": "geo:GSE12345",
  "observations": [
    {"chiro": "chiro://md5:abc…", "retrieved": "2026-03-01T…"},
    {"chiro": "chiro://md5:def…", "retrieved": "2026-08-22T…"}
  ]
}
```

Three things about this are worth deciding before building it.

**It is the only mutable object in the system.** Everything else is
content-addressed. Confining mutability to the concept layer — where mutability is the
point — is a property worth preserving deliberately.

**What the list means depends on `provenance_type`.** For `self-deposited` manifests it
is an authored version sequence, and later supersedes earlier, exactly as on Zenodo.
For `observed-download` manifests it is a set of dated observations by possibly
different observers of a mutable upstream — not a chain, and not necessarily ordered
by anything but when someone happened to look. "Latest" therefore means "the most
recent observation in this registry", which is a much weaker claim than Zenodo's
latest, and must not be presented as the same thing.

**Concept membership is an unverifiable assertion.** A manifest claiming
`record_accession: GSE12345` might be lying, and nothing in the format can tell. Version
references need no trust; concept membership does. This is the real cost of the concept
layer, and the only sane mitigation is that indexes are per-scope and single-owner by
default — the team registry's index is curated, and anyone else's is their own problem.

Cross-archive identity, by contrast, needs no index and no trust at all: two manifests
describe the same content when their multisets of per-file digests match. That is
provable, so it is worth preferring wherever it will do.

---

## 10. Implementation status

| Piece | Status |
|---|---|
| `chiro://` syntax, parsing, formatting | **built** (`ChiroRef`) |
| Self-validation of fetched bytes against the digest | **built** (`check_bytes`, `load_and_check`) |
| Sharded path computation | **built** (`shard_path`, remainder-form) |
| Full-digest shard filenames | not built — pending decision |
| Scope search, cache, config file, env overrides | not built |
| `chiro-concept:` scheme | not built — parser rejects it |
| Concept indexes | not built |
| `chiro` fsspec protocol in archivefs | not built |

The honest summary: the parts of this that are *format* exist and are tested. The
parts that are *deployment* are a design on paper. Building the smallest useful
version — a directory of manifests plus `chiro validate` in CI — is the recommended
next step, because it will reveal which of the above actually matters.
