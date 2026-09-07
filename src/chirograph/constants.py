"""Frozen constants of the ``.chiro.json`` format.

Nothing in here may change without a ``schema_version`` bump. The values are
gathered in one module so that a reimplementer has a single place to read the
format's vocabulary off, and so that no other module hard-codes a literal.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CONTAINER_FIELD_ORDER",
    "CONTAINER_FORMATS",
    "CONTAINER_FORMAT_FALLBACK_CLASS",
    "DEFAULT_HASH_ALGORITHM",
    "ENTRY_FIELD_ORDER",
    "HASH_ALGORITHMS",
    "HEADER_FIELD_ORDER",
    "MANIFEST_SUFFIX",
    "PROVENANCE_OBSERVED",
    "PROVENANCE_SELF",
    "PROVENANCE_TYPES",
    "REQUIRED_CONTAINER_FIELDS",
    "RESERVED_HASH_ALGORITHMS",
    "SCHEMA",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_MAJOR_2",
    "SCHEMA_VERSION_MINOR_1",
    "SUPPORTED_MAJOR",
    "SUPPORTED_MINOR",
    "SUPPORTED_MINORS",
]

#: Value of the internal ``schema`` field. The fallback identifier when the
#: filename has been mangled by a repository that dislikes double extensions
#: (spec §1.1).
SCHEMA: Final = "chirograph"

#: Format version this implementation writes *by default* — i.e. for a manifest
#: that uses no field newer than 1.0. A manifest carrying a 1.1 field (``concept``,
#: ``record_version``, ``file_version``) advertises ``SCHEMA_VERSION_MINOR_1``
#: instead; see ``Manifest.__post_init__``. Keeping the floor at 1.0 is what makes
#: every existing manifest byte-identical.
SCHEMA_VERSION: Final = "1.0"

#: The minor bump that adds the optional staleness fields (spec §8).
SCHEMA_VERSION_MINOR_1: Final = "1.1"

#: The major bump that adds the ``containers`` block for ZIP/tar members
#: (docs/container-model.md). A manifest is ``2.0`` iff it carries containers;
#: everything else stays ``1.x`` and byte-identical.
SCHEMA_VERSION_MAJOR_2: Final = "2.0"

#: Highest implemented *minor* per major that this implementation reads strictly.
#: A major beyond the keys is a hard error; a minor above its major's entry is
#: additive and merely relaxes unknown-field strictness (docs/decisions.md D-07).
#: Major 1 goes up to the staleness fields (1.1); major 2 is the container block
#: (2.0).
SUPPORTED_MINORS: Final[dict[int, int]] = {1: 1, 2: 0}

#: Highest readable major, and its highest implemented minor — derived from
#: :data:`SUPPORTED_MINORS` so there is one source of truth.
SUPPORTED_MAJOR: Final = max(SUPPORTED_MINORS)
SUPPORTED_MINOR: Final = SUPPORTED_MINORS[SUPPORTED_MAJOR]

#: Compound extension identifying a manifest to humans and to globs (spec §1.1).
MANIFEST_SUFFIX: Final = ".chiro.json"

# --------------------------------------------------------------------------- #
# Hash algorithms
# --------------------------------------------------------------------------- #

#: Supported algorithms, mapped to their hex-digest length. md5 is the default
#: for DVC alignment; sha256 is permitted for callers whose policy forbids md5
#: (e.g. FIPS-mode interpreters), at the cost of DVC ``.dir`` interop.
HASH_ALGORITHMS: Final[dict[str, int]] = {
    "md5": 32,
    "sha256": 64,
}

DEFAULT_HASH_ALGORITHM: Final = "md5"

#: Recognised but *rejected* in v1. Kept in a named set so the error message can
#: say "reserved, not supported in v1" rather than "unknown algorithm", which
#: would misleadingly suggest a typo. Rationale in docs/decisions.md D-04.
RESERVED_HASH_ALGORITHMS: Final[frozenset[str]] = frozenset({"etag"})

# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

PROVENANCE_SELF: Final = "self-deposited"
PROVENANCE_OBSERVED: Final = "observed-download"
PROVENANCE_TYPES: Final[tuple[str, ...]] = (PROVENANCE_SELF, PROVENANCE_OBSERVED)

# --------------------------------------------------------------------------- #
# Field inventories and on-disk ordering
# --------------------------------------------------------------------------- #

#: Header field order used when serialising. Purely cosmetic — canonicalisation
#: (and therefore ``body_hash``) is independent of key order — but a stable
#: order makes manifests diff cleanly in Git, which is how they are shared
#: (see docs/howto-publish.md).
HEADER_FIELD_ORDER: Final[tuple[str, ...]] = (
    "schema",
    "schema_version",
    "hash_algorithm",
    "provenance_type",
    "record_accession",
    "concept",  # 1.1
    "source_url",
    "created",
    "retrieved",
    "record_version",  # 1.1
    "generator",
    "size",
    "nfiles",
    "body_hash",
)

#: Entry field order used when serialising, for the same reason.
ENTRY_FIELD_ORDER: Final[tuple[str, ...]] = (
    "relpath",
    "md5",
    "size",
    "source_path",
    "isexec",
    "version_id",
    "etag",
    "file_version",  # 1.1
    "crc32",  # 2.0
)

#: Container descriptor field order (2.0, docs/container-model.md §3).
CONTAINER_FIELD_ORDER: Final[tuple[str, ...]] = (
    "source_root",
    "format",
    "md5",
    "size",
    "etag",
    "version_id",
)

#: Required container fields.
REQUIRED_CONTAINER_FIELDS: Final[frozenset[str]] = frozenset({"source_root", "format", "md5"})

#: Blessed container formats, mapped to their confirm class (container-model §6):
#: ``indexed`` (per-member CRC, metadata-only confirm — zip), ``sequential``
#: (targeted read — tar), ``stream`` (whole-stream, download to confirm). An
#: *unrecognised* format is treated as ``stream`` (opaque), never rejected — the
#: forward-compatibility rule, so a newer format stays usable by an older reader.
CONTAINER_FORMATS: Final[dict[str, str]] = {
    "zip": "indexed",
    "tar": "sequential",
    "tar.gz": "stream",
    "tar.bz2": "stream",
    "tar.xz": "stream",
    "tar.zst": "stream",
}

#: Confirm class assigned to a container whose ``format`` is not in
#: :data:`CONTAINER_FORMATS` (spec §8 opaque fallback).
CONTAINER_FORMAT_FALLBACK_CLASS: Final = "stream"

#: Header fields that must be present.
REQUIRED_HEADER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "schema_version",
        "hash_algorithm",
        "provenance_type",
        "created",
        "size",
        "nfiles",
        "body_hash",
    }
)

#: Entry fields that must be present.
REQUIRED_ENTRY_FIELDS: Final[frozenset[str]] = frozenset({"relpath", "md5", "size"})
