"""Manifest comparison — the basis of a drift report (registry design §7b)."""

from __future__ import annotations

from chirograph.constants import PROVENANCE_OBSERVED
from chirograph.diff import diff_manifests
from chirograph.manifest import FileEntry, Manifest

A = "b1946ac92492d2347c6235b4d2611184"
B = "591785b794601e212b260e25925636fd"
C = "5eb63bbbe01eeed093cb22bb8f5acdc3"


def manifest(*entries: FileEntry, **header: object) -> Manifest:
    return Manifest(files=list(entries), created="2026-07-01T00:00:00Z", **header)


def test_identical_manifests() -> None:
    one = manifest(FileEntry("a.txt", A, 6))
    result = diff_manifests(one, manifest(FileEntry("a.txt", A, 6)))
    assert result.identical
    assert "identical" in result.summary()


def test_content_change_is_the_drift_signal() -> None:
    result = diff_manifests(manifest(FileEntry("a.txt", A, 6)), manifest(FileEntry("a.txt", B, 7)))
    assert [c.kind for c in result.changed] == ["content"]
    assert result.content_changed
    assert result.changed[0].old.md5 == A
    assert result.changed[0].new.md5 == B


def test_added_and_removed() -> None:
    result = diff_manifests(manifest(FileEntry("a.txt", A, 6)), manifest(FileEntry("b.txt", B, 6)))
    assert [e.relpath for e in result.added] == ["b.txt"]
    assert [e.relpath for e in result.removed] == ["a.txt"]


def test_pure_reorganisation_reads_as_a_rename() -> None:
    """'The deposit was restructured' is a different fact from 'it was replaced'."""
    result = diff_manifests(
        manifest(FileEntry("flat_a.txt", A, 6)), manifest(FileEntry("sampleA/a.txt", A, 6))
    )
    assert result.renamed == [("flat_a.txt", "sampleA/a.txt")]
    assert not result.added
    assert not result.removed
    assert not result.content_changed


def test_partial_rename_leaves_the_rest_as_add_remove() -> None:
    result = diff_manifests(
        manifest(FileEntry("x.txt", A, 6), FileEntry("y.txt", B, 6)),
        manifest(FileEntry("moved/x.txt", A, 6), FileEntry("z.txt", C, 6)),
    )
    assert result.renamed == [("x.txt", "moved/x.txt")]
    assert [e.relpath for e in result.added] == ["z.txt"]
    assert [e.relpath for e in result.removed] == ["y.txt"]


def test_metadata_only_change() -> None:
    result = diff_manifests(
        manifest(FileEntry("a.txt", A, 6)),
        manifest(FileEntry("a.txt", A, 6, source_path="deposit_a.txt")),
    )
    assert [c.kind for c in result.changed] == ["metadata"]
    assert not result.content_changed


def test_same_digest_different_size_is_a_broken_manifest_not_drift() -> None:
    result = diff_manifests(manifest(FileEntry("a.txt", A, 6)), manifest(FileEntry("a.txt", A, 99)))
    assert [c.kind for c in result.changed] == ["size-only"]


def test_header_changes_are_reported() -> None:
    result = diff_manifests(
        manifest(FileEntry("a.txt", A, 6), source_url="https://old/"),
        manifest(FileEntry("a.txt", A, 6), source_url="https://new/"),
    )
    assert result.header_changes["source_url"] == ("https://old/", "https://new/")


def test_cross_algorithm_diff_does_not_pretend_to_compare_digests() -> None:
    md5 = manifest(FileEntry("a.txt", A, 6))
    sha = manifest(
        FileEntry("a.txt", "a" * 64, 6),
        hash_algorithm="sha256",
    )
    result = diff_manifests(md5, sha)
    assert [c.kind for c in result.changed] == ["metadata"]
    assert not result.renamed


def test_observed_snapshots_capture_a_realistic_drift_scenario() -> None:
    """GEO mutating in place: one file edited, one withdrawn, one added."""
    before = manifest(
        FileEntry("counts.mtx", A, 100),
        FileEntry("old_annotation.tsv", B, 50),
        provenance_type=PROVENANCE_OBSERVED,
        source_url="https://geo/GSE1/",
        retrieved="2026-01-01T00:00:00Z",
    )
    after = manifest(
        FileEntry("counts.mtx", C, 120),
        FileEntry("new_annotation.tsv", "d" * 32, 60),
        provenance_type=PROVENANCE_OBSERVED,
        source_url="https://geo/GSE1/",
        retrieved="2026-06-01T00:00:00Z",
    )
    result = diff_manifests(before, after)
    assert len(result.content_changed) == 1
    assert [e.relpath for e in result.removed] == ["old_annotation.tsv"]
    assert [e.relpath for e in result.added] == ["new_annotation.tsv"]
    assert result.header_changes["retrieved"][1] == "2026-06-01T00:00:00Z"


def test_diff_json_is_serialisable() -> None:
    import json

    result = diff_manifests(manifest(FileEntry("a.txt", A, 6)), manifest(FileEntry("a.txt", B, 6)))
    assert json.loads(json.dumps(result.to_dict()))["identical"] is False
