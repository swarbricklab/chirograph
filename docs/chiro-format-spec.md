# `.chiro.json` Format Specification

**Format schema version:** `2.0` · **Spec revision:** v0.6 · **Status:** frozen (`1.0` core; `1.1` adds the optional §8 staleness fields; `2.0` adds the §9 container block)

This document is **normative**. It defines what a `.chiro.json` manifest is, what
each field means, and how the hashes are computed. It deliberately contains no
rationale: the reasoning, the trade-offs and the history live in
[`chiro-format-rationale.md`](chiro-format-rationale.md), so that this document can
be read as a specification and diffed as one.

- **Why the format is this way:** [`chiro-format-rationale.md`](chiro-format-rationale.md)
- **How `chiro://` references resolve:** [`resolution-and-config.md`](resolution-and-config.md)
- **How to use it:** the `howto-*.md` documents, indexed in [`README.md`](../README.md)

Requirement keywords (**MUST**, **MUST NOT**, **SHOULD**, **MAY**) are used in the
RFC 2119 sense.

---

## 1. File identity

### 1.1 Filename

A manifest **SHOULD** be named:

```
<digest>.chiro.json
22fe1ab2c4a307849fad2cc98eea37dd.chiro.json
```

where `<digest>` is the lowercase hex digest of the manifest's **entire file
content**, under the algorithm named by its own `hash_algorithm` field. This is the
same value that a `chiro://` reference to the manifest carries (§6).

- Where a filename carries a digest, that digest **MUST** be the whole-file digest,
  so that the filename and any `chiro://` reference to the file agree. A filename
  carrying the canonical-document digest, or any other value, is non-conforming.
- A manifest **MAY** instead be named after the record it describes
  (`GSE000000.chiro.json`) in the one case where a human browses the directory
  directly: a deposit that will be read by consumers who do not use Chirograph.
- Where a deposition target rejects double-dotted filenames, `.json` **MAY** be used.
- A reader **MUST NOT** infer a manifest's content from its filename, and **MUST
  NOT** rely on the filename to determine that a document is a manifest. The
  `schema` field (§3.1) is the identifying marker.

A digest-named manifest is verifiable with no Chirograph tooling at all:

```console
$ md5sum *.chiro.json
22fe1ab2c4a307849fad2cc98eea37dd  22fe1ab2c4a307849fad2cc98eea37dd.chiro.json
```

Because the name is a function of the content, a digest-named manifest **MUST** be
treated as immutable: editing or reformatting it invalidates its own name. See
[`chiro-format-rationale.md`](chiro-format-rationale.md) §13.

### 1.2 Content type

A manifest is a single JSON object.

- A manifest **MUST** be encoded as UTF-8.
- A manifest **MUST NOT** begin with a byte-order mark.
- A reader **MUST** reject any other encoding rather than decode it.

At `schema_version` `1.0` the encoding is plain JSON. No streaming or line-delimited
encoding is defined.

---

## 2. Top-level structure

