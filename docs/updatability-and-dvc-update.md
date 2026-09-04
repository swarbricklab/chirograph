# Updatability, the three URLs, and what `dvc update` can and cannot do

> **Status: design, with empirical findings.** The *reference model* here builds on
> [`resolution-and-config.md`](resolution-and-config.md) (the version-vs-concept
> reference split) and [`howto-adopt-dvc.md`](howto-adopt-dvc.md). The findings marked
> **[measured]** were established against a live archive (BioImage Archive / BioStudies
> `S-BIAD3080`) using the sibling `archivefs` project as the DVC-facing filesystem, in
> a cross-project design session. They are facts about DVC's and the archive's
> behaviour, not chirograph decisions — but they constrain chirograph's design, which
> is why they live here.
>
> This document exists to settle one recurring tangle: **how a `.dvc` file that
> depends on a content-addressed chiro manifest can be *updated* when the upstream
> record moves**, without giving up content-addressing.
>
> **Verb ownership (settled after the first draft).** The two verbs are
> **`archivefs check`** and **`archivefs update`**, not chirograph commands. Both must
> read the live archive — for the current record version, and for the new checksums —
> and that I/O is archive-specific, so it belongs where the archive backends live.
> chirograph stays network-free and supplies the manifest primitives the verbs call.
> The chirograph-side view is [`design-check-and-update.md`](design-check-and-update.md).

---

## 0. The tangle, stated plainly

A `chiro://<hash>` reference is content-addressed: `name = content_hash`. That buys the
two things chirograph exists for — trustless, DNS-style lookup (any store can serve a
manifest; you trust it by its hash) and self-verifying integrity (the name *is* the
proof). It is also, *by that same construction*, **immutable**: you cannot change the
name without changing the thing.

So when someone writes `dvc import-url chiro://<hash> …` and later runs
`dvc update data.dvc`, expecting it to notice that the upstream archive record has
changed, they hit a wall — and it *feels* like `chiro://` is broken for update. It
is not. The reference is doing exactly its job. The mistake is asking one immutable
name to also be the thing that tracks "latest".

The resolution is not to weaken content-addressing. It is to see that **the mutability
belongs one layer down, inside the manifest**, and that *"update"* is two different
operations that DVC's single `update` verb conflates.

---

## 1. Content-addressing and updatability are dual — the Git model

You never *update* a Git commit. A commit SHA is content-addressed, immutable,
self-verifying, distributable; to move forward you create a *new* commit and advance a
*branch*, which is a separate, mutable name. Trying to make one name be both the SHA
and the branch is a category error.

A chiro manifest **is a commit.** The mutable reference has to be a different thing —
and chirograph already provides it: the **concept reference**
(`resolution-and-config.md` §1), the spelling for "whatever this dataset points at
now". The version reference (`chiro://<algo>:<digest>`) is the SHA; the concept
reference (`chiro-concept:doi/…`) is the branch.

The corollary that unties the knot: **you never mutate a manifest, you supersede it.**
An update *creates a new immutable manifest* (new `content_hash`) and *repoints* the
dependency at it. Any design that tries to mutate a manifest in place is fighting
content-addressing and will lose.

---

## 2. The manifest is the boundary object — mutability lives *inside* it

This is what lets you keep `name = content_hash` and *still* have updatability. The
manifest's outer skin is immutable and hashed; its *contents* carry a deliberately
mutable pointer downward.

```
target URL  chiro://<content_hash>        immutable, content-addressed, self-verifying   ← the PIN
      └─ resolves to ─▶  MANIFEST      immutable content; manifest version = its hash
                            └─ carries: source URL (e.g. bia://S-BIAD3080)             ← the TRACKER
                                          + record version R it snapshotted
                                          + per-file file versions
                                 └─ resolves to ─▶  live archive record   ← mutable
```

The manifest is the *one* object that holds both halves: an immutable, verifiable
identity on the outside, and a mutable source reference on the inside. So nothing about
content-addressing is surrendered — the tracking lives at the source layer, where
mutation actually happens.

---

## 3. Three URLs

Naming these precisely removes most of the confusion.

1. **Target URL (typed)** — the argument to `dvc import-url`. For a pinned import this
   *is* `chiro://<content_hash>`; for a tracking import it is the concept reference.
2. **Target URL (recorded)** — what lands in the `.dvc` `deps` as the dependency +
   its hash. **When `name = content_hash`, (1) and (2) are the same value** — the
   identifier and the integrity proof have collapsed into one token. *That collapse is
   exactly what makes it a pin and not a tracker*, and it is why in-place `dvc update`
   has nothing to move.
3. **Source URL (inside the manifest)** — a field on the manifest pointing at the
   record in the origin archive (`bia://…`, `zenodo://…`, `geo:…`). This is the mutable
   thing, and it is *not* visible to DVC.

DVC only ever sees URLs (1)/(2). It has no concept of (3), the source *behind* the
manifest — so it structurally cannot check whether the upstream record moved. Only a
chirograph-aware tool can, by following the source URL.

---

## 4. Three versions

Each layer of §2 has its own notion of "changed", with different cost and authority.

