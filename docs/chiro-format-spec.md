# `.chiro.json` Format Specification (Chirograph)

**Status:** draft v0.3 · **Format schema version:** `1.0`

> **Errata in v0.3.** Two claims in v0.2 about DVC equivalence were checked
> against a real DVC installation (dvc 3.66.1 / dvc-data 3.18.2) during
> implementation and found to be **false**. They are corrected in place below, and
> marked *(corrected v0.3)*. The reasoning, the evidence, and what was done
> instead are in [`decisions.md`](decisions.md) **D-01** and **D-02**. In summary:
> a DVC `.dir` carries **no `size` field**, and DVC serialises it with Python's
> **default** `json.dumps` separators and `ensure_ascii=True` — so `body_hash` is
> **not** a DVC `.dir` hash. The canonical form specified in §4 is unchanged;
> DVC equivalence is now a separately *derived* value (§4, §11).
>
> §4's canonicalisation is now **frozen** and pinned by published test vectors —
> see §11.

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
is a strict superset of a DVC `.dir` (`{relpath, md5}` entries, plus `size`), so a
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

- *(corrected v0.3)* DVC `.dir` entries are **`{"md5": ..., "relpath": ...}`** — a
  JSON array **sorted by `relpath`**, paths joined with POSIX separators. There is
  **no `size`**: size lives in DVC's `Meta`, which is only serialised in the
  `with_meta=True` variant of `Tree.as_bytes()`, and that variant is not what DVC
  hashes or writes to the cache. (v0.2 claimed `size` was present.)
- *(corrected v0.3)* DVC serialises with `json.dumps(lst, sort_keys=True)` — i.e.
  Python's **default** separators (`", "` and `": "`, *with spaces*) and **default**
  `ensure_ascii=True` (non-ASCII escaped to `\uXXXX`). This is **not** the compact
  form §4 specifies for `body_hash`.
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
sort order, and DVC's top-level `size` / `nfiles`.

*(corrected v0.3)* Where our body carries no extension fields it **is valid input
to DVC's `Tree.from_list`** — DVC accepts the extra `size` key, routing it into
`Meta` — so a DVC-literate tool or person still reads our body with zero
translation, which was the point. But because of the two corrections above, our
`body_hash` **does not equal** DVC's `.dir` hash: DVC drops `size` and uses
different serialisation. The equivalence is instead available as a *derived* value,
computed on demand (§4 "DVC `.dir` interop", `chiro dvc-dir`), which is verified
byte-for-byte against real DVC output. See [`decisions.md`](decisions.md) D-01 for
why the canonical form was **not** changed to chase DVC's.

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

