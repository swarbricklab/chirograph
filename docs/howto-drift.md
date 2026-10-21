# How to detect that upstream data has changed

> **Status: mostly real.** `chiro capture`, `chiro diff` and `chiro verify` do the work
> today. The cheap staleness checks in §3 use the `1.1` fields, which are now a **shipped
> part of the format**, not a proposal ([`chiro-format-spec.md`](chiro-format-spec.md)
> §8); and the archive-specific side ships in archivefs — `archivefs update` re-resolves
> a manifest's `concept` against the live archive and reports what moved, `archivefs
> check` compares a deposit to its manifest. What remains deferred is only the
> network-registry automation layered on top.

**The situation:** you depend on data you do not control. Some archives version their
records; GEO famously does not, so a stable accession can mutate in place. You want to
know when that happens.

**Two different questions, depending on where you are:**

- Mid-project: *is there newer reference data, and should I update?*
- Post-publication: *has the data behind my paper been altered?*

Same check. Opposite correct response — update in the first case, report in the second.

---

## 1. The check that always works

Re-observe, and diff against what you recorded:

```bash
# Re-fetch the upstream data to a scratch directory, then:
chiro capture /scratch/$PROJECT/$USER/recheck \
    --source-url 'geo://GSE111111' \
    --accession GSE111111 \
    -o "$SCRATCH/tmp.chiro.json"

chiro diff .chiro/manifests/<digest>.chiro.json "$SCRATCH/tmp.chiro.json"
```

`chiro diff --exit-code` makes this a CI gate: non-zero when the observations differ.

This is definitive — it compares actual digests — and it is expensive, because it means
downloading everything. Everything else in this document is an attempt to avoid paying
that cost when nothing has changed.

If the data is already on disk, the cheaper equivalent is simply:

```bash
chiro verify .chiro/manifests/<digest>.chiro.json --sample 100
```

though that tells you your *copy* is intact, not that the upstream still agrees with it.

---

## 2. The rule that governs everything below

> **A changed signal proves change. An unchanged signal proves nothing.**

Every cheap check works by comparing some value the archive reports — a version, a
date, an ETag — against what you recorded. If that value has changed, the data has
changed: useful, and cheap. If it has not changed, you have learned almost nothing,
because an archive can replace a file without updating anything observable. GEO does
exactly this.

The one exception is an archive that guarantees its identifiers are immutable. There,
"same version" *is* proof, and that is worth a lot.

Never let an unchanged signal skip verification in a context where correctness matters.
This is the same discipline that keeps the `etag` field non-authoritative
([`chiro-format-rationale.md`](chiro-format-rationale.md) §7).

---

## 3. Cheap checks, by what the archive supports

> Partly built: the `concept`, `record_version` and `file_version` fields exist as of
> `schema_version` 1.1 (spec §8) — `chiro write`/`capture` accept `--concept` and
> `--record-version`. The *cheap query* that reads them back from a live archive is
> archivefs's `archivefs check` (still to build), so the workflow below is only as
> automated as that half.

### Archives with immutable versions — Zenodo, Mendeley Data, cBioPortal, Dataverse, Figshare, versioned S3

The same concept/version split appears across archives that version their records.
**Mendeley Data** has a concept DOI and per-version DOIs (`10.17632/<id>.<N>`).
**cBioPortal** is versioned by its datahub's git history, so the concept is the study
(`cbioportal:brca_metabric`) and the pinned version is a commit — `record_accession`
is `brca_metabric@<commit sha>`. These are **sha256** manifests, but the drift logic is
identical: record the version you observed plus the concept to re-resolve, then ask the
archive whether the concept still points there.

Zenodo distinguishes a *concept* DOI (always the latest) from a *version* DOI
(immutable). The manifest records the version it observed, plus the concept to
re-resolve:

```json
{
  "record_accession": "10.5281/zenodo.7654321",
  "concept": "doi:10.5281/zenodo.7654320",
  "provenance_type": "observed-download",
  "retrieved": "2026-08-22T04:11:00Z"
}
```

The check is one API call to the concept DOI:

- Latest version equals what you recorded → **definitively up to date. No download.**
- Latest version differs → a new version exists; fetch its manifest.

This is the good case, and it costs one HTTP request.

### Archives with no versions — GEO, SRA, ArrayExpress, BIA

There is nothing immutable to compare, so you compare change signals instead, in two
stages.

**Stage 1, record level.** One request for the record's last-update date:

```json
{ "record_accession": "GSE111111", "record_version": "2026-07-30" }
```

Unchanged → stop. This is the cheap negative, and it is where nearly all the savings
are, because most records don't change most of the time.

Two caveats. GEO's series last-update date has **day granularity**, so two edits on one
day are invisible to it. And it is a *claim* by GEO that the date is bumped whenever
anything changes — a legitimate cost optimisation, never a substitute for verification.

**Stage 2, file level.** If the record-level date moved, fetch the directory listing —
one request — and compare per-file dates and sizes:

```json
{ "relpath": "GSE111111_processed.h5ad", "md5": "dd44…", "size": 512900021,
  "file_version": "2026-07-30T09:02:00Z" }
