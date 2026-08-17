"""The manifest object model, load/dump, and validation policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chirograph.constants import PROVENANCE_OBSERVED, PROVENANCE_SELF
from chirograph.errors import (
    BodyHashMismatch,
    PathSafetyError,
    SchemaError,
    UnsupportedAlgorithm,
    UnsupportedVersion,
)
from chirograph.manifest import FileEntry, Manifest
from chirograph.paths import validate_path

MD5_A = "b1946ac92492d2347c6235b4d2611184"
MD5_B = "591785b794601e212b260e25925636fd"
SHA_A = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"


def sample() -> Manifest:
    return Manifest(
        provenance_type=PROVENANCE_SELF,
        files=[
            FileEntry("sampleA/barcodes.tsv", MD5_A, 6),
            FileEntry("sampleA/matrix.mtx", MD5_B, 6, source_path="GSE1_matrix.mtx"),
        ],
        record_accession="GSE000000",
        source_url="https://example.org/suppl/",
        created="2026-07-01T00:00:00Z",
    )


# --------------------------------------------------------------------------- #
# Derived fields cannot be wrong
# --------------------------------------------------------------------------- #


def test_size_and_nfiles_are_derived() -> None:
    manifest = sample()
    assert manifest.nfiles == 2
    assert manifest.size == 12


def test_derived_fields_follow_edits() -> None:
    """The structural guarantee: you cannot build an inconsistent manifest."""
    manifest = sample()
    before = manifest.body_hash
    updated = manifest.with_files([*manifest.files, FileEntry("extra.txt", MD5_A, 99)])
    assert updated.nfiles == 3
    assert updated.size == 111
    assert updated.body_hash != before


def test_entries_are_sorted_on_construction() -> None:
    manifest = Manifest(
        files=[FileEntry("z.txt", MD5_A, 1), FileEntry("a.txt", MD5_B, 1)],
    )
    assert [entry.relpath for entry in manifest.files] == ["a.txt", "z.txt"]


def test_duplicate_relpath_rejected_on_construction() -> None:
    with pytest.raises(SchemaError, match="duplicate relpath"):
        Manifest(files=[FileEntry("a.txt", MD5_A, 1), FileEntry("a.txt", MD5_B, 1)])


# --------------------------------------------------------------------------- #
# Round trip
# --------------------------------------------------------------------------- #


def test_round_trip(tmp_path: Path) -> None:
    original = sample()
    target = original.dump(tmp_path / "m.chiro.json")
    reloaded = Manifest.load(target)
    assert reloaded.to_dict() == original.to_dict()
    assert reloaded.body_hash == original.body_hash


def test_serialisation_is_byte_deterministic() -> None:
    """A precondition for the whole-file hash meaning anything."""
    assert sample().to_bytes() == sample().to_bytes()


def test_absent_fields_are_omitted_not_null() -> None:
    document = Manifest(files=[FileEntry("a.txt", MD5_A, 1)]).to_dict()
    assert "record_accession" not in document
    assert "retrieved" not in document
    assert None not in document.values()


def test_on_disk_entries_are_sorted() -> None:
    document = sample().to_dict()
    paths = [entry["relpath"] for entry in document["files"]]
    assert paths == sorted(paths)


def test_dump_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    sample().dump(tmp_path / "m.chiro.json")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.chiro.json"]


def test_default_filename_prefers_the_accession() -> None:
    assert sample().default_filename() == "GSE000000.chiro.json"
    assert Manifest().default_filename() == "manifest.chiro.json"


def test_default_filename_sanitises_hostile_accessions() -> None:
    manifest = Manifest(record_accession="../../etc/passwd")
    assert "/" not in manifest.default_filename()
    assert ".." not in manifest.default_filename().replace(".chiro.json", "")


# --------------------------------------------------------------------------- #
# Two-path model
# --------------------------------------------------------------------------- #


def test_source_defaults_to_relpath() -> None:
    assert FileEntry("a.txt", MD5_A, 1).source == "a.txt"
    assert FileEntry("a.txt", MD5_A, 1, source_path="b.txt").source == "b.txt"


def test_fetch_plan_resolves_urls() -> None:
    plan = {item.relpath: item for item in sample().fetch_plan()}
    assert plan["sampleA/matrix.mtx"].url == "https://example.org/suppl/GSE1_matrix.mtx"
    assert plan["sampleA/barcodes.tsv"].url == ("https://example.org/suppl/sampleA/barcodes.tsv")


def test_source_url_without_trailing_slash_does_not_lose_a_segment() -> None:
    """The urljoin trap: a base without a slash discards its last segment."""
    manifest = Manifest(
        files=[FileEntry("a.txt", MD5_A, 1)], source_url="https://example.org/suppl"
    )
    assert manifest.fetch_plan()[0].url == "https://example.org/suppl/a.txt"


def test_fetch_plan_percent_encodes_spaces() -> None:
    manifest = Manifest(
        files=[FileEntry("run 1/a.txt", MD5_A, 1)], source_url="https://example.org/x/"
    )
    assert manifest.fetch_plan()[0].url == "https://example.org/x/run%201/a.txt"


def test_relocated_flag() -> None:
    assert not FileEntry("a", MD5_A, 1).relocated
    assert not FileEntry("a", MD5_A, 1, source_path="a").relocated
    assert FileEntry("a", MD5_A, 1, source_path="b").relocated


# --------------------------------------------------------------------------- #
# Validation on read
# --------------------------------------------------------------------------- #


def document(**changes: object) -> dict:
    base = json.loads(json.dumps(sample().to_dict()))
    for key, value in changes.items():
        if value is _ABSENT:
            base.pop(key, None)
        else:
            base[key] = value
    return base


class _Absent:
    pass


_ABSENT = _Absent()


def test_wrong_schema_token_rejected() -> None:
    with pytest.raises(SchemaError, match="not a chirograph manifest"):
        Manifest.from_dict(document(schema="tessera"))


def test_future_major_version_rejected() -> None:
    with pytest.raises(UnsupportedVersion, match="major version"):
        Manifest.from_dict(document(schema_version="2.0"))


def test_higher_minor_version_is_tolerated_and_relaxes_unknown_fields() -> None:
    """Minor bumps are additive, so refusing them makes every new field breaking."""
    payload = document(schema_version="1.7", future_field="whatever")
    manifest = Manifest.from_dict(payload)
    assert manifest.schema_version == "1.7"
    assert manifest.extra["future_field"] == "whatever"


def test_unknown_header_field_rejected_at_our_own_version() -> None:
    with pytest.raises(SchemaError, match="unrecognised header field"):
        Manifest.from_dict(document(mystery=1))


def test_unknown_entry_field_rejected() -> None:
    payload = document()
    payload["files"][0]["mtime"] = 12345
    with pytest.raises(SchemaError, match="unrecognised field"):
        Manifest.from_dict(payload)


def test_unknown_fields_can_be_preserved_explicitly() -> None:
    payload = document(mystery="kept")
    manifest = Manifest.from_dict(payload, allow_unknown_fields=True, check_body_hash=False)
    assert manifest.extra["mystery"] == "kept"
    assert manifest.to_dict()["mystery"] == "kept"


def test_etag_algorithm_rejected_with_an_explanation() -> None:
    with pytest.raises(UnsupportedAlgorithm, match="reserved but not supported"):
        Manifest.from_dict(document(hash_algorithm="etag"))


def test_body_hash_mismatch_rejected() -> None:
    with pytest.raises(BodyHashMismatch, match="body_hash mismatch"):
        Manifest.from_dict(document(body_hash="0" * 32))


def test_nfiles_disagreement_rejected() -> None:
    with pytest.raises(SchemaError, match="nfiles"):
        Manifest.from_dict(document(nfiles=99))


def test_size_disagreement_rejected() -> None:
    with pytest.raises(SchemaError, match=r"entry sizes sum"):
        Manifest.from_dict(document(size=99))


def test_traversal_relpath_rejected_on_read() -> None:
    payload = document()
    payload["files"] = [{"relpath": "../../etc/passwd", "md5": MD5_A, "size": 6}]
    payload["nfiles"] = 1
    payload["size"] = 6
    with pytest.raises(PathSafetyError, match="path traversal"):
        Manifest.from_dict(payload)


def test_absolute_source_path_rejected_on_read() -> None:
    payload = document()
    payload["files"][0]["source_path"] = "/etc/passwd"
    with pytest.raises(PathSafetyError, match="absolute"):
        Manifest.from_dict(payload)


def test_null_field_rejected() -> None:
    with pytest.raises(SchemaError, match="null"):
        Manifest.from_dict(document(record_accession=None))


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(SchemaError, match="timezone offset"):
        Manifest.from_dict(document(created="2026-07-01T00:00:00"))


def test_offset_timestamp_is_normalised_to_utc() -> None:
    manifest = Manifest(created="2026-07-01T10:00:00+10:00")
    assert manifest.created == "2026-07-01T00:00:00Z"


def test_digest_length_must_match_the_algorithm() -> None:
    with pytest.raises(SchemaError, match="sha256 digests"):
        Manifest.from_dict(document(hash_algorithm="sha256"))


def test_negative_size_rejected() -> None:
    payload = document()
    payload["files"][0]["size"] = -1
    with pytest.raises(SchemaError, match="negative"):
        Manifest.from_dict(payload)


def test_bool_size_rejected() -> None:
    """bool is an int subclass in Python and would serialise as `true`."""
    payload = document()
    payload["files"][0]["size"] = True
    with pytest.raises(SchemaError, match="size must be an integer"):
        Manifest.from_dict(payload)


def test_bom_rejected(tmp_path: Path) -> None:
    target = tmp_path / "bom.chiro.json"
    target.write_bytes(b"\xef\xbb\xbf" + sample().to_bytes())
    with pytest.raises(SchemaError, match="BOM"):
        Manifest.load(target)


def test_load_error_names_the_file(tmp_path: Path) -> None:
    target = tmp_path / "bad.chiro.json"
    target.write_text('{"schema": "nope"}')
    with pytest.raises(SchemaError, match=r"bad\.chiro\.json"):
        Manifest.load(target)


def test_sha256_manifest_round_trips(tmp_path: Path) -> None:
    manifest = Manifest(files=[FileEntry("a.txt", SHA_A, 5)], hash_algorithm="sha256")
    target = manifest.dump(tmp_path / "s.chiro.json")
    assert Manifest.load(target).hash_algorithm == "sha256"
    assert len(manifest.body_hash) == 64


def test_dvc_style_algorithm_named_key_is_accepted_on_read() -> None:
    """A real DVC .dir with sha256 writes the digest under a 'sha256' key."""
    entry = FileEntry.from_dict(
        {"relpath": "a.txt", "sha256": SHA_A, "size": 5}, algorithm="sha256"
    )
    assert entry.md5 == SHA_A


# --------------------------------------------------------------------------- #
# Lint
# --------------------------------------------------------------------------- #


def codes(manifest: Manifest) -> set[str]:
    return {finding.code for finding in manifest.lint()}


def test_observed_without_source_url_or_retrieved_is_linted() -> None:
    manifest = Manifest(provenance_type=PROVENANCE_OBSERVED, files=[FileEntry("a", MD5_A, 1)])
    assert "observed-missing-source-url" in codes(manifest)
    assert "observed-missing-retrieved" in codes(manifest)


def test_self_deposited_with_retrieved_is_linted() -> None:
    manifest = Manifest(
        provenance_type=PROVENANCE_SELF,
        retrieved="2026-01-01T00:00:00Z",
        files=[FileEntry("a", MD5_A, 1)],
    )
    assert "self-deposited-with-retrieved" in codes(manifest)


def test_redundant_source_path_is_linted() -> None:
    manifest = Manifest(files=[FileEntry("a.txt", MD5_A, 1, source_path="a.txt")])
    assert "redundant-source-path" in codes(manifest)


def test_case_collision_is_linted() -> None:
    manifest = Manifest(files=[FileEntry("README.md", MD5_A, 1), FileEntry("readme.md", MD5_B, 1)])
    assert "case-collision" in codes(manifest)


def test_windows_reserved_name_is_linted() -> None:
    manifest = Manifest(files=[FileEntry("data/NUL.txt", MD5_A, 1)])
    assert "windows-reserved" in codes(manifest)


def test_zero_byte_file_is_linted() -> None:
    manifest = Manifest(files=[FileEntry("a", MD5_A, 0)])
    assert "zero-byte-files" in codes(manifest)


def test_clean_manifest_lints_clean() -> None:
    assert codes(sample()) == set()


# --------------------------------------------------------------------------- #
# Regressions from the adversarial review
# --------------------------------------------------------------------------- #


def test_url_join_works_for_non_hierarchical_schemes() -> None:
    """urljoin returns the reference unchanged for schemes it doesn't know.

    ``geo://`` is the spec's own §3.2 example and ``s3://`` is named in §3.2 and
    §6.1, so this was the common case, not an edge one: fetch_plan() handed a
    consumer a bare relative path where a URL belonged.
    """
    for base in ("geo://GSE12345/", "s3://bucket/pre/", "gs://b/p/", "ega://EGAD001/"):
        manifest = Manifest(files=[FileEntry("a/b.gz", MD5_A, 1)], source_url=base)
        url = manifest.fetch_plan()[0].url
        assert url == base.rstrip("/") + "/a/b.gz", url
        assert "://" in url


def test_url_join_keeps_the_base_path_when_there_is_a_query() -> None:
    """Appending the slash to a query is how the base path used to get discarded."""
    manifest = Manifest(
        files=[FileEntry("a/b.gz", MD5_A, 1)],
        source_url="https://ega.example.org/files?dataset=EGAD001",
    )
    assert manifest.fetch_plan()[0].url == ("https://ega.example.org/files/a/b.gz?dataset=EGAD001")


def test_url_join_keeps_the_base_path_when_there_is_a_fragment() -> None:
    manifest = Manifest(
        files=[FileEntry("a/b.gz", MD5_A, 1)],
        source_url="https://zenodo.org/api/records/123/files#top",
    )
    assert manifest.fetch_plan()[0].url == ("https://zenodo.org/api/records/123/files/a/b.gz#top")


def test_url_join_validates_its_source_argument() -> None:
    """It is exported as the spec §9 helper, so it cannot trust its caller."""
    from chirograph import join_source_url

    for hostile in ("//evil.example/x", "sub/../../x", "/abs", ".."):
        with pytest.raises(PathSafetyError):
            join_source_url("https://host/base/", hostile)


def test_surrogate_relpath_is_a_path_safety_error_not_a_crash() -> None:
    """A "\\udcff" escape is pure ASCII on the wire, so no transport rejects it.

    Before, ``path.encode("utf-8")`` raised UnicodeEncodeError, which escaped
    every ``except ChirographError`` and aborted a whole `validate` batch.
    """
    with pytest.raises(PathSafetyError, match="surrogate"):
        validate_path("a\udcffb")


def test_surrogate_in_canonicalisation_is_a_schema_error() -> None:
    from chirograph.canonical import canonicalise

    with pytest.raises(SchemaError, match="UTF-8"):
        canonicalise([{"relpath": "a\udcffb", "md5": MD5_A, "size": 1}])


def test_uppercase_digest_is_rejected_with_a_message_about_case() -> None:
    """Silently lower-casing broke the "no semantic rewriting" promise (D-06).

    A manifest whose body_hash was exactly what §4 yields for its own uppercase
    files array was rejected as *corrupt*, which is both wrong and unhelpful.
    """
    document = document_with_uppercase_digest()
    with pytest.raises(SchemaError, match="uppercase"):
        Manifest.from_dict(document)


def document_with_uppercase_digest() -> dict:
    from chirograph.canonical import compute_body_hash

    files = [{"relpath": "a.txt", "md5": MD5_A.upper(), "size": 6}]
    return {
        "schema": "chirograph",
        "schema_version": "1.0",
        "hash_algorithm": "md5",
        "provenance_type": "self-deposited",
        "created": "2026-01-01T00:00:00Z",
        "size": 6,
        "nfiles": 1,
        "body_hash": compute_body_hash(files),
        "files": files,
    }


def test_uppercase_body_hash_is_rejected_as_a_case_problem() -> None:
    document = json.loads(json.dumps(sample().to_dict()))
    document["body_hash"] = document["body_hash"].upper()
    with pytest.raises(SchemaError, match="uppercase"):
        Manifest.from_dict(document)


def test_extra_cannot_shadow_a_real_entry_field() -> None:
    """extra is serialised after the known fields, so it would win."""
    with pytest.raises(SchemaError, match="collide"):
        FileEntry("safe.txt", MD5_A, 1, extra={"relpath": "../../etc/evil"})


def test_extra_cannot_shadow_the_computed_header_fields() -> None:
    with pytest.raises(SchemaError, match="collide"):
        Manifest(
            files=[FileEntry("a.txt", MD5_A, 1)],
            extra={"body_hash": "deadbeef", "size": 999, "nfiles": 999},
        )


def test_extra_cannot_shadow_files() -> None:
    with pytest.raises(SchemaError, match="collide"):
        Manifest(files=[FileEntry("a.txt", MD5_A, 1)], extra={"files": []})


def test_non_nfc_relpath_with_an_unknown_field_does_not_crash() -> None:
    """normalise_order used to rebuild the entry with FileEntry(**row).

    The row is a flattened mapping that includes `extra`, so the combination of a
    non-NFC path and a forward-compatible unknown field raised TypeError.
    """
    manifest = Manifest(files=[FileEntry("cafe\u0301.txt", MD5_A, 1, extra={"future_field": 1})])
    assert manifest.files[0].relpath == "caf\u00e9.txt"  # folded to NFC
    assert manifest.files[0].extra == {"future_field": 1}


# --------------------------------------------------------------------------- #
# Regressions from the second review pass
# --------------------------------------------------------------------------- #


def reencoded(manifest_path: Path, encoding: str, bom: bytes = b"") -> bytes:
    return bom + manifest_path.read_text(encoding="utf-8").encode(encoding)


def test_non_utf8_encodings_are_refused(tmp_path: Path) -> None:
    """`json.loads` accepts *bytes* and auto-detects UTF-16/32 from their BOMs.

    So reading the file as bytes and handing them straight to json.loads silently
    accepted manifests in encodings spec §1.2 forbids — and a re-encoded manifest
    has a different whole-file hash, so a registry would store two objects under
    one chiro:// reference.
    """
    good = sample().dump(tmp_path / "good.chiro.json")
    for encoding, bom in [
        ("utf-16", b""),  # codec emits its own BOM
        ("utf-16-be", b"\xfe\xff"),
        ("utf-32", b""),
        ("utf-8", b"\xef\xbb\xbf"),
    ]:
        target = tmp_path / f"{encoding}.chiro.json"
        target.write_bytes(reencoded(good, encoding, bom))
        with pytest.raises(SchemaError, match=r"UTF-8|byte-order mark"):
            Manifest.load(target)


def test_non_utf8_body_is_a_schema_error_not_a_value_error(tmp_path: Path) -> None:
    """UnicodeDecodeError is a ValueError, not a JSONDecodeError.

    A caller catching only the latter let a latin-1 body escape as an uncaught
    error, which aborted a whole `chiro validate` batch.
    """
    target = tmp_path / "latin1.chiro.json"
    target.write_bytes(sample().to_json().replace("sampleA", "caf\u00e9").encode("latin-1"))
    with pytest.raises(SchemaError, match="not valid UTF-8"):
        Manifest.load(target)


def test_entry_validates_on_construction_not_only_on_read() -> None:
    """The producer side used to be able to emit paths that never saw validation."""
    for kwargs in (
        {"relpath": "../../etc/passwd", "md5": MD5_A, "size": 1},
        {"relpath": "ok.txt", "md5": MD5_A, "size": 1, "source_path": "/abs/leak"},
        {"relpath": "ok.txt", "md5": MD5_A.upper(), "size": 1},
        {"relpath": "ok.txt", "md5": "z" * 32, "size": 1},
        {"relpath": "ok.txt", "md5": "abc", "size": 1},
        {"relpath": "ok.txt", "md5": MD5_A, "size": -1},
        {"relpath": "ok.txt", "md5": MD5_A, "size": True},
        {"relpath": 123, "md5": MD5_A, "size": 1},
    ):
        with pytest.raises((SchemaError, PathSafetyError)):
            FileEntry(**kwargs)  # type: ignore[arg-type]


def test_entry_construction_normalises_to_nfc() -> None:
    assert FileEntry("cafe\u0301.txt", MD5_A, 1).relpath == "caf\u00e9.txt"


def test_replace_still_revalidates() -> None:
    from dataclasses import replace

    entry = FileEntry("ok.txt", MD5_A, 1)
    assert replace(entry, size=2).size == 2
    with pytest.raises(PathSafetyError):
        replace(entry, relpath="../escape")


def test_non_string_relpath_is_rejected_by_the_constructor() -> None:
    """Where the "normalise_order leaks TypeError" fix ended up.

    Adding validation to ``__post_init__`` in the same commit made the
    ``normalise_order`` type check unreachable — no public API can get a
    non-string ``relpath`` past the constructor — so that branch was removed and
    this is the behaviour that actually holds. It is a ``PathSafetyError``, not the
    ``SchemaError`` the removed branch raised.
    """
    with pytest.raises(PathSafetyError, match="must be a string"):
        FileEntry(relpath=123, md5=MD5_A, size=1)  # type: ignore[arg-type]

    # And through the loader, where the field name is reported.
    payload = document()
    payload["files"] = [{"relpath": 123, "md5": MD5_A, "size": 6}]
    with pytest.raises(PathSafetyError, match=r"files\[0\]\.relpath"):
        Manifest.from_dict(payload)
