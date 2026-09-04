#!/usr/bin/env python3
"""Generate ``src/chirograph/schemas/chirograph-1.0.schema.json``.

Written as a generator rather than typed by hand because the path-safety pattern
is a nest of lookaheads whose backslash escaping is very easy to get wrong when
written directly into JSON. Run it, then check the output in;
``tests/test_schema.py`` asserts the checked-in file still matches this output.

Note the character-class escapes are spelled ``\\x00`` etc. rather than embedded
literally, so that neither this file nor the generated JSON contains a raw
control character.
"""

from __future__ import annotations

import json
import pathlib

# Reject: absolute paths, doubled/trailing separators, "." and ".." components,
# Windows drive prefixes, control characters, and backslashes.
PATH_PATTERN = (
    r"^"
    r"(?!/)"  # not absolute
    r"(?!.*//)"  # no empty interior component
    r"(?!.*/$)"  # no trailing separator
    r"(?!(?:.*/)?\.\.?(?:/|$))"  # no "." or ".." component
    r"(?![A-Za-z]:)"  # not a Windows drive path
    r"[^\x00-\x1f\x7f\\]+"  # no control characters, no backslash
    r"$"
)

TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$"

# Lowercase only, per docs/decisions.md D-18: canonicalisation hashes the array as
# given, so accepting either case would give one manifest two body_hash values.
HEX_ANY = r"^([0-9a-f]{32}|[0-9a-f]{64})$"
HEX_MD5 = r"^[0-9a-f]{32}$"
HEX_SHA256 = r"^[0-9a-f]{64}$"

RELPATH = {
    "type": "string",
    "minLength": 1,
    "maxLength": 4096,
    "pattern": PATH_PATTERN,
    "description": (
        "Workspace path: where the file is materialised on checkout, relative to the "
        "import target. POSIX-separated, relative, NFC-normalised. Unique within a "
        "manifest, and the primary sort key."
    ),
}

SOURCE_PATH = dict(RELPATH)
SOURCE_PATH["description"] = (
    "Deposit/source path: where to fetch the bytes from, relative to source_url. "
    "Present only when the deposit layout differs from the workspace layout; if "
    "omitted, source_path == relpath."
)

