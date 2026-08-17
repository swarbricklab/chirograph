"""Conformance against the frozen test vectors.

This is the M0 deliverable: the vectors are what a second implementation checks
itself against, and this test is what stops *our* implementation drifting away
from them.

It reads the checked-in vectors and asserts the implementation reproduces them.
It deliberately does **not** regenerate them — regenerating would turn "you
changed the answer" into "the answer is whatever the code says", which is exactly
the failure the vectors exist to prevent.

``tools/check_vectors_independently.sh`` closes the remaining loop by re-deriving
every hash with coreutils, so the frozen values are not merely self-consistent
with the code that produced them.
"""

from __future__ import annotations

import pytest

from chirograph.canonical import canonical_text, compute_body_hash, dvc_dir_bytes, dvc_dir_hash
from chirograph.errors import ChirographError
from chirograph.hashing import hash_bytes
from chirograph.manifest import Manifest
from chirograph.paths import validate_path
from chirograph.vectors import check_vectors, load_vectors

VECTORS = load_vectors()
CANONICAL_CASES = VECTORS["canonicalisation"]


def case_id(case: dict[str, object]) -> str:
    return str(case["name"])


def test_vector_file_is_pure_ascii() -> None:
    """So the NFC/NFD cases are unambiguous about which codepoints they mean."""
    from chirograph.vectors import vectors_path

    raw = vectors_path().read_bytes()
    assert max(raw) < 128, "vectors file must be pure ASCII (use \\uXXXX escapes)"


def test_vector_file_declares_its_own_freeze() -> None:
    assert VECTORS["format"] == "chirograph-test-vectors"
    assert VECTORS["spec_version"] == "1.0"
    assert "FROZEN" in VECTORS["$comment"]


@pytest.mark.parametrize("case", CANONICAL_CASES, ids=case_id)
def test_canonical_string_matches(case: dict) -> None:
    """Compare the STRING, not the hash.

    A digest mismatch tells you nothing. The string diff tells you whether you
    got the separators, the escaping, the sort, or the normalisation wrong.
    """
    assert canonical_text(case["input"]) == case["canonical"]


@pytest.mark.parametrize("case", CANONICAL_CASES, ids=case_id)
def test_canonical_byte_length_matches(case: dict) -> None:
    assert len(case["canonical"].encode("utf-8")) == case["canonical_utf8_bytes"]


@pytest.mark.parametrize("case", CANONICAL_CASES, ids=case_id)
def test_canonical_sha256_matches(case: dict) -> None:
    """A second digest over the same bytes, so md5 is not the only witness."""
    raw = canonical_text(case["input"]).encode("utf-8")
    assert hash_bytes(raw, "sha256") == case["canonical_sha256"]


@pytest.mark.parametrize("case", CANONICAL_CASES, ids=case_id)
def test_body_hash_matches(case: dict) -> None:
    assert compute_body_hash(case["input"], case["hash_algorithm"]) == case["body_hash"]


@pytest.mark.parametrize("case", CANONICAL_CASES, ids=case_id)
def test_dvc_dir_interop_matches(case: dict) -> None:
    algorithm = case["hash_algorithm"]
    assert dvc_dir_bytes(case["input"], algorithm).decode("utf-8") == case["dvc_dir"]
    assert dvc_dir_hash(case["input"], algorithm) == case["dvc_dir_hash"]


@pytest.mark.parametrize(
    "case", VECTORS["path_safety"]["accept"], ids=lambda c: repr(c["path"])[:40]
)
def test_path_safety_accepts(case: dict) -> None:
    validate_path(case["path"])


@pytest.mark.parametrize(
    "case", VECTORS["path_safety"]["reject"], ids=lambda c: repr(c["path"])[:40]
)
def test_path_safety_rejects(case: dict) -> None:
    with pytest.raises(ChirographError):
        validate_path(case["path"])


@pytest.mark.parametrize("case", VECTORS["manifests"]["valid"], ids=case_id)
def test_valid_manifests_load(case: dict) -> None:
    Manifest.from_dict(case["document"])


@pytest.mark.parametrize("case", VECTORS["manifests"]["invalid"], ids=case_id)
def test_invalid_manifests_are_refused(case: dict) -> None:
    with pytest.raises(ChirographError) as caught:
        Manifest.from_dict(case["document"])
    names = {base.__name__ for base in type(caught.value).__mro__}
    assert case["error"] in names, (
        f"expected {case['error']}, got {type(caught.value).__name__}: {caught.value}"
    )


def test_check_vectors_helper_agrees() -> None:
    """The shipped self-check (``chiro test-vectors --check``) must pass too."""
    result = check_vectors()
    assert result.ok, "\n".join(result.failures)
    assert result.total > 60, "the conformance suite has lost cases"


# --------------------------------------------------------------------------- #
# Invariants the vectors are supposed to encode
# --------------------------------------------------------------------------- #


def _by_name() -> dict[str, dict]:
    return {case["name"]: case for case in CANONICAL_CASES}


def test_vectors_pin_sort_invariance() -> None:
    cases = _by_name()
    assert cases["unsorted-input"]["body_hash"] == cases["sorted-input"]["body_hash"]


def test_vectors_pin_key_order_invariance() -> None:
    cases = _by_name()
    assert cases["key-order-scrambled"]["body_hash"] == cases["single-minimal"]["body_hash"]


def test_vectors_pin_that_redundant_source_path_changes_the_hash() -> None:
    """docs/decisions.md D-06: canonicalisation does no semantic rewriting."""
    cases = _by_name()
    assert cases["redundant-source-path"]["body_hash"] != cases["terse-no-source-path"]["body_hash"]


def test_vectors_cover_the_named_traps() -> None:
    """Guard against a future edit quietly deleting a case."""
    required = {
        "empty",
        "sort-hyphen-before-slash",
        "sort-case-sensitive",
        "nfd-input-normalised",
        "nfc-then-sort-ordering-trap",
        "non-ascii-literal-in-canonical",
        "astral-plane-codepoint",
        "large-size-beyond-double",
        "redundant-source-path",
        "terse-no-source-path",
        "extension-fields",
        "sha256-algorithm",
        "many-entries-adversarial-order",
    }
    missing = required - set(_by_name())
    assert not missing, f"vectors are missing required cases: {sorted(missing)}"
