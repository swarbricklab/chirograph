"""Canonicalisation of the ``files`` array, and the Layer-2 ``body_hash``.

This is the fragility point of the whole format. A reimplementation that
canonicalises *almost* identically produces non-matching hashes silently, so
every rule below is stated explicitly and pinned by the frozen test vectors in
``chirograph/vectors/canonicalisation.json`` (``chiro test-vectors --check``).

The canonical form (spec §4)::

    1. NFC-normalise every relpath / source_path.
    2. Sort entries by relpath, as a plain string comparison of the POSIX path.
    3. json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    4. Encode UTF-8. body_hash = HEX(hash_algorithm(that)).

Four properties are worth spelling out because they are where reimplementations
go wrong:

**Order of operations.** Normalise *then* sort, never the reverse. NFD's
combining marks sort differently from the characters they compose into:
``"cafe\\u0301"`` sorts before ``"caff"``, but its NFC form ``"café"`` sorts
*after*. Sorting first and normalising second silently yields a different array.

**Sort key is the joined string, not the component tuple.** ``"a-b/x"`` sorts
before ``"a/b"`` because ``-`` (U+002D) precedes ``/`` (U+002F); splitting into
components first would reverse them. This matches DVC, which sorts by the
already-joined ``relpath``.

**No semantic rewriting.** Canonicalisation is a pure syntactic function of the
array it is handed. In particular a ``source_path`` that happens to equal its
``relpath`` is *not* dropped, so such a manifest hashes differently from the
equivalent terse one. Writers omit redundant ``source_path``; canonicalisers
must not, or two implementations will disagree about which normalisations to
apply. (docs/decisions.md D-06.)

**Duplicate relpaths are rejected, not sorted stably.** With two equal sort keys
the output would depend on input order, so there would be no canonical form at
all. Rejecting keeps the function total and deterministic.

Independent of on-disk pretty-printing, key order, and indentation — which is
the point, since these files live in Git and get reformatted by editors.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

from .errors import SchemaError
from .hashing import hash_bytes
from .paths import nfc

__all__ = [
    "canonical_text",
    "canonicalise",
    "compute_body_hash",
    "dvc_dir_bytes",
    "dvc_dir_hash",
    "normalise_entries",
]

#: Path-valued entry fields, which are NFC-normalised before sorting.
_PATH_FIELDS = ("relpath", "source_path")

Entry = Mapping[str, Any]


def _check_scalar(key: str, value: Any, *, where: str) -> Any:
    """Reject values that cannot round-trip through the canonical form.

    ``None`` is rejected rather than treated as absent: the format expresses
    "no value" by omitting the key, and accepting both spellings would give two
    canonical forms for one manifest. ``float`` is rejected because ``1.0`` and
    ``1`` are the same number but different bytes, and sizes are integers.
    ``bool`` is rejected for non-boolean fields because Python's ``bool`` is an
    ``int`` subclass and would sail through a naive integer check while
    serialising as ``true``.
    """
    if value is None:
        raise SchemaError(
            f"{where}: field {key!r} is null; the format omits absent fields "
            f"rather than writing null"
        )
    if isinstance(value, float):
        raise SchemaError(
            f"{where}: field {key!r} is a float ({value!r}); the format has no "
            f"float-valued fields and float serialisation is not canonical"
        )
    if isinstance(value, (list, dict, tuple, set)):
        raise SchemaError(f"{where}: field {key!r} has a non-scalar value; entries are flat")
    if not isinstance(value, (str, int)):
        raise SchemaError(f"{where}: field {key!r} has unsupported type {type(value).__name__}")
    return value


def normalise_entries(files: Sequence[Entry]) -> list[dict[str, Any]]:
    """Return *files* as plain dicts, NFC-normalised and sorted by ``relpath``.

    The array half of canonicalisation, exposed separately so a caller can see
    exactly which entries went into a hash (``chiro info``, diagnostics) without
    re-deriving the ordering.
    """
    # Guard against untyped callers (JSON straight off disk), which is why this
    # narrows an `object` rather than the annotated parameter.
    candidate: object = files
    if isinstance(candidate, (str, bytes)) or not isinstance(candidate, Sequence):
        raise SchemaError(f"files must be an array, got {type(files).__name__}")

    normalised: list[dict[str, Any]] = []
    for index, entry in enumerate(files):
        where = f"files[{index}]"
        if not isinstance(entry, Mapping):
            raise SchemaError(f"{where} must be an object, got {type(entry).__name__}")
        if "relpath" not in entry:
            raise SchemaError(f"{where} has no 'relpath'")
        out: dict[str, Any] = {}
        for key, value in entry.items():
            if not isinstance(key, str):
                raise SchemaError(f"{where} has a non-string key {key!r}")
            _check_scalar(key, value, where=where)
            if key in _PATH_FIELDS:
                if not isinstance(value, str):
                    raise SchemaError(f"{where}: {key!r} must be a string")
                out[key] = nfc(value)
            else:
                out[key] = value
        normalised.append(out)

    normalised.sort(key=lambda entry: str(entry["relpath"]))

    for previous, current in pairwise(normalised):
        if previous["relpath"] == current["relpath"]:
            raise SchemaError(
                f"duplicate relpath {current['relpath']!r}: relpath is an entry's "
                f"identity within a manifest and must be unique, otherwise there is "
                f"no canonical ordering"
            )
    return normalised


def canonical_text(files: Sequence[Entry]) -> str:
    """Return the canonical serialisation of *files* as a :class:`str`.

    Provided alongside :func:`canonicalise` because a reimplementer debugging a
    hash mismatch needs to diff the *string*, not stare at two digests. The
    frozen test vectors carry this text literally for exactly that reason.
    """
    return json.dumps(
        normalise_entries(files),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonicalise(files: Sequence[Entry]) -> bytes:
    """Return the canonical UTF-8 bytes of *files* (spec §4)."""
    return canonical_text(files).encode("utf-8")


def compute_body_hash(files: Sequence[Entry], algorithm: str = "md5") -> str:
    """Return the Layer-2 ``body_hash`` of *files* under *algorithm*."""
    return hash_bytes(canonicalise(files), algorithm)


# --------------------------------------------------------------------------- #
# DVC `.dir` interop
# --------------------------------------------------------------------------- #
#
# Spec §0 and §4 originally claimed that a manifest body carrying only
# {relpath, md5, size} *is* a DVC `.dir` and that `body_hash` therefore equals
# DVC's `.dir` hash. Checked against dvc 3.66.1 / dvc-data 3.18.2, both halves
# of that claim are false:
#
#   1. A DVC `.dir` entry is {"md5", "relpath"} — there is NO `size`. Size lives
#      in DVC's `Meta`, which is only serialised in the `with_meta=True` variant,
#      and that variant is not what gets hashed.
#   2. DVC serialises with `json.dumps(lst, sort_keys=True)` — i.e. DEFAULT
#      separators (", " and ": ", with spaces) and DEFAULT ensure_ascii=True
#      (non-ASCII escaped to \uXXXX). Not the compact form the spec specifies.
#
# Rather than weld our frozen canonical form to a DVC implementation detail that
# has already changed once across DVC versions, the equivalence is provided as a
# *derived* function computed on demand. See docs/decisions.md D-01 and D-02.


def dvc_dir_bytes(files: Sequence[Entry], algorithm: str = "md5") -> bytes:
    """Return the bytes DVC would hash for the equivalent ``.dir`` object.

    Reproduces ``dvc_data.hashfile.tree.Tree.as_bytes()``: project each entry
    down to ``{<algorithm>: digest, "relpath": path}``, sort by ``relpath``, then
    ``json.dumps(..., sort_keys=True)`` with Python's default separators and
    ASCII escaping.

    ``size``, ``source_path``, ``isexec``, ``version_id`` and ``etag`` are all
    dropped: DVC keeps them in ``Meta``, outside the hashed serialisation.

    Verified byte-for-byte against dvc 3.66.1 for ``algorithm="md5"``. The
    sha256 form follows DVC's ``HashInfo.to_dict()`` (the key is the algorithm
    name) but is not covered by a DVC round-trip test, since DVC does not use
    sha256 for local caches.
    """
    key = "md5" if algorithm == "md5" else algorithm
    projected = [
        {key: entry["md5"], "relpath": entry["relpath"]} for entry in normalise_entries(files)
    ]
    projected.sort(key=lambda entry: str(entry["relpath"]))
    return json.dumps(projected, sort_keys=True).encode("utf-8")


def dvc_dir_hash(files: Sequence[Entry], algorithm: str = "md5") -> str:
    """Return the hash DVC would give the equivalent ``.dir`` object.

    This is the value that appears in a ``.dvc`` file as ``md5: <hash>.dir``
    (without the ``.dir`` suffix). A DVC user can therefore check a chirograph
    manifest with DVC's own logic — the interop the spec wanted — while
    ``body_hash`` stays free of DVC's serialisation quirks.
    """
    return hash_bytes(dvc_dir_bytes(files, algorithm), algorithm)
