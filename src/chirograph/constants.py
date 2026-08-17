"""Frozen constants of the ``.chiro.json`` format.

Nothing in here may change without a ``schema_version`` bump. The values are
gathered in one module so that a reimplementer has a single place to read the
format's vocabulary off, and so that no other module hard-codes a literal.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DEFAULT_HASH_ALGORITHM",
    "ENTRY_FIELD_ORDER",
    "HASH_ALGORITHMS",
    "HEADER_FIELD_ORDER",
    "MANIFEST_SUFFIX",
    "PROVENANCE_OBSERVED",
    "PROVENANCE_SELF",
    "PROVENANCE_TYPES",
    "RESERVED_HASH_ALGORITHMS",
    "SCHEMA",
    "SCHEMA_VERSION",
    "SUPPORTED_MAJOR",
    "SUPPORTED_MINOR",
]

#: Value of the internal ``schema`` field. The fallback identifier when the
#: filename has been mangled by a repository that dislikes double extensions
#: (spec §1.1).
SCHEMA: Final = "chirograph"

#: Format version this implementation writes.
SCHEMA_VERSION: Final = "1.0"

#: Highest ``schema_version`` this implementation can read. A differing *major*
#: is a hard error; a higher *minor* is additive and merely relaxes unknown-field
#: strictness (see docs/decisions.md D-07).
SUPPORTED_MAJOR: Final = 1
SUPPORTED_MINOR: Final = 0

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
#: (spec §7).
HEADER_FIELD_ORDER: Final[tuple[str, ...]] = (
    "schema",
    "schema_version",
    "hash_algorithm",
    "provenance_type",
    "record_accession",
    "source_url",
    "created",
    "retrieved",
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
)

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
