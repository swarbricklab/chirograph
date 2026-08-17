"""``chiro://`` references — content-addressed manifest identity.

A reference names a *manifest*, not data; the manifest in turn names the data
(``source_url`` + per-entry ``source_path``). Registry design §2::

    chiro://<algo>:<whole-file-hash>        e.g. chiro://md5:abc123…

The hash is the manifest's **Layer-3 whole-file hash** — the hash of the entire
``.chiro.json`` including its header — not ``body_hash``, which would collide two
manifests that list the same files but point at different ``source_url``\\ s.

Only reference *syntax*, self-validation, and the registry's sharded path layout
live here. **Resolution** — walking an ordered registry list over the network,
local caching — is Milestone 2 and lands in the separate ``chirograph-registry``
repo (plan.md §"Milestone 2"). The split is deliberate: syntax is part of the
format and must be frozen with it; resolution is policy and infrastructure.

.. rubric:: The reformatting hazard

A whole-file hash is a hash of *bytes*, so re-indenting a manifest changes its
``chiro://`` reference without changing a single fact it asserts. That is fine
for Layer 3 as DVC uses it (DVC hashes a file it wrote itself and never
reformats), but it is a sharp edge for a registry keyed on the hash. This module
therefore offers both:

* :meth:`ChiroRef.for_path` — the specified whole-file hash. Byte-level; needs no
  JSON parser; matches what a ``.dvc`` records.
* :func:`canonical_ref` — a hash over the manifest's *canonical document form*,
  which is invariant under reformatting, key reordering and indentation changes.

See docs/decisions.md D-09 and docs/ideas.md for the recommendation to make the
canonical form load-bearing when the registry is built.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .constants import HASH_ALGORITHMS, MANIFEST_SUFFIX
from .errors import RefError
from .hashing import check_algorithm, hash_bytes, hash_file
from .paths import nfc

if TYPE_CHECKING:  # pragma: no cover
    from .manifest import Manifest

__all__ = [
    "SCHEME",
    "ChiroRef",
    "canonical_document_bytes",
    "canonical_ref",
    "load_and_check",
    "shard_path",
]

SCHEME = "chiro"

_REF_RE = re.compile(r"^chiro://(?P<algorithm>[a-z0-9]+):(?P<digest>[0-9a-fA-F]+)$")

#: Number of leading hex characters used as the shard directory. Two gives 256
#: directories, matching Git and DVC (registry design §5).
SHARD_WIDTH = 2


@dataclass(frozen=True)
class ChiroRef:
    """An immutable, self-validating reference to one exact manifest."""

    algorithm: str
    digest: str

    def __post_init__(self) -> None:
        check_algorithm(self.algorithm)
        expected = HASH_ALGORITHMS[self.algorithm]
        if len(self.digest) != expected or not all(c in "0123456789abcdef" for c in self.digest):
            raise RefError(
                f"{self.algorithm} reference digest must be {expected} lowercase hex "
                f"characters, got {self.digest!r}"
            )

    def __str__(self) -> str:
        return f"{SCHEME}://{self.algorithm}:{self.digest}"

    # -- construction ------------------------------------------------------- #

    @classmethod
    def parse(cls, text: str) -> ChiroRef:
        """Parse ``chiro://<algo>:<hash>``.

        The algorithm prefix is mandatory: a reference is the one place the
        algorithm cannot be read from inside the file it names, so it has to be
        on the outside (registry design §2).
        """
        if not isinstance(text, str):
            raise RefError(f"a reference must be a string, got {type(text).__name__}")
        match = _REF_RE.match(text.strip())
        if not match:
            raise RefError(
                f"malformed reference {text!r}; expected chiro://<algorithm>:<hex-digest>, "
                f"e.g. chiro://md5:d41d8cd98f00b204e9800998ecf8427e"
            )
        return cls(match["algorithm"], match["digest"].lower())

    @classmethod
    def for_bytes(cls, data: bytes, algorithm: str = "md5") -> ChiroRef:
        """The reference for a manifest whose exact bytes are *data*."""
        return cls(algorithm, hash_bytes(data, algorithm))

    @classmethod
    def for_path(cls, path: str | os.PathLike[str], algorithm: str = "md5") -> ChiroRef:
        """The reference for the manifest file at *path* (Layer-3 whole-file hash)."""
        return cls(algorithm, hash_file(path, algorithm))

    # -- self-validation ---------------------------------------------------- #

    def matches_bytes(self, data: bytes) -> bool:
        """Whether *data* is the manifest this reference names."""
        return hash_bytes(data, self.algorithm) == self.digest

    def matches_path(self, path: str | os.PathLike[str]) -> bool:
        """Whether the file at *path* is the manifest this reference names."""
        return hash_file(path, self.algorithm) == self.digest

    def check_bytes(self, data: bytes, *, origin: str = "<bytes>") -> None:
        """Raise :class:`~chirograph.errors.RefError` unless *data* matches.

        This is the property that makes a registry an untrusted *cache* rather
        than a trusted *authority* (registry design §2): a registry cannot forge
        an answer, only fail to answer.
        """
        actual = hash_bytes(data, self.algorithm)
        if actual != self.digest:
            raise RefError(
                f"{origin} does not match {self}: got {self.algorithm}:{actual}. "
                f"The content served is not the manifest that was asked for — reject it."
            )

    def check_path(self, path: str | os.PathLike[str]) -> None:
        """Raise unless the file at *path* matches this reference."""
        actual = hash_file(path, self.algorithm)
        if actual != self.digest:
            raise RefError(f"{path} does not match {self}: got {self.algorithm}:{actual}")

    # -- registry layout ---------------------------------------------------- #

    @property
    def shard_path(self) -> str:
        """Sharded content-addressed path within a registry (registry design §5).

        ``chiro://md5:abc123…`` → ``manifests/ab/c123….chiro.json``. Included
        here rather than in the (deferred) registry package because it is a pure
        function of the reference and consumers need it to build a plain HTTPS
        GET without any registry client.
        """
        return f"manifests/{self.digest[:SHARD_WIDTH]}/{self.digest[SHARD_WIDTH:]}{MANIFEST_SUFFIX}"


def shard_path(ref: ChiroRef | str) -> str:
    """Convenience wrapper: sharded registry path for a reference or its text."""
    return (ChiroRef.parse(ref) if isinstance(ref, str) else ref).shard_path


# --------------------------------------------------------------------------- #
# Reformat-invariant addressing
# --------------------------------------------------------------------------- #


def canonical_document_bytes(manifest: Manifest) -> bytes:
    """Return the manifest's canonical *whole-document* serialisation.

    The same rules as the body canonicalisation of spec §4, applied to the entire
    object: NFC paths, entries sorted by ``relpath``, ``sort_keys=True``,
    ``separators=(",", ":")``, ``ensure_ascii=False``, UTF-8. Unlike the on-disk
    pretty form this is invariant under reformatting, so a hash over it is a
    stable identity for the manifest's *content* rather than for one particular
    rendering of it.

    Header string values are NFC-normalised too, for the same
    cross-platform reason paths are.
    """
    document = manifest.to_dict()
    normalised = {
        key: (nfc(value) if isinstance(value, str) else value)
        for key, value in document.items()
        if key != "files"
    }
    normalised["files"] = document["files"]
    return json.dumps(normalised, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def canonical_ref(manifest: Manifest, algorithm: str | None = None) -> ChiroRef:
    """A reference over the canonical document form — immune to reformatting.

    Not the reference specified by registry design §2 (which is the whole-file
    hash). Offered as the more robust alternative for when the registry is built;
    see docs/decisions.md D-09.
    """
    chosen = algorithm or manifest.hash_algorithm
    return ChiroRef.for_bytes(canonical_document_bytes(manifest), chosen)


def load_and_check(path: str | os.PathLike[str], ref: ChiroRef | str, **kwargs: object) -> Manifest:
    """Load a manifest from *path*, refusing it unless it matches *ref*.

    The order matters: hash the bytes *before* parsing them, so a manifest that
    is not the one requested is rejected without its contents ever being trusted.
    """
    from .manifest import Manifest as _Manifest

    reference = ChiroRef.parse(ref) if isinstance(ref, str) else ref
    data = Path(path).read_bytes()
    reference.check_bytes(data, origin=str(path))
    return _Manifest.loads(data, **kwargs)
