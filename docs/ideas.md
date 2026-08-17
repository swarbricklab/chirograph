# Ideas & extensions

Things worth doing that are **out of scope for the current plan**, recorded while
implementing it. Nothing here has been built. Ordered by (my estimate of)
value-per-effort within each section.

Decisions actually taken are in [`decisions.md`](decisions.md); this file is
strictly speculative.

---

## 1. Format & correctness

### 1.1 Make the canonical-document hash the registry's address ★
Follows directly from [D-09](decisions.md#d-09). The specified `chiro://` address
is the manifest's whole-file hash, which changes when somebody re-indents the JSON
— and manifests live in Git, where that happens. `canonical_ref()` is already
implemented and is invariant under reformatting.

Concretely, for Milestone 2: `chiro://` addresses the canonical document form;
the whole-file hash keeps its Layer-3 role in the `.dvc`, where DVC owns the bytes
and never reformats them. Cost: validating a registry's answer needs a JSON parse
rather than a byte comparison. Benefit: a registry's primary key stops depending
on whitespace.

### 1.2 A cross-language conformance runner
The vectors ship inside the package (`chiro test-vectors`) and carry the canonical
string literally, so a second implementation can diff strings rather than digests.
The missing piece is a *runner*: a tiny harness that takes
`command < input.json > output.json` and reports pass/fail per vector, so a Rust
or Go or R implementation can be checked without writing Python. Perhaps 100 lines
and it is what makes the freeze actually useful to anyone else.

### 1.3 Per-entry `hash_algorithm`, if ETag ever has to come back
[D-04](decisions.md#d-04) rejects `hash_algorithm: "etag"` wholesale. If a real
use case appears (a genuinely un-downloadable multi-TB S3 source), the honest
shape is a per-entry `hash_algorithm` under a `schema_version` bump, so a mixed
manifest says per file which entries are verifiable and which are only
corroborated. That keeps `body_hash` computable (it always uses the header
algorithm) and keeps `chiro://` content-addressing intact, while admitting a
partially-unverifiable manifest *as such*. Do not do this pre-emptively.

### 1.4 Empty directories, if they turn out to matter
Neither the format nor DVC can represent one; the walk reports them. For imaging
and spatial data an empty directory is occasionally load-bearing (a tool that
expects `spatial/` to exist). A `directories` array, or a zero-byte `.keep`
convention, would fix it. Both are ugly; wait for a real case.

### 1.5 JSON-Lines variant
Already deferred in spec §10 pending >~10⁵ files. One observation from
implementing: the canonicalisation is *already* per-entry — the canonical form is
`[` + entries joined by `,` + `]`. So a JSONL body's `body_hash` could be defined
as the hash of the same byte stream, keeping one canonicalisation rule for both
encodings rather than two. Worth writing down before anyone designs it
independently.

### 1.6 `superseded_by` / `supersedes` header fields
Registry design §7b wants drifted manifests linked non-destructively. That link is
a header field pair holding `chiro://` references. Deliberately not added yet: it
is registry vocabulary, and adding an unused field to a format being frozen is how
formats rot. But `chiro diff` already produces exactly the information such a link
would summarise.

---

## 2. Library & CLI

### 2.1 `chiro drift-report` ★
`chiro diff` already produces the drift information registry design §7b describes.
The missing step is emitting it in the content-minimal, anonymous-by-default shape
§7b specifies for reporting (manifest hash, relpath, observed digest, timestamp —
nothing else). That is a small formatter over the existing diff, and having the
report *format* defined before the registry exists means consumers can start
collecting drift observations locally today, into a file, for later submission.

### 2.2 Resume / incremental re-hashing ★
For a multi-TB deposit, `chiro write` is a long single-shot operation and an
interruption loses everything. Two cheap wins:

- **Reuse an existing manifest**: given `--from-manifest old.chiro.json`, skip
  hashing any file whose size *and* mtime are unchanged, and re-hash the rest.
  Fast and honest as long as it says how many entries it trusted rather than
  verified — this is a `dvc status` style guarantee, not a `dvc add` one, and the
  manifest must not claim otherwise. Perhaps a `--assume-unchanged` flag so it is
  never the default.
- **Checkpointing**: write partial results to a sidecar so a re-run resumes.

### 2.3 HPC-dispatched hashing
Registry design §7 makes "where verification runs" a pluggable backend. The
concrete near-term version at this site: hashing a staged multi-TB deposit on Gadi
should be a job array, not a login-node loop. `hash_files()` is already the single
choke point, and the parallelism is already threaded; a `--jobs`-shaped backend
that shells out to `qxub` and merges per-shard results would fit behind it without
touching anything else. This is the tractable half of §7 — no repository buy-in
needed, just our own scheduler.

### 2.4 `chiro fetch` — materialise a manifest
`Manifest.fetch_plan()` gives a consumer everything it needs; `archivefs` is
meant to do the fetching. But a dependency-free `chiro fetch` over plain HTTPS
(verifying each file against its entry as it lands, and refusing to write a file
that does not match) would make a manifest self-sufficient for the commonest case
without pulling in fsspec. Careful: this is scope creep towards being a download
tool. Only worth it if the "hand someone a manifest and they can use it with
nothing installed" story matters.

### 2.5 Progress and reporting polish
Currently a percentage on stderr when attached to a terminal. For a real deposit,
bytes/second and an ETA are what tell you whether to go home. Also: `chiro write`
on a huge tree should report the largest files first in `--json` output, since
that is what dominates the runtime.

### 2.6 `chiro validate --explain`
The lint codes are stable strings (`case-collision`, `redundant-source-path`, …).
A `--explain CODE` that prints the full reasoning — including the decisions.md
reference — would let the short warning stay short.

### 2.7 Shell completion
`argparse` has no built-in completion. `shtab` generates it from an existing
parser with a few lines and no runtime dependency. Nice-to-have.

---

## 3. Registry (Milestone 2, when it happens)

### 3.1 The CLI is already the CI gate ★
Worth noticing before designing registry CI from scratch: registry design §6's
Tier-1 check *is* `chiro validate` (exit 1 on a finding, 2 on a tool failure —
[D-16](decisions.md#d-16)), and Tier-2b *is* `chiro verify`, which already reports
`tier1` / `tier2b-full` / `tier2b-sampled` in the design note's own vocabulary. A
registry's workflow should be a thin wrapper over these two commands rather than a
reimplementation, so that the gate and the local tool can never disagree about
what "verified" means.

Similarly, §8's "cheapest CI checks first" is already the layering:
`validate` reads no data at all, `verify --layer 2` reads no data,
`verify --sample N` reads a bounded amount, `verify` reads everything.

### 3.2 Sampling as a policy, recorded per manifest
`--sample-fraction` is deterministic and seeded from `body_hash`
([D-12](decisions.md#d-12)), so a registry can record *which* files a sampled
verification covered without storing a list — just the fraction and the manifest
hash. That makes `tier2b-sampled` auditable after the fact by anyone, which is
what stops it being a euphemism.

### 3.3 A registry needs no registry code to start
`ChiroRef.shard_path` plus a public Git repo plus `chiro validate` in a workflow
file is a functioning single-owner registry today. Worth trying that before
building a framework, because it will reveal what the framework actually needs —
and registry design §3b already argues the in-repo baseline is usually enough.

---

## 4. Testing & infrastructure

### 4.1 Property-based canonicalisation testing
The frozen vectors cover the traps we thought of. `hypothesis` generating random
path sets and asserting the invariants — sort-independence, key-order-independence,
NFC idempotence, `canonicalise(x) == canonicalise(shuffle(x))` — would cover the
ones we did not. Cheap, and the natural complement to a fixed vector set.

### 4.2 Interop tests against older DVC versions
[D-01](decisions.md#d-01) and [D-02](decisions.md#d-02) came from checking
dvc 3.66.1. The `.dir` format has demonstrably changed before (`size` used to be
present in some form), so the DVC-interop function is pinned to one version's
behaviour. A matrix over two or three DVC majors would tell us how stable
`dvc_dir_hash()` really is — and would justify or refute the D-01 decision to keep
the equivalence *derived* rather than baked into the format.

### 4.3 A large-tree performance test
Everything is tested on trees of three files. The realistic case is 10⁴–10⁶ files
and terabytes. A generated large tree (sparse files, so it costs no disk) would
catch the O(n²) mistakes that do not show up at n=3, and belongs in CI as a
guard rather than as a one-off measurement.

A one-off measurement was taken (in-memory, CPython 3.11, quadrupling n from 1000
to 64000) and everything scales **linearly** — canonicalisation, `Manifest`
construction, lint, serialisation and load all show ≈4× per 4× n, nothing
quadratic. At n = 64 000 the whole round trip is ≈7 s, of which the JSON *load*
is ≈3.7 s and dominates.

Measured directly at 10⁵ entries afterwards: `load()` takes ≈6.8 s, of which the
JSON parse dominates. That supports spec §10's ">~10⁵ files" threshold for the
JSON-Lines variant, and says that when it starts to hurt it will be **parse** time
rather than our canonicalisation. Worth re-measuring on a real deposit before
acting on it.

(For reference, moving path and digest validation into `FileEntry.__post_init__`
cost ≈19% on `load` and ≈7% on in-memory construction at that size, and changed
nothing about the linearity. Cheap for making it impossible to hold an entry that
could not be written out and read back.)

Two O(n²) traps to be aware of, both currently avoided but neither enforced:
`Manifest.entry()` is a linear scan, and `Manifest.by_relpath` rebuilds its dict
on every access. Calling either inside a loop over entries would be quadratic. All
current callers hoist them; a large-tree test in CI is what would catch a future
one that does not.

### 4.4 Fuzz the reader
The reader is a security boundary: it parses untrusted third-party manifests and
its output gets materialised to disk. `atheris` or a simple mutation loop over the
valid vectors, asserting only ever `ChirographError` or success, never a traceback
— a few hours' work for a real assurance step.

### 4.5 Publish, and dogfood the freeze
Publishing to PyPI is what makes the vectors reachable by a second implementation
(`pip download chirograph`) and is the point at which the freeze becomes a
commitment. Worth doing early *precisely because* it is a commitment — it forces
the D-01 spec correction to be settled first.
