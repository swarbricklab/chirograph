"""Interop against a real DVC installation.

These are the tests that keep the corrected DVC claims (docs/decisions.md D-01,
D-02) honest. Everything here runs the actual ``dvc`` CLI rather than a
hand-built fake cache, because the whole point is to check our *understanding* of
DVC's on-disk format against DVC itself — a fake cache would only check our code
against our own assumptions.

Skipped automatically when ``dvc`` is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chirograph.canonical import dvc_dir_bytes
from chirograph.dvc import DvcError, manifest_from_dvc, read_dvc_file
from chirograph.verify import verify_tree
from conftest import dvc_run, requires_dvc

pytestmark = [pytest.mark.dvc, requires_dvc]


def dvc_recorded_hash(repo: Path, name: str = "data.dvc") -> str:
    """The ``.dir`` hash DVC itself put in the ``.dvc`` file."""
    for line in (repo / name).read_text().splitlines():
        if "md5:" in line:
            return line.split("md5:")[1].strip().removesuffix(".dir")
    raise AssertionError(f"no md5 in {name}")


def dvc_dir_object(repo: Path, digest: str) -> Path:
    candidates = [
        repo / ".dvc" / "cache" / "files" / "md5" / digest[:2] / (digest[2:] + ".dir"),
        repo / ".dvc" / "cache" / digest[:2] / (digest[2:] + ".dir"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise AssertionError(f"no .dir object for {digest}")


# --------------------------------------------------------------------------- #
# The two corrected claims
# --------------------------------------------------------------------------- #


def test_dvc_dir_really_has_no_size_field(dvc_repo: Path) -> None:
    """Spec §0 claimed DVC `.dir` entries are {relpath, md5, size}. They are not."""
    listing = json.loads(dvc_dir_object(dvc_repo, dvc_recorded_hash(dvc_repo)).read_text())
    assert listing, "empty .dir"
    for row in listing:
        assert set(row) == {"md5", "relpath"}, f"unexpected keys: {sorted(row)}"


def test_dvc_serialises_with_default_separators(dvc_repo: Path) -> None:
    """Spec §4 specified compact separators. DVC does not use them."""
    raw = dvc_dir_object(dvc_repo, dvc_recorded_hash(dvc_repo)).read_text()
    assert '", "' in raw
    assert '": ' in raw


def test_our_derived_dvc_dir_hash_equals_dvcs_own(dvc_repo: Path) -> None:
    """The interop guarantee, checked against the real tool."""
    result = manifest_from_dvc(dvc_repo / "data.dvc")
    assert result.manifest.dvc_dir_hash() == dvc_recorded_hash(dvc_repo)


def test_our_derived_dvc_dir_bytes_are_byte_identical(dvc_repo: Path) -> None:
    digest = dvc_recorded_hash(dvc_repo)
    expected = dvc_dir_object(dvc_repo, digest).read_bytes()
    result = manifest_from_dvc(dvc_repo / "data.dvc")
    entries = [entry.to_dict() for entry in result.manifest.files]
    assert dvc_dir_bytes(entries) == expected


def test_body_hash_differs_from_the_dvc_dir_hash(dvc_repo: Path) -> None:
    """The claim the spec got wrong, now pinned as a deliberate difference."""
    result = manifest_from_dvc(dvc_repo / "data.dvc")
    assert result.manifest.body_hash != result.manifest.dvc_dir_hash()


def test_dvc_dir_hash_of_a_non_ascii_tree_matches(tmp_path: Path) -> None:
    """The ensure_ascii difference is where a naive interop attempt breaks."""
    repo = tmp_path / "uni"
    repo.mkdir()

    def run(*args: str) -> None:
        dvc_run(repo, *args)

    run("git", "init", "-q", ".")
    run("dvc", "init", "-q", "--no-scm")
    data = repo / "data"
    data.mkdir()
    (data / "café.txt").write_bytes(b"hello\n")
    (data / "naïve—dash.txt").write_bytes(b"world\n")
    run("dvc", "add", "data")

    result = manifest_from_dvc(repo / "data.dvc")
    assert result.manifest.dvc_dir_hash() == dvc_recorded_hash(repo)


# --------------------------------------------------------------------------- #
# Transcription
# --------------------------------------------------------------------------- #


def test_transcription_recovers_hashes_without_rehashing(dvc_repo: Path) -> None:
    result = manifest_from_dvc(dvc_repo / "data.dvc")
    listing = json.loads(dvc_dir_object(dvc_repo, dvc_recorded_hash(dvc_repo)).read_text())
    by_path = {row["relpath"]: row["md5"] for row in listing}
    assert {e.relpath: e.md5 for e in result.manifest.files} == by_path


def test_transcription_recovers_sizes_from_the_cache(dvc_repo: Path) -> None:
    """A `.dir` has no sizes, so they must come from somewhere (dvc.py docstring)."""
    result = manifest_from_dvc(dvc_repo / "data.dvc")
    assert "cache" in result.size_source
    assert result.manifest.entry("a.txt").size == 6
    assert result.manifest.size == sum(e.size for e in result.manifest.files)


def test_transcription_agrees_with_the_dvc_files_declared_total(dvc_repo: Path) -> None:
    result = manifest_from_dvc(dvc_repo / "data.dvc")
    assert result.out.size == result.manifest.size
    assert result.out.nfiles == result.manifest.nfiles
    assert not [note for note in result.notes if "declares" in note]


def test_transcribed_manifest_verifies_against_the_workspace(dvc_repo: Path) -> None:
    """End-to-end: DVC's own hashes, checked back against the bytes."""
    result = manifest_from_dvc(dvc_repo / "data.dvc")
    assert verify_tree(result.manifest, dvc_repo / "data").ok