A manifest has two parts: the **header** (every top-level field except `files`) and
the **body** (`files`). The distinction is load-bearing for hashing — see §4.

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
  "generator": "chirograph 0.1.0",
  "size": 88440952,
  "nfiles": 2,
  "body_hash": "<hash of the canonicalised files array — §4>",
  "files": [
    { "relpath": "sampleA/barcodes.tsv.gz", "md5": "aa11…", "size": 20481,
      "source_path": "GSE000000_sampleA_barcodes.tsv.gz" },
    { "relpath": "sampleA/matrix.mtx.gz",   "md5": "cc33…", "size": 88420471,
      "source_path": "GSE000000_sampleA_matrix.mtx.gz" }
  ]
}
```

An absent field and a field present with value `null` are **not** equivalent: a field
that does not apply **MUST** be omitted, not set to `null`.

---

## 3. Field definitions

### 3.1 Header fields

| Field | Required | Type | Definition |
|---|---|---|---|
| `schema` | **yes** | string | **MUST** be the constant `"chirograph"`. |
| `schema_version` | **yes** | string | `"<major>.<minor>"`. A reader **MUST** reject a differing major version. A reader **MUST** tolerate and preserve unknown fields at a higher minor version. A minor version **MAY** add only scalar-valued header or entry fields (a nested value has no canonical form; see §3.3). A writer **MUST** emit the lowest minor whose fields it uses: a manifest carrying a 1.1 field declares `"1.1"`, one carrying none declares `"1.0"`. |
| `hash_algorithm` | **yes** | string | `"md5"` or `"sha256"`. Applies to `body_hash` and to every `files[].md5`. No other value is valid at `1.0`. A consumer **MUST** read this field before verifying anything. |
| `provenance_type` | **yes** | string | `"self-deposited"` or `"observed-download"`. See §5. |
| `created` | **yes** | string | ISO 8601 timestamp with an explicit UTC offset. When the manifest was written. A naive timestamp **MUST** be rejected. |
| `size` | **yes** | integer | Sum of all `files[].size`, in bytes. |
| `nfiles` | **yes** | integer | Number of entries in `files`. |
| `body_hash` | **yes** | string | Lowercase hex digest, per `hash_algorithm`, of the canonicalised `files` array (§4). |
| `record_accession` | optional | string | The deposition accession or identifier this manifest describes. |
| `concept` | optional *(1.1)* | string | Mutable tracker for the origin *dataset*, as a CURIE (`geo:GSE12345`, `doi:10.5281/zenodo.7654320`) — what a staleness check re-resolves to find a newer version. Distinct from `source_url` (§8). Advisory. Present ⇒ `schema_version` ≥ `"1.1"`. |
| `record_version` | optional *(1.1)* | string | The source's record-level version at the observed time, in whatever form the archive exposes (a date, a version DOI, a serial). **Advisory** — compared for change only, never authoritative for content (§8). Present ⇒ `schema_version` ≥ `"1.1"`. |
| `source_url` | conditional | string | Base location of the data, and the base against which `source_path` resolves (§3.4). **Required by convention** when `provenance_type` is `"observed-download"`. |
| `retrieved` | conditional | string | ISO 8601 timestamp with an explicit UTC offset; when the bytes were fetched. **Required by convention** when `provenance_type` is `"observed-download"`. **MUST** be omitted when `provenance_type` is `"self-deposited"`. |
| `generator` | optional | string | Tool and version that wrote the manifest. |

"Required by convention" means: valid to omit as far as the schema is concerned, but a
validator **SHOULD** report its absence as a finding, because the manifest's claim is
not meaningful without it.

The following **MUST NOT** be present at `1.0`: free-text `title`, `description`, or
`abstract` fields, and any `contact` or other personal-identifier field.

### 3.2 Body: `files`

`files` is an array of entries, each describing exactly one file.

| Field | Required | Type | Definition |
|---|---|---|---|
| `relpath` | **yes** | string | **Workspace path**: where the file is materialised, relative to the import target. The entry's identity within the manifest, and the sort key for §4. |
| `md5` | **yes** | string | Digest of the file's bytes per `hash_algorithm`. **MUST** be lowercase hexadecimal of exactly the algorithm's digest length. (The field is named `md5` for DVC compatibility regardless of algorithm.) |
| `size` | **yes** | integer | File size in bytes. |
| `source_path` | optional | string | **Source path**: where the bytes are fetched from, relative to `source_url`. When absent, `source_path` is defined to equal `relpath`. |
| `isexec` | optional | boolean | Execute bit. Reserved for DVC compatibility. |
| `version_id` | optional | string | Provider-native object version identifier. Reserved for DVC compatibility. |
| `etag` | optional | string | The source's native HTTP or S3 validator, as observed. **Non-authoritative**: a consumer **MUST NOT** use this field to verify content, and it **MUST NOT** substitute for `md5`. |
| `file_version` | optional *(1.1)* | string | The source's per-file change signal, as observed (a per-file mtime, say). **Advisory** — a changed value is evidence of change, an unchanged value is evidence of nothing (§8). It **is** covered by `body_hash` (it is an entry field), but **MUST NOT** be used to verify content. Present ⇒ `schema_version` ≥ `"1.1"`. |
| `crc32` | optional *(2.0)* | string | Hex CRC-32 (8 lowercase hex digits) of the member's *uncompressed* bytes, from a ZIP central directory — the coordinate for download-free confirmation (§9). **Weak and non-authoritative**: a cheap cross-check, **MUST NOT** be a content-address or used to verify content. Covered by `body_hash`. Only meaningful for a member under a container (§9); present ⇒ the entry sits under a container and `schema_version` is `"2.0"`. |

### 3.3 Entry fields are flat

Every value in a `files` entry **MUST** be a scalar — a string, an integer, or a
boolean. An entry field **MUST NOT** be an object, an array, or `null`. A float
**MUST** be rejected.

Header fields are not subject to this constraint.

### 3.4 Path rules

These rules are a security boundary: a consumer materialises `relpath` to disk, and
an `observed-download` manifest describes a third party's deposit.

- `relpath` values **MUST** be unique within a manifest.
- `relpath` and `source_path` **MUST** be relative and POSIX-separated (`/`).
- `relpath` and `source_path` **MUST NOT** contain a `..` component, a leading `/`,
  a drive letter, or any other absolute component.
- `relpath` and `source_path` **MUST** be normalised to Unicode NFC before hashing
  and sorting (§4).
- Entries **SHOULD** be stored on disk sorted by `relpath`.

A reader **MUST** reject a manifest violating any **MUST** above. A reader
**SHOULD** additionally report, without rejecting, paths that are safe but lossy on
some filesystems: entries differing only by case, and Windows-reserved names.

Two entries **MAY** share an `md5` — identical content at two paths is valid.

---

## 4. Hashing model

Three layers. No layer hashes the file that contains it.

### Layer 1 — per-file digests

`files[].md5` is the digest of that file's bytes under `hash_algorithm`. This is what
verifies materialised data.

### Layer 2 — `body_hash`

`body_hash` covers the `files` array **only**, never the header. It is therefore not
self-referential: the header contains `body_hash`, the body does not.

`body_hash` is computed over a **canonical serialisation** of the array, not over its
on-disk formatting:

```
1. Normalise every entry's relpath and source_path to NFC.
2. Sort entries by relpath, compared as a single POSIX path string.
3. Serialise:
     json.dumps(entries, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False).encode("utf-8")
