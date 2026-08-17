"""Chirograph — portable, content-addressed manifests for scientific datasets.

A *chirograph* was a medieval document written in duplicate with a word inscribed
across the join, then cut through that word so that authenticity could be proven
by matching the two halves. A Chirograph manifest does the same for data.

Quick start::

    import chirograph

    # Describe a directory you are about to deposit.
    result = chirograph.build_from_directory("deposit/", record_accession="GSE000000")
    result.manifest.dump("GSE000000.chiro.json")

    # Later, check bytes against it.
    manifest = chirograph.load("GSE000000.chiro.json")
    report = chirograph.verify_tree(manifest, "deposit/")
    assert report.ok

For consumers (the ``archivefs`` integration surface), the three things you want
are :func:`load`, :meth:`Manifest.fetch_plan`, and :func:`verify_entry`::

    manifest = chirograph.load(path)              # validates Layers 2 + schema
    for item in manifest.fetch_plan():            # spec §9 fetch resolution
        fetch(item.url) -> place at item.relpath  # then check against item.md5

Nothing here needs a network, a registry, or DVC. See ``docs/`` for the format
spec, the registry design, and the decisions log.
"""

from __future__ import annotations

from .canonical import (
    canonical_text,
    canonicalise,
    compute_body_hash,
    dvc_dir_bytes,
    dvc_dir_hash,
    normalise_entries,
)
from .constants import (
    DEFAULT_HASH_ALGORITHM,
    HASH_ALGORITHMS,
    MANIFEST_SUFFIX,
    PROVENANCE_OBSERVED,
    PROVENANCE_SELF,
    PROVENANCE_TYPES,
    SCHEMA,
    SCHEMA_VERSION,
)
from .diff import EntryChange, ManifestDiff, diff_manifests
from .errors import (
    BodyHashMismatch,
    ChirographError,
    DvcError,
    HashMismatch,
    IntegrityError,
    ManifestError,
    PathSafetyError,
    RefError,
    SchemaError,
    SizeMismatch,
    UnsupportedAlgorithm,
    UnsupportedVersion,
)
from .hashing import hash_bytes, hash_file, hash_stream
from .manifest import (
    FetchItem,
    FileEntry,
    Lint,
    Manifest,
    dump,
    dumps,
    load,
    loads,
)
from .paths import join_source_url, lint_paths, nfc, validate_path
from .ref import ChiroRef, canonical_document_bytes, canonical_ref, shard_path
from .rewrite import RewriteRule, parse_rules
from .schema import schema_path, schema_text, validate_document
from .verify import FileResult, VerificationReport, verify_body, verify_entry, verify_tree
from .version import GENERATOR, __version__
from .walk import WalkReport, walk_tree
from .writer import BuildResult, build_from_directory, capture_from_directory

__all__ = [
    # version / constants
    "DEFAULT_HASH_ALGORITHM",
    "GENERATOR",
    "HASH_ALGORITHMS",
    "MANIFEST_SUFFIX",
    "PROVENANCE_OBSERVED",
    "PROVENANCE_SELF",
    "PROVENANCE_TYPES",
    "SCHEMA",
    "SCHEMA_VERSION",
    # errors
    "BodyHashMismatch",
    # writing
    "BuildResult",
    # references
    "ChiroRef",
    "ChirographError",
    "DvcError",
    # verification / comparison
    "EntryChange",
    # model
    "FetchItem",
    "FileEntry",
    "FileResult",
    "HashMismatch",
    "IntegrityError",
    "Lint",
    "Manifest",
    "ManifestDiff",
    "ManifestError",
    "PathSafetyError",
    "RefError",
    "RewriteRule",
    "SchemaError",
    "SizeMismatch",
    "UnsupportedAlgorithm",
    "UnsupportedVersion",
    "VerificationReport",
    "WalkReport",
    "__version__",
    "build_from_directory",
    "canonical_document_bytes",
    "canonical_ref",
    # canonicalisation / hashing
    "canonical_text",
    "canonicalise",
    "capture_from_directory",
    "compute_body_hash",
    "diff_manifests",
    "dump",
    "dumps",
    "dvc_dir_bytes",
    "dvc_dir_hash",
    "hash_bytes",
    "hash_file",
    "hash_stream",
    # paths
    "join_source_url",
    "lint_paths",
    "load",
    "loads",
    "nfc",
    "normalise_entries",
    "parse_rules",
    # schema
    "schema_path",
    "schema_text",
    "shard_path",
    "validate_document",
    "validate_path",
    "verify_body",
    "verify_entry",
    "verify_tree",
    "walk_tree",
]


def __getattr__(name: str) -> object:
    """Lazily expose the DVC and vector helpers.

    ``chirograph.dvc`` needs PyYAML, which is an optional extra; importing it
    eagerly would make the whole package fail to import wherever the extra is
    absent. Same idea for the vectors, which read a data file off disk.
    """
    if name in ("manifest_from_dvc", "read_dvc_file", "DvcTranscription"):
        from . import dvc as _dvc

        return getattr(_dvc, name)
    if name in ("load_vectors", "check_vectors", "vectors_path"):
        from . import vectors as _vectors

        return getattr(_vectors, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
