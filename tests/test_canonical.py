"""Canonicalisation — the rules that must never drift.

These tests restate the properties in code, independently of the frozen vectors
(``test_vectors.py``). A property test and a frozen vector fail differently: the
vector says "you changed the answer", the property says "you broke the rule".
"""

from __future__ import annotations

import json
import unicodedata

import pytest

from chirograph.canonical import (
    canonical_text,
    canonicalise,
    compute_body_hash,
    dvc_dir_bytes,
    normalise_entries,
)
from chirograph.errors import SchemaError

MD5_A = "b1946ac92492d2347c6235b4d2611184"
MD5_B = "591785b794601e212b260e25925636fd"


def entry(relpath: str, digest: str = MD5_A, size: int = 1, **extra: object) -> dict[str, object]:
    return {"relpath": relpath, "md5": digest, "size": size, **extra}


# --------------------------------------------------------------------------- #
# Serialisation shape
# --------------------------------------------------------------------------- #


def test_empty_array_is_two_bytes() -> None:
    assert canonicalise([]) == b"[]"


def test_no_insignificant_whitespace() -> None:
    text = canonical_text([entry("a.txt")])
    assert ", " not in text
    assert ": " not in text
    assert text == '[{"md5":"' + MD5_A + '","relpath":"a.txt","size":1}]'


def test_keys_are_sorted_within_each_entry() -> None:
    text = canonical_text([entry("a.txt", source_path="x", isexec=True, version_id="v", etag="e")])
    keys = list(json.loads(text)[0])
    assert keys == sorted(keys)


def test_key_insertion_order_is_irrelevant() -> None:
    forward = {"relpath": "a.txt", "md5": MD5_A, "size": 1}
    backward = {"size": 1, "md5": MD5_A, "relpath": "a.txt"}
    assert canonicalise([forward]) == canonicalise([backward])


def test_non_ascii_is_literal_utf8_not_escaped() -> None:
    nfc = "caf\u00e9.txt"  # precomposed U+00E9
    text = canonical_text([entry(nfc)])
    assert nfc in text
    assert "\\u00e9" not in text
    assert canonicalise([entry(nfc)]).decode("utf-8") == text


def test_large_integers_survive_exactly() -> None:
    huge = 2**53 + 1
    text = canonical_text([entry("h", size=huge)])
    assert str(huge) in text
    assert "9007199254740992" not in text


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #


def test_input_order_does_not_matter() -> None:
    forward = [entry("a.txt"), entry("sub/b.txt", MD5_B)]
    assert canonicalise(forward) == canonicalise(list(reversed(forward)))


def test_sort_key_is_the_joined_string_not_the_component_tuple() -> None:
    """'-' (U+002D) precedes '/' (U+002F), so 'a-b/x' sorts before 'a/b'."""
    order = [
        row["relpath"] for row in normalise_entries([entry("a/b"), entry("a-b/x"), entry("a.b")])
    ]
    assert order == ["a-b/x", "a.b", "a/b"]


def test_sort_is_codepoint_not_case_folded() -> None:
    order = [row["relpath"] for row in normalise_entries([entry("a.txt"), entry("B.txt")])]
    assert order == ["B.txt", "a.txt"]


def test_normalise_happens_before_sort() -> None:
    """The order-of-operations trap: NFD sorts differently from NFC.

    In NFD, ``cafe\u0301`` < ``caff`` (compare 'e' with 'f'). In NFC it becomes
    ``caf\u00e9``, which is > ``caff``. Sorting first would give the other answer.
    """
    nfd = "cafe\u0301"  # e + COMBINING ACUTE ACCENT U+0301
    assert nfd < "caff"  # the trap: pre-normalisation, this is the order
    assert unicodedata.normalize("NFC", nfd) > "caff"  # post-normalisation, reversed
    order = [row["relpath"] for row in normalise_entries([entry(nfd), entry("caff", MD5_B)])]
    assert order == ["caff", "caf\u00e9"]


