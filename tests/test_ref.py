"""``chiro://`` references and the registry shard layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from chirograph.errors import RefError, UnsupportedAlgorithm
from chirograph.manifest import FileEntry, Manifest
from chirograph.ref import ChiroRef, canonical_document_bytes, canonical_ref, shard_path

MD5_A = "b1946ac92492d2347c6235b4d2611184"
EMPTY_MD5 = "d41d8cd98f00b204e9800998ecf8427e"


def sample() -> Manifest:
    return Manifest(
        files=[FileEntry("a.txt", MD5_A, 6)],
        record_accession="GSE1",
        created="2026-07-01T00:00:00Z",
    )


# --------------------------------------------------------------------------- #
# Syntax
# --------------------------------------------------------------------------- #


def test_round_trip() -> None:
    text = f"chiro://md5:{EMPTY_MD5}"
    assert str(ChiroRef.parse(text)) == text


def test_algorithm_prefix_is_mandatory() -> None:
    """A reference is the one place the algorithm can't be read from inside."""
    with pytest.raises(RefError, match="malformed"):
        ChiroRef.parse(f"chiro://{EMPTY_MD5}")


def test_scheme_is_mandatory() -> None:
    with pytest.raises(RefError, match="malformed"):
        ChiroRef.parse(f"md5:{EMPTY_MD5}")


def test_uppercase_digest_is_normalised() -> None:
    assert ChiroRef.parse(f"chiro://md5:{EMPTY_MD5.upper()}").digest == EMPTY_MD5


def test_surrounding_whitespace_tolerated() -> None:
    assert ChiroRef.parse(f"  chiro://md5:{EMPTY_MD5}\n").digest == EMPTY_MD5


def test_wrong_digest_length_rejected() -> None:
    with pytest.raises(RefError, match="32 lowercase hex"):
        ChiroRef("md5", "abc")


def test_sha256_reference() -> None:
    digest = "a" * 64
    assert str(ChiroRef("sha256", digest)) == f"chiro://sha256:{digest}"


def test_unknown_algorithm_rejected() -> None:
    with pytest.raises(UnsupportedAlgorithm):
        ChiroRef.parse(f"chiro://crc32:{EMPTY_MD5}")


def test_etag_algorithm_rejected_in_a_reference() -> None:
    with pytest.raises(UnsupportedAlgorithm, match="reserved"):
        ChiroRef.parse(f"chiro://etag:{EMPTY_MD5}")


def test_non_string_rejected() -> None:
    with pytest.raises(RefError, match="must be a string"):
        ChiroRef.parse(42)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Self-validation — why a registry need not be trusted
# --------------------------------------------------------------------------- #


def test_reference_for_bytes_self_validates() -> None:
    data = b"whatever"
    reference = ChiroRef.for_bytes(data)
    assert reference.matches_bytes(data)
    assert not reference.matches_bytes(data + b"!")
    reference.check_bytes(data)


def test_check_bytes_explains_the_rejection() -> None:
    reference = ChiroRef.for_bytes(b"a")
    with pytest.raises(RefError, match="not the manifest that was asked for"):
        reference.check_bytes(b"b", origin="registry-x")


def test_reference_for_a_file(tmp_path: Path) -> None:
    target = sample().dump(tmp_path / "m.chiro.json")
    reference = ChiroRef.for_path(target)
    assert reference.matches_path(target)
    reference.check_path(target)


def test_check_path_rejects_a_different_file(tmp_path: Path) -> None:
    a = sample().dump(tmp_path / "a.chiro.json")
    other = Manifest(files=[FileEntry("b.txt", MD5_A, 6)], created="2026-07-01T00:00:00Z")
    b = other.dump(tmp_path / "b.chiro.json")
    with pytest.raises(RefError, match="does not match"):
        ChiroRef.for_path(a).check_path(b)


def test_load_and_check_hashes_before_parsing(tmp_path: Path) -> None:
    """Order matters: reject the wrong manifest before trusting its contents."""
    from chirograph.ref import load_and_check

    target = sample().dump(tmp_path / "m.chiro.json")
    reference = ChiroRef.for_path(target)
    assert load_and_check(target, reference).record_accession == "GSE1"
    with pytest.raises(RefError):
        load_and_check(target, f"chiro://md5:{EMPTY_MD5}")


# --------------------------------------------------------------------------- #
# Registry layout
# --------------------------------------------------------------------------- #


def test_shard_path_matches_the_registry_design() -> None:
    reference = ChiroRef("md5", "abc123" + "0" * 26)
    assert reference.shard_path == f"manifests/ab/c123{'0' * 26}.chiro.json"
    assert shard_path(str(reference)) == reference.shard_path


def test_shard_path_reconstructs_the_digest() -> None:
    reference = ChiroRef.for_bytes(b"x")
    shard = reference.shard_path
    _, prefix, rest = shard.split("/")
    assert prefix + rest.removesuffix(".chiro.json") == reference.digest


# --------------------------------------------------------------------------- #
# The reformatting hazard (docs/decisions.md D-09)
# --------------------------------------------------------------------------- #


def test_whole_file_reference_changes_with_indentation(tmp_path: Path) -> None:
    """The hazard, pinned so nobody assumes otherwise."""
    manifest = sample()
    pretty = manifest.dump(tmp_path / "pretty.chiro.json", indent=2)
    compact = manifest.dump(tmp_path / "compact.chiro.json", indent=None)
    assert ChiroRef.for_path(pretty) != ChiroRef.for_path(compact)


def test_canonical_reference_is_invariant_under_reformatting() -> None:
    """The mitigation: address the content, not one rendering of it."""
    manifest = sample()
    assert canonical_ref(manifest) == canonical_ref(manifest)
    # Reload from a differently-indented rendering and get the same address.
    from chirograph.manifest import Manifest as M

    compact = M.loads(manifest.to_json(indent=None))
    pretty = M.loads(manifest.to_json(indent=8))
    assert canonical_ref(compact) == canonical_ref(pretty) == canonical_ref(manifest)


def test_canonical_document_is_compact_and_key_sorted() -> None:
    raw = canonical_document_bytes(sample()).decode("utf-8")
    assert raw.startswith('{"body_hash":')  # sorted keys put body_hash first
    assert ", " not in raw
    assert "\n" not in raw


def test_canonical_reference_differs_from_the_whole_file_one(tmp_path: Path) -> None:
    manifest = sample()
    target = manifest.dump(tmp_path / "m.chiro.json")
    assert canonical_ref(manifest) != ChiroRef.for_path(target)


def test_canonical_reference_changes_when_content_changes() -> None:
    a = sample()
    b = a.with_files([FileEntry("a.txt", MD5_A, 6), FileEntry("b.txt", MD5_A, 6)])
    assert canonical_ref(a) != canonical_ref(b)


def test_canonical_reference_changes_when_the_source_url_changes() -> None:
    """Why the address is the whole file and not body_hash (registry design §2)."""
    a = sample()
    b = Manifest(
        files=list(a.files),
        record_accession=a.record_accession,
        created=a.created,
        source_url="https://elsewhere.example/",
    )
    assert a.body_hash == b.body_hash  # same file list...
    assert canonical_ref(a) != canonical_ref(b)  # ...different manifest