A leading UTF-8 BOM is a hard error, not something to strip: it would change the
whole-file hash and therefore the `chiro://` reference (§4 Layer 3) while leaving
the manifest looking identical.

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
| `schema_version` | **yes** | string | Format version (`"1.0"`). Lets future parsers handle old files. *(v0.3)* A reader MUST reject a differing **major**. A higher **minor** is additive: unknown fields are tolerated and preserved. Consequently a minor version MAY only add **scalar** fields — a nested value has no canonical form under §4, so tolerating one would create exactly the silent divergence §4 exists to prevent. See [`decisions.md`](decisions.md) D-07, D-19. |
| `hash_algorithm` | **yes** | string | Algorithm for `body_hash` and every `files[].md5`. `"md5"` (default, DVC-aligned) or `"sha256"`. A consumer MUST read this to verify anything. *(frozen v0.3)* `"etag"` is **reserved but not valid** at `schema_version` 1.0 — an ETag is not a function of content alone, so it can neither address content nor be checked against bytes; see §6.1 and [`decisions.md`](decisions.md) D-04. |
| `provenance_type` | **yes** | string | `"self-deposited"` or `"observed-download"`. Governs trust semantics (§5). |
| `record_accession` | opt | string | Deposition accession/ID this manifest describes (EGA `EGAD…`, GEO `GSE…`, BIA `…`). Cross-linking; lets a backend confirm it has the right manifest. |
| `source_url` | opt* | string | Base location of the data. **Required by convention for `observed-download`** (where a reproducer re-fetches). For `self-deposited`, where we put it. Also the base against which `source_path` (§3.2) resolves. |
| `created` | **yes** | string | ISO 8601 UTC. When the manifest was written. |
| `retrieved` | opt* | string | ISO 8601 UTC. **Required by convention for `observed-download`** — makes "the version I got" meaningful against a mutable upstream. Omit for `self-deposited`. |
| `generator` | opt | string | Tool + version that wrote the file. Aids *our* debugging; not consumer-actionable, kept separate. |
| `size` | **yes** | integer | Sum of all `files[].size`, bytes. (DVC `.dvc` top-level `size`.) Cheap sanity check; used by `dvc list`-style views. |
| `nfiles` | **yes** | integer | Count of entries in `files` (recursive file count). (DVC `.dvc` top-level `nfiles`.) |
| `body_hash` | **yes** | string | Hash (per `hash_algorithm`) of the canonicalised `files` array (§4), lowercase hex. Enables standalone integrity checking. *(corrected v0.3)* This is **not** the DVC `.dir` hash under any circumstances — see the errata note and §4. |

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
| `md5` | **yes** | string | Hash of the file's bytes, per `hash_algorithm`. *(v0.3)* **Lowercase** hexadecimal, exactly `hash_algorithm`'s digest length. Case is mandated rather than normalised on read, because canonicalisation hashes the array as given (§4) — accepting either case would give one manifest two `body_hash` values. See [`decisions.md`](decisions.md) D-18. |
| `size` | **yes** | integer | File size in bytes. |
| `source_path` | opt | string | **Deposit/source path** — where to *fetch* the bytes from, relative to `source_url`. Present only when the deposit layout differs from the workspace layout. **If omitted, `source_path == relpath`.** |
| `isexec` | opt | bool | DVC-compat: execute bit. Rarely meaningful for scientific data; omit by default. Reserved so ingesting a DVC `.dir` with `isexec` doesn't collide. |
| `version_id` | opt | string | DVC-compat: cloud-provider version ID for versioned buckets (e.g. S3). Reserved for future BIA/S3 use; omit otherwise. |
| `etag` | opt | string | *(added v0.3)* The repository's native HTTP/S3 validator, as observed. **Supplementary and non-authoritative** — never the content hash, because an S3 multipart ETag depends on the part size used at upload and so is not a function of content alone. Sits *alongside* a real `md5`. Also DVC-compat (DVC's `Meta` has an `etag` field). See §6.1 and [`decisions.md`](decisions.md) D-04. |

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

**Canonicalisation (the single most important detail).** *(frozen v0.3.)*
`body_hash` is computed over a canonical serialisation of the `files` array, NOT
over on-disk formatting (which editors reformat). Rule:

```
# 1. Normalise each entry's relpath/source_path to NFC.
# 2. Sort entries by relpath (posix).
# 3. Serialise with sorted keys and no insignificant whitespace:
canonical = json.dumps(files_sorted, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
body_hash = HEX( hash_algorithm( canonical ) )
```

- Independent of pretty-printing, key order, indentation.
- **Normalise before sorting, never the reverse.** NFD's combining marks sort
  differently from the characters they compose into. In NFD, `"cafe\u0301"` sorts
  *before* `"caff"` (compare `e` U+0065 with `f` U+0066); after NFC it is
  `"caf\u00e9"`, which sorts *after* `"caff"` (U+00E9 > U+0066). Sort-then-normalise
  silently yields the reverse order, and therefore a different hash.
- **The sort key is the joined POSIX path as a plain string**, not a tuple of
  components: `"a-b/x"` precedes `"a/b"` because `-` (U+002D) precedes `/`
  (U+002F). Splitting into components first reverses them. This matches DVC.
