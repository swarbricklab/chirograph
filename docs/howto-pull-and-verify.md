# How to pull tracked data, and verify it is what you expect

> **Status: mixed.** `chiro` commands are real. `archivefs` commands shown
> (`get`, `check`, `verify`, `dvc`) exist in the shipped CLI. The `dvc pull` path via a
> `chiro://` dependency is **aspirational** — archivefs currently registers the
> `geo`, `zenodo`, `ega`, `bia` and `dbgap` protocols, not `chiro`. See
> [`resolution-and-config.md`](resolution-and-config.md) §2.

**The situation:** you have a repository — yours, a colleague's, or one attached to a
paper — that tracks data with DVC and Chirograph. You want the data on disk, at the
paths the code expects, and you want to know that it is the right data.

---

## For the impatient

```bash
git clone <repo> && cd <repo>
dvc pull                                   # fetch the data
for m in .chiro/manifests/*.chiro.json; do chiro verify "$m" || exit 1; done
```

Exit `0` from that loop means every tracked dataset matches its manifest. That is the
whole check.

---

## What actually happens on pull

Three hops, whether or not you can see them:

1. **The reference resolves to a manifest.** A `.dvc` names either a `chiro://`
   reference or an archive URL; either way a manifest is located and, if referenced by
   digest, checked against that digest before it is parsed.
2. **The manifest says where the bytes are.** `source_url` plus each entry's
   `source_path` gives a fetch location. This is the step that means you do not need to
   know which archive the data came from.
3. **The bytes are fetched and placed.** Each file is written to its `relpath` — the
   workspace path your code contains — not to the flat name the archive uses.

Step 3 is why the paths in the code work. The manifest is a routing table between the
deposit layout and the layout the analysis was written against.

---

## Pulling without DVC

The manifest is self-sufficient. If you have it, you can fetch the data with nothing
else:

```bash
archivefs get .chiro/manifests/GSE111111.chiro.json --dest ./data/reference/GSE111111
```

`archivefs get` downloads and verifies against the manifest's digests as it goes. For
credentialed archives (EGA, dbGaP) it will need credentials configured; `archivefs
info` reports what is wired up.

To see what a manifest expects before fetching anything:

```bash
chiro ls   .chiro/manifests/GSE111111.chiro.json     # both paths, per entry
chiro info .chiro/manifests/GSE111111.chiro.json     # header, totals, provenance
```

---

## Verifying

Three levels, cheapest first. All three use the same exit-code contract: `0` success,
`1` a finding, `2` the tool could not run.

```bash
# Reads no data at all. Checks the manifest's own integrity (Layer 2).
chiro validate .chiro/manifests/GSE111111.chiro.json

# Bounded cost: hash a deterministic sample of files.
chiro verify .chiro/manifests/GSE111111.chiro.json --sample 100

# Full: hash every byte.
chiro verify .chiro/manifests/GSE111111.chiro.json
```

`chiro validate` is worth knowing about. It confirms that the file list has not been
truncated or corrupted and that the manifest is internally consistent, without
touching the data — so it costs nothing and can run on every commit. It cannot tell you
the *data* is right; only that the manifest is intact.

Useful flags on `verify`:

| Flag | Use |
|---|---|
| `--root DIR` | Where the data is, if not next to the manifest |
| `--source-layout` | Data is in the deposit layout, not the workspace layout |
| `--sample N` / `--sample-fraction F` | Bounded-cost spot check, deterministic |
| `--strict` | Also fail on files present but not listed |
| `--layer 2` | Manifest integrity only; reads no data |
| `--json` | Machine-readable report |

---

## When verification fails

The exit code tells you *that* something is wrong. The report tells you *what*. The
useful question is which of four quite different things happened.

### Read `provenance_type` first

It changes the interpretation completely.

- **`self-deposited`** — the manifest is authoritative. A mismatch means your copy is
  wrong: corruption, an incomplete transfer, or the wrong file.
- **`observed-download`** — the manifest records one observation of something you don't
  control. A mismatch is *more likely* to mean the upstream changed than that you are
  corrupt. `chiro verify` reports it in those terms rather than calling it corruption.

### The four cases

**1. A few files mismatch, sizes differ.** Usually an incomplete or interrupted
transfer. Re-fetch just those files. `chiro verify --json` gives you the list.

**2. A few files mismatch, sizes identical.** More interesting. Identical size with a
different digest suggests content substitution rather than truncation — a
recompression, a line-ending translation, or an upstream file replaced in place. For an
`observed-download` manifest, suspect upstream drift and go to
[`howto-drift.md`](howto-drift.md).

**3. Files missing.** Check whether you needed `--source-layout`. The commonest cause
of "everything is missing" is verifying a freshly-downloaded deposit against workspace
paths, or vice versa.

**4. `body_hash` mismatch (Layer 2 fails).** The manifest itself is damaged or was
edited. No amount of re-fetching data will help. Get a clean copy of the manifest —
from git, or by its `chiro://` reference. Note that re-indenting or re-saving a manifest
changes its whole-file digest even when `body_hash` still matches, which is why
manifests should be treated as immutable artifacts rather than edited.

### Diagnosing without the data

```bash
chiro verify manifest.chiro.json --layer 2     # is the manifest itself intact?
chiro info   manifest.chiro.json               # what does it claim, and how was it made?
chiro diff   old.chiro.json new.chiro.json     # what changed between two observations?
```

---

## If you are reviewing or reproducing someone else's work

You have a repository and no context. What you want to establish, in order:

**1. Does the repository's own record hold together?** Costs nothing, needs no data:

```bash
for m in .chiro/manifests/*.chiro.json; do chiro validate "$m" || echo "BAD: $m"; done
```

**2. What does it claim to depend on, and of what kind?**

```bash
for m in .chiro/manifests/*.chiro.json; do chiro info "$m"; done
```

Look at `provenance_type` for each. `self-deposited` manifests are the authors' own
data. `observed-download` manifests are third-party reference data, with a `retrieved`
date — that date is when the authors looked, and the upstream may have changed since.
This is the distinction that tells you which parts of the analysis depend on things
outside the authors' control.

**3. Fetch and check.**

```bash
dvc pull    # or: archivefs get <manifest> --dest <path>
for m in .chiro/manifests/*.chiro.json; do chiro verify "$m" || exit 1; done
```

**4. If a third-party dataset fails to verify, that is a finding about the archive, not
about the paper.** An `observed-download` mismatch means the upstream record changed
after the authors used it. The manifest is the evidence of what they used, and it is
the only such evidence that exists. `chiro capture` the current state and
`chiro diff` it against the committed manifest to see exactly what moved.

---

## Next

- The upstream changed and you want to decide what to do:
  [`howto-drift.md`](howto-drift.md)
- You are preparing your own repository for publication:
  [`howto-publish.md`](howto-publish.md)