```

Now you know *which* files moved, so you re-download only those. On a 200-file series
where one sample was corrected, that is one file instead of the whole record.

Per-file dates are also unreliable in the same way: mirrors reset them, they can go
backwards. Compare for inequality, never for ordering.

### Archives that offer nothing

Some sources expose no version and no usable date. Say so rather than implying
freshness: the honest report is *"no change detected (no upstream signal available)"*,
not *"up to date"*. The only real answer is §1's re-observe-and-diff.

---

## 4. Responding to detected drift

### Mid-project

Decide whether to move. Nothing forces you to.

```bash
chiro diff .chiro/manifests/<digest>.chiro.json "$SCRATCH/tmp.chiro.json"
```

If the change is irrelevant to your analysis, record the observation and keep using the
old data — but make sure the committed manifest still describes the data your results
came from. If you adopt the new data, commit the new manifest and rerun what depends on
it.

**Keep both observations.** Two dated observations of one accession are a drift
record, and it is the only evidence that the record changed. Digest-based filenames
(spec §1.1) make this automatic: two observations differ in content, so they get
different names and coexist. This is the main reason the convention exists — under
accession-derived naming the second capture silently overwrites the first, destroying
exactly the record you wanted.

### Post-publication

Do not update anything. The published manifest is the record of what the paper used,
and it stays as it is.

What you do is report: the archive changed a record after publication, and here is
exactly what changed.

```bash
chiro diff published/<digest>.chiro.json today/<digest>.chiro.json --json
```

This is the case that motivated the whole format. A GEO record can be silently
rewritten under a stable accession, and without a manifest there is no way to
demonstrate — to a reader, a reviewer, or yourself — that the data behind a figure is
not the data now sitting at that accession.

---

## 5. Running it on a schedule

The check is cheap enough to automate and the exit codes are stable, so:

```bash
#!/usr/bin/env bash
# Re-observe every observed-download manifest; report drift, change nothing.
set -uo pipefail
status=0
for m in .chiro/manifests/*.chiro.json; do
    [[ $(chiro info --json "$m" | jq -r .provenance_type) == observed-download ]] || continue
    # ... re-fetch to $tmp per the manifest's source_url ...
    chiro diff "$m" "$tmp/tmp.chiro.json" --exit-code || status=1
done
exit $status
```

The loop above is the manual form, built from the real primitives `chiro capture`,
`chiro diff` and `chiro verify`. The *packaged* form of this — the cheap record-version
check that avoids re-fetching, and the deliberate supersede-and-repoint that also
refreshes the `.dvc` — lives in the sibling `archivefs` project as `archivefs check`
and `archivefs update`, because both need archive-specific network that chirograph
deliberately does not do. See
[`design-check-and-update.md`](design-check-and-update.md).

Two design notes for whoever automates this properly:

- **Report, never auto-update.** An automation that silently adopts new upstream data
  destroys the drift record it exists to produce.
- **Bound the cost.** For large records, `chiro verify --sample N` on a schedule plus a
  full check occasionally is a reasonable trade. Say in the report what was sampled —
  a bounded check reported as a complete one is worse than no check.
