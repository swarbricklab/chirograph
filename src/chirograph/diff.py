"""Comparing two manifests.

The obvious use is reviewing a change before committing it. The interesting one
is **drift**: registry design §7b describes consumers detecting that an upstream
dataset has mutated, and the useful unit of that report is not "something
changed" but "these three files changed content, one was removed, two are new".

Diffing two ``observed-download`` manifests of the same accession taken at
different times is exactly the auto-versioning signal the design note wants, and
it needs no registry to be useful today.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .manifest import FileEntry, Manifest

__all__ = ["EntryChange", "ManifestDiff", "diff_manifests"]


@dataclass(frozen=True)
class EntryChange:
    """One file that differs between two manifests."""

    relpath: str
    kind: str  # "content" | "size-only" | "moved" | "metadata"
    old: FileEntry | None
    new: FileEntry | None

    def to_dict(self) -> dict[str, object]:
        def describe(entry: FileEntry | None) -> dict[str, object] | None:
            if entry is None:
                return None
            out: dict[str, object] = {"md5": entry.md5, "size": entry.size}
            if entry.source_path is not None:
                out["source_path"] = entry.source_path
            return out

        return {
            "relpath": self.relpath,
            "kind": self.kind,
            "old": describe(self.old),
            "new": describe(self.new),
        }


@dataclass
class ManifestDiff:
    """The difference between two manifests, keyed on ``relpath``."""

    added: list[FileEntry] = field(default_factory=list)
    removed: list[FileEntry] = field(default_factory=list)
    changed: list[EntryChange] = field(default_factory=list)
    header_changes: dict[str, tuple[object, object]] = field(default_factory=dict)
    #: Content that exists in both manifests under different names. Detected by
    #: digest, so a pure reorganisation shows up as a rename rather than as a
    #: wholesale add-plus-remove — which is the difference between "the deposit
    #: was restructured" and "the deposit was replaced".
    renamed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not (self.added or self.removed or self.changed or self.renamed)

    @property
    def content_changed(self) -> list[EntryChange]:
        """Changes where the bytes differ — the drift-relevant subset."""
        return [change for change in self.changed if change.kind in ("content", "size-only")]

    def to_dict(self) -> dict[str, object]:
        return {
            "identical": self.identical,
            "added": [{"relpath": e.relpath, "md5": e.md5, "size": e.size} for e in self.added],
            "removed": [{"relpath": e.relpath, "md5": e.md5, "size": e.size} for e in self.removed],
            "changed": [change.to_dict() for change in self.changed],
            "renamed": [{"from": old, "to": new} for old, new in self.renamed],
            "header_changes": {
                key: {"old": old, "new": new} for key, (old, new) in self.header_changes.items()
            },
        }

    def summary(self) -> str:
        if self.identical:
            return "manifests describe identical file sets"
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} added")
        if self.removed:
            parts.append(f"{len(self.removed)} removed")
        if self.content_changed:
            parts.append(f"{len(self.content_changed)} content-changed")
        metadata = len(self.changed) - len(self.content_changed)
        if metadata:
            parts.append(f"{metadata} metadata-changed")
        if self.renamed:
            parts.append(f"{len(self.renamed)} renamed")
        return ", ".join(parts)


_HEADER_FIELDS = (
    "schema_version",
    "hash_algorithm",
    "provenance_type",
    "record_accession",
    "source_url",
    "created",
    "retrieved",
    "generator",
)


def diff_manifests(old: Manifest, new: Manifest) -> ManifestDiff:
    """Compare two manifests.

    Comparing digests across differing ``hash_algorithm`` values is meaningless,
    so in that case every entry is reported as added/removed rather than
    pretending to compare an md5 with a sha256.
    """
    result = ManifestDiff()

    for name in _HEADER_FIELDS:
        before, after = getattr(old, name), getattr(new, name)
        if before != after:
            result.header_changes[name] = (before, after)

    comparable = old.hash_algorithm == new.hash_algorithm
    old_index = old.by_relpath
    new_index = new.by_relpath

    for relpath in sorted(new_index.keys() - old_index.keys()):
        result.added.append(new_index[relpath])
    for relpath in sorted(old_index.keys() - new_index.keys()):
        result.removed.append(old_index[relpath])

    for relpath in sorted(old_index.keys() & new_index.keys()):
        before, after = old_index[relpath], new_index[relpath]
        if not comparable:
            result.changed.append(EntryChange(relpath, "metadata", before, after))
            continue
        if before.md5 != after.md5:
            kind = "content"
        elif before.size != after.size:
            # Same digest, different size: not drift, a broken manifest.
            kind = "size-only"
        elif (before.source_path, before.isexec, before.version_id, before.etag) != (
            after.source_path,
            after.isexec,
            after.version_id,
            after.etag,
        ):
            kind = "metadata"
        else:
            continue
        result.changed.append(EntryChange(relpath, kind, before, after))

    if comparable and (result.added or result.removed):
        removed_by_digest: dict[str, list[FileEntry]] = {}
        for entry in result.removed:
            removed_by_digest.setdefault(entry.md5, []).append(entry)
        still_added: list[FileEntry] = []
        matched: set[str] = set()
        for entry in result.added:
            candidates = removed_by_digest.get(entry.md5)
            if candidates:
                source = candidates.pop(0)
                result.renamed.append((source.relpath, entry.relpath))
                matched.add(source.relpath)
            else:
                still_added.append(entry)
        result.added = still_added
        result.removed = [entry for entry in result.removed if entry.relpath not in matched]
        result.renamed.sort()

    return result