- **Duplicate `relpath` values are rejected, not sorted stably.** With two equal
  sort keys the output would depend on input order, so there would be no canonical
  form at all.
- **No semantic rewriting.** Canonicalisation is a pure syntactic function of the
  array it is given. In particular, a `source_path` that happens to equal its
  `relpath` is **not** dropped, so such a manifest hashes differently from the
  equivalent terse one. Writers omit redundant `source_path`; canonicalisers must
  not remove it ([`decisions.md`](decisions.md) D-06).
- Extension fields (`source_path`, `isexec`, `version_id`, `etag`) are included in
  the canonical serialisation when present, so they are covered by `body_hash`.
- **Locked by test vectors** (§11). A reimplementation that canonicalises *almost*
  identically produces non-matching hashes silently.

**DVC `.dir` interop — a derived value, not `body_hash`.** *(corrected v0.3.)*
v0.2 claimed that a body carrying only `{relpath, md5, size}` has
`body_hash == ` DVC's `.dir` hash. It does not: DVC drops `size` and serialises
with default separators and ASCII escaping (§0). The equivalence is instead
computed on demand, by projecting each entry down to `{md5, relpath}`, sorting by
`relpath`, and serialising the way DVC does:

```
dvc_dir = json.dumps([{"md5": e.md5, "relpath": e.relpath} for e in sorted_entries],
                     sort_keys=True).encode("utf-8")     # DEFAULT separators, ensure_ascii=True
dvc_dir_hash = HEX( md5( dvc_dir ) )
```

That value is what a `.dvc` records as `md5: <hash>.dir`, so a DVC user can still
verify a chirograph manifest with DVC's own logic — via `chiro dvc-dir` rather
than by inspection. It is verified byte-for-byte against real `dvc add` output.
Keeping it derived rather than making it *be* `body_hash` deliberately avoids
welding a frozen format to a DVC implementation detail that has already changed
once. See [`decisions.md`](decisions.md) D-01.

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

### 6.1 The md5-vs-ETag reality (HTTP/S3 sources) — *resolved v0.3*
DVC uses ETag (not md5) for HTTP/S3/GCS/Azure. For files **we deposit**, we compute
real md5s regardless of transport. But for an `observed-download` manifest of an
HTTP/S3 source, the validator the repo exposes *without downloading* may be an ETag,
and S3 multipart ETags are **not** plain md5s.

v0.2 left open whether such a manifest could carry `hash_algorithm: "etag"`.
**Decision: it cannot.** An ETag is not a function of content alone — the same bytes
uploaded with a different part size yield a different ETag — so it cannot address
content (`chiro://etag:…` would not be immutable), cannot produce a meaningful
`body_hash`, and cannot be recomputed by a consumer holding the file, which makes
Layer-1 verification impossible. A manifest whose hashes cannot be checked is not a
weaker manifest; it is a different, unverifiable artifact wearing the same name.

So: **download and hash, or do not claim a hash.** Where an ETag is worth recording
as corroborating evidence, put it in the per-entry `etag` field (§3.2) *next to* a
real digest, which is honest about exactly which is which — the goal v0.2 was
reaching for. Full reasoning and the reversal path in
[`decisions.md`](decisions.md) D-04.

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

### 8.2 Observed-download, layout preserved (reference dataset, DVC-`.dir`-shaped body)

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
No `source_path` → deposit layout == workspace layout. *(corrected v0.3)* The
`files` array here is valid input to DVC's `Tree.from_list`, but `body_hash` is
**not** its DVC `.dir` hash — DVC drops `size` and serialises differently (§0, §4).
Get the DVC hash with `chiro dvc-dir --hash-only`.

### 8.3 Reproducing 8.1 with the reference CLI