4. body_hash = lowercase_hex(hash_algorithm(that byte string))
```

The following are normative properties of that procedure:

- Normalisation **MUST** happen before sorting.
- The sort key **MUST** be the joined path string, **NOT** a tuple of path
  components.
- Duplicate `relpath` values **MUST** be rejected, not sorted stably.
- Canonicalisation **MUST** be a purely syntactic function of the array given. In
  particular a `source_path` equal to its `relpath` **MUST NOT** be removed.
- All present entry fields, including optional and unknown ones, **MUST** be
  included.

This procedure is frozen and pinned by the conformance vectors of §7. An
implementation that does not reproduce every vector is not a conforming
implementation.

**With a `containers` block (`2.0`, §9)**, `body_hash` covers *both* arrays: the
canonical serialisation is over the two-key document
`{"containers": <sorted>, "files": <sorted>}` instead of the bare `files` array —
`json.dumps(sort_keys=True, …)` fixes the key order as `containers` then `files`,
`containers` sort by `source_root`, and every per-element rule above is unchanged.
A manifest with **no** `containers` uses the files-only form above, byte-for-byte as
in `1.x`. Because an older reader cannot fold a block it does not know into
`body_hash`, this is a **major**-version change — which is why a container manifest
declares `"2.0"` and a `1.x` reader refuses it (§2 major-version rule).

### Layer 3 — whole-file hash

The digest of the entire manifest file — header, body and `body_hash` together — is
held **outside** the manifest: in a `.dvc` that tracks it, or in a `chiro://`
reference to it (§6).

This yields the chain:

```
Git vouches for the .dvc  →  .dvc vouches for the whole .chiro.json
  →  body_hash vouches for the files array  →  files[].md5 vouch for the bytes
```

