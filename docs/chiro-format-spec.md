# `.chiro.json` Format Specification (Chirograph)

**Status:** draft v0.2 · **Format schema version:** `1.0`

> **Naming.** Project **Chirograph** — after the medieval document written in
> duplicate with a word inscribed across the join, then *cut through that word* so
> authenticity was proven by matching the two halves (and the split letters). That is
> content-addressed verification, centuries early: the document and its counterpart
> must match exactly or the match fails — precisely what a manifest does against its
> data. Technical token **`chiro`**: files `.chiro.json`, references `chiro://…`, CLI
> `chirograph` (alias `chiro`). PyPI package **`chirograph`** (unqualified — clean).
> The internal `schema` field value is `"chirograph"`. (Earlier working names retired:
> `xdir` — invisible etymology + directory-lister collisions; `tessera`/`bulla` — both
> taken, `bulla` by a conceptually-adjacent agent-receipt tool.) The separate fsspec
> plugin is **`archivefs`**.

A manifest format for scientific-data depositions: a machine-readable list of the
files in a deposition, each with a checksum, size, and path, plus header metadata.

It is designed to **mirror DVC's `.dir` format as closely as possible** — the body
is a strict superset of a DVC `.dir` (`{relpath, md5, size}` entries), so a
DVC-literate tool or person reads it with zero translation, and generating a
manifest from a DVC cache is near-literal transcription. It adds a header block,
provenance semantics, a two-path model, and a body-integrity hash for standalone
use.

It is the **central abstraction** of the associated fsspec/DVC plugin: a repository
URL resolves to (or auto-discovers) an `.chiro.json`, and that manifest *is* the
directory listing + checksums — download-free by construction. Backend-specific code
then only has to (a) locate bytes in the repo and (b) place them in the workspace.

---

## 0. What we adopt from DVC (and why)

Verified against DVC's docs and serialization source:

- DVC `.dir` entries are `{"relpath": ..., "md5": ..., "size": ...}`, a JSON array
  **sorted by `relpath`**, paths joined with POSIX separators.
- The **directory hash is the md5 of the `.dir` JSON content** — DVC's own answer to
  "is the manifest corrupt." Our Layer-3 (whole-file hash in the `.dvc`) is exactly
  this mechanism.
- The `.dvc` directory entry carries `md5` (the `.dir` hash), `size` (sum of all
  files), `nfiles` (recursive count), `path`.
- DVC hash field is location-dependent: **md5** for local/SSH; **ETag** for
  HTTP/S3/GCS/Azure; special checksums for HDFS/WebHDFS. Only md5 is "first-class."
- Optional DVC per-entry fields exist: `isexec` (execute bit), `version_id` (cloud
  versioning).

**Consequence:** we use **md5**, field names **`relpath` / `md5` / `size`**, DVC's
sort-and-serialise method, and DVC's top-level `size` / `nfiles`. Where our body
carries no extension fields, it *is* a valid DVC `.dir` and our `body_hash` equals
DVC's `.dir` hash.

---

## 1. File identity

### 1.1 Extension
Files use the double extension **`.chiro.json`**.

- Effective extension is `.json` → editors, linters, highlighters, and standard JSON
  parsers work with no special handling.
- The `.chiro.json` compound name identifies the file as a chiro manifest to humans
  and to glob patterns (`*.chiro.json`), which auto-discovery relies on.
- Separates *format* (JSON — parseable by everything) from *role* (chiro manifest —
  discoverable by name). Same pattern as `.tar.gz`, `.d.ts`.

**Deposit caveat.** If a target repo rejects a double-dotted name, the internal
`schema` field (§3.1) is the fallback identifier and the file degrades to plain
`.json`.

### 1.2 Content type
A single JSON object (UTF-8, no BOM). v1.0 is plain JSON. A streamable JSON-Lines
variant is **deferred** (see §10) — it would break the DVC-`.dir` equivalence and
complicate the body hash, so it is not worth adopting until a real deposit's file
count makes JSON parse-time hurt (realistically >~10^5 files).

---

## 2. Top-level structure

