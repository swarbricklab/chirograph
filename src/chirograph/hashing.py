"""Content hashing: algorithm policy, streaming file digests, parallel walk.

The algorithm policy (docs/decisions.md D-03, D-04) is enforced here and nowhere
else, so there is exactly one place to change when a future ``schema_version``
widens it.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from .constants import HASH_ALGORITHMS, RESERVED_HASH_ALGORITHMS
from .errors import SchemaError, UnsupportedAlgorithm

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

__all__ = [
    "CHUNK_SIZE",
    "check_algorithm",
    "check_digest",
    "default_jobs",
    "hash_bytes",
    "hash_file",
    "hash_files",
    "hash_stream",
    "new_hasher",
]

#: 4 MiB. Large enough that the GIL is released for the whole of each
#: ``hasher.update()`` (so threads genuinely parallelise), and a friendly
#: request size for the parallel filesystems these manifests get built on.
CHUNK_SIZE = 4 * 1024 * 1024


def check_algorithm(algorithm: str) -> str:
    """Validate a ``hash_algorithm`` value, returning it unchanged.

    ``"etag"`` gets its own message: spec §3.1 lists it as permitted, but v1
    rejects it, and telling the caller "unsupported" without saying "deliberately
    so, here is why" would read as a bug in chirograph rather than a decision.
    """
    if not isinstance(algorithm, str):
        raise SchemaError(f"hash_algorithm must be a string, got {type(algorithm).__name__}")
    if algorithm in HASH_ALGORITHMS:
        return algorithm
    if algorithm in RESERVED_HASH_ALGORITHMS:
        raise UnsupportedAlgorithm(
            f"hash_algorithm {algorithm!r} is reserved but not supported in schema_version 1.0. "
            f"An {algorithm} is not a function of content alone (S3 multipart etags depend on "
            f"the part size used at upload), so it cannot address content, cannot produce a "
            f"meaningful body_hash, and cannot be checked against bytes on disk. Record a real "
            f"digest instead, and put the repository's validator in the per-entry "
            f"'etag' field as supplementary evidence. See docs/decisions.md D-04."
        )
    supported = ", ".join(sorted(HASH_ALGORITHMS))
    raise UnsupportedAlgorithm(f"unknown hash_algorithm {algorithm!r}; supported: {supported}")


def check_digest(digest: object, algorithm: str, *, field: str = "md5") -> str:
    """Validate a hex digest against *algorithm*, returning it unchanged.

    Length is checked against the algorithm because a 32-character digest in a
    ``sha256`` manifest is not a typo, it is a manifest that was written by
    ingesting md5s under the wrong header — the kind of mistake that would
    otherwise surface as a mass verification failure much later.

    **Digests must be lowercase** (docs/decisions.md D-18). This used to silently
    lower-case them instead, which broke the promise that canonicalisation does no
    semantic rewriting (spec §4, D-06): a manifest whose ``body_hash`` was exactly
    what §4 yields for its own uppercase ``files`` array was rejected with a
    ``BodyHashMismatch`` blaming corruption. Rejecting with a message that names
    the actual problem is the only option that keeps §4 a pure function *and*
    tells the truth.
    """
    if not isinstance(digest, str):
        raise SchemaError(f"{field} must be a string, got {type(digest).__name__}")
    expected = HASH_ALGORITHMS[check_algorithm(algorithm)]
    if len(digest) != expected:
        raise SchemaError(
            f"{field} {digest!r} is {len(digest)} characters; {algorithm} digests "
            f"are {expected} hex characters"
        )
    if not all(c in "0123456789abcdef" for c in digest):
        if all(c in "0123456789abcdefABCDEF" for c in digest):
            raise SchemaError(
                f"{field} {digest!r} is uppercase. Digests must be lowercase hexadecimal: "
                f"canonicalisation hashes the array exactly as given, so accepting either "
                f"case would give one manifest two body_hash values "
                f"(docs/decisions.md D-18). Lower-case it: {digest.lower()}"
            )
        raise SchemaError(f"{field} {digest!r} is not hexadecimal")
    return digest


def new_hasher(algorithm: str) -> Any:
    """Return a fresh hash object for *algorithm*.

    ``usedforsecurity=False`` is passed for md5 so that manifests can still be
    built inside a FIPS-mode interpreter, where the security-flagged md5
    constructor raises. We are using md5 as a content fingerprint, not as a
    security primitive; the collision weakness is irrelevant here (registry
    design §5) and preimage resistance is all the format relies on.
    """
    check_algorithm(algorithm)
    if algorithm == "md5":
        try:
            return hashlib.md5(usedforsecurity=False)
        except TypeError:  # pragma: no cover - Python < 3.9 / exotic builds
            return hashlib.md5()
    return hashlib.new(algorithm)


def hash_bytes(data: bytes, algorithm: str = "md5") -> str:
    """Return the hex digest of *data*."""
    hasher = new_hasher(algorithm)
    hasher.update(data)
    return str(hasher.hexdigest())


def hash_file(path: str | os.PathLike[str], algorithm: str = "md5") -> str:
    """Return the hex digest of the file at *path*, read in chunks.

    Streaming rather than slurping, because payload files here are routinely
    larger than memory (a Visium matrix, a BAM).
    """
    hasher = new_hasher(algorithm)
    # A directory is refused with a clear message; other special files are not,
    # for the reasons in `manifest.read_manifest_bytes`. Streaming a pipe into a
    # digest is legitimate.
    target = os.fspath(path)
    if os.path.isdir(target):
        raise IsADirectoryError(f"{target} is a directory, not a file to hash")
    with open(target, "rb", buffering=0) as handle:
        while chunk := handle.read(CHUNK_SIZE):
            hasher.update(chunk)
    return str(hasher.hexdigest())


def default_jobs() -> int:
    """A sensible default worker count for parallel hashing.

    Capped at 8: past that point the bottleneck is the filesystem rather than
    the CPU, and on a shared HPC login node politeness matters more than the
    last increment of throughput.
    """
    return min(8, os.cpu_count() or 1)


def hash_files(
    paths: Sequence[Path],
    algorithm: str = "md5",
    *,
    jobs: int | None = None,
    on_done: Callable[[Path, str], None] | None = None,
) -> list[str]:
    """Hash *paths* concurrently, returning digests in the input order.

    Threads rather than processes: :mod:`hashlib` releases the GIL for updates
    over a few KiB, so the hashing itself overlaps, and the read syscalls
    overlap too — which is where most of the win is on a networked filesystem.
    Processes would add pickling and per-worker interpreter cost for no benefit.

    *on_done* is called (from a worker thread) after each file completes, for
    progress reporting.
    """
    check_algorithm(algorithm)
    if not paths:
        return []
    workers = jobs if jobs is not None else default_jobs()
    workers = max(1, min(workers, len(paths)))

    def work(path: Path) -> str:
        digest = hash_file(path, algorithm)
        if on_done is not None:
            on_done(path, digest)
        return digest

    if workers == 1:
        return [work(path) for path in paths]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(work, paths))


def hash_stream(chunks: Iterable[bytes], algorithm: str = "md5") -> str:
    """Return the hex digest of an iterable of byte chunks.

    Present for consumers like ``archivefs`` that hash bytes as they arrive off
    the network and never touch a local file.
    """
    hasher = new_hasher(algorithm)
    for chunk in chunks:
        hasher.update(chunk)
    return str(hasher.hexdigest())
