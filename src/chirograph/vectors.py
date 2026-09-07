"""Access to the frozen conformance vectors, and a self-check against them.

The vectors ship *inside the package* so that ``chiro test-vectors --check`` works
from an installed wheel, and so that anyone writing a second implementation can
get them with ``pip download chirograph`` rather than cloning a repo.

Why this matters more than a normal test fixture: spec §4 warns that "a
reimplementation that canonicalises *almost* identically produces non-matching
hashes silently". A shipped, runnable conformance suite is the only thing that
turns that silent failure into a loud one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .canonical import (
    canonical_document_text,
    canonical_text,
    compute_body_hash,
    dvc_dir_bytes,
    dvc_dir_hash,
)
from .errors import ChirographError
from .hashing import hash_bytes
from .manifest import Manifest
from .paths import validate_path

__all__ = ["ConformanceResult", "check_vectors", "load_vectors", "vectors_path"]

VECTORS_FILENAME = "canonicalisation.json"


def vectors_path() -> Path:
    """Filesystem path to the frozen vectors."""
    return Path(__file__).resolve().parent / "vectors" / VECTORS_FILENAME


@lru_cache(maxsize=1)
def load_vectors() -> Mapping[str, Any]:
    """Parse the frozen vectors."""
    result: Mapping[str, Any] = json.loads(vectors_path().read_text(encoding="utf-8"))
    return result


@dataclass
class ConformanceResult:
    """The outcome of checking an implementation against the vectors."""

    passed: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def total(self) -> int:
        return self.passed + len(self.failures)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "passed": self.passed,
            "failed": len(self.failures),
            "total": self.total,
            "failures": self.failures,
        }


def check_vectors() -> ConformanceResult:
    """Check this implementation against every frozen vector.

    Deliberately checks the *canonical string* before the hash. A digest mismatch
    tells you nothing; the string diff tells you whether you got the separators,
    the escaping, the sort, or the normalisation wrong.
    """
    data = load_vectors()
    result = ConformanceResult()

    for case in data["canonicalisation"]:
        name = case["name"]
        algorithm = case["hash_algorithm"]
        entries = case["input"]

        try:
            produced = canonical_text(entries)
        except ChirographError as exc:
            result.failures.append(f"canonicalisation/{name}: raised {type(exc).__name__}: {exc}")
            continue

        if produced != case["canonical"]:
            result.failures.append(
                f"canonicalisation/{name}: canonical string mismatch\n"
                f"    expected: {case['canonical']!r}\n"
                f"    produced: {produced!r}"
            )
            continue

        raw = produced.encode("utf-8")
        if len(raw) != case["canonical_utf8_bytes"]:
            result.failures.append(
                f"canonicalisation/{name}: canonical form is {len(raw)} UTF-8 bytes, "
                f"expected {case['canonical_utf8_bytes']}"
            )
            continue
        if hash_bytes(raw, "sha256") != case["canonical_sha256"]:
            result.failures.append(f"canonicalisation/{name}: canonical_sha256 mismatch")
            continue
        if compute_body_hash(entries, algorithm) != case["body_hash"]:
            result.failures.append(f"canonicalisation/{name}: body_hash mismatch")
            continue

        produced_dvc = dvc_dir_bytes(entries, algorithm).decode("utf-8")
        if produced_dvc != case["dvc_dir"]:
            result.failures.append(
                f"canonicalisation/{name}: DVC .dir interop string mismatch\n"
                f"    expected: {case['dvc_dir']!r}\n"
                f"    produced: {produced_dvc!r}"
            )
            continue
        if dvc_dir_hash(entries, algorithm) != case["dvc_dir_hash"]:
            result.failures.append(f"canonicalisation/{name}: dvc_dir_hash mismatch")
            continue

        result.passed += 1

    for case in data.get("container_canonicalisation", []):
        name = case["name"]
        algorithm = case["hash_algorithm"]
        containers = case["containers"]
        entries = case["files"]

        try:
            produced = canonical_document_text(containers, entries)
        except ChirographError as exc:
            result.failures.append(
                f"container_canonicalisation/{name}: raised {type(exc).__name__}: {exc}"
            )
            continue

        if produced != case["canonical"]:
            result.failures.append(
                f"container_canonicalisation/{name}: canonical string mismatch\n"
                f"    expected: {case['canonical']!r}\n"
                f"    produced: {produced!r}"
            )
            continue

        raw = produced.encode("utf-8")
        if len(raw) != case["canonical_utf8_bytes"]:
            result.failures.append(
                f"container_canonicalisation/{name}: {len(raw)} UTF-8 bytes, "
                f"expected {case['canonical_utf8_bytes']}"
            )
            continue
        if hash_bytes(raw, "sha256") != case["canonical_sha256"]:
            result.failures.append(f"container_canonicalisation/{name}: canonical_sha256 mismatch")
            continue
        if compute_body_hash(entries, algorithm, containers=containers) != case["body_hash"]:
            result.failures.append(f"container_canonicalisation/{name}: body_hash mismatch")
            continue

        result.passed += 1

    safety = data["path_safety"]
    for case in safety["accept"]:
        try:
            validate_path(case["path"])
        except ChirographError as exc:
            result.failures.append(
                f"path_safety/accept {case['path']!r} ({case['why']}): rejected with {exc}"
            )
        else:
            result.passed += 1
    for case in safety["reject"]:
        try:
            validate_path(case["path"])
        except ChirographError:
            result.passed += 1
        else:
            result.failures.append(
                f"path_safety/reject {case['path']!r} ({case['why']}): was ACCEPTED"
            )

    manifests = data["manifests"]
    for case in manifests["valid"]:
        try:
            Manifest.from_dict(case["document"])
        except ChirographError as exc:
            result.failures.append(
                f"manifests/valid {case['name']}: rejected with {type(exc).__name__}: {exc}"
            )
        else:
            result.passed += 1
    for case in manifests["invalid"]:
        expected = case["error"]
        try:
            Manifest.from_dict(case["document"])
        except ChirographError as exc:
            actual = type(exc).__name__
            # Subclasses count: rejecting a traversal path with PathSafetyError or
            # with its parent ManifestError are both correct refusals. What must
            # not happen is acceptance.
            if actual == expected or _is_subclass_name(type(exc), expected):
                result.passed += 1
            else:
                result.failures.append(
                    f"manifests/invalid {case['name']}: expected {expected}, got {actual}: {exc}"
                )
        else:
            result.failures.append(
                f"manifests/invalid {case['name']} ({case['why']}): was ACCEPTED"
            )

    return result


def _is_subclass_name(cls: type, expected: str) -> bool:
    return any(base.__name__ == expected for base in cls.__mro__)