### Which layers apply

| Situation | Layer 3 | Layer 2 | Layer 1 |
|---|---|---|---|
| Manifest tracked by DVC or addressed by `chiro://` | yes | redundant | on materialisation |
| Manifest standalone | not available | yes | on materialisation |

### Derived value: DVC `.dir` equivalence

`body_hash` is **not** a DVC `.dir` hash and never equals one. The DVC-equivalent
value is derived on demand by projecting entries to `{md5, relpath}` and serialising
the way DVC does:

```
json.dumps([{"md5": e.md5, "relpath": e.relpath} for e in sorted_entries],
           sort_keys=True).encode("utf-8")      # DEFAULT separators, ensure_ascii=True
```

The md5 of that byte string is what a `.dvc` records as `md5: <hash>.dir`. It is
available as `chiro dvc-dir`.

---

## 5. Provenance semantics

`provenance_type` states which guarantee a consumer is being offered.

| | `self-deposited` | `observed-download` |
|---|---|---|
| Claim | These are the bytes I published, hashed against my own originals. | These are the bytes I retrieved from `source_url` at `retrieved`. |
| Guarantee | Integrity from the authoritative source. | Reproducibility of one observation. **Not** authoritative, and **not** a claim about any canonical upstream version. |
| Requires | `created` | `created`, `source_url`, `retrieved` |

A mismatch against an `observed-download` manifest **SHOULD** be reported as probable
upstream change rather than as local corruption.

---

## 6. `chiro://` references

A `chiro://` reference addresses a manifest by the digest of its **entire file
content** (Layer 3).

```
chiro://<algorithm>:<lowercase-hex-digest>
chiro://md5:d41d8cd98f00b204e9800998ecf8427e
```

- `<algorithm>` **MUST** be `md5` or `sha256`.
- `<digest>` **MUST** be lowercase hex of exactly that algorithm's length.
- A consumer that fetches a manifest by `chiro://` reference **MUST** verify the
  retrieved bytes against the digest before parsing them.

A `chiro://` reference is content-addressed and therefore immutable. No other form of
`chiro://` reference is valid at `1.0`. How such a reference is *located* is not part
of this specification; see [`resolution-and-config.md`](resolution-and-config.md).

---

## 7. Conformance vectors (normative)

§4's canonicalisation is pinned by a published vector set that ships inside the
package.

```bash
chiro test-vectors            # print the vector set
chiro test-vectors --check    # check an implementation against it
chiro test-vectors --path     # where it lives
```

Each case carries the exact canonical **string**, not only its digest, so that a
failing implementation can diff the string rather than guess from a digest mismatch.
Each digest is independently re-derivable with no Chirograph code:
`printf '%s' "<canonical>" | md5sum`.

**These values MUST NOT change.** A correction is published as a new vector set under
a new version, never as an edit in place.

---

## 8. The staleness fields (`1.1`)

*(Added in `1.1`.)* Three optional scalar fields for detecting that an upstream record
has changed without re-downloading it: `concept` and `record_version` (header, §3.1)
and `file_version` (entry, §3.2). Each corresponds to one of the "three versions" in
[`updatability-and-dvc-update.md`](updatability-and-dvc-update.md) §4, whose field names
this section is reconciled with:

| Field | Level | archivefs term |
|---|---|---|
| `concept` | header | "source URL (the tracker)", URL-3 |
| `record_version` | header | "record version" |
| `file_version` | entry | "file version" |

A manifest that carries any of these **MUST** declare `schema_version` `"1.1"`; one that
carries none **MUST** declare `"1.0"` (and is byte-identical to a `1.0` manifest). A
`1.0`-only reader tolerates them as unknown fields at a higher minor (§3.1) and preserves
them.

All three are **advisory only**. A consumer **MUST NOT** treat an unchanged value
as evidence that content is unchanged. And the authority of a *changed* value is
**archive-dependent**:

- Where `record_version` is a genuine version identifier that the archive guarantees
  immutable (a Zenodo version DOI), a changed value authoritatively means a new version
  exists.
