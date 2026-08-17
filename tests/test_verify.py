"""Verification: Layers 1 and 2, sampling, and the drift interpretation."""

from __future__ import annotations

from pathlib import Path

import pytest

from chirograph.constants import PROVENANCE_OBSERVED
from chirograph.manifest import FileEntry, Manifest
from chirograph.verify import verify_body, verify_entry, verify_tree
from chirograph.writer import build_from_directory

MD5_HELLO = "b1946ac92492d2347c6235b4d2611184"


def test_clean_tree_verifies(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    report = verify_tree(manifest, tree)
    assert report.ok
    assert report.body_hash_ok
    assert report.checked == 3
    assert report.verification_level == "tier2b-full"


def test_layer_2_only_reads_no_data(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    for entry in tree.rglob("*"):
        if entry.is_file():
            entry.unlink()
    report = verify_tree(manifest, tree, layer="2")
    assert report.ok
    assert report.checked == 0
    assert report.verification_level == "tier1"


def test_hash_mismatch_is_detected(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    (tree / "sampleA" / "barcodes.tsv").write_bytes(b"HELLO\n")  # same length!
    report = verify_tree(manifest, tree)
    assert not report.ok
    assert [(r.relpath, r.status) for r in report.failures] == [
        ("sampleA/barcodes.tsv", "hash-mismatch")
    ]
    failure = report.failures[0]
    assert failure.expected_md5 == MD5_HELLO
    assert failure.actual_md5 != MD5_HELLO


def test_size_mismatch_is_detected_without_hashing(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    (tree / "sampleA" / "barcodes.tsv").write_bytes(b"truncated")
    report = verify_tree(manifest, tree)
    failure = report.failures[0]
    assert failure.status == "size-mismatch"
    assert failure.actual_md5 is None  # never hashed


def test_missing_file_is_detected(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    (tree / "sampleA" / "matrix.mtx").unlink()
    report = verify_tree(manifest, tree)
    assert [(r.relpath, r.status) for r in report.failures] == [("sampleA/matrix.mtx", "missing")]


def test_body_hash_damage_is_detected() -> None:
    manifest = Manifest(files=[FileEntry("a.txt", MD5_HELLO, 6)])
    assert verify_body(manifest)
    assert not verify_body(manifest, declared_body_hash="0" * 32)


def test_extra_files_are_reported_but_not_failures(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    (tree / "derived.h5ad").write_bytes(b"x")
    report = verify_tree(manifest, tree, check_extra=True)
    assert report.extra_files == ["derived.h5ad"]
    assert report.ok  # a manifest describes a set, not the absence of others


def test_source_layout_verification(tmp_path: Path) -> None:
    deposit = tmp_path / "deposit"
    deposit.mkdir()
    (deposit / "GSE1_sampleA_x.txt").write_bytes(b"hello\n")
    from chirograph.rewrite import parse_rules

    manifest = build_from_directory(
        deposit, relpath_from_source=parse_rules([r"^GSE1_([^_]+)_(.*)$=>\1/\2"])
    ).manifest
    # relpath layout does not exist on disk...
    assert not verify_tree(manifest, deposit).ok
    # ...but the source layout does.
    assert verify_tree(manifest, deposit, use_source_layout=True).ok


def test_parallel_and_serial_agree(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    serial = verify_tree(manifest, tree, jobs=1)
    parallel = verify_tree(manifest, tree, jobs=4)
    assert [r.to_dict() for r in serial.results] == [r.to_dict() for r in parallel.results]


# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #


def big_manifest(count: int = 50) -> Manifest:
    return Manifest(files=[FileEntry(f"f{i:03d}.dat", MD5_HELLO, 6) for i in range(count)])


def test_sampling_checks_a_subset(tmp_path: Path) -> None:
    manifest = big_manifest()
    for entry in manifest.files:
        (tmp_path / entry.relpath).write_bytes(b"hello\n")
    report = verify_tree(manifest, tmp_path, sample=5)
    assert report.checked == 5
    assert report.sample_size == 5
    assert report.verification_level == "tier2b-sampled"
    assert report.ok


def test_sampling_is_deterministic(tmp_path: Path) -> None:
    """Otherwise 'tier2b-sampled' is not a reproducible or auditable claim."""
    manifest = big_manifest()
    for entry in manifest.files:
        (tmp_path / entry.relpath).write_bytes(b"hello\n")

    def chosen() -> list[str]:
        report = verify_tree(manifest, tmp_path, sample=7)
        return [r.relpath for r in report.results if r.status != "skipped"]

    assert chosen() == chosen()
    assert len(chosen()) == 7


def test_sampling_depends_on_the_manifest_not_the_clock(tmp_path: Path) -> None:
    """Two different manifests should not pick identical indexes by accident."""
    a = big_manifest(50)
    b = Manifest(files=[FileEntry(f"g{i:03d}.dat", MD5_HELLO, 6) for i in range(50)])
    pick_a = {
        r.relpath[1:] for r in verify_tree(a, tmp_path, sample=7).results if r.status != "skipped"
    }
    pick_b = {
        r.relpath[1:] for r in verify_tree(b, tmp_path, sample=7).results if r.status != "skipped"
    }
    assert pick_a != pick_b


def test_sample_fraction(tmp_path: Path) -> None:
    manifest = big_manifest(50)
    report = verify_tree(manifest, tmp_path, sample_fraction=0.2)
    assert report.checked == 10


def test_sample_larger_than_the_manifest_checks_everything(tmp_path: Path) -> None:
    manifest = big_manifest(3)
    report = verify_tree(manifest, tmp_path, sample=99)
    assert report.checked == 3
    assert report.sample_size is None
    assert report.verification_level == "tier2b-full"


def test_conflicting_sample_arguments_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        verify_tree(big_manifest(), tmp_path, sample=1, sample_fraction=0.5)


def test_bad_sample_fraction_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sample_fraction"):
        verify_tree(big_manifest(), tmp_path, sample_fraction=1.5)


def test_bad_layer_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="layer"):
        verify_tree(big_manifest(), tmp_path, layer="3")


# --------------------------------------------------------------------------- #
# Interpretation
# --------------------------------------------------------------------------- #


def test_observed_download_mismatch_is_explained_as_probable_drift(tree: Path) -> None:
    manifest = build_from_directory(
        tree, provenance_type=PROVENANCE_OBSERVED, retrieved="2026-01-01T00:00:00Z"
    ).manifest
    (tree / "sampleA" / "barcodes.tsv").write_bytes(b"HELLO\n")
    explanation = " ".join(verify_tree(manifest, tree).explain())
    assert "upstream record mutated" in explanation
    assert "2026-01-01T00:00:00Z" in explanation


def test_self_deposited_mismatch_gets_no_drift_excuse(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    (tree / "sampleA" / "barcodes.tsv").write_bytes(b"HELLO\n")
    explanation = " ".join(verify_tree(manifest, tree).explain())
    assert "upstream" not in explanation


def test_report_json_is_serialisable(tree: Path) -> None:
    import json

    manifest = build_from_directory(tree).manifest
    payload = verify_tree(manifest, tree).to_dict()
    assert json.loads(json.dumps(payload, default=str))["ok"] is True


def test_verify_entry_on_a_directory_reports_unreadable(tmp_path: Path) -> None:
    (tmp_path / "adir").mkdir()
    result = verify_entry(FileEntry("adir", MD5_HELLO, 6), tmp_path / "adir")
    assert result.status in ("unreadable", "size-mismatch")
    assert not result.ok


def test_nfd_on_disk_is_found_via_fallback(tmp_path: Path) -> None:
    """A manifest built on Linux, verified against a macOS-style NFD checkout."""
    nfd = "cafe\u0301.txt"  # as macOS writes it
    nfc = "caf\u00e9.txt"  # as a manifest records it
    (tmp_path / nfd).write_bytes(b"hello\n")
    manifest = Manifest(files=[FileEntry(nfc, MD5_HELLO, 6)])
    report = verify_tree(manifest, tmp_path)
    assert report.ok, [r.to_dict() for r in report.failures]


def test_nfd_fallback_works_when_the_root_itself_has_an_accent(tmp_path: Path) -> None:
    """The fallback used to decompose the *whole* path, root included.

    So a present file under a root like `réf_root/` was looked for under a
    decomposed root that does not exist, and reported missing.
    """
    nfd_name = "cafe\u0301.txt"  # e + COMBINING ACUTE ACCENT, as macOS writes it
    nfc_name = "caf\u00e9.txt"  # precomposed, as a manifest records it
    for root_name in ("ascii_root", "r\u00e9f_root", "cafe\u0301_root"):
        root = tmp_path / root_name
        root.mkdir()
        (root / nfd_name).write_bytes(b"hello\n")
        manifest = Manifest(files=[FileEntry(nfc_name, MD5_HELLO, 6)])
        report = verify_tree(manifest, root)
        assert report.ok, (root_name, [r.to_dict() for r in report.failures])


def test_special_payload_file_is_unreadable_not_a_hang(tmp_path: Path) -> None:
    """A payload path comes from the *manifest*, not from something a user typed.

    Reading one is unbounded, not merely wrong: a FIFO blocks until a writer appears
    and /dev/zero never ends. Both report st_size 0, so a manifest declaring size 0
    walked straight past the size check into an endless read. Contrast D-20, which
    deliberately allows special files where the *caller* named the path.

    No timeout here on purpose: if this regresses, the suite hangs, and that is the
    signal.
    """
    import os

    root = tmp_path / "root"
    root.mkdir()
    manifest = Manifest(files=[FileEntry("a.txt", "d41d8cd98f00b204e9800998ecf8427e", 0)])

    try:
        os.mkfifo(root / "a.txt")
    except (AttributeError, OSError):  # pragma: no cover
        pytest.skip("cannot create a FIFO here")
    report = verify_tree(manifest, root)
    assert not report.ok
    assert report.failures[0].status == "unreadable"
    assert "not a regular file" in (report.failures[0].detail or "")

    (root / "a.txt").unlink()
    if os.path.exists("/dev/zero"):
        (root / "a.txt").symlink_to("/dev/zero")
        report = verify_tree(manifest, root)
        assert not report.ok
        assert report.failures[0].status == "unreadable"
