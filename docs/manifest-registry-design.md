# Manifest Registry — Design Note

**Status:** design / exploratory · companion to `chiro-format-spec.md` and the
fsspec/DVC plugin plan.

This note covers the layer *above* the manifest format: how manifests are
addressed (content-addressed `chiro://` refs), where they live (registries), how
they are trusted (CI-gated verification), and how that verification scales
(dispatched compute via `qxub`). It argues for building a **framework for manifest
registries plus one reference registry**, not a universal central repository.

> **Naming.** The project is **Chirograph** — after the medieval document written in
> duplicate with a word inscribed across the join, then *cut through that word* so
> authenticity was proven by matching the two halves. That is content-addressed
> verification centuries early, and exactly what a manifest does against its data. The
> technical token is **`chiro`**: files are `.chiro.json`, references are `chiro://…`,
> the CLI is `chirograph` (alias `chiro`), the internal `schema` value is
> `"chirograph"`. PyPI package: **`chirograph`** (clean, unqualified). (Retired working
> names: `xdir` — invisible etymology + `xdir` directory-lister collisions;
> `tessera`/`bulla` — both taken, `bulla` by a conceptually-adjacent agent-receipt
> tool.) The fsspec plugin is the separate **`archivefs`** package.

---

## 1. Motivation — the rigor/scale/convention trilemma

The open-tier deposition problem has no single-repository solution. Empirically
(verified during design):

- **Domain-conventional + scales** (GEO, SRA, GDC) ⟹ **weaker rigor.** GEO computes
  md5s but treats them as optional submitter-supplied QC metadata absorbed into the
  upload spreadsheet; it explicitly instructs submitters *not* to provide checksums
  as separate files, and the public SOFT/MINiML records link supplementary files by
  **URL only** — no retrievable per-file checksum. GEO accessions are also
  **unversioned and mutable in place** (GEO DataSets is explicitly in NCBI's
  no-version-suffix category; records are edited via an UPDATE button under a stable
  accession).
- **Strong rigor** (Zenodo, Dataverse, Dryad, Figshare) ⟹ **generalist + scaling
  questions.** Zenodo has per-record size/file-count limits; Dataverse scales better
  but is less genomics-conventional; Dryad charges fees. None is the domain-expected
  home for expression data.

There is **no repository that is simultaneously genomics-conventional, large-scale,
and rigorous** on checksums + versioning. That empty cell is what the manifest layer
fills: keep data in the domain-conventional, scalable repository (GEO) for
discoverability, and add the missing rigor — retrievable checksums + immutable
content-addressed versioning — as a thin layer on top.

**GEO is therefore the motivating case, not the weak link.** `.chiro` supplies the
retrievable checksums GEO withholds; content-addressed `chiro://` supplies the
immutable versioning GEO lacks.

### Prior art (validates the pattern, with a caveat)
`mirdata` (Music Information Retrieval) does essentially this: dataset curators
publish a JSON "index" mapping each file to its path + expected MD5, and a library
downloads/validates against it. It reached a stable 1.0, is assessed "sustainable,"
is cited by new datasets as their distribution mechanism. **Caveat:** its adoption
is *community-scoped*, not universal — it is a framework + community-contributed
indexes maintained by the MIR community, not a central authority. That distinction
drives §4.

---

## 2. `chiro://` — content-addressed manifest references

The primary reference is not to data but to a **manifest**, addressed by its content
hash. The manifest in turn names the data location (`source_url` + per-file
`source_path`).

```
chiro://<algo>:<whole-file-hash>        e.g. chiro://md5:abc123…
```

