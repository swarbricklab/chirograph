"""Building manifests: from a directory tree, or from a DVC cache.

The writer is the highest-value artifact in the plan — usable for a real deposit
before any consumer exists — so its job is narrow and its failure modes loud:
walk, hash, emit, and refuse to produce a manifest that quietly misdescribes the
tree.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .constants import DEFAULT_HASH_ALGORITHM, PROVENANCE_OBSERVED, PROVENANCE_SELF
from .errors import ChirographError
from .hashing import default_jobs, hash_files
from .manifest import FileEntry, Manifest
from .rewrite import RewriteRule, apply_rules
from .timestamps import now_timestamp
from .version import GENERATOR
from .walk import WalkReport, walk_tree

__all__ = ["BuildResult", "build_from_directory", "capture_from_directory"]


@dataclass
class BuildResult:
    """A built manifest plus everything the walk wants to tell you about it."""

    manifest: Manifest
    walk: WalkReport
    #: Bytes hashed. Differs from ``manifest.size`` only if something raced.
    bytes_hashed: int = 0
    notes: list[str] = field(default_factory=list)


def build_from_directory(
    root: str | os.PathLike[str],
    *,
    provenance_type: str = PROVENANCE_SELF,
    hash_algorithm: str = DEFAULT_HASH_ALGORITHM,
    source_url: str | None = None,
    record_accession: str | None = None,
    concept: str | None = None,
    record_version: str | None = None,
    created: str | None = None,
    retrieved: str | None = None,
    generator: str | None = None,
    relpath_from_source: Sequence[RewriteRule] = (),
    source_from_relpath: Sequence[RewriteRule] = (),
    excludes: Iterable[str] | None = None,
    include_hidden: bool = True,
    follow_symlinks: bool = False,
    skip_unreadable: bool = False,
    jobs: int | None = None,
    progress: Callable[[int, int, int, int], None] | None = None,
) -> BuildResult:
    """Walk *root*, hash every file, and return a manifest describing it.

    Exactly one rewrite direction may be given. Supplying both would leave the
    walked path meaning neither thing, and the resulting manifest would route
    fetches through a layout that exists nowhere.

    *progress* is called as ``(files_done, files_total, bytes_done,
    bytes_total)`` from a worker thread.
    """
    if relpath_from_source and source_from_relpath:
        raise ChirographError(
            "give either relpath_from_source or source_from_relpath, not both: the walked "
            "tree is one layout or the other, and with both rules applied the walked path "
            "would mean neither"
        )

    base = Path(root)
    report = walk_tree(
        base,
        excludes=excludes,
        include_hidden=include_hidden,
        follow_symlinks=follow_symlinks,
        skip_unreadable=skip_unreadable,
    )

    # Read through `disk_paths`, not `paths`: a tree written on macOS holds NFD
    # filenames, and the NFC spelling that goes into the manifest does not exist
    # on disk.
    sizes = [(base / on_disk).stat().st_size for on_disk in report.disk_paths]
    total_bytes = sum(sizes)
    done = _ProgressCounter(len(report.paths), total_bytes, progress)

    digests = hash_files(
        [base / on_disk for on_disk in report.disk_paths],
        hash_algorithm,
        jobs=jobs if jobs is not None else default_jobs(),
        on_done=done.record,
    )

    entries: list[FileEntry] = []
    for walked, digest, size in zip(report.paths, digests, sizes, strict=True):
        relpath, source_path = _assign_paths(walked, relpath_from_source, source_from_relpath)
        entries.append(
            FileEntry(
                relpath=relpath,
                md5=digest,
                size=size,
                source_path=source_path,
            )
        )

    manifest = Manifest(
        provenance_type=provenance_type,
        files=entries,
        hash_algorithm=hash_algorithm,
        source_url=source_url,
        record_accession=record_accession,
        concept=concept,
        record_version=record_version,
        created=created or now_timestamp(),
        retrieved=retrieved,
        generator=generator if generator is not None else GENERATOR,
    )

    result = BuildResult(manifest=manifest, walk=report, bytes_hashed=total_bytes)
    if report.empty_dirs:
        result.notes.append(
            f"{len(report.empty_dirs)} empty directory/directories are not representable in a "
            f"manifest and will not be recreated on materialisation"
        )
    if report.renormalised:
        result.notes.append(
            f"{len(report.renormalised)} filename(s) were not in Unicode NFC and have been "
            f"normalised; the manifest paths differ from the on-disk bytes for these"
        )
    return result


def capture_from_directory(
    root: str | os.PathLike[str],
    *,
    source_url: str,
    retrieved: str | None = None,
    **kwargs: object,
) -> BuildResult:
    """Build an ``observed-download`` manifest for a tree that was fetched.

    A thin preset over :func:`build_from_directory`, but a worthwhile one: it
    makes ``source_url`` mandatory and defaults ``retrieved`` to now, which are
    exactly the two ``opt*`` fields spec §5 requires for this provenance type and
    exactly the two a hurried user forgets.

    ``retrieved`` defaults to *now*, which is a lie if the download finished
    yesterday — pass it explicitly when capturing after the fact.
    """
    if not source_url:
        raise ChirographError(
            "capture requires source_url: an observed-download manifest whose whole claim is "
            "'what I retrieved from somewhere' must say where"
        )
    kwargs.pop("provenance_type", None)
    return build_from_directory(
        root,
        provenance_type=PROVENANCE_OBSERVED,
        source_url=source_url,
        retrieved=retrieved or now_timestamp(),
        **kwargs,  # type: ignore[arg-type]
    )


def _assign_paths(
    walked: str,
    relpath_from_source: Sequence[RewriteRule],
    source_from_relpath: Sequence[RewriteRule],
) -> tuple[str, str | None]:
    """Map one walked path onto ``(relpath, source_path)``.

    ``source_path`` comes back as ``None`` whenever it would equal ``relpath``,
    which keeps the terse DVC-shaped common case terse (spec §3.2) — including
    when a rewrite rule was supplied but did not match this particular file.
    """
    if relpath_from_source:
        source_path: str | None = walked
        relpath = apply_rules(relpath_from_source, walked, field="relpath")
    elif source_from_relpath:
        relpath = walked
        source_path = apply_rules(source_from_relpath, walked, field="source_path")
    else:
        return walked, None
    if source_path == relpath:
        return relpath, None
    return relpath, source_path


class _ProgressCounter:
    """Thread-safe progress accumulator for :func:`hash_files`."""

    def __init__(
        self,
        total_files: int,
        total_bytes: int,
        callback: Callable[[int, int, int, int], None] | None,
    ) -> None:
        import threading

        self._lock = threading.Lock()
        self._files = 0
        self._bytes = 0
        self._total_files = total_files
        self._total_bytes = total_bytes
        self._callback = callback

    def record(self, path: Path, _digest: str) -> None:
        if self._callback is None:
            return
        try:
            size = path.stat().st_size
        except OSError:  # pragma: no cover
            size = 0
        with self._lock:
            self._files += 1
            self._bytes += size
            self._callback(self._files, self._total_files, self._bytes, self._total_bytes)