```json
{
  "schema": "chirograph",
  "schema_version": "1.0",
  "hash_algorithm": "md5",
  "provenance_type": "self-deposited",
  "record_accession": "GSE000000",
  "source_url": "https://…",
  "created": "2026-07-01T00:00:00Z",
  "retrieved": "2026-07-01T00:00:00Z",
  "generator": "chiro-tools 0.1.0",
  "size": 88440952,
  "nfiles": 3,
  "body_hash": "<md5 of canonicalised files array — see §4>",
  "files": [
    { "relpath": "sampleA/barcodes.tsv.gz", "md5": "aa11…", "size": 20481,
      "source_path": "GSE000000_sampleA_barcodes.tsv.gz" },
    { "relpath": "sampleA/matrix.mtx.gz",   "md5": "cc33…", "size": 88410231,
      "source_path": "GSE000000_sampleA_matrix.mtx.gz" }
  ]
}
```

Two parts: **header fields** (everything except `files`) and the **body** (`files`).
The distinction matters for hashing (§4).

---

## 3. Field definitions

Design discipline: **a field exists only if a consumer or reproducer acts on it.**
Decorative metadata is excluded because it goes stale and erodes trust in the fields
that matter.

### 3.1 Header fields

| Field | Req? | Type | Meaning / who acts on it |
|---|---|---|---|
| `schema` | **yes** | string | Constant `"chirograph"`. Fallback identifier if the filename is mangled; validators key on it. |
| `schema_version` | **yes** | string | Format version (`"1.0"`). Lets future parsers handle old files. |
| `hash_algorithm` | **yes** | string | Algorithm for `body_hash` and every `files[].md5`. `"md5"` (default, DVC-aligned). MAY be `"sha256"` or `"etag"` (see §6.1, §10). A consumer MUST read this to verify anything. |
| `provenance_type` | **yes** | string | `"self-deposited"` or `"observed-download"`. Governs trust semantics (§5). |
| `record_accession` | opt | string | Deposition accession/ID this manifest describes (EGA `EGAD…`, GEO `GSE…`, BIA `…`). Cross-linking; lets a backend confirm it has the right manifest. |
| `source_url` | opt* | string | Base location of the data. **Required by convention for `observed-download`** (where a reproducer re-fetches). For `self-deposited`, where we put it. Also the base against which `source_path` (§3.2) resolves. |
| `created` | **yes** | string | ISO 8601 UTC. When the manifest was written. |
| `retrieved` | opt* | string | ISO 8601 UTC. **Required by convention for `observed-download`** — makes "the version I got" meaningful against a mutable upstream. Omit for `self-deposited`. |
| `generator` | opt | string | Tool + version that wrote the file. Aids *our* debugging; not consumer-actionable, kept separate. |
| `size` | **yes** | integer | Sum of all `files[].size`, bytes. (DVC `.dvc` top-level `size`.) Cheap sanity check; used by `dvc list`-style views. |
| `nfiles` | **yes** | integer | Count of entries in `files` (recursive file count). (DVC `.dvc` top-level `nfiles`.) |
| `body_hash` | **yes** | string | Hash (per `hash_algorithm`) of the canonicalised `files` array (§4). Enables standalone integrity checking; equals the DVC `.dir` hash when the body carries no extension fields. |

\* `opt*` = optional in schema but **required by convention** for the stated
`provenance_type`; a validator SHOULD warn if missing for `observed-download`.

**Deliberately excluded:** free-text `title`/`description`/`abstract` (disclosure
surface + goes stale — do **not** reproduce study abstracts here) and `contact`
(PII; permanent once committed/deposited).

### 3.2 Body: `files` — the two-path model

Each entry describes one file. It separates **where the file lives in the repository**
from **where it is placed in the workspace** — the "data as stored vs data as used"
distinction, made explicit:

| Field | Req? | Type | Meaning |
|---|---|---|---|
| `relpath` | **yes** | string | **Workspace path** — where the file is materialised on `pull`/`checkout`, relative to the import target. POSIX-separated, relative, no leading `/`, no `.`/`..`. This is the file's identity within the manifest and the primary sort key. Matches DVC `.dir` semantics exactly. |
| `md5` | **yes** | string | Hash of the file's bytes, per `hash_algorithm`. |
| `size` | **yes** | integer | File size in bytes. |
| `source_path` | opt | string | **Deposit/source path** — where to *fetch* the bytes from, relative to `source_url`. Present only when the deposit layout differs from the workspace layout. **If omitted, `source_path == relpath`.** |
| `isexec` | opt | bool | DVC-compat: execute bit. Rarely meaningful for scientific data; omit by default. Reserved so ingesting a DVC `.dir` with `isexec` doesn't collide. |
| `version_id` | opt | string | DVC-compat: cloud-provider version ID for versioned buckets (e.g. S3). Reserved for future BIA/S3 use; omit otherwise. |