- **Hash = the whole-file hash of the `.chiro.json`** (the spec's Layer-3 hash), NOT
  `body_hash`. The whole-file hash uniquely identifies a *complete* manifest
  including where it points; `body_hash` would collide manifests with identical file
  lists but different `source_url`s.
- **Algorithm-prefixed** (`md5:` / `sha256:`) — the URL is the one place the
  algorithm can't be read from inside the file, so it must be explicit. Default md5
  (DVC-aligned).
- **Immutability for free:** `chiro://md5:abc123` always means the same manifest
  forever — any change yields a different hash, hence a different URL. This is what
  retrofits immutable versioning onto mutable upstreams (see §3).
- **Integrity for free:** fetch the manifest, hash it, confirm it matches the URL.
- **The `.dvc` shrinks to one reference:** an import's `deps` becomes a single
  `chiro://` hash that transitively pins manifest → per-file checksums → bytes. The
  whole provenance chain hangs off one committed hash.

### Resolution: DNS-like ordered resolvers over content-addressed names

The hash says *what*, not *where*. Resolution works **like DNS**: the user configures
an **ordered list of trusted registries** (the analogue of `/etc/resolv.conf`), and
`chiro` walks them in order until one returns a manifest whose hash matches the
requested `chiro://` reference. The bare content hash is a **location-independent name**
(portable across anyone who has *any* registry carrying it); the configured list is
the "where."

**Content-addressing makes this strictly safer than DNS.** In DNS you must *trust the
resolver* to return the honest answer for a name — a malicious resolver can lie
(spoofing), which is why DNSSEC exists. Here, a `chiro://<hash>` answer is
**self-validating**: you fetch the manifest, hash it, and confirm it matches the
requested hash. A malicious or buggy registry **cannot forge** an answer — it can only
fail to answer or return something that doesn't match (which the client detects and
rejects). So:

- **Registries need not be trusted for correctness.** They are untrusted *caches*,
  not trusted *authorities*. You can safely consult a stranger's registry, because
  the hash validates the answer. This is the DNSSEC guarantee, for free, by
  construction.
- **Order affects latency and metadata, not correctness.** Any registry returning a
  hash-matching manifest returns the *same* manifest (content-addressing guarantees
  it). Order is a performance / trust-*metadata* preference (whose verification-level
  labels you see — §6), never a way to get a "wrong" manifest.
- **Mirrors are trivial and cannot drift.** Same hash → same bytes, guaranteed. A
  firewalled lab runs a local mirror; a consortium runs a shared one; they stay
  consistent automatically (the IPFS / Git-object federation pattern).
- **Registry-trust reduces to one soft question:** do you believe a registry's
  *verification-level* claims (did it really check the checksums against upstream)?
  That's the only thing trust buys — not the manifest's identity, which is
  self-proving.

### Caching (better than DNS — no staleness possible)
Because a hash never changes meaning, a resolved manifest can be **cached locally
forever** — no TTL, no staleness for a given hash. Each `chiro://` reference is resolved
from the network at most once, ever, then served from a local content-addressed
cache. This also sharply cuts registry load. Negative results (no registry has the
hash) should **not** be cached long — a manifest may be added later.

### Resolution order (the tiers, DNS-model first)
1. **Explicit** — URL names an `.chiro.json` directly → use it.
2. **`chiro://` registry resolution** — walk the configured ordered registry list;
   first hash-matching manifest wins (self-validated). **This is the primary flow.**
3. **Auto-discovery at the data record root** — *demoted to optional courtesy.* Only
   relevant when `deps.url` points directly at data (e.g. bare `geo://…`) *and* a
   manifest happens to sit alongside. Redundant whenever the entry point is `chiro://`
   (the manifest is already in hand) and unnecessary for good-checksum repos
   (Zenodo/EGA expose checksums natively). **Do not architect around anchoring
   manifests to data repos** — route manifests through the registry instead (this also
   sidesteps the question of whether GEO even accepts a manifest supplementary file).
   Anchoring remains worthwhile only as a good-citizen gesture for non-`chiro` consumers
   of self-deposits to repos that welcome supplementary files (Zenodo/BIA).
4. **Native fallback** — no manifest; best-effort native listing (weaker/no
   download-free checksums).

---

## 3. What content-addressing does for mutable upstreams (the GEO win)

