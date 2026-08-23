# Why the `.chiro.json` format is the way it is

Commentary on [`chiro-format-spec.md`](chiro-format-spec.md). The spec says what the
format *is*; this says why, what was rejected, and where the sharp edges are.

Nothing here is normative. Where this document and the spec disagree, the spec wins
and this document is wrong.

**Related:** [`decisions.md`](decisions.md) is the dated record of decisions taken
during implementation, including several that this document summarises. It is
referenced rather than restated, because its value is that it was written at the time
and has not been retrofitted.

---

## 1. The problem the format exists to solve

Many scientific data repositories don't expose retrievable per-file checksums, and
some — GEO most notably — don't version their records at all. A stable accession can
mutate in place, silently. So an analysis that cites `GSE12345` has cited something
that may no longer exist in the form it was analysed.

Chirograph is a thin layer *on top of* whichever repository the data must live in. It
does not replace the repository, mirror it, or ask it to change. It records what was
there, precisely enough that any later copy can be checked against the record.

That framing sets the design constraints. The format has to survive being deposited
into a repository that knows nothing about it, downloaded by a tool that knows nothing
about it, and read by a person ten years later with no tooling at all beyond
`md5sum`.

---

## 2. Why it mirrors DVC's `.dir` format

The body is a near-superset of a DVC `.dir`: entries of `{relpath, md5}` plus `size`,
sorted by `relpath`, POSIX separators. Field names are DVC's, including the field
called `md5` that may hold a sha256 — renaming it would have been tidier and would
have broken the property that makes the choice worth anything.

The payoff is that a DVC-literate tool or person reads the body with no translation,
and that generating a manifest from a DVC cache is transcription rather than
computation — no re-hashing of terabytes.

**But `body_hash` is not a DVC `.dir` hash.** Version 0.2 of the spec claimed it was.
That claim was checked against dvc 3.66.1 during implementation and is false, in two
independent ways:

- A DVC `.dir` carries **no `size`**. Size lives in DVC's `Meta`, which is serialised
  only by the `with_meta=True` variant of `Tree.as_bytes()` — and that variant is not
  what DVC hashes or writes to the cache.
- DVC serialises with `json.dumps(lst, sort_keys=True)` — Python's **default**
  separators (`", "` and `": "`, with spaces) and **default** `ensure_ascii=True`, so
  non-ASCII is escaped to `\uXXXX`. The spec's canonical form is compact and does not
  escape.

The canonical form was deliberately **not** changed to match DVC's. DVC's
serialisation is an implementation detail that has already changed once; welding a
frozen format to it would import future breakage for a cosmetic gain. Instead the
equivalence is a *derived* value (`chiro dvc-dir`), tested byte-for-byte against real
`dvc add` output, including for non-ASCII trees where the `ensure_ascii` difference
actually bites. See [`decisions.md`](decisions.md) D-01 and D-02.

The general lesson, which cost two spec revisions to learn: **claims about another
tool's format belong in a test, not in prose.**

---

## 3. Why canonicalisation is frozen, and why the vectors carry strings

Canonicalisation is the format's single fragility point. Everything else fails
loudly — a malformed manifest won't parse, a wrong digest won't match. But an
implementation that canonicalises *almost* correctly produces digests that don't
match, with no indication of why. Two correct-looking implementations, mutually
unintelligible.

So the procedure is frozen and pinned by published vectors, and each vector carries
the **exact canonical string** rather than only its digest. A digest mismatch tells a
reimplementer nothing; a string diff tells them whether they got the separators, the
escaping, the sort order or the normalisation wrong.

Every vector digest is re-derivable with coreutils alone
(`printf '%s' "<canonical>" | md5sum`), and `tools/check_vectors_independently.sh`
does exactly that — so the frozen values are not merely self-consistent with the code
that generated them.

The vector file is pure ASCII, using `\uXXXX` escapes, because the NFC/NFD cases would
otherwise be unreviewable: you cannot tell a composed character from a decomposed one
by looking at it. This was learned the hard way — several test cases in early drafts
silently tested nothing, because typing "café" produces the precomposed form and the
NFD case never existed.

