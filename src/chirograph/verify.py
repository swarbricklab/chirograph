"""Verification: Layer 2 (the file list) and Layer 1 (the bytes).

Spec §4 splits integrity into three layers. Two of them are checkable from a
manifest plus the data:

* **Layer 2** — recompute ``body_hash`` over the canonicalised ``files`` array.
  Free, needs no data, catches truncation and corruption of the list itself.
  This is the whole of a registry's Tier-1 check (registry design §6).
* **Layer 1** — hash each payload file and compare. Costs a full read of the
  dataset, which is where all of the scale expense lives.

Layer 3 (the whole-file hash of the manifest, recorded externally in a ``.dvc``
or as a ``chiro://`` reference) is :mod:`chirograph.ref`'s job.

A mismatch here is not necessarily a fault. For an ``observed-download``
manifest, bytes that no longer match usually mean the *upstream* mutated since
``retrieved`` — the GEO case (registry design §3, §7b) — so the report
distinguishes the outcomes rather than collapsing them into a boolean.
"""

from __future__ import annotations

import os
import random
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .canonical import compute_body_hash
from .constants import PROVENANCE_OBSERVED
from .hashing import default_jobs, hash_file
from .manifest import FileEntry, Manifest
from .walk import DEFAULT_EXCLUDES, walk_tree

__all__ = [
    "FileResult",
    "VerificationReport",
    "verify_body",
    "verify_entry",
    "verify_tree",
]

#: Per-file outcomes. Ordered by severity for reporting.
STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_MISSING = "missing"
STATUS_UNREADABLE = "unreadable"
STATUS_SIZE_MISMATCH = "size-mismatch"
STATUS_HASH_MISMATCH = "hash-mismatch"

_FAILURE_STATUSES = frozenset(
    {STATUS_MISSING, STATUS_UNREADABLE, STATUS_SIZE_MISMATCH, STATUS_HASH_MISMATCH}
)


@dataclass(frozen=True)
class FileResult:
    """The outcome of checking one entry."""

    relpath: str
    status: str
    expected_md5: str
    expected_size: int
    actual_md5: str | None = None
    actual_size: int | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status not in _FAILURE_STATUSES

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "relpath": self.relpath,
            "status": self.status,
            "expected_md5": self.expected_md5,
            "expected_size": self.expected_size,
        }
        if self.actual_md5 is not None:
            out["actual_md5"] = self.actual_md5
        if self.actual_size is not None:
            out["actual_size"] = self.actual_size
        if self.detail is not None:
            out["detail"] = self.detail
        return out