```bash
chiro capture ./deposit \
    --source-url 'https://ftp.ncbi.nlm.nih.gov/geo/series/GSE000nnn/GSE000000/suppl/' \
    --accession GSE000000 \
    --relpath-from-source '^GSE000000_([^_]+)_(.*)$=>\1/\2' \
    -o GSE000000.chiro.json
```

The walked tree is the **deposit** layout, so each walked path becomes
`source_path` and the rule derives `relpath`. The inverse direction — a workspace
tree you are about to deposit flat — is `--source-from-relpath`. See
[`decisions.md`](decisions.md) D-14.

---

## 9. Reference algorithms (informative)

### Writing (from a directory or DVC cache)
```
1. Walk the tree / read cache entries.
2. Per file: record { relpath (NFC, /-sep), md5, size, [source_path if layout
   differs] }.  From DVC cache: md5 already exists → transcribe, don't recompute.
3. Sort files by relpath.
4. body_hash = HEX(hash_algorithm(canonicalise(files)))     # §4
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
base   = source_url if source_url.endswith("/") else source_url + "/"   # see below
fetch_url = urljoin(base, percent_encode(source))
place_at  = import_target / f.relpath
```

*(added v0.3)* **Do not use `urljoin` for this.** It is wrong here in three
separate ways, and two of them affect the repositories this format is aimed at:

1. **Non-hierarchical schemes lose the base entirely.** `urljoin` only resolves
   relative references for schemes in `urllib.parse.uses_relative`; for anything
   else it returns the reference unchanged. So `urljoin("geo://GSE12345/", "a.gz")`
   is `"a.gz"` — not a URL at all. `geo://` is §3.2's own example and `s3://`
   appears in §3.2 and §6.1, so this is the common case.
2. **A base without a trailing slash loses its last segment**, because `urljoin`
   reads it as naming a file: `urljoin("https://x/suppl", "a.gz")` →
   `https://x/a.gz`, silently wrong and only visible as a 404 much later.
3. **A base with a query or fragment loses its path**, because appending the
   trailing slash appends it to the *query*.

Join on the path component instead, carrying scheme, authority, query and fragment
through untouched, and treat a query as a parameter of the request rather than part
of the location. Paths must also be percent-encoded: spaces and `#` are legal in
filenames (§3.2) and significant in URLs.

The reference implementation exposes this as `Manifest.fetch_plan()`, which returns
`(source, relpath, md5, size, url)` per entry — the intended integration point for
a data-access layer such as `archivefs`.

---

## 10. Open items

### Resolved in v0.3

- ✅ **Canonicalisation test vectors.** Frozen and published — see §11. This was
  the fragility point and the blocker on any second implementation.
- ✅ **Hash algorithm.** `md5` (default) or `sha256`. sha256 exists for
  environments where md5 is unavailable at all (FIPS-mode interpreters), at the
  cost of DVC interop. Nothing else. ([`decisions.md`](decisions.md) D-03.)
- ✅ **ETag on observed HTTP/S3 sources.** `hash_algorithm: "etag"` is **rejected**
  in v1; a per-entry, non-authoritative `etag` field carries the repository's
  validator alongside a real digest instead. (§6.1, D-04.)
- ✅ **JSON Schema published** — `chirograph-1.0.schema.json`, `chiro schema`. Note
  it can express a manifest's *shape* but not its integrity (no arithmetic, no
  aggregation, no hashing), so schema validity is necessary and never sufficient.
- ✅ **DVC-compat reserved fields.** `isexec`, `version_id` reserved and accepted;
  not emitted by default. `etag` joins them (§3.2).
- ✅ **Timestamps.** Must carry an explicit UTC offset; naive timestamps are
  rejected. An `observed-download` manifest's whole claim is "these bytes at *this
  instant*", which a naive timestamp does not express. (D-05.)
- ✅ **Unknown fields.** Rejected at the reader's own `schema_version`, tolerated
  and preserved at a higher *minor*. Since unknown fields are covered by
  `body_hash`, tolerating them in one implementation and dropping them in another
  would make one manifest hash two ways. (D-07.)

