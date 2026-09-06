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
    "SCHEMA_VERSION_MINOR_1",
    "SUPPORTED_MAJOR",
    "SUPPORTED_MINOR",
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

#: Highest ``schema_version`` this implementation can read. A differing *major*
#: is a hard error; a higher *minor* is additive and merely relaxes unknown-field
#: strictness (see docs/decisions.md D-07). Bumped to 1 for the 1.1 fields, so a
#: ``1.1`` document is read *strictly* (its fields are known) and only ``1.2+`` is
#: tolerated leniently.
SUPPORTED_MAJOR: Final = 1
SUPPORTED_MINOR: Final = 1

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
