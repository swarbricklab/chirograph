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

    # Surrogates cannot be UTF-8 encoded, so a path containing one cannot go into
    # a manifest at all. They arrive two ways: a JSON document containing a
    # "\udcff" escape (pure ASCII on the wire, so no transport rejects it), and —
    # far more commonly — from the filesystem, where Python decodes a filename
    # that is not valid UTF-8 using surrogateescape. A latin-1-named file in a
    # deposit is entirely routine. Checking here converts what would otherwise be
    # a bare UnicodeEncodeError escaping every `except ChirographError` into a
    # normal, catchable rejection that names the field.
    surrogates = sorted(c for c in set(path) if "\ud800" <= c <= "\udfff")
    if surrogates:
        codes = ", ".join(f"U+{ord(c):04X}" for c in surrogates)
        raise PathSafetyError(
            f"{field} contains unpaired surrogate(s) {codes} and so cannot be encoded as "
            f"UTF-8: {path!r}. On a filesystem this means the filename's bytes are not "
            f"valid UTF-8; rename the file, because no JSON manifest can represent it."
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

    Implements the fetch-location rule (rationale §11). Deliberately does **not** use
    :func:`urllib.parse.urljoin`, which gets this wrong in three separate ways
    for the URLs this format actually carries:

    1. **Non-hierarchical schemes lose the base entirely.** ``urljoin`` only
       resolves relative references for schemes in
       :data:`urllib.parse.uses_relative`; for anything else it returns the
       reference unchanged. So ``urljoin("geo://GSE12345/", "a.gz")`` yields
       ``"a.gz"`` — not a URL at all. ``geo://`` is the spec's own §3.2 example
       and ``s3://`` is named in §3.2 and §6.1, so this is the common case, not
       an edge one.
    2. **A base without a trailing slash loses its last segment**, because
       ``urljoin`` reads it as naming a file: ``urljoin("https://x/suppl",
       "a.gz")`` → ``https://x/a.gz``.
    3. **A base with a query or fragment loses its path.** Appending a slash to
       ``https://h/files?dataset=X`` appends it to the *query*, so ``urljoin``
       then strips ``/files``.

    So the join is done explicitly on the path component: the base path is made
    directory-like, the source is appended to it, and the scheme, netloc, query
    and fragment are carried through untouched. A query is treated as a parameter
    of the request rather than part of the location, which is the only reading
    that keeps an API-style base usable.

    *source* is validated first, because this is exported as the public fetch helper
    and a caller may reach it without going through a manifest.
    """
    from urllib.parse import quote, urlsplit, urlunsplit

    validate_path(source, field="source")
    parts = urlsplit(source_url)
    base_path = parts.path
    if not base_path.endswith("/"):
        base_path += "/"
    # '/' stays unescaped because `source` is a multi-component relative path;
    # everything else that is legal in a filename but significant in a URL
    # (space, '#', '?', '%') is encoded.
    joined = base_path + quote(source, safe="/~")
    return urlunsplit((parts.scheme, parts.netloc, joined, parts.query, parts.fragment))
