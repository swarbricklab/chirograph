"""Path normalisation, safety validation, and materialisation lint.

Two distinct jobs, deliberately separated:

**Safety** (:func:`validate_path`) — hard rejection. A consumer materialises
``relpath`` to disk, and an ``observed-download`` manifest describes somebody
else's deposit, so paths are untrusted input. Anything that could escape the
import target, or that no filesystem could hold, is an error (spec §3.2).

**Lint** (:func:`lint_paths`) — warnings only. Paths that are *safe* but will
bite somebody on a case-insensitive filesystem or on Windows. Not errors,
because plenty of legitimate scientific data has such names, but worth saying
out loud at write time when they can still be fixed.
"""

from __future__ import annotations

import posixpath
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from .errors import PathSafetyError

__all__ = [
    "MAX_COMPONENT_BYTES",
    "MAX_PATH_BYTES",
    "PathWarning",
    "join_source_url",
    "lint_paths",
    "nfc",
    "validate_path",
]

#: Generous caps chosen to match real filesystem limits, so a manifest whose
#: paths could never be materialised is rejected before any bytes move. Also a
#: cheap first-line defence for a registry's CI (registry design §8, defence 1).
MAX_PATH_BYTES = 4096
MAX_COMPONENT_BYTES = 255

_DISALLOWED_CODEPOINTS = frozenset(chr(c) for c in [*range(0x00, 0x20), 0x7F])

#: Basenames Windows refuses regardless of extension.
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def nfc(text: str) -> str:
    """Return *text* in Unicode NFC.

    macOS hands out NFD filenames and Linux NFC, so the same file walked on two
    machines yields two different byte strings and therefore two different
    ``body_hash`` values. Normalising to NFC before hashing *and* before sorting
    is what makes the manifest platform-independent (spec §3.2).

    Note the order matters: normalising after sorting can produce a different
    sequence than normalising before, because NFD's combining marks sort
    differently from the composed characters they fold into. Callers must
    normalise first — see :func:`chirograph.canonical.canonicalise`.
    """
    return unicodedata.normalize("NFC", text)


def validate_path(path: object, *, field: str = "relpath") -> str:
    """Validate one manifest path and return it NFC-normalised.

    Raises :class:`~chirograph.errors.PathSafetyError` for anything a consumer
    must not materialise. *field* names the offending field in the message so
    the caller can tell a bad ``relpath`` from a bad ``source_path``.
    """
    if not isinstance(path, str):
        raise PathSafetyError(f"{field} must be a string, got {type(path).__name__}")
    if path == "":
        raise PathSafetyError(f"{field} must not be empty")

    bad = sorted(_DISALLOWED_CODEPOINTS & set(path))
    if bad:
        codes = ", ".join(f"U+{ord(c):04X}" for c in bad)
        raise PathSafetyError(f"{field} contains control character(s) {codes}: {path!r}")

    # Backslash is ambiguous — a Windows separator on one platform and a legal
    # filename character on another. Refusing it outright is the only way to
    # keep `relpath` unambiguously POSIX (spec §3.2).
    if "\\" in path:
        raise PathSafetyError(
            f"{field} must be POSIX-separated; backslash is not allowed: {path!r}"
        )
    if path.startswith("/"):
        raise PathSafetyError(f"{field} must be relative, not absolute: {path!r}")
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        raise PathSafetyError(
            f"{field} must be relative; looks like a Windows drive path: {path!r}"
        )

    encoded = path.encode("utf-8")
    if len(encoded) > MAX_PATH_BYTES:
        raise PathSafetyError(
            f"{field} is {len(encoded)} bytes, exceeding the {MAX_PATH_BYTES}-byte limit"
        )

    components = path.split("/")
    for component in components:
        if component == "":
            raise PathSafetyError(
                f"{field} has an empty path component (leading, trailing, or doubled '/'): {path!r}"
            )
        if component in (".", ".."):
            raise PathSafetyError(
                f"{field} contains a {component!r} component (path traversal): {path!r}"
            )
        if len(component.encode("utf-8")) > MAX_COMPONENT_BYTES:
            raise PathSafetyError(
                f"{field} has a component longer than {MAX_COMPONENT_BYTES} bytes: {path!r}"
            )

    normalised = nfc(path)
    # Normalisation must not be able to introduce a separator or a traversal
    # component. It cannot in practice, but the check is one comparison and this
    # is the security boundary.
    if normalised.split("/") != [nfc(c) for c in components]:
        raise PathSafetyError(f"{field} changes structure under NFC normalisation: {path!r}")
    return normalised


class PathWarning:
    """A non-fatal problem with a set of manifest paths."""

    __slots__ = ("code", "message", "paths")

    def __init__(self, code: str, message: str, paths: list[str]) -> None:
        self.code = code
        self.message = message
        self.paths = paths

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PathWarning({self.code!r}, {self.message!r}, {self.paths!r})"

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "paths": self.paths}


def lint_paths(paths: Iterable[str]) -> list[PathWarning]:
    """Report safe-but-hazardous paths.

    Everything here materialises fine on Linux and would be lost or silently
    merged elsewhere, which is exactly the class of bug a checksum manifest is
    supposed to make visible rather than hide.
    """
    paths = list(paths)
    warnings: list[PathWarning] = []

    # Case-insensitive collisions: two distinct entries that become one file on
    # macOS/Windows. The second overwrites the first and verification then fails
    # on a file the manifest is perfectly right about.
    by_lower: defaultdict[str, list[str]] = defaultdict(list)
    for path in paths:
        by_lower[path.lower()].append(path)
    collisions = sorted(
        (group for group in by_lower.values() if len(group) > 1),
        key=lambda group: group[0],
    )
    if collisions:
        flat = [path for group in collisions for path in sorted(group)]
        warnings.append(
            PathWarning(
                "case-collision",
                f"{len(collisions)} group(s) of paths differ only by case and will "
                f"collide on a case-insensitive filesystem (macOS, Windows)",
                flat,
            )
        )

    reserved = sorted(
        path
        for path in paths
        if posixpath.basename(path).split(".")[0].upper() in _WINDOWS_RESERVED
    )
    if reserved:
        warnings.append(
            PathWarning(
                "windows-reserved",
                f"{len(reserved)} path(s) use a Windows-reserved device name "
                f"and cannot be materialised on Windows",
                reserved,
            )
        )

    trailing = sorted(path for path in paths if any(c != c.rstrip(" .") for c in path.split("/")))
    if trailing:
        warnings.append(
            PathWarning(
                "trailing-space-or-dot",
                f"{len(trailing)} path(s) have a component ending in a space or dot; "
                f"Windows silently strips these",
                trailing,
            )
        )

    return warnings


def join_source_url(source_url: str, source: str) -> str:
    """Resolve one entry's fetch URL from ``source_url`` and its source path.

    Implements spec §9 "Resolving a fetch location", with one correction the
    spec glosses over: :func:`urllib.parse.urljoin` treats a base without a
    trailing slash as naming a *file*, and discards its last segment. So
    ``urljoin("https://x/suppl", "a.gz")`` yields ``https://x/a.gz`` — silently
    wrong, and wrong in a way that only shows up as a 404 much later. We append
    the slash, because ``source_url`` is documented as a *base* location
    (spec §3.1) and is therefore always directory-like.
    """
    from urllib.parse import quote, urljoin

    base = source_url if source_url.endswith("/") else source_url + "/"
    # Percent-encode the path segment but leave already-safe characters alone.
    # '/' is safe because `source` is a multi-component relative path.
    return urljoin(base, quote(source, safe="/~"))