ENTRY = {
    "type": "object",
    "required": ["relpath", "md5", "size"],
    "additionalProperties": False,
    "properties": {
        "relpath": RELPATH,
        "md5": {
            "type": "string",
            "pattern": HEX_ANY,
            "description": (
                "Hash of the file's bytes, per the manifest's hash_algorithm. Named "
                "'md5' for DVC .dir alignment even when the algorithm is sha256. Lowercase "
                "hexadecimal only (docs/decisions.md D-18); the exact length is constrained "
                "by hash_algorithm in the allOf below."
            ),
        },
        "size": {"type": "integer", "minimum": 0, "description": "File size in bytes."},
        "source_path": SOURCE_PATH,
        "isexec": {"type": "boolean", "description": "DVC-compat: execute bit."},
        "version_id": {
            "type": "string",
            "minLength": 1,
            "description": "DVC-compat: cloud-provider version id for versioned buckets.",
        },
        "etag": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Supplementary and NON-AUTHORITATIVE: the repository's native HTTP/S3 "
                "validator as observed. Never the content hash - an S3 multipart etag "
                "depends on the part size used at upload and so is not a function of "
                "content alone. See docs/decisions.md D-04."
            ),
        },
        "file_version": {
            "type": "string",
            "minLength": 1,
            "description": (
                "(1.1) The source's per-file change signal, as observed (e.g. a per-file "
                "mtime). Advisory only: a changed value is evidence of change, an unchanged "
                "value is evidence of nothing. Covered by body_hash. See spec section 8."
            ),
        },
    },
}

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://swarbricklab.github.io/chirograph/schemas/chirograph-1.0.schema.json",
    "title": "Chirograph manifest (.chiro.json) 1.0",
    "description": (
        "A manifest for a scientific-data deposition: the files it contains, their "
        "checksums and sizes, plus provenance. Mirrors DVC's .dir body shape "
        "(relpath/md5/size) with an added header block. See docs/chiro-format-spec.md. "
        "IMPORTANT: JSON Schema can express the shape of a manifest but not its "
        "integrity. It cannot check that size == sum(files[].size), that nfiles == "
        "len(files), that relpath values are unique, or that body_hash is the hash of "
        "the canonicalised files array. Schema validity is necessary, not sufficient - "
        "run `chiro validate` for the rest."
    ),
    "type": "object",
    "required": [
        "schema",
        "schema_version",
        "hash_algorithm",
        "provenance_type",
        "created",
        "size",
        "nfiles",
        "body_hash",
        "files",
    ],
    "additionalProperties": False,
    "properties": {
        "schema": {
            "const": "chirograph",
            "description": (
                "Format identifier. The fallback when the filename has been mangled by a "
                "repository that dislikes double extensions."
            ),
        },
        "schema_version": {
            "type": "string",
            "pattern": r"^\d+\.\d+$",
            "description": (
                "Format version. A differing major version is a hard error for a reader; "
                "a higher minor version is additive."
            ),
        },
        "hash_algorithm": {
            "enum": ["md5", "sha256"],
            "description": (
                "Algorithm for body_hash and every files[].md5. md5 is the default "
                "(DVC-aligned). 'etag' is reserved but NOT valid in 1.0 - see "
                "docs/decisions.md D-04."
            ),
        },
        "provenance_type": {
            "enum": ["self-deposited", "observed-download"],
            "description": (
                "Which guarantee the manifest offers. 'self-deposited': integrity from "
                "source, authoritative. 'observed-download': reproducibility provenance "
                "for bytes retrieved at a point in time; NOT authoritative."
            ),
        },
        "record_accession": {
            "type": "string",
            "minLength": 1,
            "description": "Deposition accession this manifest describes (GSE..., EGAD...).",
        },
        "concept": {
            "type": "string",
            "minLength": 1,
            "description": (
                "(1.1) Mutable tracker for the origin record, as a CURIE (geo:GSE12345, "
                "doi:10.5281/zenodo.7654320) - what a staleness check re-resolves to find a "
                "newer version. Distinct from source_url (the fetch base). See spec section 8."
            ),
        },
        "record_version": {
            "type": "string",
            "minLength": 1,
            "description": (
                "(1.1) The source's record-level version at the observed time (a date, "
                "version DOI, or serial). Advisory - compared for change only, never "
                "authoritative for content. See spec section 8."
            ),
        },
        "source_url": {
            "type": "string",
            "minLength": 1,
            "format": "uri",
            "description": (
                "Base location of the data, and the base against which source_path "
                "resolves. Required by convention for observed-download."
            ),
        },
        "created": {
            "type": "string",
            "pattern": TIMESTAMP_PATTERN,
            "description": "ISO 8601 with an explicit offset. When the manifest was written.",
        },
        "retrieved": {
            "type": "string",
            "pattern": TIMESTAMP_PATTERN,
            "description": (
                "ISO 8601 with an explicit offset. When the bytes were fetched. Required "
                "by convention for observed-download; omit for self-deposited."
            ),
        },
        "generator": {
            "type": "string",
            "minLength": 1,
            "description": "Tool and version that wrote the file.",
        },
        "size": {
            "type": "integer",
            "minimum": 0,
            "description": "Sum of all files[].size, in bytes.",
        },
        "nfiles": {
            "type": "integer",
            "minimum": 0,
            "description": "Number of entries in files.",
        },
        "body_hash": {
            "type": "string",
            "pattern": HEX_ANY,
            "description": (
                "Hash of the canonicalised files array (spec §4). NOT equal to a DVC .dir "
                "hash - see docs/decisions.md D-01."
            ),
        },
        "files": {
            "type": "array",
            "items": ENTRY,
            "description": "The body. Sorted by relpath on disk; relpath values are unique.",
        },
    },
    "allOf": [
        {
            "if": {
                "properties": {"hash_algorithm": {"const": "md5"}},
                "required": ["hash_algorithm"],
            },
            "then": {
                "properties": {
                    "body_hash": {"pattern": HEX_MD5},
                    "files": {"items": {"properties": {"md5": {"pattern": HEX_MD5}}}},
                }
            },
        },
        {
            "if": {
                "properties": {"hash_algorithm": {"const": "sha256"}},
                "required": ["hash_algorithm"],
            },
            "then": {
                "properties": {
                    "body_hash": {"pattern": HEX_SHA256},
                    "files": {"items": {"properties": {"md5": {"pattern": HEX_SHA256}}}},
                }
            },
        },
    ],
}


def render() -> str:
    return json.dumps(SCHEMA, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    out = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src"
        / "chirograph"
        / "schemas"
        / "chirograph-1.0.schema.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
