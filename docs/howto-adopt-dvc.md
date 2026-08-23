# How to adopt Chirograph in a repository that already uses DVC

> **Status: real.** Every command here runs today. DVC interop is tested against the
> actual `dvc` CLI rather than a hand-built fixture.

**The situation:** you already track data with DVC. The digests exist. You want
Chirograph manifests without re-reading terabytes.

**What it costs:** one `stat` per file. No re-hashing.

---

## The short version

```bash
chiro write --from-dvc-cache data/reference.dvc -o .chiro/manifests/tmp.chiro.json
D=$(chiro ref .chiro/manifests/tmp.chiro.json | sed 's|^chiro://[a-z0-9]*:||')
mv .chiro/manifests/tmp.chiro.json ".chiro/manifests/$D.chiro.json"
```

Manifests are named after the digest of their own content (spec §1.1), which is why
the name is assigned after writing. DVC has already computed the md5 of every file. `--from-dvc-cache` transcribes them.
The only thing it has to look up is file *sizes*, because a DVC `.dir` object does not
record them — it `stat`s the content-addressed cache object, falling back to the
workspace copy.

---

## Why this is nearly free

DVC's `.dir` object is an array of `{relpath, md5}` entries, sorted by `relpath`, POSIX
separators. Chirograph's body is the same shape plus `size`. Field names are DVC's
deliberately, so this really is transcription rather than translation.

That shared shape is also why the reverse direction works: a manifest body with no
extension fields is valid input to DVC's own `Tree.from_list`.

---

## What to expect

```bash
chiro write --from-dvc-cache data/reference.dvc -o .chiro/manifests/tmp.chiro.json
chiro info .chiro/manifests/tmp.chiro.json
```

The manifest defaults to `self-deposited`, which is usually right for data your DVC repo
already owns. If the tracked data is actually a third-party download you should say so,
because it changes what the manifest claims:

```bash
chiro write --from-dvc-cache data/reference.dvc \
    --provenance observed-download \
    --source-url 'https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_44/' \
    --retrieved 2026-03-14T00:00:00Z \
    -o .chiro/manifests/tmp.chiro.json
```

Use the date you actually downloaded it, not today. The whole point of `retrieved` is
that it dates the observation.

---

## Where the cache is

If the cache is not in the default location:

```bash
chiro write --from-dvc-cache data/reference.dvc --cache-dir /scratch/$PROJECT/dvc-cache \
    -o .chiro/manifests/tmp.chiro.json
```

Both DVC cache layouts are handled: 3.x (`cache/files/md5/ab/cdef…`) and 2.x
(`cache/ab/cdef…`).

If a file is in the workspace but its cache object is missing, `--prefer-workspace-sizes`
takes sizes from the workspace instead. Where both exist they are cross-checked, and a
disagreement is reported rather than silently resolved.

---

## The one case that is refused

**DVC 2.x-era `md5-dos2unix` hashes are rejected.** Older DVC computed the digest of
text files after translating CRLF to LF, so the recorded value is *not* the md5 of the
bytes on disk. Transcribing it into a manifest would produce entries that no
verification could ever satisfy — a manifest that is wrong in a way that looks right.

```bash
chiro write --from-dvc-cache old.dvc --allow-legacy-md5 ...   # only if you know why
```

The flag exists for the case where you have independently established that the files
have no CRLF line endings, so the two digests coincide. If you are not sure, re-hash
instead: `chiro write` on the directory is slower and correct.

---

## Checking that the transcription is right

Verify the manifest against the workspace. This *does* read the data, and that is the
point — it confirms the transcribed digests actually describe the bytes:

```bash
chiro verify ".chiro/manifests/$D.chiro.json" --root data/reference
```

Worth doing once per repository when you adopt. After that the manifest is checked the
same way as any other.

---

## Getting DVC's directory hash back out

For confirming a manifest against DVC's own logic:

```bash
chiro dvc-dir ".chiro/manifests/$D.chiro.json" --hash-only
```

That value is what a `.dvc` records as `md5: <hash>.dir`. Note it is **not**
`body_hash` — DVC drops `size` and serialises differently, so the two never coincide.
The reasoning is in [`chiro-format-rationale.md`](chiro-format-rationale.md) §2; the
short version is that the equivalence is computed on demand rather than welded into a
frozen format.

---

## Where this leaves you

You now have manifests describing data DVC was already tracking. What that adds over
DVC alone:

- **The manifest travels.** A `.dvc` is meaningful only inside its repository with its
  cache or remote. A manifest can be deposited into an archive, emailed, or published
  alongside a paper, and still be checked.
- **Provenance is recorded.** `self-deposited` versus `observed-download`, with a
  `retrieved` date. DVC has no notion of whether data is yours.
- **Drift becomes detectable.** [`howto-drift.md`](howto-drift.md).
- **The two-path model.** If the deposit layout differs from the workspace layout, the
  manifest records both — see [`howto-deposit-data.md`](howto-deposit-data.md).

Next: [`howto-publish.md`](howto-publish.md).