### The traps the procedure pins

**Normalise before sorting, never the reverse.** NFD's combining marks sort
differently from the characters they compose into. In NFD, `"café"` sorts
*before* `"caff"` (`e` U+0065 precedes `f` U+0066); after NFC it is `"café"`,
which sorts *after* `"caff"` (U+00E9 exceeds U+0066). Sort-then-normalise yields the
reverse order and therefore a different hash.

**Sort on the joined string, not path components.** `"a-b/x"` precedes `"a/b"`
because `-` (U+002D) precedes `/` (U+002F). Splitting into components first reverses
them. This matches DVC.

**Reject duplicate `relpath`s rather than sorting stably.** With two equal sort keys,
output order would depend on input order — meaning there would be no canonical form
at all, which is the one thing canonicalisation cannot tolerate.

**No semantic rewriting.** A `source_path` that happens to equal its `relpath` is not
dropped during canonicalisation, so a redundant manifest hashes differently from the
equivalent terse one. Writers omit redundant `source_path`; canonicalisers must not.
Making canonicalisation "helpful" here would make it a semantic transformation, and
then every implementation would need to agree on the semantics as well as the
syntax. See [`decisions.md`](decisions.md) D-06.

---

## 4. Why the header is not hashed

`body_hash` covers the body only. A file cannot contain its own hash — writing the
hash changes the hash — so something has to be left out, and the header is the part
that must contain the digest.

The consequence is that in *raw standalone* use the header is unprotected. That is
accepted deliberately:

- Realistic corruption of the header shows up as malformed JSON or as a `size` /
  `nfiles` mismatch against the body.
- The moment the manifest is DVC-tracked or `chiro://`-addressed, Layer 3 covers the
  whole file including the header.
- The alternative — a header digest over a canonicalised header — would mean two
  canonicalisation procedures to keep in agreement across implementations, doubling
  the format's only real fragility for a case that Layer 3 already covers.

---

## 5. Why `relpath` is inside `body_hash`

It would be defensible to hash only content digests, so that the same data described
with two different workspace layouts hashed identically. The format does the opposite:
layout is hashed alongside content.

This is because the layout is part of the claim. "Paths-in-code match paths-to-data"
is a reproducibility property, not a convenience — if a reader materialises a
different tree, the published code does not run. So a manifest attests *which bytes,
and where they go*, and verification fails if either differs.

A side effect worth knowing: two manifests describing the same data fetched from
different archives will have **different** `body_hash` values, because `source_path`
differs. Cross-archive identity is therefore established by comparing the multiset of
per-file digests, not by comparing `body_hash`.

---

## 6. Why two paths

`relpath` is where a file goes in the workspace; `source_path` is where its bytes
come from in the deposit. The manifest is a small routing table between the two.

This exists because deposit layouts are shaped by the repository's constraints and
workspace layouts are shaped by the code. GEO wants a flat directory of
`GSE12345_sampleA_matrix.mtx.gz`; the analysis wants `sampleA/matrix.mtx.gz`. Without
the two-path model, one of those has to lose, and in practice it is the code that
loses — every published pipeline acquires a rename step that nobody documents.

When the layouts agree, `source_path` is omitted everywhere and the body is
DVC-`.dir`-shaped. The verbosity is paid only when the layouts actually diverge.

---

## 7. Why `hash_algorithm: "etag"` is rejected

Version 0.2 left open whether an `observed-download` manifest of an HTTP or S3 source
could declare `hash_algorithm: "etag"`, recording the validator the repository exposes
without downloading anything. The answer is no, and the reason is not fastidiousness.

An ETag is not a function of content alone. The same bytes uploaded to S3 with a
different multipart part size produce a different ETag. Therefore an ETag:

- cannot address content — `chiro://etag:…` would not be immutable;
- cannot produce a meaningful `body_hash`;
- cannot be recomputed by a consumer holding the file, which makes Layer-1
  verification impossible.

A manifest whose digests cannot be checked is not a weaker manifest. It is a
different, unverifiable artifact wearing the same name — and the format's entire value
is that a `.chiro.json` means one thing.

