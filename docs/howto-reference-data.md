# How to include public reference data in a project

> **Status: partly aspirational.** The `chiro` commands shown are real and run today.
> The `dvc`/`archivefs` integration is described in the two-hop form that archivefs
> actually implements; see [`resolution-and-config.md`](resolution-and-config.md) §2
> for the three-hop model this is intended to become.

**The situation:** your analysis needs data you did not generate — a GENCODE
annotation, a reference genome, a public GEO series you are re-analysing. You want the
paths in your code to be sane, and you want to be able to prove later exactly which
bytes you used.

**What you get:** a manifest that records what was at that URL when you fetched it,
committed to your repo, so that any later copy can be checked against it and any
upstream change detected.

---

## The short version

```bash
# 1. Fetch the data however you normally would.
curl -O https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_44/gencode.v44.annotation.gtf.gz

# 2. Record what you got, named after its own digest.
chiro capture ./data/reference/gencode-v44 \
    --source-url 'https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_44/' \
    --accession gencode-v44 \
    --name-by-digest -o .chiro/manifests

# 3. Commit it.
git -C .chiro/manifests add . && git commit -m "Pin GENCODE v44"
```

That's the whole thing. The manifest is a few kilobytes of text and it is now the
record of what you used.

Two details in there that matter:

**The manifest is named after the digest of its own content** (spec §1.1). `--name-by-digest`
writes `<digest>.chiro.json` into the directory you give `-o`, where the digest is the
file's own whole-file hash — so the filename *equals* the `chiro://` reference and is
checkable with `md5sum` alone. It also means a later re-observation of the same data
coexists with this one instead of overwriting it, which is what makes drift detection
possible. See [`chiro-format-rationale.md`](chiro-format-rationale.md) §13.

**GENCODE is addressed by its plain URL** because no archivefs backend exists for the
EBI FTP site. Where a backend does exist, use its scheme instead — see below.

---

## Why `capture` and not `write`

`chiro capture` is `chiro write --provenance observed-download` with two fields made
mandatory rather than optional: `--source-url`, and `--retrieved` (which defaults to
now).

Those two fields are what make the manifest mean anything. "These bytes" is not a
useful claim about reference data; "these bytes, from this URL, on this date" is. If
the upstream later changes, the second form tells a reader *when* your observation was
taken and *where* to go to see whether it still holds. The first form tells them
nothing they can act on.

Using `write` here would produce a `self-deposited` manifest, which claims something
false: that you are the authoritative source for GENCODE.

---

## Getting the paths right

This is the part worth spending a minute on, because it is the part you cannot easily
change later.

`relpath` is where the file lands in your workspace — the path your code will contain.
`source_path` is where the bytes come from at the source. When they are the same,
`source_path` is omitted and nothing more is needed.

They are often not the same. Reference data tends to arrive with names shaped by the
provider's release process, not by your analysis:

```bash
chiro capture ./deposit \
    --source-url 'geo://GSE111111' \
    --accession GSE111111 \
    --relpath-from-source '^GSE111111_([^_]+)_(.*)$=>\1/\2' \
    --name-by-digest -o .chiro/manifests
# wrote .chiro/manifests/<digest>.chiro.json
```

Note the source URL: `geo://GSE111111`, not the FTP path. The archive scheme is what
`archivefs` resolves, so the manifest survives NCBI reorganising its directory layout
— and GEO's `GSE111nnn` path sharding is exactly the sort of thing that changes.
Available schemes are `geo://`, `zenodo://`, `ega://`, `bia://` and `dbgap://`.

A resolved fetch location looks like this, which you can check before fetching
anything:

```console
$ chiro ls --urls .chiro/manifests/*.chiro.json
geo://GSE111111/GSE111111_sampleA_matrix.mtx.gz
geo://GSE111111/GSE111111_sampleA_barcodes.tsv.gz
```

The walked tree is the *source* layout, so each walked path becomes `source_path`, and
the rule derives `relpath` from it. Here a flat deposit of
`GSE111111_sampleA_matrix.mtx.gz` becomes a workspace tree of
`sampleA/matrix.mtx.gz`.

Check the layout before committing:

```bash
chiro ls .chiro/manifests/*.chiro.json
```

Two reasons to care:

- **The layout is part of the record.** `relpath` is inside `body_hash`, so the
  manifest attests which bytes *and where they go*. Changing your mind later changes
  the manifest's digest.
- **This is the step that removes the undocumented rename.** Without it, every
  published pipeline grows a shell script that moves files around, which nobody
  mentions in the methods and everybody has to reverse-engineer.

---

## Verifying what you fetched

```bash
chiro verify .chiro/manifests/*.chiro.json --root ./data/reference/gencode-v44
```

Exit code `0` means the bytes match. `1` means a finding. `2` means the tool could not
run. That contract is stable, so this works as a CI step.

If the data sits in the *source* layout rather than the workspace layout — which it
does immediately after download, before you rearrange anything — add
`--source-layout`.

---

## Recording a large reference without re-hashing it

If the data is already tracked by DVC, do not hash it again. See
[`howto-adopt-dvc.md`](howto-adopt-dvc.md) — `chiro write --from-dvc-cache` transcribes
the digests DVC already computed.

---

## What this does not do

It does not tell you whether GENCODE v44 is still the current release, and it does not
notice when a GEO record mutates under you. That is a separate operation, on your
schedule rather than automatic: [`howto-drift.md`](howto-drift.md).

It also does not make the reference data available to someone who clones your repo —
the manifest describes the data, it doesn't contain it. See
[`howto-pull-and-verify.md`](howto-pull-and-verify.md).

---

## Where the manifest should live

`.chiro/manifests/` in the repo, committed.

Not in a registry, or at least not *only* in a registry. A registry is a convenience
for finding manifests you don't already have. It should never be a runtime dependency
of an analysis: compute nodes often have no outbound network, and a registry may not
outlive the paper. A committed manifest works offline, forever, and is covered by the
same git history as the code that used it.

The relevant reasoning is in [`resolution-and-config.md`](resolution-and-config.md)
§8.
