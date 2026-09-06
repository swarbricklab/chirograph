"""Exception hierarchy for chirograph.

Every error raised by the library derives from :class:`ChirographError`, so a
consumer (e.g. ``archivefs``) can catch the whole surface with one ``except``.

The split matters for consumers:

* :class:`ManifestError` and subclasses mean *the manifest is bad* — refuse it.
* :class:`IntegrityError` and subclasses mean *the bytes do not match a
  manifest that is itself fine* — the interesting case, because for an
  ``observed-download`` manifest it usually means the upstream drifted rather
  than that anything is corrupt (see the registry design note, §7b).
"""

from __future__ import annotations

__all__ = [
    "BodyHashMismatch",
    "ChirographError",
    "DvcError",
    "HashMismatch",
    "IntegrityError",
    "ManifestError",
    "PathSafetyError",
    "RefError",
    "SchemaError",
    "SizeMismatch",
    "UnsupportedAlgorithm",
    "UnsupportedVersion",
]


class ChirographError(Exception):
    """Base class for every error raised by chirograph."""


# --------------------------------------------------------------------------- #
# The manifest itself is unusable
# --------------------------------------------------------------------------- #


class ManifestError(ChirographError):
    """The manifest is malformed, inconsistent, or otherwise unusable."""


class SchemaError(ManifestError):
    """The manifest does not conform to the ``.chiro.json`` schema."""


class UnsupportedVersion(ManifestError):
    """``schema_version`` names a format this reader cannot handle."""


class UnsupportedAlgorithm(ManifestError):
    """``hash_algorithm`` names an algorithm this version does not support.

    Notably raised for ``"etag"``, which spec §3.1 lists as permitted but which
    v1 rejects — see ``docs/decisions.md`` D-04.
    """


class PathSafetyError(ManifestError):
    """A ``relpath``/``source_path`` is unsafe to materialise to disk.

    Absolute paths, ``..`` components, and similar. Raised on *read* as well as
    write, because ``observed-download`` manifests describe other people's
    deposits and are therefore untrusted input.
    """


# --------------------------------------------------------------------------- #
# The manifest is fine; the bytes disagree with it
# --------------------------------------------------------------------------- #


class IntegrityError(ChirographError):
    """Content does not match what a manifest asserts about it."""


class BodyHashMismatch(IntegrityError):
    """Layer 2: the recomputed ``body_hash`` differs from the recorded one.

    The ``files`` array has been truncated, reordered-and-edited, or corrupted.
    """


class HashMismatch(IntegrityError):
    """Layer 1: a payload file's bytes do not hash to the recorded value."""


class SizeMismatch(IntegrityError):
    """A payload file's size differs from the recorded value.

    Checked before hashing because it is free and catches the common cases
    (truncated download, wrong file) without reading the whole file.
    """


# --------------------------------------------------------------------------- #
# Peripheral
# --------------------------------------------------------------------------- #


class RefError(ChirographError):
    """A ``chiro://`` reference is malformed or does not match its target."""


class DvcError(ChirographError):
    """Something went wrong reading a DVC ``.dvc`` file, lockfile, or cache."""