GEO is unversioned and mutates in place, so a GEO `.chiro` is *necessarily* an
`observed-download` time-snapshot — the `retrieved` date is the entire semantic
content of its provenance ("GSE12345 as observed on date D").

Content-addressing rescues this: **the manifest can offer the immutable reference
GEO cannot.** Capture `GSE12345`-as-of-D into a `.chiro`, address it by content hash,
and `chiro://md5:abc123` is a permanent, immutable pointer to that exact snapshot —
even as GEO mutates `GSE12345` underneath. You retrofit immutable versioning onto a
repository that has none, purely at the manifest layer.

Consequence for consumers: re-downloading `GSE12345` today may yield different bytes
than the manifest describes — which is *correct and desirable* (they should know the
record changed), made legible by the `retrieved` date. CI verifies "this *was* true
as of D"; it cannot guarantee "this is still what GEO serves."

---

## 3b. The in-repo manifest is the baseline; the registry is an optimisation

**Committing a manifest directly into the consuming repo already does most of the
job.** For a single lab reproducing its own single analysis, an in-repo
`.<manifest>.json` gives: reproducibility (travels with the code that uses it),
Git-anchored integrity (the `.dvc` hashes it), version history, and **zero
infrastructure**. For that case the registry adds nothing.

The registry earns its keep only in three specific situations — none of which is
"single project, single lab":

1. **Cross-project dedup at scale.** If N analysis repos consume the same reference
   dataset, in-repo means N committed copies, re-derived/re-verified N times. The
   registry stores it once, content-addressed, referenced by `chiro://…`. Matters at
   BCARC/consortium scale where reference datasets recur across many projects;
   irrelevant for one project.
2. **Discovery/reuse by people who don't have your repo.** An in-repo manifest is
   invisible to anyone not cloning that specific repo. The registry makes "verified
   checksums for GSE12345" a public, discoverable, reusable fact independent of any
   consuming project. In-repo helps *you reproduce your work*; the registry helps
   *others verify a dataset you characterised*.
3. **Verification-once economics + stable trust address.** The registry is the
   natural home for CI-gated verification: a dataset is verified once, at merge, and
   every later consumer inherits it. Without a registry, trust is re-established
   per-repo and entangled with each repo's state.

**Adoption path:** start with **in-repo manifests** (zero infra, immediate value —
e.g. the imminent Visium/Xenium deposit), and stand up the registry **only when**
cross-project dedup or public discovery becomes concrete. The registry is an
optimisation over the in-repo baseline, not a prerequisite. This de-risks the project
and matches the "start single-owner, grow if it wants to" instinct below. **Be honest
in adoption guidance: many everyday uses never need the registry.**

## 4. Framework, not cathedral — build a registry framework + one reference registry

