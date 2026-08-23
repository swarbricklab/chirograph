# How to publish data and code together

> **Status: partly aspirational.** The `chiro` commands are real. The publication lint
> (`chiro freeze`) does not exist yet — §5 describes what it should check, and the
> shell equivalents work today.

**The situation:** a paper is going out. You need to ship code and data such that a
reader can reproduce the analysis, and such that you can later prove which data the
figures came from.

**The two claims you are making:**

1. *"This is the data we generated."* — your deposits.
2. *"This is the reference data we used."* — everything you did not generate.

They are not symmetric, and the format distinguishes them. Claim 1 is authoritative:
`self-deposited`, digests computed from your own originals. Claim 2 cannot be
authoritative, because you do not control the source — it is `observed-download`, and
the claim is "this is what was at that URL on that date". Getting a reader to
understand which is which is most of the value.

---

## 1. Publication is a freeze

This is the governing principle, and it decides everything else.

During development you want to track upstream: is there a newer GENCODE, has the GEO
record changed. At publication you want the opposite — **every reference pinned, and
nothing that can move.** A published analysis that silently resolves to newer reference
data next year is not reproducible, however convenient the tracking was while you
worked.

So publication is a *lowering* operation: rewrite every mutable reference into the
immutable thing it currently resolves to, and check that none are left.

---

## 2. What the published repository contains

```
paper-repo/
  .dvc/
  data/
    generated/xenium-panel.dvc          # -> chiro://md5:aaa…
    reference/gencode-v44.dvc           # -> chiro://md5:bbb…
    reference/GSE111111.dvc             # -> chiro://md5:ccc…
  .chiro/manifests/
    aaa….chiro.json                     # self-deposited   — "data we generated"
    bbb….chiro.json                     # observed-download — "reference data we used"
    ccc….chiro.json                     # observed-download
  code/
  README.md
```

Three things about this are deliberate.

**The manifests are committed, not merely registered.** A registry is a convenience for
finding manifests you do not have. It must not be a runtime dependency of a published
analysis: registries can go away, and compute nodes often have no network. A manifest is
a few kilobytes; the repository will be archived by the journal and the registry will
not.

**Git ties the set together.** There is no top-level manifest-of-manifests, because a
commit's tree hash is already a content-addressed digest over exactly that set of
files — and it covers the code as well. Adding a Chirograph artifact to do the same job
would attest the manifests (kilobytes of text under version control, the least at-risk
thing here) while saying nothing about the data.

**The `.dvc` files bind paths-in-code to paths-in-archive.** That binding is part of the
reproducibility claim, not a convenience: `relpath` is inside `body_hash`, so if a
reader materialises a different layout, verification fails — correctly, because the code
would not have run against it.

---

## 3. Preparing the deposited data

Follow [`howto-deposit-data.md`](howto-deposit-data.md), and do not skip step 4.
Verifying the archive's copy against the manifest you wrote from your workspace is the
step that turns "we deposited some files" into "we deposited the files the figures came
from".

The ordering matters and is easy to invert: **write the manifest from your local
originals, then verify the deposit against it.** Generating the manifest from the
deposited copy attests whatever the archive did to your files.

---

## 4. Pinning the reference data

Every `observed-download` manifest should already exist from
[`howto-reference-data.md`](howto-reference-data.md). What publication adds is a
decision, per dataset: is this still the version the paper used?

```bash
# For each reference dataset, confirm the committed manifest still matches your working copy.
for m in .chiro/manifests/*.chiro.json; do chiro verify "$m" || echo "DRIFT: $m"; done
```

If something drifted mid-project and you re-fetched, make sure the committed manifest
describes the data the figures were actually made from — not the newest observation.
This is the most common way a publication record goes subtly wrong: the analysis ran on
the March data and the manifest records the August re-fetch.

`chiro diff` between the two observations tells you whether it matters.

---

## 5. The freeze check

Before submission, assert that nothing in the repository can move. A
`chiro freeze --check` should verify all of the following; until it exists, the shell
equivalents work.

**Every manifest is internally intact** — reads no data, so this is free:

```bash
for m in .chiro/manifests/*.chiro.json; do chiro validate "$m" || exit 1; done
```

**Every dataset matches its manifest:**

```bash
for m in .chiro/manifests/*.chiro.json; do chiro verify "$m" || exit 1; done
```

**No mutable references remain.** Every dependency must be content-addressed. A
`chiro-concept:` reference, a bare archive URL standing in for a version, or a "latest"
of any kind is a reproducibility hole:

```bash
grep -rn 'chiro-concept:\|latest' --include='*.dvc' . && echo "MUTABLE REFERENCE FOUND"
```

**Every `observed-download` manifest has `source_url` and `retrieved`.** Without them
the manifest records bytes but not the observation, which is the part a reader needs.
`chiro validate` reports these as findings.

**Every manifest is committed and clean:**

```bash
git status --porcelain .chiro/manifests/     # must be empty
```

---

## 6. What to write in the data availability statement

State the two claims separately, because they carry different guarantees.

> Data generated in this study are deposited under accession GSE000000. Manifests
> recording the per-file checksums of all deposited and reference data, together with
> the analysis code, are available at `<repo DOI>`. Reference datasets are recorded as
> observed on the dates given in their manifests; checksums permit verification that a
> retrieved copy matches the data analysed here.

Two things to avoid claiming.

**Do not claim the reference data is immutable.** It isn't; you recorded an observation
of it. The honest and more useful claim is that a reader can *detect* whether it has
changed.

**Do not claim the manifests prove authorship.** Chirograph provides integrity and
drift detection, not authenticity — nothing in a manifest proves who wrote it. The
authenticity comes from the accession being under your control and the repository
carrying your commit history. See
[`chiro-format-rationale.md`](chiro-format-rationale.md) §14.

---

## 7. After publication, the check inverts

The same machinery keeps working, with the opposite intent.

Before publication, a drift check asked *"is there newer reference data, should I
update?"* After publication it asks *"has the data behind this paper been altered?"* —
and updating is now the wrong response. Reporting is the right one.

This is where Chirograph is worth the most. For a GEO record that mutates under a
stable accession after you publish, your committed manifest is the only evidence that
anything changed. Keep the check running: [`howto-drift.md`](howto-drift.md).
