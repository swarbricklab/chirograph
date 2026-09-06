# Decisions log

Every decision taken while implementing [`plan.md`](plan.md) that was **not**
settled by the design docs — either because the plan explicitly left it open, or
because implementing it turned up something the docs got wrong.

Read D-01 and D-02 first: they correct factual errors in
[`chiro-format-spec.md`](chiro-format-spec.md) that were found by checking the
spec's claims against a real DVC installation.

Format: **decision**, then **why**, then **what it costs** and **how to reverse
it** where that is not obvious.

| # | Decision | Status |
|---|---|---|
| [D-01](#d-01) | `body_hash` is **not** a DVC `.dir` hash; interop is a derived function | spec correction |
| [D-02](#d-02) | A DVC `.dir` has no `size` field; sizes are recovered by `stat` | spec correction |
| [D-03](#d-03) | Hash policy: md5 default, sha256 permitted | plan item closed |
| [D-04](#d-04) | `hash_algorithm: "etag"` rejected in v1; per-entry `etag` field added | plan item closed |
| [D-05](#d-05) | Timestamps require an explicit UTC offset | new |
| [D-06](#d-06) | Canonicalisation does no semantic rewriting | new |
| [D-07](#d-07) | Unknown fields: strict at 1.0, tolerant at 1.x | new |
| [D-08](#d-08) | Zero required runtime dependencies | new |
| [D-09](#d-09) | `chiro://` reformatting hazard, and a canonical-document alternative | design gap |
| [D-10](#d-10) | Path-safety and lint boundaries | new |
| [D-11](#d-11) | Walk semantics: symlinks, empty dirs, unreadable files | new |
| [D-12](#d-12) | Deterministic, `body_hash`-seeded verification sampling | new |
| [D-13](#d-13) | Refuse DVC 2.x-era `md5-dos2unix` hashes by default | new |
| [D-14](#d-14) | Rewrite rules as the CLI surface for the two-path model | new |
| [D-15](#d-15) | `chiro://` syntax is in-scope; resolution is not | scope |
| [D-16](#d-16) | CLI exit codes distinguish "finding" from "tool failure" | new |
| [D-17](#d-17) | Derived fields are computed, never stored on the object | new |
| [D-18](#d-18) | Digests must be lowercase hex; no silent lower-casing | review finding |
| [D-19](#d-19) | A 1.x minor may only add **scalar** fields | format constraint |
| [D-20](#d-20) | Special files are read, not refused; a writerless FIFO blocks | review reversal |
| [D-21](#d-21) | Non-UTF-8 filenames stay recoverable in `--json`, not portable | review reversal |

---

## D-01 — `body_hash` is not a DVC `.dir` hash. Interop is a derived function. {#d-01}

**The spec is wrong.** §0 and §4 claim that when a manifest body carries only
`{relpath, md5, size}`, the body *is* a DVC `.dir` and `body_hash` *equals* DVC's
`.dir` hash. Checked against **dvc 3.66.1 / dvc-data 3.18.2**, that is false on
two counts:

1. DVC serialises its `.dir` with `json.dumps(lst, sort_keys=True)` — Python's
   **default** separators (`", "` and `": "`, with spaces) and **default**
   `ensure_ascii=True` (non-ASCII escaped to `\uXXXX`). The spec's canonical form
   uses `separators=(",", ":")` and `ensure_ascii=False`. Different bytes,
   different hash.
2. A DVC `.dir` entry has **no `size`** — see [D-02](#d-02).

Verified empirically (`tests/test_dvc_interop.py`), and the raw evidence:

```
$ cat .dvc/cache/files/md5/53/69cb520f35f5d77e793cf181884a27.dir
[{"md5": "b1946ac92492d2347c6235b4d2611184", "relpath": "a.txt"}, {"md5": "591785b794601e212b260e25925636fd", "relpath": "sub/b.txt"}]
$ grep md5 data.dvc
  md5: 5369cb520f35f5d77e793cf181884a27.dir
```

Note the spaces, the absent `size`, and that the file's own md5 is the `.dvc`
hash.

**Decision.** Keep the spec's canonical form for `body_hash` exactly as written,
and provide DVC equivalence as a **separately derived** function:
`chirograph.canonical.dvc_dir_hash()` / `Manifest.dvc_dir_hash()` /
`chiro dvc-dir`.

**Why not change the canonical form to match DVC?** Because it would weld a
frozen format to a DVC *implementation detail* that has already changed once
across DVC versions (the `size` field). Spec §4 calls canonicalisation "the single
most important detail" and warns that near-miss reimplementations fail silently —
pinning it to another project's internal serialisation is the opposite of
freezing it. The spec's chosen form is also simply the better canonical form:
compact, `ensure_ascii=False`, the shape every JSON-canonicalisation scheme uses.

**What it costs.** The claim "a DVC user can verify our manifest with DVC's own
logic" now needs one command (`chiro dvc-dir --hash-only`) rather than being true
by inspection. The interop is verified byte-for-byte against real DVC output, so
nothing is lost but the one-step-ness.

**Spec corrected in place** (v0.3), marked *(corrected v0.3)* at each site: the
errata note at the top, §0 bullets 1–2, §0 "Consequence", the intro paragraph,
§3.1 `body_hash` row, §4 (Layer-2 canonicalisation, plus a new "DVC `.dir` interop"
subsection), and §8.2's closing sentence. **Please review the wording** — the
finding is not in doubt, but the phrasing is mine and the spec is your document.
Correcting in place rather than only logging it here, because a known-false claim
left standing in a spec is what a second implementer would build against.

---

## D-02 — A DVC `.dir` carries no sizes, so `--from-dvc-cache` recovers them by `stat`. {#d-02}

Spec §0 states DVC `.dir` entries are `{"relpath", "md5", "size"}`. They are
`{"md5", "relpath"}`. `size` lives in DVC's `Meta`, which is only serialised in
the `with_meta=True` variant of `Tree.as_bytes()` — and `Tree.digest()` hashes the
*without*-meta form, which is also what lands in the cache.

This is a real roadblock for the plan's "`chiro write --from-dvc-cache` —
transcribe existing DVC md5s (near-free; no recompute)", because our format
requires `size` per entry and DVC has not recorded it.

**Decision.** Recover each size by `stat`, never by re-reading the file, in this
order:

1. **The cache object** (`…/files/md5/ab/cdef…`). Authoritative — the object is
   content-addressed, so its size *is* the file's size — and works even when the
   workspace has never been checked out.
2. **The workspace file.** Works when the cache has been garbage-collected.
3. Neither → a clear error naming both places we looked and the `dvc fetch` /
   `dvc checkout` that would fix it.

Where both exist and **disagree**, the disagreement is reported as a note (the
workspace has drifted from the cache) and the cache wins unless
`--prefer-workspace-sizes` is given. Silently picking one would bake a
plausible-looking wrong size into the manifest.

**Cost.** One `stat` per file — still "near-free", still no re-hashing. The
promise in the plan holds.

---

## D-03 — Hash policy: md5 by default, sha256 permitted, nothing else. {#d-03}

Closes the plan item *"Decide hash-algorithm policy: md5-only for v1, or allow
sha256?"*

**Decision.** `hash_algorithm ∈ {"md5", "sha256"}`. md5 is the default.

**Why allow sha256 at all** — the case is not cryptographic (md5's collision
weakness is irrelevant to a content fingerprint, and registry design §5 already
says so). It is *operational*: a FIPS-mode Python refuses `hashlib.md5()`
outright, and some institutional environments mandate it. A format that cannot be
used in those environments at all is worse than one that loses DVC interop there.
`hashlib.md5(usedforsecurity=False)` covers most FIPS builds, and is used, but
sha256 is the escape hatch when it does not.

**Why not more** — every additional algorithm multiplies the conformance surface
for no user demand.

**Cost.** sha256 manifests have no DVC `.dir` interop (`chiro dvc-dir` produces
the analogous form but it is untested against DVC, which does not use sha256 for
local caches). `chiro validate` emits a `non-default-hash-algorithm` warning
saying so.

**Wart accepted.** The wire key stays `md5` even under sha256, per spec §3.2
("`md5` | Hash of the file's bytes, per `hash_algorithm`"). The library exposes
`FileEntry.digest` as a neutral alias. The reader *additionally* accepts a key
named after the algorithm (`"sha256"`), because a real DVC `.dir` with sha256
spells it that way — so ingesting one needs no pre-pass.

---

## D-04 — `hash_algorithm: "etag"` is rejected in v1. A per-entry `etag` field is added instead. {#d-04}

Closes the plan item *"Decide ETag handling for observed HTTP/S3 sources"*. Spec
§3.1 lists `"etag"` as a permitted value; §10 leaves the question open and leans
towards "download-to-md5 or mark the whole manifest etag".

**Decision.** `"etag"` is **not** valid for `hash_algorithm` at schema_version
1.0. It is recognised by name so the error can say *"reserved, not supported in
v1, and here is why"* rather than *"unknown algorithm"* — which would read as a
typo.

**Why.** An S3 multipart ETag is not a function of content alone: it depends on
the part size used at upload, so the same bytes uploaded twice can yield two
ETags. That breaks three things at once:

- **It cannot address content.** `chiro://etag:…` would not be an immutable
  reference, which is the entire premise of registry design §2.
- **It cannot produce a meaningful `body_hash`.** There is no "etag of an array".
- **It cannot be checked against bytes on disk.** A consumer holding the file
  cannot recompute it, so Layer-1 verification is impossible — the format's
  primary job.

A manifest whose hashes cannot be checked is not a weaker manifest; it is a
different, unverifiable artifact wearing the same name.

**Instead:** a per-entry, optional, explicitly **non-authoritative** `etag`
field, alongside a real digest. It records what the repository told us as
supplementary evidence, and it is DVC-compatible (DVC's `Meta` already has an
`etag` field). Being honest about which is which was the spec's stated goal
(§6.1) and this achieves it without pretending an ETag is a content hash.

**Cost.** You cannot build a manifest of an HTTP/S3 source without downloading it
once. That is real, and it is exactly the cost registry design §6 already
identifies as Tier-2b — the only path for checksum-less repositories. The
alternative was a manifest nobody can verify.

**How to reverse.** If a genuine use case appears, add it under a
`schema_version` bump with a per-entry `hash_algorithm`, so a mixed manifest is
honest per file rather than degrading wholesale. Sketched in
[`ideas.md`](ideas.md).

---

## D-05 — Timestamps must carry an explicit UTC offset. {#d-05}

Spec §3.1 says `created`/`retrieved` are "ISO 8601 UTC" without saying what to do
with a naive timestamp.

**Decision.** Reject timezone-naive timestamps. Accept any explicit offset on
read; always write `...Z`, at second resolution.

**Why.** The entire semantic content of an `observed-download` manifest is "these
were the bytes at *this instant*" (registry design §3). `2026-07-01T09:15:00` is
an instant only if you already know which machine wrote it — which is precisely
what a provenance record must not require. Accepting an offset other than UTC and
normalising is forgiving where forgiveness is free; accepting *no* offset would
make the field quietly meaningless.

---

## D-06 — Canonicalisation does no semantic rewriting. {#d-06}

A `source_path` that happens to equal its `relpath` is **not** dropped during
canonicalisation, so such a manifest has a different `body_hash` from the
otherwise-identical terse one.

**Why.** Canonicalisation must be a pure syntactic function of the array it is
handed. The moment it contains a semantic special-case, every reimplementation has
to agree on *which* normalisations to apply, and any disagreement produces a
silent hash mismatch — the exact failure mode spec §4 warns about. One rule
("serialise what you were given, in canonical order") is checkable; a list of
rewrites is not.

The *writer* omits redundant `source_path`, so it never arises in practice.
`chiro validate` warns (`redundant-source-path`) when it does, and
`chiro remap --tidy` removes it — visibly changing `body_hash`, which is correct
and now explicit rather than accidental.

Frozen as a paired vector: `redundant-source-path` and `terse-no-source-path` are
required to have different `body_hash` values.

**Corollary, same reasoning:** duplicate `relpath` values are **rejected** rather
than stably sorted. With two equal sort keys the output would depend on input
order, so there would be no canonical form at all.

---

## D-07 — Unknown fields: rejected at 1.0, tolerated at 1.x. {#d-07}

**Decision.** A reader implementing 1.0:

- **major version differs** → hard error;
- **minor version higher than ours** → warn-and-tolerate; unknown fields are
  preserved verbatim in `Manifest.extra` / `FileEntry.extra`, and included in
  canonicalisation;
- **same version** → unknown fields are an **error**, overridable with
  `allow_unknown_fields=True`.

**Why strict at our own version.** Unknown fields are covered by `body_hash`.
If one implementation preserves them and another drops them, the same manifest
hashes two ways — and that divergence would surface as an unexplainable
`BodyHashMismatch` months later. Rejecting an unrecognised field at the version
that does not define it is the only behaviour that cannot produce that.

**Why tolerant at higher minors.** A minor bump is additive by definition.
Refusing them would make every future field a breaking change and would strand
old readers on manifests they could otherwise use safely.

---

## D-08 — Zero required runtime dependencies. {#d-08}

**Decision.** `dependencies = []`. PyYAML is the `[dvc]` extra (only
`--from-dvc-cache` needs it); jsonschema is the `[schema]` extra (only
`validate --jsonschema` needs it). Both are imported lazily inside the function
that needs them, and the ImportError message names the exact install command. The
CLI is `argparse`, not `click`.

**Why.** `chirograph` is the reference implementation of a *file format*, and
`archivefs` — plus, eventually, a registry's CI and any consumer — should be able
to depend on it without inheriting a dependency tree. Consumers of a
checksum-verification library have the strongest possible reason to want its
supply chain to be nothing.

**Rejected alternative:** hand-rolling a parser for "the subset of YAML DVC
happens to emit", to avoid the PyYAML extra. That is a bug farm: DVC is free to
emit a quoted key or a flow mapping at any point, and a silently-mismatching
parse of a `.dvc` file would produce a wrong manifest rather than an error.

---

## D-09 — The `chiro://` reformatting hazard, and a canonical-document alternative. {#d-09}

**A design gap in registry design §2**, surfaced by implementing it.

The reference is specified as the manifest's **whole-file hash**. A whole-file
hash is a hash of *bytes*, so re-indenting a manifest — or writing it with a
different JSON library — changes its `chiro://` reference without changing a
single fact it asserts. Manifests are meant to live in Git (spec §7), where
editors, pre-commit hooks and merge tools reformat JSON routinely. In a registry
keyed on that hash, a reformat silently relocates the object.

**Decision for now.** Implement the specified whole-file hash as the primary
(`ChiroRef.for_path`, `chiro ref`) — it matches Layer 3 and what a `.dvc`
records, and it needs no JSON parser. Make our own serialisation byte-deterministic
(`indent=2`, fixed field order, `ensure_ascii=False`, entries sorted by `relpath`,
trailing newline) so the hash is reproducible in practice. **Additionally**
implement `canonical_ref()` / `chiro ref --canonical`, a hash over the manifest's
canonical *document* form — the §4 rules applied to the whole object — which is
invariant under reformatting.

**Recommendation for Milestone 2:** make the canonical-document form
load-bearing for registry addressing, and keep the whole-file hash for the `.dvc`
/ Layer-3 role where DVC owns the bytes. The cost is that validating an answer
needs a JSON parse rather than a byte comparison; the benefit is that the
registry's primary key stops depending on whitespace. Not decided here because it
is a registry decision and Milestone 2 is deferred. Expanded in
[`ideas.md`](ideas.md).

---

## D-10 — Where path safety ends and lint begins. {#d-10}

Spec §3.2 requires rejecting `..` and absolute components. Implementing it needed
a boundary drawn.

**Hard rejection** (`PathSafetyError`, on read as well as write — an
`observed-download` manifest is an untrusted third-party description): empty
paths; absolute paths; Windows drive prefixes (`C:/x`, `c:x`); **backslashes**;
`.` or `..` components; empty interior components (`a//b`) and trailing
separators; control characters (C0 and DEL); paths over 4096 bytes or components
over 255 bytes.

*Backslash* is rejected outright because it is a separator on one platform and a
legal filename character on another — there is no interpretation that is safe
everywhere, so `relpath` cannot be unambiguously POSIX while allowing it. The
length caps match real filesystem limits, so a manifest that could never be
materialised is refused before any bytes move; this doubles as the cheap-check
first line a registry's CI wants (registry design §8, defence 1).

**Warning, not error** (`chiro validate`): case-only collisions (two entries that
become one file on macOS/Windows); Windows-reserved device basenames (`NUL.txt`);
components with trailing spaces or dots. All materialise fine on Linux and all
silently lose a file elsewhere. Warnings rather than errors because plenty of
legitimate scientific data is named this way — but a checksum manifest exists to
make that class of loss *visible*, so it must at least say so.

**A related hard error, found by a test:** if two on-disk filenames normalise to
the same NFC `relpath`, the walk refuses and names both spellings. Linux permits
the NFD and NFC forms of a name to coexist; a manifest could only describe one of
them, and would then fail verification against the other.

---

## D-11 — Walk semantics: symlinks, empty directories, unreadable files. {#d-11}

Unspecified by the docs. The governing principle: **anything not in the manifest
must be reported, never silently dropped.** A manifest that quietly omits a file
is worse than no manifest, because verification then passes against an incomplete
picture.

- **Directory symlinks: not followed** by default. A link to an ancestor makes the
  walk infinite; a link out of the tree silently publishes bytes the user did not
  mean to include. `--follow-symlinks` opts in, with a `(device, inode)` identity
  guard, which `os.walk(followlinks=True)` does not provide at all — without it a
  link to an ancestor recurses until the path-length limit, i.e. it hangs rather
  than erroring.

  The guard deduplicates by identity rather than only breaking true cycles, so a
  link to a *sibling* subtree is skipped too. That is deliberate: two `relpath`s
  for one file leave a consumer no way to decide which to materialise. Real
  directories are visited before symlinked ones at each level so that the surviving
  name is the real path rather than whichever alias sorted earlier — found by a
  test that initially asserted the opposite of what the code did.
- **File symlinks: included**, hashed by their target's content. That is what DVC
  does and what a user means by "this file".
- **Broken symlinks, FIFOs, sockets, devices: skipped with a stated reason.**
- **Unreadable files: a hard error** naming `--skip-unreadable`. The default must
  not be "omit it".
- **Empty directories:** unrepresentable in the format (as in DVC). Reported as a
  note, because for imaging and spatial data an empty directory is occasionally
  load-bearing.
- **Default excludes** include `*.chiro.json`, so a manifest written into the tree
  it describes does not break that tree the moment it lands. `--exclude` adds to
  the defaults; `--no-default-excludes` clears them.
- **Non-NFC filenames** are recorded in NFC in the manifest but read from disk
  under their real names, and the divergence is reported.

---

## D-12 — Verification sampling is deterministic, seeded from `body_hash`. {#d-12}

Registry design §6 allows `tier2b-sampled` as "tunable assurance at bounded
cost".

**Decision.** `--sample N` / `--sample-fraction F` pick their subset with a PRNG
seeded from the manifest's `body_hash`.

**Why.** A sampled verification that checks different files each run cannot be
reproduced or audited, so recording `tier2b-sampled` as an assurance level would
mean nothing. Seeding from the manifest makes "verify 10% of this manifest" denote
the same 10% on any machine, in any run, forever — while still differing between
manifests.

`VerificationReport.verification_level` reports the registry design's own
vocabulary (`tier1` / `tier2b-full` / `tier2b-sampled`), so the CLI already speaks
the language a registry will need. Tier 2a is an API cross-check against a
repository and is not something a local verifier can perform, so it never appears.

---

## D-13 — Refuse DVC 2.x-era `md5-dos2unix` hashes by default. {#d-13}

DVC ≤2.x hashed files it classified as *text* **after converting CRLF to LF**, so
the recorded md5 is not the md5 of the bytes on disk. DVC 3.x marks modern hashes
with an explicit `hash: md5`; older `.dvc` files have no `hash:` key at all.

**Decision.** Absence of the `hash:` key means **legacy**, and legacy is refused
with an explanation and an `--allow-legacy-md5` override. Explicit
`hash: md5-dos2unix` is refused the same way.

**Why.** Transcribing those md5s produces a manifest that fails verification on
exactly the files whose line endings differ — a subtle, delayed, baffling failure
that would look like data corruption. Treating a missing marker as "assume
modern" gets this precisely backwards.

---

## D-14 — Rewrite rules are the CLI surface for the two-path model. {#d-14}

Spec §3.2's motivating example is a flat GEO deposit
(`GSE000000_sampleA_barcodes.tsv.gz`) materialising as a per-sample tree
(`sampleA/barcodes.tsv.gz`). Doing that by hand for a real deposit is not viable,
and the docs do not say how it should be driven.

**Decision.** Two flags, taking `REGEX=>REPLACEMENT` rules:

- `--relpath-from-source` — the tree in front of you is the **deposit** layout
  (you downloaded it). The walked path becomes `source_path`; the rule computes
  `relpath`.
- `--source-from-relpath` — the tree is the **workspace** layout (you are about
  to deposit it). The walked path becomes `relpath`; the rule computes
  `source_path`.

Supplying both is refused: the walked path would then mean neither thing.

Rules are **first-match-wins**, not applied in sequence — chained substitutions
are near-impossible to reason about when rule 2 fires on rule 1's output. The
result is validated as a manifest path, so a rule producing `../escape` fails at
write time rather than at some consumer's checkout. A rule that matches nothing
leaves the entry in the terse form, so non-matching files do not acquire a
redundant `source_path`.

`chiro remap` applies the same rules to an existing manifest without re-reading
data.

Spec §8.1 is reproducible in one command; `tests/test_cli.py::test_full_deposit_workflow`
does exactly that.

---

## D-15 — `chiro://` syntax is in scope; resolution is not. {#d-15}

Plan Milestone 2 defers the registry, including the `chiro://` resolver, to a
separate `chirograph-registry` repo.

**Decision.** Implement here: reference parsing and formatting, self-validation
against bytes or a file, and the sharded registry path
(`manifests/ab/c123….chiro.json`). Do **not** implement here: the ordered
registry list, network fetching, the local cache, CI templates.

**Why.** Reference *syntax* is part of the format and has to be frozen with it —
a registry cannot define it later without forking the format. The shard path is a
pure function of the reference, and a consumer needs it to build a plain HTTPS GET
with no registry client at all (registry design §5: "consumers never need Git").
Resolution, by contrast, is policy and infrastructure, and belongs where the
registry lives.

---

## D-16 — Exit codes distinguish a finding from a tool failure. {#d-16}

`0` success · `1` a **finding** (bytes disagree with a manifest, or a manifest is
invalid) · `2` the tool **could not run** (bad usage, missing file, unreadable
input).

**Why.** This is meant to run as a registry merge gate (registry design §6).
Collapsing 1 and 2 would make CI treat "this manifest is a lie" and "the path was
wrong" identically — the first must block a merge, the second must page a human.
Accordingly, `ManifestError` and `IntegrityError` map to 1; everything else to 2.

---

## D-17 — Derived fields are computed, never stored on the object. {#d-17}

`size`, `nfiles` and `body_hash` are properties over `files`, not fields of
`Manifest`. On load, the values found in the document are *checked* against the
recomputed ones and then discarded.

**Why.** It makes an internally inconsistent `Manifest` structurally impossible
to construct, and it makes the whole of Tier-1 verification (registry design §6)
fall out of the object model rather than depending on a separate pass somebody can
forget to run. Spec §9's "cheap sanity check" is not a step you remember to
perform; it is the only way to get an object at all.

---

## D-18 — Digests must be lowercase hex. They are not silently lower-cased. {#d-18}

Found by adversarial review. ``check_digest`` used to lower-case a digest and
carry on, which quietly broke [D-06](#d-06): canonicalisation is supposed to be a
pure syntactic function of the array it is handed, and lower-casing is a
rewrite. The visible symptom was worse than the principle — a manifest whose
``body_hash`` was *exactly* what spec §4 yields for its own uppercase ``files``
array was rejected with a ``BodyHashMismatch`` blaming truncation or corruption:

```
error: BodyHashMismatch: … declares '11528e44…', recomputed '5f37d7ba…' …
       The file list has been truncated, edited, or corrupted …
```

Nothing was corrupt. The reader had hashed a lower-cased copy of an array it then
compared against a hash of the original.

**Decision.** Digests — ``files[].md5`` and ``body_hash`` — must be lowercase
hexadecimal, stated in spec §3.2, and an uppercase one is a ``SchemaError`` whose
message says so and shows the lower-cased value.

Either half of the alternative was worse: rewriting before hashing makes §4
impure and leaves a second implementation "non-conformant" against an unwritten
rule, while accepting both cases without rewriting gives one manifest two
``body_hash`` values. Mandating the case is the only option that keeps §4 a pure
function *and* tells the truth in the error.

No vector changes: all frozen vectors were already lowercase. The JSON Schema
likewise needed no change — its `^([0-9a-f]{32}|[0-9a-f]{64})$` patterns were
already lowercase-only, so only the descriptions were updated. (An earlier commit
message claimed the schema was newly "enforced"; it was already correct.)

**Not applied to `chiro://` references.** `ChiroRef.parse` still accepts an
uppercase digest and lower-cases it. That is deliberate rather than an oversight: a
reference is typed, copied and pasted by humans, and it is *not hashed*, so
lenience there cannot produce two `body_hash` values for one manifest. The
divergence D-18 guards against does not exist for references.

---

## D-19 — A ``1.x`` minor version may only add **scalar** fields. {#d-19}

A limitation of [D-07](#d-07), surfaced by review: unknown fields at a higher
minor are tolerated and preserved, but only if their values are scalars. A
non-scalar unknown field is still rejected, so D-07's forward-compatibility
promise is narrower than it first reads.

**Decision.** Keep it, and state it in the spec as a constraint on future minor
versions rather than papering over it.

**Why.** An unknown field is included in canonicalisation, so tolerating one means
committing to a canonical form for its value. For a scalar that is already
defined by spec §4. For a nested object or array it is not: key ordering within a
nested object, and whether nested paths get NFC-normalised, would both need
answering — and every implementation would have to guess the same answer or
produce a different ``body_hash``. Accepting non-scalars "for forward
compatibility" would therefore *create* the silent divergence that §4 exists to
prevent.

So a future minor may add scalar fields freely; anything structured needs a major
version, where readers are expected to fail loudly rather than tolerate.

---

## D-20 — Special files are read, not refused. A writerless FIFO blocks, and that is accepted. {#d-20}

A reversal of my own earlier fix, forced by review.

`chiro validate` on a named FIFO with no writer blocks forever, which in CI is a
hung job rather than a failed one. I "fixed" that by refusing anything that is not
a regular file. That broke the two idioms for checking a manifest you never wrote
to disk:

```bash
chiro validate <(curl -s https://…/GSE1.chiro.json)
curl -s https://…/GSE1.chiro.json | chiro validate /dev/stdin
```

Process substitution *is* a FIFO, so **no guard stops the hang and keeps those
working**. The choice is between them.

**Decision.** Keep the streaming forms. A directory is still refused (it never
blocks, and the message beats an `IsADirectoryError` from inside a read); nothing
else is.

**Why.** Pointing any Unix tool at a writerless FIFO blocks — `cat`, `jq` and
`md5sum` all do — so the behaviour is unsurprising and is explicitly requested by
the path the user typed. Losing the streaming form removes a capability a
registry's CI actually wants, in exchange for defending against a foot-gun that
every other tool shares. The first version of this reasoning was wrong because I
only considered the hang, not what the guard cost.

**If it ever needs solving properly:** open non-blocking and `select` with a short
timeout, so a FIFO with a live writer works and a dead one errors. Real complexity
for a rare case; not worth it yet.

---

## D-21 — A non-UTF-8 filename stays *recoverable* in `--json`, at the cost of interoperability. {#d-21}

Also a reversal of my own fix.

A path whose bytes are not valid UTF-8 reaches us as unpaired surrogates (Python's
`surrogateescape`), and gets emitted as a `\uDCxx` escape. That is syntactically
valid JSON and round-trips losslessly in Python, but RFC 8259 §8.2 marks unpaired
surrogates as not interoperable: Go's `encoding/json` substitutes U+FFFD and
`serde_json` rejects them. I rewrote them to a literal-backslash spelling to fix
that.

Review showed the cure was worse in two ways:

1. **The result could no longer be turned back into a filename**, so a consumer
   could not act on the report — and naming a file precisely is the report's entire
   purpose.
2. **Two distinct files collapsed to one string.** A directory holding a file with
   an invalid byte *and* a file literally named with those six characters produced
   identical output, so a consumer that deduplicates saw one extra file where two
   existed.

**Decision.** Revert. Emit the lossless escape, document that such entries are not
portable, and tell consumers in other languages to treat them as opaque.

**Where this actually needs solving:** the registry's drift-report format
(registry design §7b), which is the thing that will cross language boundaries. It
should carry the raw bytes in a dedicated, explicitly-encoded field — percent-
encoded or base64 — rather than trying to squeeze them into a JSON string. Recorded
in [`ideas.md`](ideas.md) §2.1.

---

## Follow-ups

What needs your eye, in priority order:

1. **Review the spec corrections for D-01 and D-02** (spec v0.3). The finding is
   verified against a real DVC install and is not in doubt; the *wording* is mine
   and the spec is your document. Every change is marked *(corrected v0.3)* and
   there is an errata note at the top of the file. This is the single most
   consequential thing in the whole night's work: it changes what the format
   claims about DVC.
2. **Confirm D-03 and D-04**, the two policy questions the plan explicitly assigned
   ("decide hash-algorithm policy", "decide ETag handling"). Both are now closed in
   the spec and enforced in code, so reversing them is a spec change, not just a
   flag flip. D-04 in particular *removes* a capability the spec previously listed
   as permitted.
3. **Decide D-09 for Milestone 2** — whether registry addressing uses the
   whole-file hash (as registry design §2 specifies) or the reformat-invariant
   canonical-document hash. Both are implemented; only the registry can choose,
   and choosing late is cheap. Nothing is blocked on it today.
4. **The vectors are now a freeze.** `tools/gen_vectors.py` refuses to overwrite
   them without `--force` for that reason. If any expected value in
   `src/chirograph/vectors/canonicalisation.json` is wrong, it needs a corrected
   set under a new `spec_version` rather than an in-place edit — but nothing should
   be published anywhere until you are happy with them, because publishing is what
   makes the freeze a commitment (see `ideas.md` §4.5).

Smaller loose ends:

5. The auto-discovery filename convention (spec §6) is *partially* addressed:
   `Manifest.default_filename()` implements the accession-derived convention, but
   which filenames GEO/BIA/Zenodo actually permit is an empirical question that
   needs those repositories.
6. Nothing has been pushed. The work is on the branch
   `implement-milestone-0-and-1`; `main` is untouched.

### Found by adversarial review

An adversarial review pass ran every high-risk area against actual
reproductions. Ten findings, all fixed, all with regression tests. The ones worth
your attention:

- **`join_source_url` returned URLs outside the base — twice over.** It used
  `urllib.parse.urljoin`, which (a) only resolves relative references for schemes
  in `uses_relative`, so `geo://GSE12345/` + `a.gz` returned a bare `"a.gz"`, and
  (b) appends a trailing slash to the *query* when the base has one, discarding the
  base path. `geo://` is the spec's own §3.2 example and `s3://` is named in §3.2
  and §6.1, so `fetch_plan()` was handing `archivefs` a relative path where a URL
  belonged, for the commonest cases. Now joined explicitly on the path component,
  carrying scheme/netloc/query/fragment through. The exported helper also
  validates its own `source` argument now.
- **`chiro remap` destroyed paths when a rule matched nothing.** `apply_rules`
  returns its input unchanged on no match, and remap assigned that
  unconditionally — so `--relpath-from-source` with a non-matching rule
  overwrote every uncovered entry's `relpath` with its `source_path`, silently
  replacing the workspace layout with the deposit layout; the mirror case deleted
  `source_path`, losing the fetch location. Split into `try_apply_rules`, which
  returns `None` on no match, and the count of untouched entries is now reported.
- **A non-UTF-8 filename crashed rather than erroring.** Python surfaces such
  names as surrogates, `str.encode` raises `UnicodeEncodeError`, and that escaped
  every `except ChirographError` — turning a *finding* into a *tool failure* and
  aborting a `chiro validate` batch before it reported the manifests it had
  already checked. A latin-1 filename in a deposit is entirely routine. Rejected
  properly now, with the file named. Writing the message then crashed too (it
  *contains* the surrogate), so the CLI's streams are wrapped to survive it.
- **`--from-dvc-cache` emitted manifests the tool then rejected.** It hard-coded
  `hash_algorithm="md5"` while accepting sha256 and non-hex digests, and
  `--created` was assigned *after* construction so it bypassed the timestamp
  validation of [D-05](#d-05). Both wrote a file and exited 0. The algorithm now
  comes from the `.dvc`, digests are validated before anything is written, and
  `created` goes through the constructor.
- **`--stdout` suppressed every note and warning**, so the obvious
  `chiro write … --stdout > m.chiro.json` idiom silently discarded things like "the
  `.dvc`'s declared size disagrees with the recovered sizes; one of them is stale".
  They go to stderr now, which cannot contaminate the piped manifest.
- **`extra` could shadow a computed field**, since it is serialised last:
  `extra={"relpath": "../../etc/evil"}` emitted a path that never saw
  `validate_path`, and `extra={"body_hash": …}` emitted a body_hash that was not a
  hash of the body. Unreachable through `from_dict`, but this is the documented
  public object model, so the constructor now refuses colliding keys.
- Plus: the NFD fallback decomposed the *root* as well as the relative path (so a
  present file under `réf_root/` verified as "missing"); `normalise_order` raised
  `TypeError` for a non-NFC path plus an unknown field; `dvc_dir_bytes` raised a
  bare `KeyError`; `chiro validate` and `chiro verify` disagreed on the exit code
  for a BOM'd manifest; and `_check_scalar`'s docstring claimed to reject `bool`
  when it accepts it (correctly — `isexec` is boolean). See [D-18](#d-18) and
  [D-19](#d-19) for the two that became decisions.

### Found by reviewing the fixes

Fixes introduce bugs, so the fix commit got its own adversarial pass. Six more
findings, two of them **regressions I had just introduced**:

- **A UTF-16 manifest was accepted as valid** (regression). Consolidating the BOM
  check, I switched `chiro validate` from `read_text(encoding="utf-8")` to
  `read_bytes()` + `json.loads(raw)` — and `json.loads` accepts *bytes*, happily
  auto-detecting UTF-16/UTF-32 from their BOMs. So a manifest in an encoding spec
  §1.2 forbids passed, and `chiro ref` would mint a `chiro://` reference for it.
  Since re-encoding changes the whole-file hash, a registry would have stored two
  different objects under one reference. There is now a single
  `decode_document()` gate that both `validate` and `load` use, so they cannot
  drift apart again.
- **A non-UTF-8 body still aborted the whole `validate` batch** (left behind by the
  previous fix). `UnicodeDecodeError` is a `ValueError`, not a
  `json.JSONDecodeError`, so it slipped past the handler that was added precisely
  to keep the batch going.
- **`normalise_order` leaked `TypeError`** (regression) — the same defect class the
  previous commit had fixed one instance of. Normalising the path before any type
  check meant a `relpath=123` never reached canonical.py's check.
- **sha256 DVC transcription was only half-wired.** Deriving the algorithm from the
  `.dvc` made sha256 *reachable*, but `find_cache_dirs` still offered only
  `cache/files/md5` and the `.dir` reader still looked only for an `"md5"` key —
  so a sha256 **directory** output, the main case, failed twice over. DVC shards
  the cache by algorithm name and writes the digest under it.
- **`FileEntry` validated only on read, not on construction.** So the *producer*
  side could still build `relpath="../../etc/passwd"` or an uppercase digest and
  write it out; only a reader objected. `__post_init__` now validates, which also
  covers `dataclasses.replace`.
- **`verify --check-extra` discarded a passing report** because a stray *unlisted*
  file had a non-UTF-8 name. It only enumerates names, so the walk now has a
  `skip_unrepresentable` mode for that caller — while `chiro write` still refuses
  loudly, since the two want opposite behaviour. Such names are still counted as
  extras, so `--strict` sees them.

This pass cleared `join_source_url` (400k-case fuzz over hostile bases: no
authority or scheme drift, and `%` correctly double-encodes so a filename that
merely *looks* percent-encoded round-trips), the `rewrite.py` split (writer output
byte-identical across 10 old-vs-new comparisons), `cmd_remap`'s full 6×3 matrix,
`_SurrogateSafeStream` (including `json.dump`'s many small writes), and
`verify_entry(root=…)` (no in-tree input can produce a path outside the root).

### Found by reviewing the fixes-for-fixes

A third pass found **no regressions** — every `FileEntry` construction site,
`copy`/`pickle`/`replace` round-trips, and a 25-command CLI sweep were byte-identical
before and after. It did find seven defects of a different kind, all fixed:

- **`chiro ref` still minted a reference for a non-UTF-8 manifest** — the exact hole
  the previous commit's message claimed to have closed. `validate` was fixed;
  `chiro ref`, the one command whose whole job is producing the reference a registry
  keys on, still hashed raw bytes. It now confirms the file is a manifest first,
  with `--no-validate` as the escape hatch. `ChiroRef.for_path` stays a pure byte
  hash, which is the right primitive for a library.
- **`--json` emitted unpaired surrogates.** The `skip_unrepresentable` fix made a
  report possible where previously there was none, and that report could contain a
  filename with no valid UTF-8. RFC 8259 forbids unpaired surrogates: Go's
  `encoding/json` silently substitutes U+FFFD and `serde_json` errors, so a CI gate
  in another language got a mangled or rejected document where it used to get a
  visible failure. Strings are now escaped to a literal `\udcff` spelling that is
  valid JSON.
- **Two fixes in one commit collided.** Adding validation to `__post_init__` made
  the `normalise_order` type-check — the fix the *same* commit advertised —
  redundant for any real `FileEntry`, and changed the user-visible error from the
  intended `SchemaError` to `PathSafetyError`. **I then removed the check on the
  mistaken grounds that only a subclass could reach it; a later pass showed a
  duck-typed object reaches it through two *public* functions**
  (`normalise_order` and `Manifest.with_files`), where a bare `TypeError` from
  `unicodedata.normalize` is not a `ChirographError` and names neither the entry nor
  the field. The check is back.
- **`__post_init__`'s docstring contradicted its own code** six lines later,
  claiming digest length was not checked when it is. Corrected, and the
  per-construction `set()` build hoisted to a module constant.
- **The "keep the batch going" handler was still narrow.** `json.loads` raises plain
  `ValueError` for an integer past the 4300-digit limit and `RecursionError` for a
  deeply-nested array; both aborted the batch. Same defect class the previous commit
  spent a bullet closing one instance of.
- **`chiro validate` hung forever on a FIFO**, which in CI is a hung job rather than
  a failed one. Non-regular files are now refused before being opened. Relatedly,
  tool failures were labelled `INVALID <path>`, which reads as a verdict on the
  manifest; they now say `ERROR`.
- **`skip_unrepresentable`'s docstring over-claimed** (it catches anything
  `validate_path` refuses, not only surrogates), and a directory whose only content
  was such a file was reported as empty.

Also confirmed by that pass: nothing is O(n²), and BOM-less UTF-16 input now fails
at the JSON parse where it previously *loaded* — closing a hole the commit had not
even claimed.

### Found by reviewing the fixes-for-fixes-for-fixes

A fourth pass found eight findings, **three of them regressions from the third
round**, which is why the rounds kept going. Two became decisions
([D-20](#d-20), [D-21](#d-21)); the rest:

- **A real TOCTOU in `chiro ref`.** Making it validate meant `Manifest.load(path)`
  and then `ChiroRef.for_path(path)` — two opens. Swapping the file between them
  was demonstrated to mint a `chiro://` address for content that was never
  validated, at exit 0, in the one command whose entire job is producing the address
  a registry keys on. It now reads the bytes **once**, validates *those bytes*, and
  hashes *those bytes*. Same fix in `chiro validate`, which was also reading twice —
  and whose second read was outside the per-manifest handler, so it could abort a
  batch.
- **`ref --check` was conflating two questions.** `--check REF` asks "did I fetch
  the right manifest?", which is byte identity. Validating on that path reported
  failure for a byte-perfect download of a manifest that merely had a finding, gave
  it the same exit code as a genuine byte mismatch, and emitted nothing at all on
  `--json`. It no longer validates; `chiro validate` answers the other question.
- **`ref` leaked raw tracebacks** on pathological JSON (deep nesting →
  `RecursionError`, a 5000-digit integer → plain `ValueError`) — in the very commit
  that added handling for both to `validate`. Fixed once in `Manifest.loads`, so
  every command gets it rather than each growing its own handler.
- **`--canonical --no-validate` was accepted and silently ignored** (canonicalising
  requires a parse). Now an explicit usage error.

Cost note the pass also surfaced: `chiro ref` on an 18 MB manifest goes from 0.5 s
to 10 s because validating means parsing. That is the price of not minting addresses
for non-manifests; `--no-validate` restores the fast path when the caller already
knows.

### Found by the fifth pass

Ten findings, **three of them regressions from round 4** — two serious, and both in
the same function I had just rewritten:

- **`ref --canonical --check` silently ignored `--canonical`.** Moving the `--check`
  branch above the `--canonical` one meant `--check` always compared the *whole-file*
  address, producing a false MISMATCH for the canonical address the user asked about
  and a false OK for the one they did not — and making `--canonical`'s only real use,
  a reformat-invariant comparison, unavailable. This is the "accepted and silently
  ignored" defect I had made a hard error for `--canonical --no-validate` **two lines
  away in the same hunk**. The reference is now computed once, before any branching.
- **`chiro info` on a streamed manifest minted `md5("")` as the address.** `cmd_info`
  still read the file twice, which was harmless only while special files were
  refused; once [D-20](#d-20) allowed pipes again, the first read drained the pipe and
  the second returned nothing. A wrong `chiro://` address inside an otherwise-correct
  report, at exit 0 — precisely the failure mode the previous commit claimed to have
  closed. My own new test blessed it, because it asserted only the exit code.
- **`verify` blocked or spun forever on a special payload file.** I had coupled the
  `hash_file` revert to the `read_manifest_bytes` revert without needing to. A
  payload path comes from the *manifest*, not from something the caller typed, so
  D-20's reasoning does not apply — and a FIFO or `/dev/zero` both report size 0, so a
  manifest declaring size 0 walked past the size check into an unbounded read. The
  guard now lives in `verify_entry`, leaving `hash_file` a general primitive.

Plus: `ref` had lost the filename from its error messages (it parses bytes it already
holds, so it could not use `Manifest.load`'s prefixing); `--no-validate` had stopped
streaming, taking peak memory from 28 MB to 188 MB on a 172 MB file; the duck-type
check used `getattr(…, None)`, which reports a *missing* attribute as "got NoneType";
`--check --no-validate` was accepted and ignored; and two of my own claims were
wrong again — a `cmd_ref` comment said the single read "halves the work" when
measurement shows it saves 0.02 s of a 10 s run, and D-21's pointer to
[`ideas.md`](ideas.md) §2.1 was dangling because I never wrote the content it
deferred there. All fixed; §2.1 now carries the actual proposal.

This pass also confirmed D-21's two claims are correct as written, that `emit_json`
is byte-identical across all ten `--json`-producing commands, that the surrogate case
is genuinely lossless (a consumer can locate both files), and that the README's
streaming block works exactly as printed.

The first review also cleared several areas, which is worth recording: no accepted path escapes the import target (fullwidth and division
solidus, `....//`, percent-encoded traversal, overlong components and bidi
overrides were all tried, and no codepoint's NFC or NFD form contains `/`, `\`,
`.` or `:` — 38 do under NFKC, which is why using NFC here is right); `paths` and
`disk_paths` cannot desynchronise; sampling has no off-by-one and
`random.Random(str)` is byte-identical across CPython 3.6–3.14; and sixteen
malformed `.dvc`/`dvc.lock` shapes all produce a `DvcError` rather than a
traceback.

### Found by verification rather than by design

Recorded because they are the kind of thing that only shows up when you run the
thing rather than read it:

- **A test caught a real NFC bug.** The walk NFC-normalises paths for the manifest,
  and the writer was then using those normalised paths to `stat` and hash — which
  fails on a tree written on macOS, where the on-disk names are NFD. `WalkReport`
  now carries `paths` (manifest) and `disk_paths` (on-disk) as parallel lists, and
  `verify` falls back to the NFD spelling when the NFC one is absent. See
  [D-10](#d-10) and [D-11](#d-11).
- **Installing the built wheel into an empty virtualenv caught an exit-code bug.**
  `chiro validate --jsonschema` without the `schema` extra reported the manifest as
  INVALID and exited 1 — i.e. a CI gate would have blocked a merge because of the
  *runner's* install. Now exits 2, per [D-16](#d-16). Guarded by a `no-dependencies`
  CI job that installs the wheel bare and asserts nothing else arrives.
- **The drift explanation was too narrow.** It only fired on hash mismatches, so
  the two commonest shapes of real upstream drift — a file whose size also changed,
  and a withdrawn file — went unexplained. Now covers all three.
