# Design note: drift-check and update, and what chirograph provides for them

> **Status: design, not built. DRAFT for review.** The model here was worked out from
> the consumer side by the sibling `archivefs` project and recorded in
> [`updatability-and-dvc-update.md`](updatability-and-dvc-update.md), which is the
> authority for *why*. This note is the chirograph-side *what*.
>
> **Verb ownership.** The verbs are **`archivefs check`** and **`archivefs update`**,
> not chirograph commands. Both must read the live archive — to learn the current record
> version, and to fetch the new checksums — and that I/O is *archive-specific*: only
> archivefs knows where a Zenodo record lists its per-file md5s or that a BIA study's
> version is its `modified` date. So chirograph provides the local, filesystem-free
> primitives the verbs are built from. (The updatability doc's §7–§8 originally named
> these `chiro check` / `chiro update`; both this note and that doc now use the
> archivefs names.)

## The rule this note establishes

> **chirograph touches no network, ever.** Every network step in check/update is
> archive-specific, hence archivefs's. chirograph contributes only pure,
> filesystem-free manifest operations.

This keeps chirograph's zero-runtime-dependency promise ([`decisions.md`](decisions.md)
D-08) free of asterisks, and it draws the boundary by *capability* rather than taste:
the fetching and the DVC execution can only be done where the archive and DVC
integration already live.

## Why two operations and not one

DVC has a single `update` verb, defaulted to *track-latest*. Importing a stable archive
record wants the opposite: *pin hard, and notify me if the world moved*. Content-
addressing forces these apart — you cannot update a `chiro://<hash>` in place, because
changing what it names changes the name. So the work splits into a read half and a
write half ([`updatability-and-dvc-update.md`](updatability-and-dvc-update.md) §5, §7):

- **`archivefs check`** — read-only. "Is there anything newer upstream, and does it
  affect me?" Never writes.
- **`archivefs update`** — regenerate the manifest from the current source; if the
  content changed, register the new manifest and refresh the `.dvc`.

`dvc update` on a `chiro://` pin stays a **no-op** as far as the recorded pin goes:
there is nothing to move at the pin layer. (It will still *materialise* missing data by
re-fetching, which is fine.)

## Where the work lives

Each step maps to the tool that can do it. The archive-specific network is archivefs's;
the manifest arithmetic is chirograph's.

| step | tool | network? |
|---|---|---|
| read current `record_version` | archivefs | yes, archive-specific |
| fetch per-file checksums (download-free) or bytes | archivefs | yes, archive-specific |
| build the new manifest from those | **chirograph** | no |
| `diff` old vs new; compute `content_hash` | **chirograph** | no |
| refresh the `.dvc` (`dvc import-url`) | archivefs | executes DVC |
| verify materialised bytes | **chirograph** (`chiro verify`) | no |

## `archivefs check <target.dvc>`

Read-only drift report. Exit codes per [`decisions.md`](decisions.md) D-16: `0` no
drift, `1` a finding, `2` the tool could not run. A finding is **not** a failure — drift
is an event to adjudicate.

1. Read `<target.dvc>` for the pinned manifest reference and the source. *(Read-only;
   neither project edits a `.dvc`.)*
2. Load the pinned manifest; read its `concept` and `record_version`.
3. Fetch the source's current `record_version` (archivefs).
   - Unchanged → "no change detected", `0`. Cheap, the common case.
   - Changed → "record moved; regenerate to see whether your file-set changed", `1`.
     **Advisory** — a changed record version is *worth a look*, not a verdict.
   - No `concept`/`record_version` available → "no upstream signal; cannot tell", and
     say so rather than imply freshness.

Touches nothing. This is the CI check.

## `archivefs update <target.dvc>`

Supersede-and-repoint. The deliberate write.

1. `archivefs check` first; unchanged → stop. (The cheap record-version gate keeps the
   common case free.)
2. Fetch the current source and **build a new manifest** — via the chirograph
   primitive that fits the source (below): from published checksums where the archive
   exposes them (download-free), otherwise by downloading and hashing.
3. `chiro diff` / compare `content_hash` against the pinned manifest:
   - **Equal** → the record moved but your file-set did not (a metadata-only edit). Do
     not re-pin; optionally record the fresh `record_version` so the next check is
     quiet.
   - **Different** → register the new manifest under its own `chiro://<hash>`.
