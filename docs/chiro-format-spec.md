# `.chiro.json` Format Specification

**Format schema version:** `1.0` · **Spec revision:** v0.4 · **Status:** frozen

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
| `schema_version` | **yes** | string | `"<major>.<minor>"`. A reader **MUST** reject a differing major version. A reader **MUST** tolerate and preserve unknown fields at a higher minor version. A minor version **MAY** add only scalar-valued entry fields (see §3.3). |
| `hash_algorithm` | **yes** | string | `"md5"` or `"sha256"`. Applies to `body_hash` and to every `files[].md5`. No other value is valid at `1.0`. A consumer **MUST** read this field before verifying anything. |
| `provenance_type` | **yes** | string | `"self-deposited"` or `"observed-download"`. See §5. |
| `created` | **yes** | string | ISO 8601 timestamp with an explicit UTC offset. When the manifest was written. A naive timestamp **MUST** be rejected. |
| `size` | **yes** | integer | Sum of all `files[].size`, in bytes. |
| `nfiles` | **yes** | integer | Number of entries in `files`. |
| `body_hash` | **yes** | string | Lowercase hex digest, per `hash_algorithm`, of the canonicalised `files` array (§4). |
| `record_accession` | optional | string | The deposition accession or identifier this manifest describes. |
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

## 8. Proposed additions (not part of `1.0`)

> **Status: aspirational.** Nothing in this section is implemented or valid at
> `schema_version` `1.0`. It is recorded here so that consumers do not invent
> conflicting spellings for the same concepts. See
> [`howto-drift.md`](howto-drift.md) for the workflow these serve.

A `1.1` minor would add three scalar fields, for detecting that an upstream record
has changed without re-downloading it:

| Field | Level | Proposed definition |
|---|---|---|
| `concept` | header | Identifier of the *dataset*, as distinct from this observation of it — the mutable pointer to re-resolve when checking for a newer version. Spelled as a CURIE (`geo:GSE12345`, `doi:10.5281/zenodo.7654320`). Not derivable from `record_accession`, because a Zenodo concept DOI is not derivable from a version DOI. |
| `upstream_modified` | header | The source's own record-level change signal, as observed. |
| `last_modified` | entry | The source's per-file change signal, as observed. |

All three would be **advisory only**. A consumer **MUST NOT** treat an unchanged
value as evidence that content is unchanged; only a *changed* value is evidence, and
only of change. They **MUST NOT** be used to verify content.

Because all three are scalars, and `concept` and `upstream_modified` are header
fields, they are addable under the §3.1 minor-version rule without a new major.
