# Container model — ZIP-member identity and download-free confirmation (`2.0`)

> **Status: design of record, not yet implemented.** This note pins the `schema_version`
> `2.0` design agreed in [issue #8](https://github.com/Swarbricklab/chirograph/issues/8)
> so it can be reviewed *before* the canonicalisation core is touched. Once approved it
> becomes normative and the field/rule definitions move into
> [`chiro-format-spec.md`](chiro-format-spec.md); this note keeps the reasoning.

## 1. What it's for

A common author of a manifest is the **depositor**, who has the files locally *before*
they become an archive record. Two things follow: prepare a manifest before deposition,
and — the case this note serves — **confirm the deposit matches** afterwards.

Confirmation is cheap for loose files (compare published per-file checksums) but not for
**ZIP deposits** (common on Zenodo, EGA). A ZIP *member* has no published checksum
anywhere. What a ZIP exposes cheaply — a tail read of its central directory, no payload
download — is, per member, `(path, uncompressed_size, crc32)`, plus the enclosing
container's own md5 (which the archive publishes for the whole file). That
`(container_md5, crc32)` pair confirms a member without downloading gigabytes, and it
survives **re-zipping**: non-deterministic packaging changes the container bytes but not
the members' `(path, size, crc32)`.

To record this, a manifest must carry, per ZIP member, its **`crc32`**, and, once per
container, the container's **md5** and where it sits at the source. This note defines how.

## 2. The model

A `2.0` manifest gains a top-level **`containers`** array. Members link to a container
by their `source_path`; the container's identity lives once, in the block.

```json
{
  "schema": "chirograph",
  "schema_version": "2.0",
  "hash_algorithm": "md5",
  "provenance_type": "observed-download",
  "source_url": "https://zenodo.org/api/records/123/files/",
  "created": "2026-09-06T00:00:00Z",
  "retrieved": "2026-09-06T00:00:00Z",
  "size": 5523,
  "nfiles": 2,
  "body_hash": "<see §4>",
  "containers": [
    { "source_root": "data.zip", "md5": "<zip md5>", "size": 15728640 }
  ],
  "files": [
    { "relpath": "sampleA/matrix.mtx", "source_path": "data.zip/sampleA/matrix.mtx",
      "md5": "<member md5>", "size": 4711, "crc32": "df2441b0" },
    { "relpath": "README.md", "source_path": "README.md", "md5": "<md5>", "size": 812 }
  ]
}
```

- `sampleA/matrix.mtx` is a **ZIP member**: its `source_path` sits under the container's
  `source_root` (`data.zip/…`), so it is delivered by that container; its in-zip path is
  the remainder (`sampleA/matrix.mtx`); it carries a `crc32`.
- `README.md` is a **loose file**: its `source_path` is under no `source_root`, so it is
  fetched directly. Unchanged from `1.x`.

Both `files` entries still carry their own real `md5` — the container fields are
**additive**. The member's `md5` remains the sole authoritative content hash and is what
DVC's `.dir` hash, `dvc list --show-hash`, and verify-on-read use. `crc32` and the
container md5 are cheaper, remotely-checkable *cross-coordinates*, never substitutes.

## 3. The container descriptor

Each element of `containers` describes one container — an essential fact-set about the
archive object itself, nothing derived from its members:

| field | required | type | meaning |
|---|---|---|---|
| `source_root` | **yes** | string | The container's path relative to `source_url`. The exact prefix members match against, and how the container is fetched (`source_url` + `source_root`). The container's primary key (sort key; unique within a manifest). POSIX-separated, NFC, relative, no `..` — the same path rules as `relpath`/`source_path` (§3.4). |
| `md5` | **yes** | string | The container's whole-file hash, per `hash_algorithm`, lowercase hex. What authenticates the container byte-for-byte (so its central directory can be trusted). |
| `size` | optional | integer | The container's size in bytes. Lets a consumer compute the central-directory tail-read offset. |
| `etag` | optional | string | The container's native HTTP/S3 validator, as observed. Non-authoritative, like the per-entry `etag` (§3.2). |
| `version_id` | optional | string | The container's provider-native object version id. |

Deliberately **no member/element count.** A count of manifest members under a container
is *circular* — it is derived from the same `files` list it would purport to validate,
and it duplicates top-level `nfiles`; completeness is checked externally, by `confirm`
comparing the manifest against the live central directory. A count of the container's
central-directory entries is redundant with `md5` (if the zip changes, its md5 changes).
See issue #8 for the full argument.

## 4. Canonicalisation — the one frozen-core change

`body_hash` is defined today as the hash of the canonicalised **`files`** array
([spec §4](chiro-format-spec.md)). At `2.0` it must also cover the container identities
(they are part of the "which bytes, where" claim, and putting the container md5 in
`body_hash` is what makes it standalone-secure). The rule:

- **No `containers` (absent or empty)** — `body_hash` is computed **exactly as today**,
  over the `files` array alone. This is the entire `1.x` world, byte-for-byte unchanged.
- **`containers` present** — `body_hash` is computed over the two-key document
  `{"containers": …, "files": …}`:

  ```
  canonical = json.dumps(
      {"containers": containers_sorted, "files": files_sorted},
      sort_keys=True, separators=(",", ":"), ensure_ascii=False,
  ).encode("utf-8")
  body_hash = HEX(hash_algorithm(canonical))
  ```

  The per-element rules are the frozen §4 rules, unchanged: NFC-normalise path fields
  (`relpath`, `source_path`, `source_root`) before sorting; scalar fields only; sort
  `files` by `relpath` and `containers` by `source_root`; compact separators; sorted
  keys; `ensure_ascii=False`. `sort_keys=True` fixes the top-level order as
  `containers` then `files`.

This is the **only** change to the canonicalisation core: one branch, taken solely when
a manifest uses the new feature.

### Why this is a major (`2.0`), and why it is cheap

A forward-compatible reader re-verifies `body_hash` by canonicalising what it knows.
A reader that has never seen `containers` cannot fold that block into `body_hash`, so it
would compute a *different* hash than a `2.0` writer and wrongly declare the manifest
corrupt. Changing `body_hash` in a way older readers cannot follow is, by definition, a
**major** bump — not a minor. (Contrast a scalar *entry* field: an older reader
preserves it and canonicalises it, reproducing `body_hash` identically — which is why
the `1.1` fields were a minor. A top-level block does not have that property.)

But the bump is **surgical**: a manifest is `2.0` **iff** it carries a `containers`
block. Every loose-file manifest, and every manifest that exists today, stays `1.x` with
its `body_hash` untouched. An old reader meeting a container manifest refuses it on the
major-version gate — correct, because it genuinely cannot verify it. Nothing that exists
today changes.

## 5. Linkage — exact, by `source_path` under `source_root`

A member is delivered by container `C` **iff** its `source_path` equals
`C.source_root + "/" + p` for some non-empty in-zip path `p`. That `p` is the member's
path within the container's central directory. A member whose `source_path` is under no
declared `source_root` is a **loose** file (direct fetch), exactly as in `1.x`.

- The match is on NFC-normalised paths, with the `"/"` boundary required — so
  `data.zip.bak/x` does not match `source_root` `data.zip`.
- **No nesting.** No `source_root` may be a prefix of another (with the `"/"` boundary),
  so a member matches at most one container and matching needs no longest-prefix
  tie-break. This matches archivefs's one-level ZIP expansion.
- There is **no per-member container pointer** — membership is carried entirely by the
  `source_path` the member already needs (to be fetched and located in the central
  directory). This is why `source_path` is the natural home: it is the source-side
  layout, decoupled from `relpath` (the workspace layout), so container membership never
  couples to how the analyst arranged the workspace.

## 6. Member field: `crc32`

A ZIP member entry may carry `crc32` — the hex CRC-32 of the member's *uncompressed*
bytes, as read from the central directory. It is a per-member entry field, so it is
covered by `body_hash` via `files` (part of the "which bytes" claim). It is **optional**:
a member may be listed without being download-free-confirmable.

`crc32` is **weak on its own** — 32-bit, linear, adversarially forgeable — and **MUST
NOT** be treated as a content-address or a verification digest. It is only ever a
*locator / cheap cross-check*, and it is trustworthy only when anchored:

- by the **container `md5`**, which authenticates the whole ZIP byte-for-byte, so the
  central directory inside it (and therefore the `crc32`s it reports) is trusted; and/or
- by the member's own **`md5`**, which the manifest always carries.

So a manifest for a ZIP deposit carries **both** coordinates: `md5` for reproducibility
(DVC hashing, `dvc list --show-hash`, verify-on-read) **and** `crc32` (+ the container)
for download-free `confirm`. Different verbs use each; neither replaces the other. This
is the same discipline as the non-authoritative `etag` (D-04).

## 7. Version derivation and reader support

- A manifest declares `schema_version` `"2.0"` **iff** it carries a non-empty
  `containers` block. Otherwise it declares the lowest minor whose fields it uses
  (`"1.1"` for the staleness fields, else `"1.0"`) — the existing tier ladder, extended.
- `SUPPORTED_MAJOR` becomes `2`: this implementation reads `1.x` and `2.0`; a `3.x`
  document is a hard error, and an *older* implementation rejects `2.0` on its own gate.

## 8. Validation rules

On read (in addition to the existing rules), a `2.0` manifest **MUST** satisfy:

- Every `containers[].source_root` is unique, path-safe (§3.4), and no one is a prefix of
  another (no nesting).
- Every member whose `source_path` sits under a `source_root` is thereby a member of that
  container; a member carrying `crc32` **MUST** sit under some declared container.
- Every declared container has **≥ 1** member under it (no orphan containers).
- `containers[].md5` matches `hash_algorithm`'s digest length and is lowercase hex.

## 9. What does not change

- **`verify_tree`** is untouched. It checks each member's `md5` against the extracted
  file at its `relpath` in the workspace. The container is a fetch-time intermediary,
  never on disk after extraction, so verification does not involve it.
- **`nfiles` / `size`** stay about members: `nfiles = len(files)`, `size = Σ files[].size`
  (uncompressed member sizes). A container's own byte size lives in its descriptor,
  separate.
- **`chiro://` references** (the whole-file / Layer-3 hash) are unaffected — they hash the
  literal file bytes regardless of the `body_hash` rule.
- **DVC `.dir` interop** (`chiro dvc-dir`) projects entries to `{md5, relpath}` and drops
  every extension field, `crc32` included; containers are not entries and do not appear.

## 10. Integration points (archivefs)

chirograph provides the representation and the on-read validation; the *use* is
archivefs's:

- **`confirm`** (download-free): for each container, fetch its central directory by a tail
  read, authenticate the container by `md5`, then check each member's `(in-zip path,
  size, crc32)` against the manifest — member-granular, tamper-localising, no payload
  download.
- **`fetch_plan`** gains, for a container member, the two-level fetch it implies: fetch the
  container (`source_url` + `source_root`), extract the in-zip path, place at `relpath`.
  Loose files are unchanged.
- **Prepare-before-deposit** (use case 1): a depositor with local files builds an
  authoritative manifest — a `chiro`-side zip-aware writer that reads a local `.zip`'s
  central directory to emit members with `crc32` + the container descriptor is a natural
  follow-up, but is a separate feature; this note is about the representation so archivefs
  can emit valid manifests today.

## 11. Rejected alternatives (for the record)

- **Container as a `FileEntry`** — a container is file-*like* (bytes, md5, size, source)
  but not a *workspace* file; giving it a `relpath` (which names workspace placement) is a
  category error and forces `verify`/`nfiles` to special-case it. A distinct `containers`
  block models it honestly.
- **Inline per-member `container_md5`** — forward-compatible (a `1.x` minor) and keeps the
  container md5 in `body_hash`, but denormalises the container descriptor across every
  member and cannot carry richer container facts cleanly. Acceptable, but a worse
  reflection of reality; the multi-ZIP Zenodo shape (several containers, each with md5 +
  source + size) is what tips it to a block.
- **A member/element count** on the container — circular or md5-redundant; see §3.

## 12. Implementation surfaces

Same shape and discipline as the `1.1` work:

- **Model** (`manifest.py`): a `Container` dataclass (`source_root`, `md5`, `size`,
  `etag`, `version_id`); `Manifest.containers: list[Container]`; `FileEntry.crc32`;
  `to_dict`/`from_dict` for both; version derivation extended to the `2.0` tier;
  `body_hash` property takes the two-key path when containers are present.
- **Constants**: `SUPPORTED_MAJOR → 2`; `SCHEMA_VERSION_MAJOR_2 = "2.0"`; a
  `CONTAINER_FIELD_ORDER`; `crc32` appended to `ENTRY_FIELD_ORDER`.
- **Canonicalisation** (`canonical.py`): the single new two-key branch of §4; the
  container array reuses the existing per-element normalise/scalar/sort machinery.
- **Schema** (`gen_schema.py` + regen): a `containers` array (with its descriptor and
  `additionalProperties: false`), and `crc32` on the entry.
- **Vectors**: *added*, not edited — a container-bearing body case (pinning the two-key
  canonical string and that container fields enter `body_hash`), plus a valid `2.0`
  whole-manifest and the linkage/validation reject cases. `spec_version → 2.0`. Every
  existing hash proven unchanged; the coreutils cross-check kept green.
- **Spec**: the `containers` block and `crc32` become normative in
  [`chiro-format-spec.md`](chiro-format-spec.md); the §4 rule gains the two-key case; the
  major-version row notes `2.0`.
- **Validation** (`schema.py`/`from_dict`): the §8 rules.