@dataclass
class VerificationReport:
    """Everything one verification run established."""

    root: Path
    hash_algorithm: str
    declared_body_hash: str
    recomputed_body_hash: str
    layer: str = "all"
    results: list[FileResult] = field(default_factory=list)
    #: Files present under *root* but absent from the manifest. Only populated
    #: when the caller asked, since it costs a tree walk.
    extra_files: list[str] = field(default_factory=list)
    #: Populated when a subset was checked (registry design §6 "tier2b-sampled").
    sample_size: int | None = None
    provenance_type: str | None = None
    retrieved: str | None = None
    used_source_layout: bool = False

    # -- verdicts ----------------------------------------------------------- #

    @property
    def body_hash_ok(self) -> bool:
        return self.declared_body_hash == self.recomputed_body_hash

    @property
    def failures(self) -> list[FileResult]:
        return [result for result in self.results if not result.ok]

    @property
    def checked(self) -> int:
        return sum(1 for result in self.results if result.status != STATUS_SKIPPED)

    @property
    def ok(self) -> bool:
        """True when nothing checked disagreed with the manifest.

        Extra files do **not** make this false: a manifest describes a set of
        files, not the absence of others, and a workspace legitimately contains
        derived outputs alongside imported data. ``chiro verify --strict``
        surfaces them as a failure when a caller wants exact correspondence.
        """
        return self.body_hash_ok and not self.failures

    @property
    def sampled(self) -> bool:
        return self.sample_size is not None

    @property
    def verification_level(self) -> str:
        """The registry design's vocabulary for what this run established.

        ``tier1`` — self-consistency only. ``tier2b-full`` / ``tier2b-sampled`` —
        bytes checked, wholly or in part (registry design §6). Tier 2a is an API
        cross-check against a repository and is not something a local verifier can
        do, so it never appears here.
        """
        if self.layer == "2" or self.checked == 0:
            return "tier1"
        return "tier2b-sampled" if self.sampled else "tier2b-full"

    # -- reporting ---------------------------------------------------------- #

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for result in self.results:
            tally[result.status] = tally.get(result.status, 0) + 1
        return dict(sorted(tally.items()))

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "root": str(self.root),
            "layer": self.layer,
            "verification_level": self.verification_level,
            "hash_algorithm": self.hash_algorithm,
            "body_hash_ok": self.body_hash_ok,
            "declared_body_hash": self.declared_body_hash,
            "recomputed_body_hash": self.recomputed_body_hash,
            "nfiles": len(self.results),
            "checked": self.checked,
            "sample_size": self.sample_size,
            "counts": self.counts(),
            "provenance_type": self.provenance_type,
            "retrieved": self.retrieved,
            "used_source_layout": self.used_source_layout,
            "failures": [result.to_dict() for result in self.failures],
            "extra_files": self.extra_files,
        }

    def explain(self) -> list[str]:
        """Human-readable interpretation, including the drift caveat."""
        lines: list[str] = []
        if not self.body_hash_ok:
            lines.append(
                f"Layer 2 FAILED: body_hash declares {self.declared_body_hash}, "
                f"recomputed {self.recomputed_body_hash}. The files array does not match "
                f"the hash over it — the manifest itself is damaged."
            )
        else:
            lines.append(f"Layer 2 ok: body_hash {self.declared_body_hash} matches.")

        if self.layer == "2":
            lines.append("Layer 1 not attempted (--layer 2).")
        else:
            tally = self.counts()
            described = ", ".join(f"{count} {status}" for status, count in tally.items())
            scope = (
                f"{self.checked} of {len(self.results)} files (sampled)"
                if self.sampled
                else f"{self.checked} file(s)"
            )
            lines.append(f"Layer 1 checked {scope}: {described or 'nothing'}.")

        # Any of these three is a plausible upstream mutation, not just a differing
        # digest: an edited file usually changes size too, and a withdrawn file goes
        # missing. Only flagging hash mismatches would leave the two commonest
        # shapes of real drift unexplained.
        drift_shaped = [
            result
            for result in self.failures
            if result.status in (STATUS_HASH_MISMATCH, STATUS_SIZE_MISMATCH, STATUS_MISSING)
        ]
        if drift_shaped and self.provenance_type == PROVENANCE_OBSERVED:
            retrieved = f" retrieved {self.retrieved}" if self.retrieved else ""
            lines.append(
                f"NOTE: this is an observed-download manifest{retrieved}. These mismatches "
                f"most likely mean the upstream record mutated since then rather than that "
                f"anything is corrupt — changed bytes, a changed size, or a withdrawn file. "
                f"The manifest remains a valid record of the bytes that were analysed; see "
                f"the registry design note §7b. `chiro capture` the current state and "
                f"`chiro diff` the two to get a drift report."
            )
        if self.extra_files:
            lines.append(
                f"{len(self.extra_files)} file(s) under the root are not in the manifest "
                f"(not a failure unless --strict)."
            )
        lines.append(f"Verification level: {self.verification_level}.")
        return lines


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #


def verify_body(manifest: Manifest, declared_body_hash: str | None = None) -> bool:
    """Layer 2 on its own.

    A manifest loaded through :meth:`Manifest.load` has already had this checked
    (the loader refuses a mismatch), so this is for callers who deliberately
    loaded with ``check_body_hash=False`` in order to inspect a damaged file.
    """
    recomputed = compute_body_hash(
        [entry.to_dict() for entry in manifest.files], manifest.hash_algorithm
    )
    return recomputed == (declared_body_hash or manifest.body_hash)


def _resolve_on_disk(target: Path) -> Path:
    """Fall back to the NFD spelling of a path when the NFC one is absent.

    Manifest paths are NFC (spec §3.2), but a checkout on macOS materialises NFD
    filenames, so opening the manifest's own spelling raises
    ``FileNotFoundError`` for every accented name. Reporting all of them as
    "missing" would be technically true and completely unhelpful.
    """
    if target.exists():
        return target
    decomposed = unicodedata.normalize("NFD", str(target))
    if decomposed != str(target):
        candidate = Path(decomposed)
        if candidate.exists():
            return candidate
    return target


def verify_entry(
    entry: FileEntry,
    path: str | os.PathLike[str],
    algorithm: str = "md5",
) -> FileResult:
    """Check one file's bytes against one entry.

    Size is compared first because it is a free ``stat`` and catches truncated
    or plainly-wrong files without reading gigabytes to reach the same
    conclusion.
    """
    target = _resolve_on_disk(Path(path))
    try:
        info = target.stat()
    except FileNotFoundError:
        return FileResult(entry.relpath, STATUS_MISSING, entry.md5, entry.size)
    except OSError as exc:
        return FileResult(
            entry.relpath, STATUS_UNREADABLE, entry.md5, entry.size, detail=exc.strerror
        )

    if info.st_size != entry.size:
        return FileResult(
            entry.relpath,
            STATUS_SIZE_MISMATCH,
            entry.md5,
            entry.size,
            actual_size=info.st_size,
        )

    try:
        digest = hash_file(target, algorithm)
    except OSError as exc:
        return FileResult(
            entry.relpath, STATUS_UNREADABLE, entry.md5, entry.size, detail=exc.strerror
        )

    status = STATUS_OK if digest == entry.md5 else STATUS_HASH_MISMATCH
    return FileResult(
        entry.relpath, status, entry.md5, entry.size, actual_md5=digest, actual_size=info.st_size
    )