**Why two paths.** The **byte-fetcher** uses `source_path` (+ `source_url`) to
*locate* bytes in the repository; the **materialiser** uses `relpath` to *place*
them in the workspace. The manifest is a small routing table between deposited-layout
and used-layout. This is what lets you reorganise a flat `geo://GSE12345` deposit
into a tidy per-sample workspace tree: give `relpath` directory structure that
`source_path` (the flat deposited name) does not have.

**The terse common case.** When deposit layout mirrors workspace layout, omit
`source_path` everywhere and the body is byte-identical to a DVC `.dir`. You only pay
the verbosity when layouts actually diverge.

Rules:
- `relpath` values MUST be unique within a manifest.
- `relpath` and `source_path` MUST be relative, POSIX-separated, and MUST NOT contain
  `..` or absolute components (path-traversal safety — consumers materialise
  `relpath` to disk, and `observed-download` manifests describe others' deposits, so
  validate defensively).
- Unicode: normalise `relpath`/`source_path` to **NFC** before hashing and sorting
  (macOS yields NFD filenames, Linux NFC — the classic cross-platform footgun).
- Entries MUST be sorted by `relpath` on disk (diffs better in Git; canonicalisation
  sorts regardless — §4).
- Identical content at different `relpath`s → two entries with the same `md5`. This is
  fine and mirrors DVC's content-addressed dedup (one cache object, two paths).

---

## 4. Hashing model (three layers, no circularity)

Three integrity layers, each with a distinct job. **A file cannot contain its own
hash** — writing the hash changes the hash — so no layer hashes the whole file *from
inside* the file.

### Layer 1 — per-file hashes (`files[].md5`)
Hash of each payload file's bytes. Verifies individual files on materialisation.

### Layer 2 — body hash (`body_hash`, internal)
Covers the **`files` array only** (not the header). Purpose: **standalone integrity**
— when the manifest travels alone (deposited into GEO/BIA, or shared without an
accompanying `.dvc`), a consumer can confirm the file list wasn't truncated or
corrupted. Excludes the header, so it is **not self-referential** (the header
contains `body_hash`; the body does not).

**Canonicalisation — mirror DVC exactly (the single most important detail).**
`body_hash` is computed over a canonical serialisation of the `files` array that
matches DVC's `.dir` serialisation, NOT over on-disk formatting (which editors
reformat). Rule:

```
# 1. Normalise each entry's relpath/source_path to NFC.
# 2. Sort entries by relpath (posix).
# 3. Serialise with sorted keys and no insignificant whitespace:
canonical = json.dumps(files_sorted, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
body_hash = HEX( hash_algorithm( canonical ) )
```

- Independent of pretty-printing, key order, indentation.
- **When the body carries only `{relpath, md5, size}` (no extension fields), this
  canonical form equals a DVC `.dir`'s content, so `body_hash` == DVC's `.dir`
  hash** — a DVC user can verify our manifest with DVC's own logic.
- Extension fields (`source_path`, `isexec`, `version_id`) are included in the
  canonical serialisation when present, so they are covered by `body_hash` — but
  their presence means `body_hash` no longer equals a bare DVC `.dir` hash (expected).
- **Lock this with test vectors** (§10). A reimplementation that canonicalises
  *almost* identically produces non-matching hashes silently.

### Layer 3 — whole-file hash (external, in the `.dvc`)
The committed `.dvc` records the md5 of the **entire `.chiro.json`** (header + body +
internal hashes). This is DVC's *native* behaviour — the `.dir` hash — applied to our
manifest. Strong, tamper-evident:

```
Git vouches for the .dvc  →  .dvc vouches for the whole .chiro.json  →
  body_hash vouches for the files array  →  files[].md5 vouch for the bytes
```

Not stored inside the manifest (that would be the self-reference trap).

### When each layer applies