So: **download and hash, or do not claim a hash.** Where an ETag is worth recording as
corroborating evidence, the per-entry `etag` field carries it *next to* a real digest,
which is honest about which is which. See [`decisions.md`](decisions.md) D-04.

The same discipline governs the proposed `1.1` staleness fields (spec §8): a changed
signal is evidence of change; an unchanged signal is not evidence of anything.

---

## 8. Why entries are flat but headers are not

Entry fields must be scalars. Header fields need not be.

The asymmetry follows from §4: entry fields are inside `body_hash`, so tolerating an
unknown entry field means committing to a canonical form for its value. For a scalar
that is already defined. For a nested object or array it is not — and an
implementation that canonicalised a nested value differently would produce a
different `body_hash` from a manifest that looks identical. That is precisely the
silent divergence canonicalisation exists to prevent.

Header fields are never canonicalised, so the constraint doesn't apply to them. This
is why the proposed `1.1` additions (spec §8) are addable as a minor version: they are
scalars, and two of the three are header fields.

Note that [`decisions.md`](decisions.md) D-19 states this rule more broadly than is
true — it says a minor may add only scalar fields, without distinguishing header from
entry. The narrower statement in spec §3.3 is the correct one.

---

## 9. Why `md5`, in 2026

md5 is cryptographically broken and it is the default here anyway, because the threat
model is corruption and drift, not forgery. What md5 buys is that DVC uses it — so a
manifest interoperates with the tool that will actually be managing the data, and a
DVC cache can be transcribed without re-hashing.

`sha256` is permitted for environments where md5 is simply unavailable, such as a
FIPS-mode interpreter, at the cost of that interoperability. Nothing else is
permitted: every additional algorithm is another thing every implementation must
support to be able to read every manifest. See [`decisions.md`](decisions.md) D-03.

If forgery ever enters the threat model, the answer is a signature over the manifest,
not a stronger digest inside it — the digest's job is to identify content, and it does
that adequately.

---

## 10. Why a field exists only if a consumer acts on it

The header excludes free-text `title`, `description` and `abstract`, and excludes
`contact` and any other personal identifier.

Descriptive metadata goes stale, and stale metadata is worse than absent metadata
because it erodes trust in the fields that *are* maintained. A manifest that carries a
two-year-old abstract invites the reader to doubt the digests too. The repository is
where descriptive metadata belongs; it has curators and a correction process.

`contact` is excluded for a stronger reason: a manifest is committed to git and
deposited into public archives, both effectively permanent. Personal information
placed there cannot be withdrawn.

---

## 11. The `urljoin` trap

Resolving a fetch location looks like a one-liner and is not. `urllib.parse.urljoin`
is wrong for this in three separate ways, two of which affect exactly the
repositories this format targets:

1. **Non-hierarchical schemes lose the base entirely.** `urljoin` resolves relative
   references only for schemes in `urllib.parse.uses_relative`. For anything else it
   returns the reference unchanged, so `urljoin("geo://GSE12345/", "a.gz")` is
   `"a.gz"` — not a URL at all. `geo://` and `s3://` are the format's own examples.
2. **A base without a trailing slash loses its last segment**, because `urljoin` reads
   it as naming a file: `urljoin("https://x/suppl", "a.gz")` gives
   `https://x/a.gz`. Silently wrong, and visible only as a 404 much later.
3. **A base with a query or fragment loses its path**, because appending a trailing
   slash appends it to the query.

The correct approach joins on the path component, carries scheme, authority, query and
fragment through untouched, treats a query as a parameter of the request rather than
part of the location, and percent-encodes the path — spaces and `#` are legal in
filenames and significant in URLs.

`Manifest.fetch_plan()` exists so that a data-access layer does not have to
rediscover this.

---

## 12. Things that were tried and rejected