def verify_tree(
    manifest: Manifest,
    root: str | os.PathLike[str],
    *,
    layer: str = "all",
    use_source_layout: bool = False,
    sample: int | None = None,
    sample_fraction: float | None = None,
    check_extra: bool = False,
    jobs: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> VerificationReport:
    """Verify a materialised tree against *manifest*.

    *use_source_layout* checks files where they sit in a **deposit** (by
    ``source_path``) rather than where they belong in a workspace (by
    ``relpath``) — what you want immediately after a download, before anything
    has been reorganised.

    Sampling picks a deterministic subset, seeded from the manifest's
    ``body_hash``. Determinism matters: a sampled verification that checks
    different files each run cannot be reproduced or audited, which would defeat
    the purpose of recording ``tier2b-sampled`` as an assurance level.
    """
    if layer not in ("all", "1", "2"):
        raise ValueError(f"layer must be 'all', '1' or '2', got {layer!r}")

    base = Path(root)
    report = VerificationReport(
        root=base,
        hash_algorithm=manifest.hash_algorithm,
        declared_body_hash=manifest.body_hash,
        recomputed_body_hash=compute_body_hash(
            [entry.to_dict() for entry in manifest.files], manifest.hash_algorithm
        ),
        layer=layer,
        provenance_type=manifest.provenance_type,
        retrieved=manifest.retrieved,
        used_source_layout=use_source_layout,
    )

    if layer == "2":
        report.results = [
            FileResult(entry.relpath, STATUS_SKIPPED, entry.md5, entry.size)
            for entry in manifest.files
        ]
        return report

    selected = _select(manifest.files, sample, sample_fraction, manifest.body_hash)
    if len(selected) != len(manifest.files):
        report.sample_size = len(selected)
    chosen = {entry.relpath for entry in selected}

    def relative(entry: FileEntry) -> str:
        return entry.source if use_source_layout else entry.relpath

    total = len(selected)
    completed = 0
    results: dict[str, FileResult] = {}

    workers = jobs if jobs is not None else default_jobs()
    workers = max(1, min(workers, max(1, total)))

    if workers == 1 or total <= 1:
        for entry in selected:
            results[entry.relpath] = verify_entry(
                entry, base / relative(entry), manifest.hash_algorithm
            )
            completed += 1
            if progress:
                progress(completed, total)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    verify_entry, entry, base / relative(entry), manifest.hash_algorithm
                ): entry
                for entry in selected
            }
            for future in as_completed(futures):
                entry = futures[future]
                results[entry.relpath] = future.result()
                completed += 1
                if progress:
                    progress(completed, total)

    report.results = [
        results.get(entry.relpath, FileResult(entry.relpath, STATUS_SKIPPED, entry.md5, entry.size))
        if entry.relpath in chosen
        else FileResult(entry.relpath, STATUS_SKIPPED, entry.md5, entry.size)
        for entry in manifest.files
    ]

    if check_extra:
        walked = walk_tree(base, excludes=DEFAULT_EXCLUDES, skip_unreadable=True)
        expected = {relative(entry) for entry in manifest.files}
        report.extra_files = sorted(set(walked.paths) - expected)

    return report


def _select(
    entries: Sequence[FileEntry],
    sample: int | None,
    sample_fraction: float | None,
    seed: str,
) -> list[FileEntry]:
    """Choose which entries to check, deterministically."""
    if sample is not None and sample_fraction is not None:
        raise ValueError("give sample or sample_fraction, not both")
    if sample is None and sample_fraction is None:
        return list(entries)

    if sample_fraction is not None:
        if not 0 < sample_fraction <= 1:
            raise ValueError(f"sample_fraction must be in (0, 1], got {sample_fraction}")
        count = max(1, round(len(entries) * sample_fraction))
    else:
        assert sample is not None
        if sample <= 0:
            raise ValueError(f"sample must be positive, got {sample}")
        count = sample

    if count >= len(entries):
        return list(entries)
    # Seeded from body_hash so that "verify 10% of this manifest" always means
    # the same 10%, on any machine, in any run.
    return sorted(
        random.Random(seed).sample(list(entries), count),
        key=lambda entry: entry.relpath,
    )