### Still open

- **Auto-discovery filename convention** per repo, and whether we control deposited
  filenames on GEO / BIA / Zenodo. The convention itself is implemented
  (accession-derived, `GSE12345.chiro.json`); which names each repository actually
  permits is an empirical question needing those repositories. Low priority:
  registry-first resolution demotes auto-discovery to a courtesy.
- **Top-level integrity of the header:** accepted as unprotected in raw standalone
  use (detectable as malformed JSON / body-hash mismatch in realistic corruption);
  fully protected once DVC-tracked or `chiro://`-addressed (Layer 3).
- **Whole-file vs canonical-document addressing.** A whole-file hash changes when
  the JSON is re-indented, which is a sharp edge for a registry keyed on it. Both
  forms are implemented (`chiro ref`, `chiro ref --canonical`); which one a registry
  uses is a Milestone-2 decision. (D-09, and `ideas.md` §1.1.)
- **JSON-Lines variant:** deferred; add non-breakingly via `schema_version` +
  a `content_encoding` marker only if a deposit's file count makes JSON parse-time
  hurt (>~10^5 files). Note the canonical form is already per-entry (`[` + entries
  joined by `,` + `]`), so one canonicalisation rule could serve both encodings.

---

## 11. Conformance test vectors (normative)

*(added v0.3.)* §4's canonicalisation is **frozen** and pinned by a published
vector set. This section is normative: an implementation that does not reproduce
every vector is not a Chirograph implementation.

```bash
chiro test-vectors            # print the vector set
chiro test-vectors --check    # check this implementation against it
chiro test-vectors --path     # where it lives
```

The vectors ship *inside* the package, so `pip download chirograph` is enough to
get them — no clone required.

**Design of the vector set**, in case it needs extending:

- Each case carries the **exact canonical string**, not just its hash. Spec §4
  warns that a near-miss reimplementation fails *silently*; a digest mismatch tells
  you nothing, whereas a string diff tells you whether you got the separators, the
  escaping, the sort, or the normalisation wrong.
- Every hash can be re-derived **with no Chirograph code at all**:
  `printf '%s' "<canonical>" | md5sum`. `tools/check_vectors_independently.sh` does
  exactly that with coreutils, so the frozen values are not merely self-consistent
  with the code that produced them.
- A second digest (`canonical_sha256`) and a byte length accompany each case, so an
  implementation without md5 available can still check itself.
- The file is **pure ASCII** (`\uXXXX` escapes), because the NFC/NFD cases would
  otherwise be un-reviewable — you cannot tell a composed from a decomposed
  character by looking.
- Each case records **why it exists** — which trap it pins.
- `dvc_dir` / `dvc_dir_hash` accompany each case, covering the derived DVC interop
  form (§0, §4) whose serialisation differs.

Coverage includes: the empty array; key-order and input-order invariance; the
hyphen-before-slash sort trap; case-sensitive codepoint ordering; NFD input
normalisation; the normalise-*then*-sort ordering trap; literal-UTF-8 vs escaped
non-ASCII; an astral-plane codepoint (4-byte UTF-8, surrogate pair in UTF-16);
`2^53 + 1` as a size (exact as a JSON integer, lossy as an IEEE-754 double);
zero-size; duplicate digests at two paths; the redundant-vs-terse `source_path`
pair (D-06); all extension fields at once; sha256; and 200 entries in an
adversarial order.

Alongside them: **path-safety** accept/reject vectors (§3.2 is a security boundary
— consumers materialise `relpath` to disk, and `observed-download` manifests
describe other people's deposits) and **whole-manifest** valid/invalid documents
naming the error each must raise.

**These values must never change.** If one is wrong, publish a corrected set under
a new `spec_version` rather than editing in place — other implementations are
checking themselves against them, and silently moving the goalposts is the exact
failure the vectors exist to prevent.