def test_transcription_works_without_a_checkout(dvc_repo: Path) -> None:
    """The cache is authoritative, so a manifest can be built with no workspace."""
    for path in sorted((dvc_repo / "data").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (dvc_repo / "data").rmdir()
    result = manifest_from_dvc(dvc_repo / "data.dvc")
    assert result.manifest.nfiles == 3
    assert result.size_source == "cache"


def test_transcription_fails_clearly_without_cache_or_workspace(dvc_repo: Path) -> None:
    import shutil

    shutil.rmtree(dvc_repo / ".dvc" / "cache" / "files")
    with pytest.raises(DvcError, match=r"\.dir not found"):
        manifest_from_dvc(dvc_repo / "data.dvc")


def test_workspace_drift_from_the_cache_is_reported(dvc_repo: Path) -> None:
    # DVC checks out via hardlink/reflink to the read-only cache, so the file has
    # to be replaced rather than written in place — which is also what real
    # workspace drift looks like.
    target = dvc_repo / "data" / "a.txt"
    target.unlink()
    target.write_bytes(b"a much longer replacement\n")
    result = manifest_from_dvc(dvc_repo / "data.dvc")
    assert any("drifted from the cache" in note for note in result.notes)


def test_prefer_workspace_sizes_changes_which_wins(dvc_repo: Path) -> None:
    # DVC checks out via hardlink/reflink to the read-only cache, so the file has
    # to be replaced rather than written in place — which is also what real
    # workspace drift looks like.
    target = dvc_repo / "data" / "a.txt"
    target.unlink()
    target.write_bytes(b"a much longer replacement\n")
    from_cache = manifest_from_dvc(dvc_repo / "data.dvc")
    from_workspace = manifest_from_dvc(dvc_repo / "data.dvc", prefer_workspace_sizes=True)
    assert from_cache.manifest.entry("a.txt").size == 6
    assert from_workspace.manifest.entry("a.txt").size == 26


def test_single_file_dvc_output(tmp_path: Path) -> None:
    repo = tmp_path / "single"
    repo.mkdir()

    def run(*args: str) -> None:
        dvc_run(repo, *args)

    run("git", "init", "-q", ".")
    run("dvc", "init", "-q", "--no-scm")
    (repo / "big.bin").write_bytes(b"x" * 1000)
    run("dvc", "add", "big.bin")

    result = manifest_from_dvc(repo / "big.bin.dvc")
    assert result.manifest.nfiles == 1
    assert result.manifest.entry("big.bin").size == 1000
    assert verify_tree(result.manifest, repo).ok


def test_reading_the_dvc_file_exposes_the_hash_marker(dvc_repo: Path) -> None:
    outs = read_dvc_file(dvc_repo / "data.dvc")
    assert len(outs) == 1
    assert outs[0].hash_name == "md5"
    assert outs[0].is_dir
    assert not outs[0].legacy


# --------------------------------------------------------------------------- #
# Legacy md5-dos2unix refusal
# --------------------------------------------------------------------------- #


def test_legacy_dvc_file_without_a_hash_marker_is_refused(dvc_repo: Path) -> None:
    """DVC 2.x md5s were computed after CRLF->LF conversion for text files.

    Transcribing them yields a manifest that fails verification on exactly those
    files — a subtle, delayed failure. Absence of the ``hash:`` key means legacy,
    so absence must mean *refuse*, not *assume modern*.
    """
    dvc_file = dvc_repo / "data.dvc"
    stripped = "\n".join(line for line in dvc_file.read_text().splitlines() if "hash:" not in line)
    dvc_file.write_text(stripped + "\n")

    with pytest.raises(DvcError, match=r"dos2unix|CRLF"):
        manifest_from_dvc(dvc_file)


def test_legacy_can_be_forced_when_the_user_knows_better(dvc_repo: Path) -> None:
    dvc_file = dvc_repo / "data.dvc"
    stripped = "\n".join(line for line in dvc_file.read_text().splitlines() if "hash:" not in line)
    dvc_file.write_text(stripped + "\n")
    result = manifest_from_dvc(dvc_file, allow_legacy_md5=True)
    assert result.manifest.nfiles == 3


def test_explicit_md5_dos2unix_marker_is_refused(dvc_repo: Path) -> None:
    dvc_file = dvc_repo / "data.dvc"
    dvc_file.write_text(dvc_file.read_text().replace("hash: md5", "hash: md5-dos2unix"))
    with pytest.raises(DvcError, match="md5-dos2unix"):
        manifest_from_dvc(dvc_file)


# --------------------------------------------------------------------------- #
# dvc.lock and multi-output selection
# --------------------------------------------------------------------------- #


def test_dvc_lock_with_several_outputs_requires_selection(tmp_path: Path) -> None:
    lock = tmp_path / "dvc.lock"
    lock.write_text(
        "schema: '2.0'\n"
        "stages:\n"
        "  prepare:\n"
        "    cmd: true\n"
        "    outs:\n"
        "    - path: one\n"
        "      hash: md5\n"
        "      md5: 0123456789abcdef0123456789abcdef\n"
        "      size: 5\n"
        "    - path: two\n"
        "      hash: md5\n"
        "      md5: fedcba9876543210fedcba9876543210\n"
        "      size: 7\n"
    )
    outs = read_dvc_file(lock)
    assert {out.path for out in outs} == {"one", "two"}
    with pytest.raises(DvcError, match="--dvc-out"):
        manifest_from_dvc(lock)


def test_dvc_out_selection_names_available_options(dvc_repo: Path) -> None:
    with pytest.raises(DvcError, match="Available: data"):
        manifest_from_dvc(dvc_repo / "data.dvc", out_path="nope")


# --------------------------------------------------------------------------- #
# Regressions from the adversarial review
# --------------------------------------------------------------------------- #


def write_dvc(path: Path, body: str) -> Path:
    """A hand-written .dvc, for shapes a real `dvc add` will not produce."""
    (path.parent / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_sha256_dvc_produces_a_sha256_manifest(tmp_path: Path) -> None:
    """It used to hard-code md5, emitting 64-char digests under an md5 header.

    Written at exit 0, then rejected by our own reader — the worst of both.
    """
    from chirograph.manifest import Manifest

    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "data").mkdir()
    (repo / "data" / "a.txt").write_bytes(b"test\n")
    dvc_file = write_dvc(
        repo / "sha.dvc",
        "outs:\n"
        "- sha256: 2c8b08da5ce60398e1f19af0e5dccc744df274b826abe585eaba68c525434806\n"
        "  size: 5\n"
        "  hash: sha256\n"
        "  path: data/a.txt\n",
    )
    result = manifest_from_dvc(dvc_file)
    assert result.manifest.hash_algorithm == "sha256"
    # The round trip our own validator performs.
    Manifest.loads(result.manifest.to_json())


def test_non_hex_digest_is_refused_rather_than_written(tmp_path: Path) -> None:
    """An etag-shaped digest sailed through and produced an unloadable manifest."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "data").mkdir()
    (repo / "data" / "a.txt").write_bytes(b"test\n")
    dvc_file = write_dvc(
        repo / "etag.dvc",
        "outs:\n"
        "- md5: d41d8cd98f00b204e9800998ecf8427e-3\n"
        "  size: 5\n"
        "  hash: md5\n"
        "  path: data/a.txt\n",
    )
    with pytest.raises(DvcError, match="34 characters"):
        manifest_from_dvc(dvc_file)


def test_unsupported_hash_name_is_refused_with_an_explanation(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "data").mkdir()
    (repo / "data" / "a.txt").write_bytes(b"test\n")
    dvc_file = write_dvc(
        repo / "etagname.dvc",
        "outs:\n"
        "- md5: d8e8fca2dc0f896fd7cb4cb0031ba249\n"
        "  size: 5\n"
        "  hash: etag\n"
        "  path: data/a.txt\n",
    )
    with pytest.raises(DvcError, match="etag"):
        manifest_from_dvc(dvc_file)


def test_created_goes_through_timestamp_validation(dvc_repo: Path) -> None:
    """Assigning it after construction bypassed __post_init__ (D-05)."""
    from chirograph.errors import SchemaError

    with pytest.raises(SchemaError, match="ISO-8601"):
        manifest_from_dvc(dvc_repo / "data.dvc", created="not-a-timestamp")
    with pytest.raises(SchemaError, match="timezone offset"):
        manifest_from_dvc(dvc_repo / "data.dvc", created="2026-01-01T00:00:00")

    result = manifest_from_dvc(dvc_repo / "data.dvc", created="2026-01-01T00:00:00Z")
    assert result.manifest.created == "2026-01-01T00:00:00Z"


def test_conflicting_hash_algorithm_is_refused(dvc_repo: Path) -> None:
    """Transcription cannot convert algorithms without reading the data."""
    with pytest.raises(DvcError, match="cannot be transcribed"):
        manifest_from_dvc(dvc_repo / "data.dvc", hash_algorithm="sha256")
    # Agreeing is fine.
    assert manifest_from_dvc(dvc_repo / "data.dvc", hash_algorithm="md5").manifest


def build_sha256_dir_repo(root: Path) -> Path:
    """A sha256 `.dir`-tracked output, laid out exactly as DVC 3.x does.

    Hand-built because `dvc add` will not produce one locally — DVC uses md5 for
    local caches — and the point is that our *reading* of the layout is right.
    """
    import hashlib

    data = root / "data"
    (data / "sub").mkdir(parents=True)
    payload = {"a.txt": b"test\n", "sub/b.txt": b"more\n"}
    rows = []
    for rel, content in payload.items():
        (data / rel).write_bytes(content)
        rows.append({"sha256": hashlib.sha256(content).hexdigest(), "relpath": rel})
    rows.sort(key=lambda row: str(row["relpath"]))

    dir_bytes = json.dumps(rows, sort_keys=True).encode()
    dir_oid = hashlib.sha256(dir_bytes).hexdigest()
    # DVC shards by algorithm name: cache/files/<algorithm>/ab/cdef…
    cache = root / ".dvc" / "cache" / "files" / "sha256"
    (cache / dir_oid[:2]).mkdir(parents=True)
    (cache / dir_oid[:2] / (dir_oid[2:] + ".dir")).write_bytes(dir_bytes)
    for content in payload.values():
        oid = hashlib.sha256(content).hexdigest()
        (cache / oid[:2]).mkdir(parents=True, exist_ok=True)
        (cache / oid[:2] / oid[2:]).write_bytes(content)

    dvc_file = root / "data.dvc"
    dvc_file.write_text(
        f"outs:\n- sha256: {dir_oid}.dir\n  hash: sha256\n"
        f"  size: {sum(len(v) for v in payload.values())}\n"
        f"  nfiles: {len(payload)}\n  path: data\n"
    )
    return dvc_file


def test_sha256_directory_output_transcribes(tmp_path: Path) -> None:
    """Deriving the algorithm was only half the job.

    `find_cache_dirs` still offered only `files/md5`, and the `.dir` reader still
    looked only for a "md5" key — so a sha256 *directory* output (the main case)
    failed twice over even once the header said sha256.
    """
    from chirograph.manifest import Manifest

    dvc_file = build_sha256_dir_repo(tmp_path / "r")
    result = manifest_from_dvc(dvc_file)

    assert result.manifest.hash_algorithm == "sha256"
    assert result.manifest.nfiles == 2
    assert all(len(entry.md5) == 64 for entry in result.manifest.files)
    assert result.manifest.size == 10
    # Loadable, and the bytes actually check out.
    Manifest.loads(result.manifest.to_json())
    assert verify_tree(result.manifest, dvc_file.parent / "data").ok


def test_sha256_cache_shard_is_searched_by_algorithm_name(tmp_path: Path) -> None:
    from chirograph.dvc import find_cache_dirs

    root = tmp_path / "r"
    (root / ".dvc").mkdir(parents=True)
    md5_dirs = [str(p) for p in find_cache_dirs(root, algorithm="md5")]
    sha_dirs = [str(p) for p in find_cache_dirs(root, algorithm="sha256")]
    assert any(p.endswith("files/md5") for p in md5_dirs)
    assert any(p.endswith("files/sha256") for p in sha_dirs)
    # md5 objects may sit alongside in a migrated repo, so still look there.
    assert any(p.endswith("files/md5") for p in sha_dirs)