- Where it is only a change-*trigger* — a modification time, as GEO and
  submitted-to-BIA records expose — a changed value means *only* "the record was
  touched; re-verify", never "the content definitely changed". An mtime bumps on a
  metadata edit or an identical re-upload just as readily as on a real change.

So a `record_version` diff is a reason to regenerate and compare content hashes, never
a verdict on the content. These fields **MUST NOT** be used to verify content — that is
what `md5` (§3.2) is for, and a consumer **MUST** enforce it themselves, because DVC
does not ([`updatability-and-dvc-update.md`](updatability-and-dvc-update.md) §6).

### The tracker is `concept`, not `source_url`

`source_url` (§3.1) is the **fetch base** — where the observed bytes are retrieved
from, and the base against which `source_path` resolves. `concept` is the **mutable
tracker** — what a staleness check re-resolves to see whether a newer version exists.

For an archive whose accession is both (GEO, a BIA accession) the two hold the same
value and the distinction is invisible. For a versioned archive they diverge, and the
distinction is the whole point: `source_url` pins the exact version observed
(`zenodo://7654321`, so the exact bytes can be re-fetched), while `concept` names the
dataset (`doi:10.5281/zenodo.7654320`, which resolves to *latest*). Collapsing them
into one field would make either re-fetching-the-observed-bytes or checking-for-newer
impossible, depending on which meaning won.

These three fields are the whole of the `1.1` addition: all scalars, added under the
§3.1 minor-version rule, with no change to a `1.0` manifest's bytes.

## 9. Containers (`2.0`)

*(Added in `2.0`.)* An optional top-level **`containers`** array lets a manifest
describe files delivered *inside* an archive member (a `.zip`, `.tar`, `.tar.gz`, …),
so that its members are described individually and — for ZIP — confirmable without
downloading the payload. The design of record, with the confirm mechanics per format,
is [`container-model.md`](container-model.md); this section is the normative summary.

A manifest **MUST** declare `schema_version` `"2.0"` **iff** it carries a non-empty
`containers` array; one carrying none **MUST** stay `1.x` and is byte-identical. Placing
the container identities in `body_hash` (§4) is why this is a major version, not a minor.

### Container descriptor

Each element of `containers`:

| Field | Required | Type | Definition |
|---|---|---|---|
| `source_root` | **yes** | string | The container's path relative to `source_url`; the prefix its members' `source_path` values sit under, and how the container is fetched. Path-safe (§3.4), NFC. Unique within a manifest and the sort key for §4. |
| `format` | **yes** | string | The archive format (`"zip"`, `"tar"`, `"tar.gz"`, `"tar.bz2"`, `"tar.xz"`, `"tar.zst"`). Selects the confirm strategy. A reader **MUST** treat an **unrecognised** value as opaque (download to confirm), not reject it. |
| `md5` | **yes** | string | The container's whole-file hash, per `hash_algorithm`, lowercase hex. Authenticates the container byte-for-byte. |
| `size` | optional | integer | The container's size in bytes. |
| `etag` | optional | string | The container's native HTTP/S3 validator, as observed. Non-authoritative. |
| `version_id` | optional | string | The container's provider-native object version id. |

Container fields are flat scalars (§3.3), and the whole array is covered by `body_hash`
(§4).

### Membership and validation

- A file is **delivered by** container `C` iff its `source_path` equals
  `C.source_root + "/" + p` for a non-empty in-zip path `p`. A file under no
  `source_root` is loose (fetched directly), exactly as in `1.x`.
- `source_root` values **MUST** be unique and **non-nesting**: no `source_root` may be a
  prefix (at a `"/"` boundary) of another. So a member matches at most one container.
- Every declared container **MUST** have at least one member.
- A member carrying `crc32` **MUST** be delivered by some declared container.

`nfiles`, `size` and `verify` are unaffected — they concern members (the container's own
size lives in its descriptor); a container is a fetch-time intermediary, not a workspace
file, so it never has a `relpath` and is never materialised.