| version | layer | "did it change?" | cost | authority |
|---|---|---|---|---|
| **manifest version** = `content_hash` | the pin | re-fetch by hash, compare | cheap | **definitive** for the pinned file-set |
| **record version** (mtime / version DOI / etc.) | the source, via the manifest's source URL | ask the archive | cheap | **advisory, over-approximate** |
| **file version** (per-file mtime / etag / checksum) | individual files | HEAD per file, or a published md5 column | medium | precise diff |

Two things to hold onto:

- The **cheap** signal (record version) is the **least authoritative**. A record
  version bump can be a metadata-only edit (e.g. a depositor adding an md5 column) that
  did not touch a single byte you imported. So record-version drift means *"worth
  regenerating and diffing"*, never *"this is wrong"* — it is an **advisory**, and the
  right response is a graceful warning, not an error.
- The **authoritative** signal (`content_hash`, obtained by regenerating the manifest
  from the current source) is **expensive** — it requires re-hashing, i.e. reading the
  bytes (or, for a source that publishes per-file checksums, reading those). So the
  correct pattern is: **use the cheap record-version signal to *gate* the expensive
  `content_hash` recompute.**

### mtime is a valid *trigger* and an invalid *token* — [measured]

Tempting shortcut: use the source's mtime as the version identity, so a completed
import becomes version-aware and pullable. It was tried end-to-end against BIA:

- Setting a DVC `version_id` from the study `modified` mtime **did** make a completed
  `dvc update`-style import pullable in a fresh clone. So the *mechanism* works.
- But corrupting one file's bytes at the source and pulling → **DVC reported success
  and wrote the wrong bytes, with no error.** Repeated with `version_id` = the file's
  *md5* (a real content hash): **also silently accepted.** And with no version-awareness
  at all (a plain partial import): **also silently accepted.**

Conclusion: mtime is fine as the cheap change-*trigger* at the record layer (§4, row
2). It is unsafe as an integrity *token*, because a timestamp is decoupled from
content — but so is any token, at the DVC layer, per §6. Do not pin or verify on mtime;
only ever use it to decide *whether to recompute the manifest*.

---

## 5. The verb mismatch: `dvc update` assumes track-latest; an archive import wants pin-and-notify

`dvc update` was built for a **dynamic source** — a DVC repo, a pipeline output — where
"re-resolve and follow the latest" is the desired default. An **archive import of a
stable record** is the opposite model: **pin hard, and *notify* me if the world moved
— never silently absorb it.** A stable published record changing is an *event to
adjudicate*, not a stream to follow.

DVC offers one verb and defaults it to track-latest. That impedance mismatch is the
whole tangle. Content-addressing is *correct* for pin-and-notify; it is DVC's verb
whose semantics don't fit. So the fix is to stop routing pin-and-notify through
`dvc update`, and to split the operation the way the two layers already imply.

---

## 6. What has to enforce integrity — and it is not DVC — [measured]

A hard constraint on the whole design, established by experiment:

**DVC does not re-verify content when it fetches an import.** Its transfer/fetch path
defaults to `verify = False` (`dvc_data`'s `DEFAULT_VERIFY = False`; the read-only
`FileStorage` built for an import fallback is created without `verify`). Measured: on
`dvc pull` in a fresh clone, a source that serves *different bytes than the `.dvc`
records* is accepted silently — the recorded md5 is treated as intent, not enforced at
fetch time.

This is safe for an immutable source (a Zenodo *version DOI* cannot serve other bytes)
and unsafe for a mutable one (a BIA accession, GEO record, or any archive that mutates
in place). It means:

> **The manifest supplies the checksum; the *consumer* must do the checking.** DVC will
> not. A chiro-aware materialisation has to hash the bytes it writes and compare them
> to the manifest's per-file checksums, and **refuse** on mismatch. This is the single
> most important thing chirograph's DVC story must own — it is what converts "DVC
> silently accepted wrong bytes" into "chirograph refused them".

The manifest's advantage over any mtime/version token is precisely that it carries a
**real per-file content hash to enforce against**. That is the material; the enforcement
is a consumer responsibility, exercised at materialise/verify time (`chiro verify`, or a
verify-on-write in the filesystem), never assumed from DVC.

---

## 7. The two-verb resolution

Map the operation onto the layer it actually touches.

### `dvc update data.dvc` → graceful, at the manifest layer (like `git fetch`)

When the dependency is a version reference (`chiro://<content_hash>`), re-resolving it
yields the *same* manifest by hash — verified — so DVC correctly finds **no change and
does nothing.** This must **never fail**: there is genuinely nothing to update at the
pin layer, and a no-op is the honest answer.

Optionally, the chiro-aware filesystem may, *during* that re-resolution, follow the
manifest's source URL (§3, URL 3), read the current **record version**, and **warn** if
it has advanced past the one the manifest snapshotted. Warn, never rewrite — this is
`git fetch` telling you the upstream branch moved while changing nothing local. (Do not
rely on `dvc update` to surface a warning legibly; treat this as best-effort. The
authoritative check belongs in `archivefs check`, below.)

