# How to include data you are depositing

> **Status: real.** Every command here runs today against the shipped CLI.

**The situation:** you generated data, you are depositing it to GEO / EGA / BIA /
Zenodo / Mendeley Data, and you want to be able to prove that the bytes in the archive
are the bytes your analysis used.

**What you get:** a `self-deposited` manifest that is the authoritative record of your
own data — and a check that the archive stored what you actually sent it.

---

## The ordering matters, and it is easy to get backwards

**Write the manifest from your local files. Then verify the deposit against it. Never
generate the manifest from the deposited copy.**

Archives modify what you upload. They recompress, they rename, they translate line
endings, they unpack or repack archives. If you generate the manifest *from* the
deposited copy, you attest whatever the archive did — which silently downgrades your
claim from "this is the data I analysed" to "this is what GEO currently has". Those
are different statements, and only the first is worth making.

Writing from source and verifying against the deposit turns any mangling into a test
failure, while the deposit is still yours to fix.

---

## Step 1 — write the manifest from your workspace

```bash
chiro write ./data/generated/xenium-panel \
    --accession GSE000000 \
    --source-url 'geo://GSE000000' \
    -o .chiro/manifests
# wrote .chiro/manifests/<digest>.chiro.json
```

With `-o` naming a directory (not a `.json` file), the manifest is written as
`<digest>.chiro.json` — named after its own whole-file hash (spec §1.1), the default —
more on why in Step 3.

Use the archive scheme (`geo://GSE000000`), not the underlying FTP path. The scheme is
what `archivefs` resolves, so the manifest stays valid if NCBI reorganises its
directory layout — and `GSE000nnn`-style path sharding is exactly the sort of detail
that changes. The available schemes are `geo://`, `zenodo://`, `ega://`, `bia://` and
`dbgap://`; for a source with no backend (a plain FTP release, say) use its URL
directly.

`chiro write` defaults to `--provenance self-deposited`, which is correct here: you are
the authoritative source, the digests were computed from your own originals, and there
is no `retrieved` date because nothing was retrieved from anywhere.

`--source-url` records where the data is *going*. It is optional for a self-deposited
manifest but worth setting, because it is what lets someone else fetch the data later.

---

## Step 2 — describe the deposit layout, if it differs

Archives usually impose a flat namespace. Your workspace usually isn't flat. The
manifest bridges the two, so your code keeps the paths it already has.

Going *from* a workspace tree *to* a flat deposit is `--source-from-relpath`:

```bash
chiro write ./data/generated/xenium-panel \
    --accession GSE000000 \
    --source-url 'geo://GSE000000' \
    --source-from-relpath '^([^/]+)/(.*)$=>GSE000000_\1_\2' \
    -o .chiro/manifests
```

That turns a workspace path `sampleA/matrix.mtx.gz` into a deposit name
`GSE000000_sampleA_matrix.mtx.gz`. The manifest now records both, so the fetch
knows where to look and the materialiser knows where to put it.

(The inverse — you already have the flat deposit and want a tidy workspace — is
`--relpath-from-source`. See [`howto-reference-data.md`](howto-reference-data.md).)

Check it before you upload anything:

```bash
chiro ls .chiro/manifests/*.chiro.json
```

`chiro ls` shows both paths per entry, which is the only way to catch a regex that did
something almost right.

---

## Step 3 — the digest name

Steps 1–2 named the file after the hash of its own content (the default when `-o` is a
directory), so the filename *equals* its `chiro://` reference and is checkable with no
tooling:

```bash
md5sum .chiro/manifests/*.chiro.json
# 22fe1ab2c4a307849fad2cc98eea37dd  .chiro/manifests/22fe1ab2c4a307849fad2cc98eea37dd.chiro.json
```

From here the file is **immutable**. Reformatting or editing it invalidates its own
name, and any `chiro://` reference to it. If something needs changing, regenerate
rather than editing in place — a fresh write gets a fresh name.

Two reasons this matters more than the loss of a legible filename. A second observation
of the same accession gets a *different* digest, so it coexists with the first instead
of overwriting it — which is the whole basis of drift detection. And the manifest
verifies itself: no Chirograph install required, now or in ten years. The full argument
is [`chiro-format-rationale.md`](chiro-format-rationale.md) §13.

---

## Step 4 — deposit the data, and the manifest alongside it

Upload the files. Then upload the manifest too, if the archive allows arbitrary files.

Depositing the manifest is a courtesy to consumers who will never use Chirograph: it
gives them per-file checksums the archive itself may not expose — and for GEO, which
publishes none at all, it is the only per-file checksum a consumer can get.

**This is the one case where an accession-derived name is better.** A deposit is
browsed by humans who cannot read a digest, and the manifest may be downloaded into a
directory alongside other records' files. So deposit the copy as
`GSE000000.chiro.json` even though your repository holds it under its digest name.
The two files are byte-identical; only the label differs, and `chiro ref` on either
gives the same reference.

---

## Step 5 — check that the deposit matches your workspace

This is the step that makes the whole exercise worth doing.

Download the deposit back — the real files, from the real archive, the way a stranger
would — and verify them against the manifest you wrote in steps 1-2:

```bash
# Fetch the deposit into a scratch directory
mkdir -p /scratch/$PROJECT/$USER/deposit-check
# ... download GSE000000's supplementary files there ...

chiro verify .chiro/manifests/*.chiro.json \
    --root /scratch/$PROJECT/$USER/deposit-check \
    --source-layout \
    --strict
```

`--source-layout` is essential here: the downloaded deposit is in the *source* layout
(flat, `GSE000000_sampleA_*`), not the workspace layout. Without it, `verify` looks for
workspace paths that don't exist in a deposit.

`--strict` additionally fails on files present in the deposit that the manifest doesn't
list — which catches a half-finished upload, or the archive helpfully adding something.

### Reading the result

| Outcome | Meaning | What to do |
|---|---|---|
| exit `0` | The archive stored exactly what you sent. | Done. Commit the manifest. |
| digest mismatch on some files | The archive modified those files. | Investigate: recompression and line-ending translation are the usual causes. Fix the **deposit**, not the manifest. |
| files missing | Upload incomplete, or the archive renamed them. | Check `source_path` against what's actually there. |
| extra files (with `--strict`) | Something is in the deposit that shouldn't be. | Usually a stray upload or an archive-generated index file. |

**In every failing case, the manifest is the thing that is right.** It records what you
analysed. Do not regenerate it to make the check pass — that is exactly the inversion
this document opens by warning about.

---

## Step 6 — commit

```bash
git add .chiro/manifests/
git commit -m "Add manifest for GSE000000 deposit, verified against archive"
```

If the data is DVC-tracked in your repo, DVC records the digest of the whole manifest
file in a `.dvc`, which is Layer 3 of the hashing model — the manifest itself becomes
tamper-evident. See [`chiro-format-spec.md`](chiro-format-spec.md) §4.

---

## A note on what you are attesting

A `self-deposited` manifest claims: *these are the bytes I published, hashed against my
own originals.* That is the strongest claim in the format, and it is the one reviewers
and re-users care about.

It does not claim that you are who you say you are. Nothing in the format proves
authorship — that comes from the accession being under your control and the manifest
being in a repository with your commit history, not from the manifest itself. See
[`chiro-format-rationale.md`](chiro-format-rationale.md) §14.

---

## Next

- Publishing this alongside a paper: [`howto-publish.md`](howto-publish.md)
- Checking, years later, that the archive hasn't altered your record:
  [`howto-drift.md`](howto-drift.md)