| Scenario | Layer 3 (`.dvc`) | Layer 2 (`body_hash`) | Layer 1 (per-file) |
|---|---|---|---|
| Manifest tracked by DVC (normal) | ✅ whole-file integrity | (redundant but harmless) | ✅ on materialisation |
| Manifest standalone (deposited/shared alone) | — | ✅ file-list integrity | ✅ on materialisation |

---

## 5. Provenance semantics

`provenance_type` tells a consumer **which guarantee they are getting**. Conflating
them would let the weaker masquerade as the stronger.

| | `self-deposited` | `observed-download` |
|---|---|---|
| Claim | "What I published, hashes verified against my local originals." | "What I retrieved from `source_url` on `retrieved`; matching bytes = the same bytes I analysed." |
| Guarantee | Integrity **from source** — authoritative. | **Reproducibility** provenance — NOT authoritative; may not match any canonical upstream version. |
| Typical use | We deposited to EGA/GEO/BIA/Zenodo and shipped the manifest alongside. | We consumed someone else's dataset (e.g. a GEO record with weak/no upstream checksums) and recorded what we got, to publish with our analysis. |
| Requires | `created` | `source_url`, `retrieved` |
| Hash caveat | Real md5s (we computed them). | md5 if we downloaded+hashed; MAY be `etag` if we recorded the repo's native HTTP/S3 validator without downloading (§6.1). |

---

## 6. Auto-discovery (plugin behaviour)

Two-tier resolution. Auto-discovery is a convenience that works where the repo has a
listable record root; the explicit-URL form is the guaranteed universal path.

1. **Explicit** — the URL names an `.chiro.json` directly → use it. Always works; no
   listing needed.
2. **Auto-discover** — else look for a manifest at a well-known name within the
   record. **Plausibility varies by repo:**
   - **Zenodo** — record API lists all files → one metadata call finds
     `*.chiro.json`. Clean.
   - **GEO** — supplementary files at a predictable listable FTP/HTTPS path → glob
     works, with care for GEO's quirky layout.
   - **EGA** — dataset listing requires auth → discovery is an *authenticated* `ls`,
     not anonymous. Works, but not credential-free.
   - Non-listable repos → cannot auto-discover; must name explicitly.