def test_nfd_and_nfc_inputs_hash_identically() -> None:
    """The macOS/Linux footgun: the same file, walked on two platforms."""
    assert compute_body_hash([entry("cafe\u0301.txt")]) == compute_body_hash(
        [entry("caf\u00e9.txt")]
    )


# --------------------------------------------------------------------------- #
# Rejections that keep the function total
# --------------------------------------------------------------------------- #


def test_duplicate_relpath_is_rejected() -> None:
    """With two equal sort keys the output would depend on input order."""
    with pytest.raises(SchemaError, match="duplicate relpath"):
        canonicalise([entry("a.txt"), entry("a.txt", MD5_B)])


def test_duplicate_after_normalisation_is_rejected() -> None:
    with pytest.raises(SchemaError, match="duplicate relpath"):
        canonicalise([entry("cafe\u0301.txt"), entry("caf\u00e9.txt", MD5_B)])


def test_null_is_rejected_rather_than_treated_as_absent() -> None:
    with pytest.raises(SchemaError, match="null"):
        canonicalise([{"relpath": "a.txt", "md5": MD5_A, "size": 1, "source_path": None}])


def test_float_size_is_rejected() -> None:
    with pytest.raises(SchemaError, match="float"):
        canonicalise([{"relpath": "a.txt", "md5": MD5_A, "size": 1.0}])


def test_nested_value_is_rejected() -> None:
    with pytest.raises(SchemaError, match="non-scalar"):
        canonicalise([{"relpath": "a.txt", "md5": MD5_A, "size": 1, "extra": [1]}])


def test_missing_relpath_is_rejected() -> None:
    with pytest.raises(SchemaError, match="relpath"):
        canonicalise([{"md5": MD5_A, "size": 1}])


def test_files_must_be_an_array() -> None:
    with pytest.raises(SchemaError, match="array"):
        canonicalise({"relpath": "a"})  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# No semantic rewriting (docs/decisions.md D-06)
# --------------------------------------------------------------------------- #


def test_redundant_source_path_is_not_dropped() -> None:
    with_redundant = compute_body_hash([entry("a.txt", source_path="a.txt")])
    terse = compute_body_hash([entry("a.txt")])
    assert with_redundant != terse


def test_extension_fields_are_covered_by_body_hash() -> None:
    base = compute_body_hash([entry("a.txt")])
    extras = [
        ("isexec", True),
        ("version_id", "v1"),
        ("etag", "e"),
        ("source_path", "b"),
        ("file_version", "2026-07-30T00:00:00Z"),  # 1.1 entry field
    ]
    for field, value in extras:
        assert compute_body_hash([entry("a.txt", **{field: value})]) != base, field


# --------------------------------------------------------------------------- #
# DVC interop is a DIFFERENT serialisation
# --------------------------------------------------------------------------- #


def test_dvc_dir_uses_default_separators_and_ascii_escapes() -> None:
    raw = dvc_dir_bytes([entry("caf\u00e9.txt")]).decode("utf-8")
    assert '", "' in raw  # item separator carries a space
    assert '": ' in raw  # key separator carries a space
    assert "\\u00e9" in raw  # ensure_ascii=True
    assert "caf\u00e9" not in raw


def test_dvc_dir_drops_size_and_extension_fields() -> None:
    rows = json.loads(dvc_dir_bytes([entry("a.txt", size=99, source_path="x", isexec=True)]))
    assert rows == [{"md5": MD5_A, "relpath": "a.txt"}]


def test_body_hash_is_not_the_dvc_dir_hash() -> None:
    """The corrected claim: these are deliberately different (decisions D-01)."""
    from chirograph.canonical import dvc_dir_hash

    files = [entry("a.txt"), entry("sub/b.txt", MD5_B)]
    assert compute_body_hash(files) != dvc_dir_hash(files)
