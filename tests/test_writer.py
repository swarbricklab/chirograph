"""Walking, hashing, rewrite rules, and capture."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from chirograph.constants import PROVENANCE_OBSERVED
from chirograph.errors import ChirographError
from chirograph.rewrite import RewriteRule, apply_rules, parse_rules
from chirograph.walk import DEFAULT_EXCLUDES, matches_any, walk_tree
from chirograph.writer import build_from_directory, capture_from_directory

MD5_HELLO = "b1946ac92492d2347c6235b4d2611184"


# --------------------------------------------------------------------------- #
# Walking
# --------------------------------------------------------------------------- #


def test_walk_is_sorted_and_posix(tree: Path) -> None:
    report = walk_tree(tree)
    assert report.paths == sorted(report.paths)
    assert report.paths == [
        "sampleA/barcodes.tsv",
        "sampleA/matrix.mtx",
        "sampleB/barcodes.tsv",
    ]
    assert all("\\" not in path for path in report.paths)


def test_walk_excludes_manifests_by_default(tree: Path) -> None:
    """Otherwise a manifest written into the tree it describes breaks the tree."""
    (tree / "GSE1.chiro.json").write_text("{}")
    report = walk_tree(tree)
    assert "GSE1.chiro.json" not in report.paths
    assert ("GSE1.chiro.json", "excluded by pattern") in report.skipped


def test_walk_excludes_vcs_metadata(tree: Path) -> None:
    (tree / ".git").mkdir()
    (tree / ".git" / "config").write_text("x")
    report = walk_tree(tree)
    assert not any(path.startswith(".git") for path in report.paths)


def test_walk_can_be_told_not_to_exclude_anything(tree: Path) -> None:
    (tree / "GSE1.chiro.json").write_text("{}")
    report = walk_tree(tree, excludes=[])
    assert "GSE1.chiro.json" in report.paths


def test_matches_any_semantics() -> None:
    assert matches_any("a/.git/config", [".git"])  # bare pattern matches any component
    assert matches_any("sampleA/raw/x.fq", ["sample*/raw/*"])  # path pattern
    assert not matches_any("sampleA/raw/x.fq", ["raw/*"])  # anchored, so no match
    assert matches_any("deep/dir/x.bam", ["*.bam"])


def test_walk_reports_empty_directories(tree: Path) -> None:
    """A manifest cannot represent one, so it must at least be mentioned."""
    (tree / "sampleC").mkdir()
    report = walk_tree(tree)
    assert "sampleC" in report.empty_dirs


def test_walk_does_not_follow_directory_symlinks(tree: Path) -> None:
    (tree / "link").symlink_to(tree / "sampleA", target_is_directory=True)
    report = walk_tree(tree)
    assert not any(path.startswith("link/") for path in report.paths)
    assert any("symlink to a directory" in reason for _, reason in report.skipped)


def test_follow_symlinks_survives_a_cycle(tmp_path: Path) -> None:
    """`os.walk(followlinks=True)` has no cycle protection; this must supply it.

    Without the (device, inode) guard, a link pointing at an ancestor recurses
    until the path-length limit — a hang, not an error.
    """
    root = tmp_path / "cyc"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "file.txt").write_bytes(b"hello\n")
    (root / "a" / "b" / "loop").symlink_to(root / "a", target_is_directory=True)

    report = walk_tree(root, follow_symlinks=True)
    assert report.paths == ["a/b/file.txt"]
    assert any("already included" in reason for _, reason in report.skipped)


def test_follow_symlinks_does_not_double_count_a_sibling_link(tmp_path: Path) -> None:
    """Two relpaths for one file would leave a consumer no way to choose."""
    root = tmp_path / "dup"
    (root / "real").mkdir(parents=True)
    (root / "real" / "file.txt").write_bytes(b"hello\n")
    (root / "alias").symlink_to(root / "real", target_is_directory=True)

    report = walk_tree(root, follow_symlinks=True)
    assert report.paths == ["real/file.txt"]
    assert "alias/file.txt" not in report.paths


def test_walk_includes_file_symlinks_by_target_content(tree: Path) -> None:
    (tree / "alias.tsv").symlink_to(tree / "sampleA" / "barcodes.tsv")
    result = build_from_directory(tree)
    entry = next(e for e in result.manifest.files if e.relpath == "alias.tsv")
    assert entry.md5 == MD5_HELLO


def test_walk_skips_broken_symlinks_with_a_reason(tree: Path) -> None:
    (tree / "dangling.tsv").symlink_to(tree / "nope.tsv")
    report = walk_tree(tree)
    assert "dangling.tsv" not in report.paths
    assert any("broken symlink" in reason for _, reason in report.skipped)


def test_walk_skips_fifos(tree: Path) -> None:
    os.mkfifo(tree / "pipe")
    report = walk_tree(tree)
    assert "pipe" not in report.paths
    assert any("not a regular file" in reason for _, reason in report.skipped)


def test_walk_refuses_to_silently_omit_an_unreadable_file(tree: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    secret = tree / "sampleA" / "secret.tsv"
    secret.write_bytes(b"x")
    secret.chmod(0)
    try:
        with pytest.raises(ChirographError, match="skip_unreadable"):
            walk_tree(tree)
        report = walk_tree(tree, skip_unreadable=True)
        assert "sampleA/secret.tsv" not in report.paths
        assert any("unreadable" in reason for _, reason in report.skipped)
    finally:
        secret.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_walk_rejects_a_file_as_root(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_text("x")
    with pytest.raises(ChirographError, match="not a directory"):
        walk_tree(target)


def test_default_excludes_are_not_empty() -> None:
    assert ".git" in DEFAULT_EXCLUDES
    assert "*.chiro.json" in DEFAULT_EXCLUDES


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #


def test_build_hashes_and_sizes(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    entry = manifest.entry("sampleA/barcodes.tsv")
    assert entry is not None
    assert entry.md5 == MD5_HELLO
    assert entry.size == 6
    assert manifest.nfiles == 3
    assert manifest.size == 18


def test_identical_content_at_two_paths_shares_a_digest(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    a = manifest.entry("sampleA/barcodes.tsv")
    b = manifest.entry("sampleB/barcodes.tsv")
    assert a and b and a.md5 == b.md5


def test_build_is_reproducible(tree: Path) -> None:
    first = build_from_directory(tree, created="2026-01-01T00:00:00Z").manifest
    second = build_from_directory(tree, created="2026-01-01T00:00:00Z").manifest
    assert first.to_bytes() == second.to_bytes()


def test_build_parallel_matches_serial(tree: Path) -> None:
    serial = build_from_directory(tree, jobs=1).manifest
    parallel = build_from_directory(tree, jobs=4).manifest
    assert serial.body_hash == parallel.body_hash


def test_sha256_build(tree: Path) -> None:
    manifest = build_from_directory(tree, hash_algorithm="sha256").manifest
    assert all(len(entry.md5) == 64 for entry in manifest.files)


def test_no_source_path_when_layouts_agree(tree: Path) -> None:
    manifest = build_from_directory(tree).manifest
    assert all(entry.source_path is None for entry in manifest.files)


def test_progress_callback_is_called(tree: Path) -> None:
    seen: list[tuple[int, int]] = []
    build_from_directory(tree, progress=lambda d, t, *_: seen.append((d, t)))
    assert len(seen) == 3
    assert seen[-1] == (3, 3)


# --------------------------------------------------------------------------- #
# Rewrite rules — the two-path model in practice
# --------------------------------------------------------------------------- #


def test_relpath_from_source_reorganises_a_flat_deposit(tmp_path: Path) -> None:
    """Spec §8.1: flat GEO deposit -> per-sample tree."""
    deposit = tmp_path / "deposit"
    deposit.mkdir()
    (deposit / "GSE1_sampleA_barcodes.tsv.gz").write_bytes(b"hello\n")
    (deposit / "GSE1_sampleA_matrix.mtx.gz").write_bytes(b"world\n")

    manifest = build_from_directory(
        deposit,
        source_url="https://example.org/suppl/",
        relpath_from_source=parse_rules([r"^GSE1_([^_]+)_(.*)$=>\1/\2"]),
    ).manifest

    assert [entry.relpath for entry in manifest.files] == [
        "sampleA/barcodes.tsv.gz",
        "sampleA/matrix.mtx.gz",
    ]
    assert manifest.files[0].source_path == "GSE1_sampleA_barcodes.tsv.gz"


def test_source_from_relpath_prepares_a_flat_deposit(tree: Path) -> None:
    manifest = build_from_directory(
        tree, source_from_relpath=parse_rules([r"^(.*)/(.*)$=>GSE1_\1_\2"])
    ).manifest
    entry = manifest.entry("sampleA/barcodes.tsv")
    assert entry is not None
    assert entry.source_path == "GSE1_sampleA_barcodes.tsv"


def test_non_matching_files_stay_terse(tmp_path: Path) -> None:
    """A rule that does not match must not produce a redundant source_path."""
    deposit = tmp_path / "d"
    deposit.mkdir()
    (deposit / "GSE1_sampleA_x.txt").write_bytes(b"a")
    (deposit / "README.txt").write_bytes(b"b")
    manifest = build_from_directory(
        deposit, relpath_from_source=parse_rules([r"^GSE1_([^_]+)_(.*)$=>\1/\2"])
    ).manifest
    readme = manifest.entry("README.txt")
    assert readme is not None and readme.source_path is None


def test_both_directions_at_once_is_refused(tree: Path) -> None:
    with pytest.raises(ChirographError, match="not both"):
        build_from_directory(
            tree,
            relpath_from_source=parse_rules([r"^(.*)$=>x/\1"]),
            source_from_relpath=parse_rules([r"^(.*)$=>y/\1"]),
        )


def test_rewrite_first_match_wins() -> None:
    rules = parse_rules([r"^a=>FIRST", r"^a=>SECOND"])
    assert apply_rules(rules, "abc") == "FIRSTbc"


def test_rewrite_rule_needs_a_separator() -> None:
    with pytest.raises(ChirographError, match="has no"):
        RewriteRule.parse("just-a-regex")


def test_rewrite_rule_rejects_bad_regex() -> None:
    with pytest.raises(ChirographError, match="invalid regex"):
        RewriteRule.parse(r"([unclosed=>x")


def test_rewrite_separator_may_appear_in_the_replacement() -> None:
    rule = RewriteRule.parse(r"^a$=>b=>c")
    assert rule.replacement == "b=>c"


def test_rewrite_that_escapes_the_tree_is_refused() -> None:
    """A rule producing '../x' must fail at write time, not at someone's checkout."""
    with pytest.raises(ChirographError, match="path traversal"):
        apply_rules(parse_rules([r"^(.*)$=>../\1"]), "a.txt")


