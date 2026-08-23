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

# 2. Record what you got.
mkdir -p .chiro/manifests
chiro capture ./data/reference/gencode-v44 \
    --source-url 'https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_44/' \
    --accession gencode-v44 \
    -o .chiro/manifests/gencode-v44.chiro.json

# 3. Commit it.
git add .chiro/manifests/gencode-v44.chiro.json && git commit -m "Pin GENCODE v44"
```

That's the whole thing. The manifest is a few kilobytes of text and it is now the
record of what you used.

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
    --source-url 'https://ftp.ncbi.nlm.nih.gov/geo/series/GSE111nnn/GSE111111/suppl/' \
    --accession GSE111111 \
    --relpath-from-source '^GSE111111_([^_]+)_(.*)$=>\1/\2' \
    -o .chiro/manifests/GSE111111.chiro.json
```

The walked tree is the *source* layout, so each walked path becomes `source_path`, and
the rule derives `relpath` from it. Here a flat deposit of
`GSE111111_sampleA_matrix.mtx.gz` becomes a workspace tree of
`sampleA/matrix.mtx.gz`.

Check the result before committing:

```bash
chiro ls .chiro/manifests/GSE111111.chiro.json
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
chiro verify .chiro/manifests/gencode-v44.chiro.json --root ./data/reference/gencode-v44
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