3. **Fallback** — no manifest found (or repo can't list) → native `ls()` without a
   manifest (weaker/no download-free checksums).

**Naming convention:** prefer an accession-derived name (`GSE12345.chiro.json`) over a
generic `manifest.chiro.json` — unambiguous in a flat deposit, sorts predictably, and
survives being downloaded next to other records' manifests.

### 6.1 The md5-vs-ETag reality (HTTP/S3 sources)
DVC uses ETag (not md5) for HTTP/S3/GCS/Azure. For files **we deposit**, we compute
real md5s regardless of transport. But for an `observed-download` manifest of an
HTTP/S3 source, the validator the repo exposes *without downloading* may be an ETag,
and S3 multipart ETags are **not** plain md5s. So an observed manifest MAY carry
`hash_algorithm: "etag"` (or per-entry mixed — see open items) when a true md5 wasn't
computed. Be honest about which it is rather than mislabel an ETag as md5.

---

## 7. Sharing manifests

- **Default: Git.** Commit manifests (especially `observed-download` ones for
  reference datasets) into the analysis repo — small text, diffs well, lives where a
  reproducer looks. Layer-3 hash in the `.dvc` makes them tamper-evident.
- **Fallback (huge file-counts only):** Git-LFS or a released bundle. Git by default.

---

## 8. Worked examples

### 8.1 Self-deposited, with layout reorganisation (flat GEO deposit → per-sample tree)

```json
{
  "schema": "chirograph",
  "schema_version": "1.0",
  "hash_algorithm": "md5",
  "provenance_type": "self-deposited",
  "record_accession": "GSE000000",
  "source_url": "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE000nnn/GSE000000/suppl/",
  "created": "2026-07-01T00:00:00Z",
  "generator": "chiro-tools 0.1.0",
  "size": 88440952,
  "nfiles": 2,
  "body_hash": "9f2c…",
  "files": [
    { "relpath": "sampleA/barcodes.tsv.gz", "md5": "aa11…", "size": 20481,
      "source_path": "GSE000000_sampleA_barcodes.tsv.gz" },
    { "relpath": "sampleA/matrix.mtx.gz",   "md5": "cc33…", "size": 88420471,
      "source_path": "GSE000000_sampleA_matrix.mtx.gz" }
  ]
}
```
Deposit is flat (`GSE000000_sampleA_*`); workspace is nested (`sampleA/*`).
`source_path` routes the fetch; `relpath` places the file.

### 8.2 Observed-download, layout preserved (reference dataset, DVC-`.dir`-equivalent body)

```json
{
  "schema": "chirograph",
  "schema_version": "1.0",
  "hash_algorithm": "md5",
  "provenance_type": "observed-download",
  "record_accession": "GSE111111",
  "source_url": "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE111nnn/GSE111111/suppl/",
  "created": "2026-07-01T09:15:00Z",
  "retrieved": "2026-07-01T09:15:00Z",
  "generator": "chiro-tools 0.1.0",
  "size": 512900021,
  "nfiles": 1,
  "body_hash": "7d41…",
  "files": [
    { "relpath": "GSE111111_processed.h5ad", "md5": "dd44…", "size": 512900021 }
  ]
}
```
No `source_path` → deposit layout == workspace layout. The `files` array here is a
valid DVC `.dir`; `body_hash` equals its DVC `.dir` hash.

---

## 9. Reference algorithms (informative)

### Writing (from a directory or DVC cache)
```
1. Walk the tree / read cache entries.
2. Per file: record { relpath (NFC, /-sep), md5, size, [source_path if layout
   differs] }.  From DVC cache: md5 already exists → transcribe, don't recompute.
3. Sort files by relpath.
4. body_hash = HEX(hash_algorithm(canonicalise(files)))     # §4, DVC-matched
5. size = sum(f.size); nfiles = len(files)
6. Fill header (provenance_type, dates, source_url, accession, …).
7. Serialise to <accession>.chiro.json (sorted, pretty on disk is fine — canonical
   form is recomputed for hashing).
8. (If DVC-tracked) let DVC record the whole-file md5 in the .dvc.   # Layer 3
```

### Verifying a standalone manifest
```
1. Parse JSON; check schema == "chirograph", schema_version supported.
2. recomputed = HEX(hash_algorithm(canonicalise(files)))
3. assert recomputed == body_hash                 # Layer 2: file list intact
4. assert sum(f.size) == size and len(files) == nfiles   # cheap sanity
5. Per file, after fetch (from source_path or relpath):
     assert HEX(hash_algorithm(bytes)) == files[i].md5   # Layer 1: bytes intact
```

### Resolving a fetch location
```
source = f.source_path if "source_path" in f else f.relpath
fetch_url = urljoin(source_url, source)
place_at = import_target / f.relpath
```

---

## 10. Open items (freeze before v1.0 final)

- **Hash algorithm:** default **md5** (DVC-aligned; `body_hash` == DVC `.dir` hash).
  `hash_algorithm` allows `sha256` for callers who want it, at the cost of DVC
  equivalence. Decide whether to *ever* emit sha256 or keep md5-only for v1.
- **ETag on observed HTTP/S3 sources (§6.1):** decide whether `hash_algorithm` is
  whole-file only, or whether per-entry algorithm is allowed (some md5, some etag) —
  the latter is messier but more honest for mixed observed manifests. Likely: keep
  whole-file `hash_algorithm`, and for observed HTTP/S3 either download-to-md5 or
  mark the whole manifest `etag`.
- **Auto-discovery filename convention** per repo, and whether we control deposited
  filenames on GEO / BIA / Zenodo.
- **Canonicalisation test vectors:** lock cross-implementation determinism —
  NFC normalisation, integer `size` serialisation, key ordering, extension-field
  inclusion. This is the fragility point.
- **DVC-compat reserved fields:** `isexec`, `version_id` reserved in the entry
  namespace so ingesting a real DVC `.dir` never collides; not emitted by default.
- **Top-level integrity of header:** accepted as unprotected in raw standalone use
  (detectable as malformed JSON / body-hash mismatch in realistic corruption);
  fully protected once DVC-tracked (Layer 3).
- **JSON-Lines variant:** deferred; add non-breakingly via `schema_version` +
  a `content_encoding` marker only if a deposit's file count makes JSON parse-time
  hurt (>~10^5 files).