def test_rewrite_to_absolute_is_refused() -> None:
    with pytest.raises(ChirographError, match="absolute"):
        apply_rules(parse_rules([r"^(.*)$=>/\1"]), "a.txt")


def test_rewrite_to_empty_is_refused() -> None:
    with pytest.raises(ChirographError, match="empty string"):
        apply_rules(parse_rules([r"^.*$=>"]), "a.txt")


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #


def test_capture_sets_observed_provenance_and_retrieved(tree: Path) -> None:
    manifest = capture_from_directory(tree, source_url="https://example.org/x/").manifest
    assert manifest.provenance_type == PROVENANCE_OBSERVED
    assert manifest.retrieved is not None
    assert manifest.source_url == "https://example.org/x/"
    assert manifest.lint() == []


def test_capture_requires_a_source_url(tree: Path) -> None:
    with pytest.raises(ChirographError, match="must say where"):
        capture_from_directory(tree, source_url="")


def test_capture_honours_an_explicit_retrieved(tree: Path) -> None:
    manifest = capture_from_directory(
        tree, source_url="https://x/", retrieved="2026-01-02T03:04:05Z"
    ).manifest
    assert manifest.retrieved == "2026-01-02T03:04:05Z"


# --------------------------------------------------------------------------- #
# Notes surfaced to the user
# --------------------------------------------------------------------------- #


def test_empty_directory_produces_a_note(tree: Path) -> None:
    (tree / "empty").mkdir()
    result = build_from_directory(tree)
    assert any("empty directory" in note for note in result.notes)


def test_nfd_filename_produces_a_note(tree: Path) -> None:
    """A tree written on macOS: the manifest paths differ from the on-disk bytes."""
    nfd = "cafe\u0301.txt"  # e + COMBINING ACUTE ACCENT
    nfc = "caf\u00e9.txt"  # precomposed
    (tree / nfd).write_bytes(b"x")
    result = build_from_directory(tree)
    assert any("NFC" in note for note in result.notes)
    assert result.manifest.entry(nfc) is not None
    assert nfd not in [e.relpath for e in result.manifest.files]
