"""The published JSON Schema.

Two jobs: that the checked-in schema still matches its generator, and that it
accepts/rejects the same documents the library does — with the documented
exception of the integrity constraints, which JSON Schema simply cannot express.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from chirograph.manifest import FileEntry, Manifest
from chirograph.schema import schema_document, schema_path, schema_text, validate_document
from chirograph.vectors import load_vectors

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parent.parent


def sample_document() -> dict:
    manifest = Manifest(
        files=[FileEntry("a.txt", "b1946ac92492d2347c6235b4d2611184", 6)],
        record_accession="GSE1",
        source_url="https://example.org/suppl/",
        created="2026-07-01T00:00:00Z",
    )
    return manifest.to_dict()


def test_checked_in_schema_matches_its_generator() -> None:
    """The schema is generated because its path regex is hard to hand-escape."""
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import gen_schema
    finally:
        sys.path.pop(0)
    assert schema_text() == gen_schema.render(), (
        "src/chirograph/schemas/chirograph-1.0.schema.json is stale — re-run tools/gen_schema.py"
    )


def test_schema_is_valid_json_schema() -> None:
    validator = jsonschema.validators.validator_for(schema_document())
    validator.check_schema(schema_document())


def test_schema_path_exists() -> None:
    assert schema_path().is_file()
    assert schema_path().name == "chirograph-1.0.schema.json"


def test_schema_admits_the_library_output() -> None:
    assert validate_document(sample_document()) == []


def test_schema_admits_every_valid_vector() -> None:
    for case in load_vectors()["manifests"]["valid"]:
        assert validate_document(case["document"]) == [], case["name"]


def test_schema_documents_its_own_limits() -> None:
    """It must say so, because 'schema-valid' reads as 'valid' otherwise."""
    description = schema_document()["description"]
    assert "necessary, not sufficient" in description
    assert "body_hash" in description


# --------------------------------------------------------------------------- #
# What the schema does catch
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema": "tessera"},
        {"hash_algorithm": "etag"},
        {"provenance_type": "guessed"},
        {"schema_version": "1"},
        {"created": "2026-07-01"},
        {"created": "2026-07-01T00:00:00"},
        {"size": -1},
        {"nfiles": "1"},
        {"mystery": 1},
        {"body_hash": "xyz"},
    ],
    ids=lambda m: "-".join(m),
)
def test_schema_rejects_bad_headers(mutation: dict) -> None:
    """Assert on the reported *location*, not jsonschema's wording, which varies."""
    document = {**sample_document(), **mutation}
    errors = validate_document(document)
    assert errors, f"{mutation} should have been rejected"
    field = next(iter(mutation))
    # Unknown properties are reported against the root, everything else by name.
    expected_location = "<root>" if field == "mystery" else field
    assert any(error.startswith(expected_location) for error in errors), errors


@pytest.mark.parametrize(
    "relpath",
    ["/abs", "a//b", "a/", "..", ".", "a/../b", "./a", "C:/x", "a\\b"],
)
def test_schema_rejects_unsafe_paths(relpath: str) -> None:
    document = sample_document()
    document["files"] = [{"relpath": relpath, "md5": "0" * 32, "size": 1}]
    assert validate_document(document), f"{relpath!r} should have been rejected"


@pytest.mark.parametrize("relpath", ["a.txt", "sub/b.txt", "..hidden", "a-b/x", "x.tar.gz"])
def test_schema_accepts_safe_paths(relpath: str) -> None:
    document = sample_document()
    document["files"] = [{"relpath": relpath, "md5": "0" * 32, "size": 1}]
    document["nfiles"] = 1
    document["size"] = 1
    assert validate_document(document) == []


def test_schema_ties_digest_length_to_the_algorithm() -> None:
    document = sample_document()
    document["hash_algorithm"] = "sha256"
    assert validate_document(document), "a 32-char digest under sha256 must be rejected"


def test_schema_rejects_unknown_entry_fields() -> None:
    document = sample_document()
    document["files"][0]["mtime"] = 1
    assert validate_document(document)


# --------------------------------------------------------------------------- #
# What the schema cannot catch — the documented gap
# --------------------------------------------------------------------------- #


def test_schema_cannot_catch_integrity_violations() -> None:
    """Exactly why `chiro load` does its own checking rather than delegating."""
    from chirograph.errors import ChirographError

    for mutation in ({"body_hash": "0" * 32}, {"nfiles": 99}, {"size": 12345}):
        document = {**sample_document(), **mutation}
        assert validate_document(document) == [], f"schema unexpectedly caught {mutation}"
        with pytest.raises(ChirographError):
            Manifest.from_dict(document)


def test_schema_cannot_catch_duplicate_relpaths() -> None:
    from chirograph.errors import SchemaError

    document = sample_document()
    entry = {"relpath": "a.txt", "md5": "0" * 32, "size": 1}
    document["files"] = [entry, dict(entry)]
    document["nfiles"] = 2
    document["size"] = 2
    assert validate_document(document) == []
    with pytest.raises(SchemaError, match="duplicate relpath"):
        Manifest.from_dict(document)


def test_path_pattern_is_ecma_compatible() -> None:
    """JSON Schema patterns are ECMA-262; check the lookaheads behave in `re` too."""
    pattern = schema_document()["properties"]["files"]["items"]["properties"]["relpath"]["pattern"]
    compiled = re.compile(pattern)
    assert compiled.match("a/b.txt")
    assert not compiled.match("../x")


def test_schema_is_self_describing_for_editors() -> None:
    document = schema_document()
    assert document["$id"].endswith("chirograph-1.0.schema.json")
    assert document["$schema"].startswith("https://json-schema.org/")
    for name, definition in document["properties"].items():
        assert "description" in definition or "const" in definition, name


def test_schema_ships_inside_the_package() -> None:
    """So `chiro schema` works from an installed wheel."""
    import chirograph

    packaged = Path(chirograph.__file__).parent / "schemas" / "chirograph-1.0.schema.json"
    assert packaged.is_file()
    assert json.loads(packaged.read_text())["title"].startswith("Chirograph manifest")