**Decision: build a framework that lets anyone run a manifest registry, and run one
reference registry ourselves (swarbricklab's). Do NOT attempt a universal central
repository.**

### Why not a universal repository
- **Trust doesn't federate to universal scale.** A universal registry must accept
  manifests about all data from all contributors — making you arbiter of correctness
  for data you've never seen. CI checks checksums-match-upstream, but not that the
  manifest describes the *right* dataset or is honest about provenance.
- **Unbounded maintenance, wrong incentives.** A universal registry must track every
  upstream repository's quirks/mutations/outages forever, on one lab's resources,
  for the whole community.
- **Centralization liability.** Success makes it critical infrastructure → attracts
  governance, funding, liability, and egress-cost questions a lab shouldn't answer
  by fiat.

### Why a framework
- **Matches the proven prior art.** `mirdata` succeeded as framework +
  community-curated indexes, not a central authority. Copy what worked.
- **Scopes trust comprehensibly.** If anyone can *run* a registry (a Git repo + our
  CI templates), trust equals the operator's reputation: you trust swarbricklab's
  registry because you trust the lab; BCARC runs its own; other consortia run theirs.
  Bounded, comprehensible trust relationships instead of one registry nobody can
  fully trust.
- **Bounded deliverable.** Ship: the `.chiro` format (done), the generate/verify
  library, a reference registry *structure* (sharded layout + resolution
  convention), and CI *templates*. Others running their own registries is *their*
  maintenance, trust, and egress.

### The synthesis
Run **one reference registry** (swarbricklab's) that is simultaneously (a) real
infrastructure for our lab + BCARC + anyone who trusts us, and (b) the reference
implementation that proves the framework and keeps it honest via dogfooding. If the
pattern catches on, others stand up their own registries; a **federation** emerges,
discoverable via the configured-registries mechanism (§2). Universal *coverage*
without universal *authority* — the package-registry / Git-remote topology, not the
central-database topology.

Start with ours; let the topology grow if it wants to.

---

## 5. Registry as a Git repository — access & storage

### Read model (trivially open)
A public Git repo (GitHub/GitLab) is world-readable; fetching one manifest is a plain
HTTPS GET of a raw file at its sharded path. **Consumers never need Git** — resolving
`chiro://md5:abc123` is an HTTPS GET of `manifests/ab/c123….chiro.json`. The registry
being Git is a write-side implementation detail.

**Publishing checksums leaks nothing**, even for controlled data. md5 is
preimage-resistant (deriving content from hash is infeasible; the md5 *collision*
weakness is irrelevant here). So an EGA dataset's manifest can sit fully public — the
DAC still gates the bytes. The registry is unconditionally open-read.

### Storage layout
Content-addressed, sharded like DVC/Git: `manifests/ab/c123….chiro.json`. Note this is
somewhat redundant with Git's own internal blob addressing — accepted, because we use
Git for **distribution, versioning, review, and signing**, not its CAS-ness. If a
registry ever grows to millions of small files (where Git strains), the same
`chiro://` scheme can front a pure-CAS backend (S3/OCI) with no change to consumers —
the abstraction makes the substrate swappable.

### Write model
Content-addressed writes are **conflict-free**: identical manifests land at identical
paths; different manifests touch different sharded paths → parallel contributions
merge cleanly by construction. This is a genuine fit for Git.

---

## 6. Trust via CI-gated acceptance

The key enabling property: **a manifest makes a falsifiable, mechanically-checkable
assertion** ("file at accession X has md5 Y"). That lifts trust out of governance/
reputation (hard, social, unscalable) into verification (mechanical, automatable).

GitHub (vs a plain bucket) welds a **gate + compute + audit trail** to storage: a new
manifest can't enter `main` until CI verifies it. Trust becomes a *merge gate*, not a
post-hoc audit — which is what lets open contribution stay clean: anyone opens a PR;
only manifests that pass verification merge.

**This replaces manifest-signing as the load-bearing trust mechanism.** Signing
answers "who asserted this" (consumer must manage trusted keys); CI-gated merge
answers "was this verified true at merge" (consumer trusts one public, auditable
thing: the registry's CI config). Signing becomes *optional/additive* — useful for
attributing *who* asserted a manifest (provenance/credit), not for trust itself.

### Two verification tiers
- **Tier 1 — internal consistency (free).** Whole-file hash matches the `chiro://`
  address; `body_hash` matches the canonicalised files array. Catches corruption/
  malformed manifests. Proves self-consistency only — a manifest with fabricated
  checksums passes Tier 1. Necessary hygiene, not trust.
- **Tier 2 — upstream correspondence (the real trust).**
  - **2a — API cross-check (cheap, where available).** Verify manifest md5 ==
    repository-reported md5 with no data transfer. Works for Zenodo (md5 in file
    records); possibly EGA (pending the still-open decrypted-checksum question).
    **Not available for GEO supplementary files** — confirmed: SOFT/MINiML links
    files by URL only, no retrievable md5.
  - **2b — download-and-hash (always possible, expensive).** Fetch bytes, hash,
    compare. The only path for checksum-less repos (GEO). Where the scale cost lives
    (§7).

### Verify-once via immutability
A content-addressed manifest is immutable → **Tier 2 runs once, at merge, never
again** for that manifest. Immutability turns O(corpus × time) into O(new manifests).

### Recorded verification level (not binary)
Each manifest carries the level CI established: `tier1` / `tier2a` /
`tier2b-full` / `tier2b-sampled`. Consumers see what assurance a manifest carries and
decide what they need (a paper-rerun may accept `tier2a`; high-stakes reuse may
demand `tier2b-full`). Keeps the registry honest across cheap and expensive cases,
and honest about GEO where full verification is expensive. **Sampling** (verify a
random subset of a large dataset's files) is an allowed middle ground, tunable
assurance at bounded cost, sound when combined with immutability.

---

## 7. Scaling Tier-2b — dispatched verification via `qxub`

The entire scale cost is Tier-2b (download-and-hash for checksum-less repos). The
expense is **data movement**, not hashing — hashing is local-cheap *if you are where
the data is*.

**Idea:** instead of a GitHub runner downloading the data, **dispatch the
verification job to compute co-located with the data** and return only the result
(pass/fail + computed digests — 32 bytes, not TB). This converts the prohibitive tier
into a tractable one, using infrastructure we already have (`qxub` already runs in
our CI).

### Two-backend CI verification
The registry's CI picks a backend by dataset size / repository type:
- **local (GitHub runner)** — small datasets, and Tier-2a API cross-checks.
- **dispatched (`qxub`)** — large Tier-2b download-and-hash, sent to where the data
  is (data staged on HPC, or a compute environment with cheap access to the repo).

### Where co-location actually helps (be honest)
- **Strong:** data we've staged on our own HPC (Gadi); repositories with adjacent
  compute (SRA-on-cloud; anything S3-backed we can run batch/Lambda against).
- **Weaker:** arbitrary FTP-only repositories (e.g. public GEO) where "where the data
  lives" is someone else's server we can't run code on. Here `qxub` can still
  dispatch to a *better-connected* node (cheaper download), but it's "cheaper
  download," not "no download."

### `qxub` horizon (explicitly aspirational — do not scope now)
`qxub` began as an HPC dispatcher; the aspiration is a **general-purpose dispatcher**
(HPC → AWS/GCP/remote servers → *potentially* repository-side execution). Repository-
side dispatch (e.g. running a hash job *inside* GEO's infrastructure, returning only
the digest) would make Tier-2b free even for FTP-only repos — but requires buy-in
from the repository operators, unlikely absent tool popularity. **Recorded as a
horizon, not a near-term dependency.** The design only needs to treat "where
verification runs" as a **pluggable backend** so this arc has somewhere to land.

### Trust boundary note
Dispatched verification shifts the trust boundary: a `qxub`-verified result is as
trustworthy as the environment it ran in, which is *not* the transparently-auditable
public CI runner. The registry MUST record *where* verification happened, not just
that it passed. This is fine for a **single-owner reference registry** (we trust our
own Gadi jobs) and pairs poorly with open community contribution — you would NOT want
strangers' PRs dispatching jobs to your HPC allocation (resource-abuse + security
hole). Which reinforces §4: the framework-plus-reference-registry topology is *also*
the topology where dispatched verification is safe — each operator brings their own
compute for their own registry.

---

## 7b. Self-healing registries — distributed upstream-drift monitoring

Consumers detect two kinds of mismatch at pull time; the tooling can (optionally, but
**on by default**) report them back to the registry the manifest came from, and CI can
**auto-assess** the report. Framed correctly, this is not merely bug-reporting — it is
a **distributed monitoring network for upstream dataset mutation**, surfacing drift
that no single actor (including the source repository) cleanly exposes.

### The two mismatch cases (different meanings)

**Case 1 — manifest hash ≠ manifest content** (rare). The retrieved `.chiro.json`
doesn't hash to its `chiro://` reference. The client *already* detects and rejects this
(self-validation, §2), so the report means "registry X served a corrupted manifest."
CI triage is trivial and definitive: re-fetch at that path, recompute the hash →
either reproduces (real registry corruption → auto-flag / auto-quarantine the object)
or not (transient client transport glitch → auto-close). Rare, unambiguous, fully
auto-assessable.

**Case 2 — downloaded data ≠ manifest checksums** (common, and interesting). Bytes
pulled from `source_url` don't match the manifest's per-file md5. This usually does
**not** mean the manifest is wrong — it most likely means the **upstream mutated**
(the GEO unversioned-mutable-in-place case). The manifest was correct *as of its
`retrieved` date*; the upstream changed underneath it. So the report is a
**change-detection signal for upstream drift**, not a manifest bug. Aggregated across
consumers, the registry becomes aware of which upstream datasets have drifted — a
genuinely novel capability.

### CI auto-assessment (Case 2)
On a drift report (manifest claims file F = md5 X; consumer observed md5 Y), CI
**independently re-verifies**: fetch F from `source_url` *now*, hash it.
- CI gets **X** → reporter had a transport/local error; manifest and upstream still
  agree. Auto-close ("could not reproduce; upstream still matches — retry your
  download").
- CI gets **Y** → **confirmed upstream drift.** Auto-label the manifest
  "upstream-drifted as of D2"; optionally auto-generate a *new* `observed-download`
  manifest for the new state (new hash → new `chiro://` ref) and link them
  (supersedes / superseded-by). **Nothing is deleted** — the old manifest remains a
  valid historical record of the bytes the original analysis used; the new one
  describes current-upstream. You have **auto-versioned an unversioned upstream**,
  triggered by a consumer's mismatch.
- CI gets **Z** (a third value) → upstream actively changing/flaky → flag for human
  attention.

The result is a loop that *strengthens* over time: content-addressing makes every
observed state a distinct immutable object, so the accumulating drift history is
valuable provenance, not cruft.

### Abuse is bounded by verification (see also §8)
Reports are **untrusted but verified** — the same property that makes the DNS-like
resolver model safe. CI does not *trust* a report; it *reproduces* it. A false report
fails reproduction and auto-closes. A reporter can only *trigger a check*, never
corrupt anything. (Spam still costs verification effort — mitigations in §8.)

### Confirmation cost
Case-2 confirmation re-downloads F (Tier-2b egress). Mitigate: confirm only the
*specific* reported file (not the whole dataset), rate-limit confirmations per
manifest, and dispatch confirmation via **`qxub`** to cheap/co-located compute (§7).
The self-healing loop and dispatched verification reinforce each other.

### Report hygiene
Default-on reporting should be **anonymous-by-default and content-minimal** (manifest
hash, file relpath, observed md5, timestamp — nothing else), so default-on carries no
privacy surface. Users may opt into attribution if they want credit for finding drift.

---

## 8. Spam / abuse mitigation

**Reframing: the design has already neutralised *dangerous* spam; only *costly* spam
remains.** A spammer **cannot** submit false checksums that corrupt a consumer (CI
Tier-2 catches it at merge), **cannot** forge a manifest's identity (content-addressed
hashes self-validate), **cannot** corrupt an existing manifest (immutability), and
**cannot** poison drift reports (CI reproduces before acting, §7b). So malicious-
*payload* spam is already defeated. What remains is purely **resource-exhaustion**
spam: submissions/reports that each *cost* something to verify (CI minutes, Tier-2b
egress) even though none can harm a consumer. The threat is denial-of-CI /
denial-of-wallet, not corruption — so defences target **cost**, not trust.

Layered defences, ordered by leverage against the cost threat:

1. **Cheapest CI checks first (biggest lever, free).** Gate Tier-1 self-consistency
   (hash matches, well-formed JSON, no `..`/absolute paths) *before* any Tier-2
   download. Malformed junk is rejected at zero egress; only well-formed submissions
   ever reach the expensive tier.
2. **Single-owner until open contribution is demanded (eliminates the surface).** The
   adoption path (§3b/§4) starts with only the owning lab committing; the world reads.
   **No open write path = no spam.** Open contribution — and its spam surface — is
   deferred until there is demand, by which point defences 3–5 are designed in. "Start
   single-owner" and "reduce spam risk" are the same decision.
3. **Rate-limit expensive verification per identity / source / accession.** Tier-2b
   download-and-hash is the costly step; cap how many a given submitter can trigger per
   window. Many PRs are fine; unbounded egress is not.
4. **Durable identity to rate-limit against.** *Signed commits* (GPG/SSH) make
   submissions attributable so you can rate-limit/revoke *by key*, and a spammer can't
   cheaply mint fresh identities. **Its role is durable identity, not trust** (trust is
   already handled by content-addressing) — so it's clearly right for a single-owner
   registry (free) but is a real friction tax on *honest* open contributors (weigh
   against contribution rate). **ORCID** is a lighter alternative well-suited to a
   scientific audience: a real-identity signal users already have, low friction for
   legitimate scientists, expensive to fake at scale. Consider ORCID and/or account-
   age/affiliation gates as complements or alternatives to GPG.
5. **Sample before full download.** Spot-check one/few files before a full Tier-2b
   fetch; if the probe fails, reject before downloading the rest. Optionally require
   submitters to include their *own* verification evidence, shifting first-pass cost to
   them.

**Load-bearing defences are 1 and 2** (they attack the cost threat directly and
early); signing (4) is worth adding but is one layer among several, not the
foundation.

---

## 9. Honest caveats & open items

- **CI verifies "was true as of D," not "is still true."** Even verified manifests
  are time-stamped truths against mutable upstreams (GEO). Correct for
  reproducibility; a caveat for "get me the current version." `retrieved` stays
  essential.
- **Tier-2a for EGA** depends on the still-open question (in the plugin plan) of
  whether EGA's reported checksum is of the *decrypted* content. Resolve that check
  before relying on EGA Tier-2a.
- **Egress sustainability at popularity.** If the reference registry becomes popular,
  Tier-2b egress/compute cost is the real ceiling — a sustainability (funding/infra)
  question, not a technical one, and deferrable. The verification-level + sampling +
  `qxub`-dispatch mechanisms are what keep it honest when full verification isn't
  affordable for every manifest.
- **Signing** — optional/additive for authorship attribution; design later if
  community contribution demands it. Not load-bearing for trust (CI is).
- **`qxub` general-purpose backends** — horizon, not scope. Keep "where verification
  runs" pluggable.
- **Registry bootstrap** — start single-owner (we write, world reads); defer
  open-contribution + its heavier trust machinery until demanded.
- **GEO SOFT md5 belt-and-braces** — treat GEO-supplementary-md5-not-exposed as
  established (confirmed at field level); a one-line `zcat GSE*_family.soft.gz | grep
  -i md5` during GEO-backend implementation is the final confirmation.

---

## 10. How the pieces fit

```
  .chiro.json format  (spec)         — the manifest: files + checksums + provenance
        │
        ├── generate/verify library — make manifests (local or observed-download),
        │                             verify bytes; imported by the plugin
        │
  chiro:// content-addressed refs    — immutable, portable manifest identity
        │
  registry framework                — anyone can run a registry (Git repo + CI
        │                             templates); resolution via configured registries
        │
  reference registry (swarbricklab) — our real registry + the framework's proof
        │
  CI-gated trust                    — Tier1/2a/2b verification as a merge gate;
        │                             recorded verification level
        │
  qxub-dispatched verification      — scale Tier-2b by computing where data lives;
        │                             pluggable backends (HPC now, cloud/repo-side later)
        │
  self-healing (drift monitoring)   — consumers report mismatches; CI confirms &
        │                             non-destructively auto-versions drifted upstreams
        │
  archivefs  (fsspec/DVC plugin)    — consumes all of the above: dvc import/get/list
                                      with checksum certainty and friendly layout
```

The plugin (**`archivefs`**; `sci-fsspec` fallback) works **without** any of the
registry layer (native listing, native checksums). The registry layer is the
enhancement that adds rigor where repositories lack it — most importantly GEO.
