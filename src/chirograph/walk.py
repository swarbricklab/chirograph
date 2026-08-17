"""Deterministic directory walking for the manifest writer.

Everything here exists to make one guarantee: **the set of files in the manifest
is exactly the set of regular files under the root, and anything not included is
reported rather than silently dropped.** A manifest that quietly omits a file is
worse than no manifest, because verification will pass against an incomplete
picture.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from itertools import pairwise
from pathlib import Path

from .errors import ChirographError, PathSafetyError
from .paths import nfc, validate_path

__all__ = [
    "DEFAULT_EXCLUDES",
    "WalkReport",
    "matches_any",
    "walk_tree",
]

#: Excluded by default. Tool metadata and manifests themselves — a manifest
#: written into the tree it describes would otherwise either be in its own file
#: list (impossible: writing it changes its hash) or make the tree unverifiable
#: the moment it lands.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git",
    ".dvc",
    ".dvcignore",
    ".gitignore",
    ".gitattributes",
    ".DS_Store",
    "._*",
    "__pycache__",
    ".ipynb_checkpoints",
    "*.chiro.json",
    "*.chiro.json.tmp",
)


@dataclass
class WalkReport:
    """What the walk found, and everything it declined to include."""

    root: Path
    #: Relative POSIX paths, NFC-normalised, sorted. These are *manifest* paths.
    paths: list[str] = field(default_factory=list)
    #: The corresponding **on-disk** relative paths, parallel to :attr:`paths`.
    #: Identical to ``paths`` except where a filename was not in NFC. Reading a
    #: file must use these — a tree written on macOS holds NFD names, and opening
    #: the NFC spelling raises ``FileNotFoundError``.
    disk_paths: list[str] = field(default_factory=list)
    #: ``(relative path, reason)`` for each entry deliberately not included.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: Directories with no files anywhere beneath them. The format cannot
    #: represent an empty directory (nor can DVC), so they vanish on
    #: materialisation. Surfaced because for imaging/spatial data an empty
    #: directory is occasionally load-bearing.
    empty_dirs: list[str] = field(default_factory=list)
    #: Non-NFC names encountered and folded. Worth reporting: it means the tree
    #: was written on macOS, and the manifest's paths will not be byte-identical
    #: to the names on that disk.
    renormalised: list[str] = field(default_factory=list)
    #: Names :func:`chirograph.paths.validate_path` refuses, so no manifest could
    #: list them — non-UTF-8 bytes most often, but also backslashes, control
    #: characters and over-limit components. Recorded only when
    #: ``skip_unrepresentable`` was set. Kept in their own list rather
    #: than only in :attr:`skipped` so a caller can act on them — they are by
    #: definition files no manifest could list, which is what ``verify --strict``
    #: needs to know.
    unrepresentable: list[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum((self.root / path).stat().st_size for path in self.disk_paths)

    def items(self) -> list[tuple[str, str]]:
        """``(manifest path, on-disk path)`` pairs, in manifest order."""
        return list(zip(self.paths, self.disk_paths, strict=True))

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "nfiles": len(self.paths),
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped],
            "empty_dirs": self.empty_dirs,
            "renormalised": self.renormalised,
            "unrepresentable": self.unrepresentable,
        }


def matches_any(relpath: str, patterns: Sequence[str]) -> bool:
    """Whether *relpath* matches any exclude pattern.

    Two spellings, chosen so the common cases need no thought:

    * a pattern containing ``/`` is matched against the whole relative path
      (``sample*/raw/*``);
    * a pattern without ``/`` is matched against *every* path component, so
      ``.git`` excludes the directory wherever it sits and ``*.bam`` excludes by
      basename at any depth.
    """
    for pattern in patterns:
        if "/" in pattern:
            if fnmatch(relpath, pattern):
                return True
        elif any(fnmatch(component, pattern) for component in relpath.split("/")):
            return True
    return False


def walk_tree(
    root: str | os.PathLike[str],
    *,
    excludes: Iterable[str] | None = None,
    include_hidden: bool = True,
    follow_symlinks: bool = False,
    skip_unreadable: bool = False,
    skip_unrepresentable: bool = False,
) -> WalkReport:
    """Walk *root*, returning a deterministic :class:`WalkReport`.

    *excludes* replaces :data:`DEFAULT_EXCLUDES` entirely when given, so a caller
    who wants both must concatenate — better than a flag whose meaning depends on
    whether another flag was passed.

    Symlinks: directory symlinks are **not** followed by default, because a link
    pointing at an ancestor turns the walk into an infinite one and a link
    pointing outside the root silently pulls in bytes the caller did not mean to
    publish. File symlinks *are* included, hashed by their target's content,
    which is what DVC does and what a user means by "this file".

    *skip_unrepresentable* records a filename that cannot appear in a manifest as
    skipped rather than raising. That is any name :func:`validate_path` refuses —
    most often non-UTF-8 bytes, which Python surfaces as surrogates, but also a
    legal-on-Linux name containing a backslash or a control character, or an
    over-limit component. Off by default, because when *building* a manifest such a
    file must be a loud error — but a caller that is only *enumerating* names, as
    ``chiro verify --check-extra`` does, should not discard an otherwise-passing
    verification because of a stray file the manifest could never have listed.
    """
    base = Path(root)
    if not base.is_dir():
        raise ChirographError(f"{base} is not a directory")
    patterns = tuple(DEFAULT_EXCLUDES if excludes is None else excludes)

    report = WalkReport(root=base)
    #: (manifest path, on-disk path) accumulated during the walk.
    pairs: list[tuple[str, str]] = []
    seen_dirs: set[tuple[int, int]] = set()

    for dirpath, dirnames, filenames in os.walk(base, followlinks=follow_symlinks):
        current = Path(dirpath)
        rel_dir = current.relative_to(base).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir

        # Prune excluded and hidden directories in place so os.walk skips them.
        kept_dirs = []
        for name in sorted(dirnames):
            child_rel = f"{rel_dir}/{name}" if rel_dir else name
            if matches_any(child_rel, patterns):
                report.skipped.append((child_rel + "/", "excluded by pattern"))
                continue
            if not include_hidden and name.startswith("."):
                report.skipped.append((child_rel + "/", "hidden"))
                continue
            child = current / name
            if child.is_symlink() and not follow_symlinks:
                report.skipped.append((child_rel + "/", "symlink to a directory (not followed)"))
                continue
            kept_dirs.append(name)
        # Descend into real directories before symlinked ones. This only matters
        # when following symlinks and two names reach the same directory: the
        # identity guard below keeps whichever is visited first, and a manifest
        # should describe the tree by its real names rather than by whichever alias
        # happened to sort earlier. Traversal order only; output is sorted at the
        # end, so this does not affect the manifest's determinism.
        kept_dirs.sort(key=lambda name: ((current / name).is_symlink(), name))
        dirnames[:] = kept_dirs

        if follow_symlinks:
            # `os.walk(followlinks=True)` has no cycle protection at all, so a link
            # pointing at an ancestor recurses until the path length limit. Track
            # visited (device, inode) pairs and stop at any repeat.
            #
            # This deduplicates by identity rather than only breaking true cycles,
            # so a link to a *sibling* subtree is also skipped: its files are
            # already in the manifest under their real names, and adding them a
            # second time would give two relpaths for one file with no way to tell
            # which one a consumer should materialise. The reason string says
            # "already included" rather than "cycle" because both cases land here
            # and only one of them is a cycle.
            try:
                info = current.stat()
            except OSError:  # pragma: no cover - raced away
                continue
            key = (info.st_dev, info.st_ino)
            if key in seen_dirs:
                report.skipped.append(
                    (rel_dir or ".", "already included under another path (symlink)")
                )
                dirnames[:] = []
                continue
            seen_dirs.add(key)

        found_here = False
        for name in sorted(filenames):
            child_rel = f"{rel_dir}/{name}" if rel_dir else name
            if matches_any(child_rel, patterns):
                report.skipped.append((child_rel, "excluded by pattern"))
                continue
            if not include_hidden and name.startswith("."):
                report.skipped.append((child_rel, "hidden"))
                continue

            child = current / name
            try:
                info = child.stat()  # follows the link: we want the target
            except OSError as exc:
                if child.is_symlink():
                    report.skipped.append((child_rel, f"broken symlink ({exc.strerror})"))
                    continue
                if skip_unreadable:
                    report.skipped.append((child_rel, f"unreadable ({exc.strerror})"))
                    continue
                raise ChirographError(
                    f"cannot stat {child}: {exc.strerror}. A manifest that omits a file it "
                    f"could not read would be a lie; pass skip_unreadable=True "
                    f"(--skip-unreadable) to record the omission instead."
                ) from exc

            if not stat.S_ISREG(info.st_mode):
                report.skipped.append(
                    (child_rel, f"not a regular file (mode {stat.filemode(info.st_mode)})")
                )
                continue
            if not os.access(child, os.R_OK):
                if skip_unreadable:
                    report.skipped.append((child_rel, "unreadable (permission denied)"))
                    continue
                raise ChirographError(
                    f"cannot read {child}: permission denied. Pass skip_unreadable=True "
                    f"(--skip-unreadable) to record the omission instead."
                )

            normalised = nfc(child_rel)
            if normalised != child_rel:
                report.renormalised.append(child_rel)
            try:
                validate_path(normalised)
            except PathSafetyError as exc:
                if skip_unrepresentable:
                    report.skipped.append((child_rel, f"not representable in a manifest ({exc})"))
                    report.unrepresentable.append(child_rel)
                    # The directory is not empty — it holds a file we declined to
                    # list. Saying otherwise would tell a library caller to expect
                    # nothing here.
                    found_here = True
                    continue
                # Name the file. A filename whose bytes are not valid UTF-8 (which
                # Python surfaces as surrogates) is the realistic case here, and
                # "cannot encode" with no path attached is unactionable when the
                # tree has ten thousand files in it.
                raise PathSafetyError(f"{base / child_rel}: {exc}") from exc
            pairs.append((normalised, child_rel))
            found_here = True

        if rel_dir and not found_here and not dirnames:
            report.empty_dirs.append(rel_dir)

    pairs.sort()
    _reject_normalisation_collisions(pairs)
    report.paths = [manifest_path for manifest_path, _ in pairs]
    report.disk_paths = [disk_path for _, disk_path in pairs]
    report.skipped.sort()
    report.empty_dirs.sort()
    report.renormalised.sort()
    report.unrepresentable.sort()
    return report


def _reject_normalisation_collisions(pairs: list[tuple[str, str]]) -> None:
    """Refuse a tree where two distinct filenames fold to the same NFC path.

    Possible on Linux, which lets both the NFD and NFC spellings of a name exist
    side by side. They would become one ``relpath``, so the manifest would silently
    describe one of the two files and verification would fail against the other.
    Naming both on-disk spellings is the only actionable message.
    """
    for (previous_manifest, previous_disk), (current_manifest, current_disk) in pairwise(pairs):
        if previous_manifest == current_manifest:
            raise ChirographError(
                f"two files normalise to the same manifest path {current_manifest!r}: "
                f"{previous_disk!r} and {current_disk!r}. Unicode NFC folds them together, "
                f"so a manifest could only describe one. Rename one of them before "
                f"building a manifest."
            )