### `archivefs update data.dvc` → deliberate supersede-and-repoint (like `git merge`)

The real update is an **archivefs** operation, because it must read the live archive to
regenerate the manifest, and because it *creates a new immutable object and moves a
pointer* — which DVC's in-place-hash-bump model cannot express. Its archive-facing steps
are archivefs's; its manifest arithmetic is chirograph's:

1. Read the pinned manifest referenced by the `.dvc`.
2. Follow its **source URL**; read the source's current **record version**. *(archivefs;
   archive-specific network.)*
3. If unchanged → nothing to do (this is why the cheap record-version signal matters).
4. If changed → **regenerate** the manifest from the current source *(archivefs fetches
   the checksums or bytes; chirograph builds the manifest)*. Gate the regeneration on
   the record-version signal so the common (unchanged) case is cheap; for a source that
   publishes per-file checksums, regeneration is download-free.
5. Compare the new `content_hash` to the pinned one *(chirograph)*. **Equal** → the
   record moved but your file-set did not (a metadata-only edit); no re-pin, optionally
   refresh the recorded record version. **Different** → register the new manifest (a
   *new* `chiro://<hash>`) and refresh the `.dvc` by re-running `dvc import-url` at the
   new reference — a reviewable diff, exactly like advancing a branch. The `.dvc` is
   *regenerated by DVC*, never hand-edited.

A companion **`archivefs check data.dvc`** (read-only) does steps 1–3 and reports drift
without touching anything — the thing you run in CI to answer "is there anything
newer, and does it affect me".

### Why this factoring, not a cleverer single verb

Because it is the *only* factoring consistent with `name = content_hash`. Updating a
content-addressed object is necessarily "make a new one and repoint"; the moment a tool
tries to do that in place it has stopped being content-addressed. DVC's `update` being
"too clumsy" for this is not a DVC deficiency to work around — it is the correct signal
that the operation was mis-assigned to the wrong layer.

### Why the verbs are archivefs's, not chirograph's

Both verbs must read the live archive twice: for the current **record version** (step 2)
and for the new **checksums** (step 4). Both reads are *archive-specific* — knowing that
a Zenodo record lists per-file md5s in its API, or that a BIA study's version is its
`modified` date, is exactly archivefs's domain. chirograph has no such knowledge and,
by [`decisions.md`](decisions.md) D-08, no network. So the verbs live in archivefs, and
chirograph supplies the network-free manifest primitives they call: build-from-checksums,
build-from-directory (`capture`), `diff`, `chiro ref`, and `chiro verify`.

---

## 8. Division of labour (checklist for implementation)

Mostly this reuses the existing model; the new pieces are the source-tracking fields
(chirograph) and the two verbs (archivefs).

### chirograph provides

- **The tracker fields on the manifest.** A **`concept`** header (the mutable pointer to
  the origin record, resolvable to "current") and the **`record_version`** it was
  generated at; per entry, a **`file_version`** where the source exposes one, for a
  precise diff. `content_hash` remains the manifest version and the pin. These are
  proposed as a `1.1` minor in [`chiro-format-spec.md`](chiro-format-spec.md) §8, all
  scalars, additive under D-19. Note `concept` is *distinct from* `source_url` (the fetch
  base); see spec §8.
- **Network-free manifest primitives** the verbs call: build a manifest from
  archive-supplied checksums, `capture` from a fetched directory, `diff`, `chiro ref`,
  and `chiro verify`.

### archivefs provides

- **`archivefs check <dvc-or-manifest>`** — read-only drift at the record-version layer;
  advisory, over-approximate, graceful; exit codes per D-16 (finding ≠ failure).
- **`archivefs update <dvc>`** — supersede-and-repoint: read the source (record version,
  then checksums), call chirograph to build and compare, register the new manifest, and
  refresh the `.dvc` via `dvc import-url` (never hand-edited). Gate regeneration on the
  record-version signal.
- Both are archivefs's because both read the live archive, which is archive-specific.

### shared discipline

- **Enforcement is the consumer's, not DVC's (§6).** Whatever materialises bytes for a
  chiro-referenced import must verify them against the manifest's per-file checksums and
  refuse on mismatch. In the archivefs integration this is a verify-on-materialise in
  the filesystem; `chiro verify` is the standalone form.
- **Keep the two reference schemes distinct** (`resolution-and-config.md` §1): a
  `chiro://<hash>` in a `.dvc` is a pin and `dvc update` is a no-op on it; a
  `chiro-concept:` reference is where "track latest" is opted into deliberately. Never
  one scheme with two meanings — a `.dvc` that *looks* pinned must *be* pinned.

---

## 9. Scope note: static import vs dynamic source

Everything above is tuned for the **relatively static import of a normally-stable
archive record** — the case where a change upstream is rare and wants human
adjudication. It is deliberately *not* the model for depending on an intrinsically
dynamic DVC repo, where auto-follow is correct and `dvc update`'s native semantics are
the right tool. Chirograph's job is the former: to make a mutable or checksum-less
archive behave, for the consumer, like an immutable, verifiable, and *knowably-stale*
source — pinned by content, tracked by reference, and enforced on read.