**A manifest of manifests.** A single `paper.chiro.json` covering every manifest in a
project, as one citable digest. The format composes perfectly well — a manifest over a
directory of manifests is just a manifest — but it earns nothing: git's tree hash is
already a content-addressed digest over exactly that set, and it covers the code too.
Worse, it attests the *manifests*, which are kilobytes of text under version control
and the least at-risk component in the system, while saying nothing about the
multi-gigabyte data that is actually at risk.

**Whole-file versus canonical-document addressing.** A whole-file digest changes when
the JSON is re-indented, which is a sharp edge for anything keyed on it. A
canonical-document digest survives reformatting but requires a parser to compute, so a
reader with only `md5sum` cannot check it. Both are implemented (`chiro ref`,
`chiro ref --canonical`); `chiro://` uses the whole-file digest, on the grounds that
verifiability with no tooling is the property that matters to a reader in ten years,
and that content-addressed *filenames* make the reformatting hazard visible rather
than silent. See [`decisions.md`](decisions.md) D-09.

**A JSON-Lines body.** Deferred until a deposit's file count makes JSON parse time
actually hurt — realistically above ~10^5 files. One note for whoever implements it:
the canonical form is already per-entry (`[`, entries joined by `,`, `]`), so a JSONL
body's `body_hash` can be defined over the same byte stream, keeping **one**
canonicalisation rule rather than two.

**A four-way archive-capability taxonomy** (`pinned` / `pointer` / `signal` / `none`)
for describing how well a repository versions its records. Replaced by the observation
that the capability is implied by which fields are populated, so the enum was
ceremony. Recorded here because it will look like an obvious idea to the next person.

---

## 13. Why manifests are named after their own digest

Spec §1.1 asks for `<digest>.chiro.json` rather than `GSE000000.chiro.json`. Earlier
drafts had it the other way round, on the grounds that a human browsing a deposit can
read an accession and cannot read a hash. That is true and it is outweighed.

**Multiple observations of one record must be able to coexist.** This is the decisive
argument. Detecting that an upstream record has changed means holding two dated
observations of the same accession — that pair *is* the drift record. Under
accession-derived naming they are the same filename, so recording the second destroys
the first. The convention was actively hostile to one of the format's main purposes.

**A digest-named manifest is self-verifying with no tooling.** `md5sum *.chiro.json`
checks every name against its content. Nothing needs to be installed, and nothing
needs to be trusted. For a reader ten years from now that is the property that matters
most.

**Writes stop colliding.** Two processes recording the same manifest write the same
filename with the same bytes, so concurrent writes are safe by construction rather than
by locking. That matters on HPC, where array jobs share a filesystem.

**Immutability becomes self-enforcing.** Nobody edits a file whose name is its hash,
for the same reason nobody edits a Git object. This is not merely cultural: it
neutralises the one real hazard in whole-file addressing, which is that reformatting a
manifest silently changes its `chiro://` reference. Under digest naming the damage is
visible —

```
filename says : 6f174b49ded208801052240cf05798c8
md5sum says   : 64506a3400bc97573bab5625271983db
```

— where under accession naming it is invisible. That is the argument that settled
whether `chiro://` should address the whole file or the canonical document (§12): the
whole file, because digest *filenames* make its fragility detectable, and because
`md5sum`-verifiability is worth more than reformat-tolerance.

**The exception is real, though.** For a deposit that non-Chirograph consumers will
browse, an accession-derived name is genuinely better: it is legible, it sorts
predictably next to the record's other files, and it survives being downloaded into a
directory of other records' manifests. That case is why §1.1 says SHOULD rather than
MUST.

Note that auto-discovery does not depend on the name either way: archivefs looks for
`*.chiro.json` and refuses to guess when a deposit contains more than one.

---

## 14. What the format does not do

Worth stating plainly, because "attestation" invites over-reading.

Chirograph gives you **integrity** and **drift detection**. It does not give you
**authenticity**. Anyone can write a manifest claiming anything; nothing in the format
proves who wrote it or that the claim was true when made.

Authenticity comes from where the manifest lives — a signed commit, a repository
archived by a journal, a deposit under an accession you control — not from the
manifest itself. That chain is adequate for publication, which is why signing is not
in the format. But a data-availability statement should claim what the format
delivers, and no more.