4. Refresh the `.dvc`: `dvc import-url -f chiro://<newhash> <out>`.
5. **`chiro verify`** the materialised tree against the new manifest.

### Refreshing the `.dvc` — one command, no `remove`, no `checkout`

Verified against dvc 3.67.1:

- `dvc import-url -f` overwrites the existing `.dvc` **and** re-materialises the data in
  one step — no separate `dvc remove`, no separate `checkout`.
- `dvc remove` (without `--outs`) is non-destructive anyway: it drops the `.dvc` and
  leaves the workspace data. So even the two-step form is safe. But `-f` makes it one
  step.

### Materialising, and why not `checkout`/`pull`

The wrapper leaves the data materialised, matching `dvc update`, which does. Step 4's
plain `dvc import-url -f` does that inline. A follow-up `dvc checkout` or `dvc pull` is
**the wrong tool**, and this is measured:

- `dvc checkout` copies cache → workspace. After a fresh re-import the bytes are not in
  the cache, so it fails with `missing-files`.
- `dvc pull` fetches a DVC *remote* → cache. A freshly re-imported record was never
  pushed to a remote, so pull has nothing to fetch.
- Only re-fetching from the *source* materialises it — which plain `dvc import-url`
  does inline, and which `dvc update` also does (it re-resolves the import and
  downloads).

So materialise **as part of the import**, not as a follow-up. For a record-only refresh
that intentionally skips materialisation — CI bumping a pin without pulling gigabytes —
the wrapper can pass `--no-download`, but must then document that materialising later
needs `dvc update` (which re-fetches the source), **not** `dvc checkout` or `dvc pull`,
both of which fail on a fresh import.

### Step 5 is non-negotiable

After the import the workspace holds new bytes that **DVC did not verify** — its fetch
path is `verify=False`
([`updatability-and-dvc-update.md`](updatability-and-dvc-update.md) §6). Without the
closing `chiro verify`, `archivefs update` would report success with whatever bytes the
source served, which for a mutable archive is exactly the failure the manifest exists to
prevent.

## What chirograph provides

All local, all filesystem-free-or-local, all already implemented except the `1.1`
fields:

- **Build a manifest from checksum tuples** — construct `FileEntry(relpath, md5, size,
  …)` and pass the list to `Manifest(...)`. No disk, no network. This is the
  download-free regeneration primitive: archivefs reads the archive's published
  checksums, chirograph turns them into a manifest.
- **Build a manifest from a fetched directory** — `capture_from_directory` (the
  `chiro capture` core), for the download-then-hash path.
- **`diff`** — compare two manifests; the drift report.
- **`canonical_ref` / `chiro ref`** — the `chiro://<content_hash>` of the new manifest,
  which becomes the new pin.
- **`chiro verify`** — the materialise-time integrity check that DVC does not do.
- **Proposed `1.1` fields** (spec §8), the tracker/version signals check and update read:
  - **`concept`** (header) — the mutable tracker. Distinct from `source_url`, which
    stays the fetch base; see spec §8 "The tracker is `concept`, not `source_url`".
  - **`record_version`** (header) — what `check` compares. Scalar, additive under
    [`decisions.md`](decisions.md) D-19 / spec §3.3.
  - **`file_version`** (entry) — a precise, legible per-file diff when regeneration does
    change the content. Optional; `etag` (§3.2) already covers the S3/HTTP case.

## Boundaries

- **No chirograph command writes — or requires — a network connection.** The
  check/update *verbs* are archivefs's; chirograph supplies primitives they call. There
  is no `chiro check` or `chiro update` command. (An earlier draft had them; the network
  they need is archive-specific, so they belong where the archives are.)
- **No project writes a `.dvc`.** chirograph and archivefs may *read* one; the refresh
  is delegated to `dvc import-url`.
- **`record_version` gating trusts the archive's claim** that it bumps the version
  whenever content changes. That is a cost optimisation, never a correctness guarantee;
  the only correctness guarantee is a full `content_hash` recompute, and ultimately
  `chiro verify` against the bytes.
- **This is for stable records.** A genuinely dynamic source (a live DVC repo) wants
  DVC's native track-latest, not this. These operations exist for the archive-import
  case where a change is rare and wants human adjudication
  ([`updatability-and-dvc-update.md`](updatability-and-dvc-update.md) §9).
