"""The manifest object model: :class:`FileEntry`, :class:`Manifest`, load/dump.

Design note — **derived fields are computed, never stored on the object.**
``size``, ``nfiles`` and ``body_hash`` are properties over ``files``, so it is
structurally impossible to build an inconsistent :class:`Manifest` in memory.
On load, the values found in the document are *checked* against the recomputed
ones and then discarded. That is the whole of Tier-1 verification (registry
design §6) and it comes for free from the object model rather than from a
separate pass somebody can forget to run.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .canonical import compute_body_hash, dvc_dir_hash, normalise_entries
from .constants import (
    DEFAULT_HASH_ALGORITHM,
    ENTRY_FIELD_ORDER,
    HASH_ALGORITHMS,
    HEADER_FIELD_ORDER,
    MANIFEST_SUFFIX,
    PROVENANCE_OBSERVED,
    PROVENANCE_SELF,
    PROVENANCE_TYPES,
    REQUIRED_ENTRY_FIELDS,
    REQUIRED_HEADER_FIELDS,
    SCHEMA,
    SCHEMA_VERSION,
    SUPPORTED_MAJOR,
    SUPPORTED_MINOR,
)
from .errors import (
    BodyHashMismatch,
    ManifestError,
    SchemaError,
    UnsupportedVersion,
)
from .hashing import check_algorithm, check_digest
from .paths import join_source_url, lint_paths, nfc, validate_path
from .timestamps import canonical_timestamp, now_timestamp

__all__ = [
    "FetchItem",
    "FileEntry",
    "Lint",
    "Manifest",
    "decode_document",
    "dump",
    "dumps",
    "load",
    "loads",
    "normalise_order",
]

_KNOWN_ENTRY_FIELDS = frozenset(ENTRY_FIELD_ORDER)
_KNOWN_HEADER_FIELDS = frozenset([*HEADER_FIELD_ORDER, "files"])


# --------------------------------------------------------------------------- #
# Lint findings
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Lint:
    """A non-fatal finding about a manifest.

    Separated from exceptions because "your manifest is legal but you probably
    didn't mean this" is advice, not a refusal — and the ``opt*`` fields in
    spec §3.1 ("optional in schema but required by convention") can only be
    expressed this way.
    """

    code: str
    message: str
    paths: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "paths": list(self.paths)}


# --------------------------------------------------------------------------- #
# Entries
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FileEntry:
    """One file in a manifest — the two-path model of spec §3.2.

    ``relpath`` is where the file is *placed* in a workspace; ``source_path`` is
    where its bytes are *fetched* from, relative to the manifest's
    ``source_url``. When they agree, ``source_path`` is ``None`` and the entry is
    the terse DVC-shaped common case.

    The digest field is named ``md5`` even when ``hash_algorithm`` is ``sha256``,
    because that is the wire format (spec §3.2 defines ``md5`` as "hash of the
    file's bytes, per ``hash_algorithm``"). :attr:`digest` is the neutral alias
    to use in code that should not read as md5-specific.
    """

    relpath: str
    md5: str
    size: int
    source_path: str | None = None
    isexec: bool | None = None
    version_id: str | None = None
    etag: str | None = None
    #: Fields not in the format, preserved verbatim when the caller opted into
    #: tolerating them. Included in canonicalisation, so they *are* covered by
    #: ``body_hash``.
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """The file's hash, named without implying md5."""
        return self.md5

    @property
    def source(self) -> str:
        """Where to fetch the bytes: ``source_path`` if set, else ``relpath``."""
        return self.source_path if self.source_path is not None else self.relpath

    @property
    def relocated(self) -> bool:
        """True when the deposit layout differs from the workspace layout."""
        return self.source_path is not None and self.source_path != self.relpath

    def __post_init__(self) -> None:
        """Validate on construction, not only in :meth:`from_dict`.

        Path safety and digest hygiene used to live in ``from_dict`` alone, so the
        *producer* side could emit ``relpath="../../etc/passwd"`` or an uppercase
        digest and only the reader would object — a manifest that fails our own
        ``chiro validate``. Since ``__post_init__`` also runs for
        :func:`dataclasses.replace`, this makes it impossible to hold a
        ``FileEntry`` that could not be written out and read back.

        Digest *length* is not checked here: an entry does not know its manifest's
        ``hash_algorithm``. Case and hex-ness are, because those are algorithm
        independent (docs/decisions.md D-18).
        """
        _reject_shadowing(self.extra, _KNOWN_ENTRY_FIELDS, where="a file entry")
        object.__setattr__(self, "relpath", validate_path(self.relpath))
        if self.source_path is not None:
            object.__setattr__(
                self, "source_path", validate_path(self.source_path, field="source_path")
            )
        if not isinstance(self.md5, str):
            raise SchemaError(f"md5 must be a string, got {type(self.md5).__name__}")
        if len(self.md5) not in set(HASH_ALGORITHMS.values()):
            lengths = " or ".join(str(n) for n in sorted(set(HASH_ALGORITHMS.values())))
            raise SchemaError(
                f"md5 {self.md5!r} is {len(self.md5)} characters; supported digests are "
                f"{lengths} hex characters"
            )
        if not all(character in "0123456789abcdef" for character in self.md5):
            if all(character in "0123456789abcdefABCDEF" for character in self.md5):
                raise SchemaError(
                    f"md5 {self.md5!r} is uppercase; digests must be lowercase hexadecimal "
                    f"(docs/decisions.md D-18). Lower-case it: {self.md5.lower()}"
                )
            raise SchemaError(f"md5 {self.md5!r} is not hexadecimal")
        if isinstance(self.size, bool) or not isinstance(self.size, int):
            raise SchemaError(f"size must be an integer, got {type(self.size).__name__}")
        if self.size < 0:
            raise SchemaError(f"size must not be negative, got {self.size}")

    def to_dict(self) -> dict[str, Any]:
        """Serialise in the format's field order, omitting absent fields."""
        out: dict[str, Any] = {}
        for name in ENTRY_FIELD_ORDER:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        algorithm: str = DEFAULT_HASH_ALGORITHM,
        allow_unknown_fields: bool = False,
        where: str = "files[?]",
    ) -> FileEntry:
        if not isinstance(data, Mapping):
            raise SchemaError(f"{where} must be an object, got {type(data).__name__}")

        payload = dict(data)

        # DVC writes the digest under a key named after the algorithm
        # (`HashInfo.to_dict()` → {hi.name: hi.value}), so a real sha256 DVC
        # `.dir` says "sha256", not "md5". Accept that spelling on read and
        # normalise to the format's `md5` key, so ingesting a DVC tree never
        # needs a pre-pass.
        if "md5" not in payload and algorithm in payload:
            payload["md5"] = payload.pop(algorithm)

        missing = REQUIRED_ENTRY_FIELDS - payload.keys()
        if missing:
            raise SchemaError(f"{where} is missing required field(s): {', '.join(sorted(missing))}")

        unknown = payload.keys() - _KNOWN_ENTRY_FIELDS
        if unknown and not allow_unknown_fields:
            raise SchemaError(
                f"{where} has unrecognised field(s) {', '.join(sorted(unknown))}. "
                f"Unknown fields are rejected at schema_version {SCHEMA_VERSION} because "
                f"they are covered by body_hash: silently keeping them in one "
                f"implementation and dropping them in another would make the same "
                f"manifest hash two ways. "
                f"Pass allow_unknown_fields=True to preserve them verbatim."
            )

        relpath = validate_path(payload["relpath"], field=f"{where}.relpath")
        source_path = payload.get("source_path")
        if source_path is not None:
            source_path = validate_path(source_path, field=f"{where}.source_path")

        size = payload["size"]
        if isinstance(size, bool) or not isinstance(size, int):
            raise SchemaError(
                f"{where}.size must be an integer, got {type(size).__name__} ({size!r})"
            )
        if size < 0:
            raise SchemaError(f"{where}.size must not be negative, got {size}")

        isexec = payload.get("isexec")
        if isexec is not None and not isinstance(isexec, bool):
            raise SchemaError(f"{where}.isexec must be a boolean, got {type(isexec).__name__}")

        for optional in ("version_id", "etag"):
            value = payload.get(optional)
            if value is not None and not isinstance(value, str):
                raise SchemaError(
                    f"{where}.{optional} must be a string, got {type(value).__name__}"
                )

        return cls(
            relpath=relpath,
            md5=check_digest(payload["md5"], algorithm, field=f"{where}.md5"),
            size=size,
            source_path=source_path,
            isexec=isexec,
            version_id=payload.get("version_id"),
            etag=payload.get("etag"),
            extra={k: payload[k] for k in sorted(unknown)},
        )


@dataclass(frozen=True)
class FetchItem:
    """One unit of work for a consumer materialising a manifest.

    The reason :meth:`Manifest.fetch_plan` exists: ``archivefs`` should not have
    to re-derive spec §9's fetch-location rule (or rediscover the ``urljoin``
    trailing-slash trap in :func:`chirograph.paths.join_source_url`).
    """

    source: str
    relpath: str
    md5: str
    size: int
    url: str | None = None

    @property
    def digest(self) -> str:
        return self.md5


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #


@dataclass
class Manifest:
    """A parsed ``.chiro.json``.

    ``size``, ``nfiles`` and ``body_hash`` are **not** fields — they are
    properties derived from ``files``. Constructing a Manifest and serialising it
    therefore always produces an internally consistent document.
    """

    provenance_type: str = PROVENANCE_SELF
    files: list[FileEntry] = field(default_factory=list)
    hash_algorithm: str = DEFAULT_HASH_ALGORITHM
    schema_version: str = SCHEMA_VERSION
    record_accession: str | None = None
    source_url: str | None = None
    created: str = field(default_factory=now_timestamp)
    retrieved: str | None = None
    generator: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _reject_shadowing(self.extra, _KNOWN_HEADER_FIELDS, where="a manifest header")
        check_algorithm(self.hash_algorithm)
        if self.provenance_type not in PROVENANCE_TYPES:
            raise SchemaError(
                f"provenance_type must be one of {', '.join(PROVENANCE_TYPES)}, "
                f"got {self.provenance_type!r}"
            )
        self.created = canonical_timestamp(self.created, field="created")
        if self.retrieved is not None:
            self.retrieved = canonical_timestamp(self.retrieved, field="retrieved")
        self.files = normalise_order(self.files)

    # -- derived ------------------------------------------------------------ #

    @property
    def size(self) -> int:
        """Sum of all ``files[].size`` (spec §3.1)."""
        return sum(entry.size for entry in self.files)

    @property
    def nfiles(self) -> int:
        """Number of entries (spec §3.1)."""
        return len(self.files)

    @property
    def body_hash(self) -> str:
        """Layer-2 hash over the canonicalised ``files`` array (spec §4)."""
        return compute_body_hash([entry.to_dict() for entry in self.files], self.hash_algorithm)

    def dvc_dir_hash(self) -> str:
        """The hash DVC would assign the equivalent ``.dir`` object.

        Derived interop, not stored — see :mod:`chirograph.canonical` for why
        this is not the same value as :attr:`body_hash`.
        """
        return dvc_dir_hash([entry.to_dict() for entry in self.files], self.hash_algorithm)

    # -- container protocol ------------------------------------------------- #

    def __len__(self) -> int:
        return len(self.files)

    def __iter__(self) -> Iterator[FileEntry]:
        return iter(self.files)

    def entry(self, relpath: str) -> FileEntry | None:
        """Look up an entry by workspace path (NFC-normalised).

        A linear scan, which is right for the one-off lookup this is for. Looking
        up many paths against a large manifest should hoist :attr:`by_relpath`
        instead — calling this in a loop over the entries is quadratic.
        """
        target = validate_path(relpath)
        for candidate in self.files:
            if candidate.relpath == target:
                return candidate
        return None

    @property
    def by_relpath(self) -> dict[str, FileEntry]:
        """Index for callers doing many lookups.

        Builds a fresh dict on every access — deliberately, since ``files`` is
        mutable and a cached index could go stale — so bind it to a local once
        rather than touching the property inside a loop.
        """
        return {entry.relpath: entry for entry in self.files}

    # -- consumer helpers --------------------------------------------------- #

    def resolve_url(self, entry: FileEntry) -> str | None:
        """Absolute fetch URL for *entry*, or ``None`` without a ``source_url``."""
        if self.source_url is None:
            return None
        return join_source_url(self.source_url, entry.source)

    def fetch_plan(self) -> list[FetchItem]:
        """Everything a consumer needs to fetch and place each file (spec §9)."""
        return [
            FetchItem(
                source=entry.source,
                relpath=entry.relpath,
                md5=entry.md5,
                size=entry.size,
                url=self.resolve_url(entry),
            )
            for entry in self.files
        ]

    def with_files(self, files: Sequence[FileEntry]) -> Manifest:
        """A copy carrying different entries (derived fields follow along)."""
        return replace(self, files=list(files))

    # -- lint --------------------------------------------------------------- #

    def lint(self) -> list[Lint]:
        """Return non-fatal findings. Empty list means "nothing to say"."""
        findings: list[Lint] = []

        if self.provenance_type == PROVENANCE_OBSERVED:
            # spec §3.1: `opt*` — optional in schema, required by convention.
            if not self.source_url:
                findings.append(
                    Lint(
                        "observed-missing-source-url",
                        "provenance_type is 'observed-download' but source_url is absent; "
                        "a reproducer has nowhere to re-fetch from",
                    )
                )
            if not self.retrieved:
                findings.append(
                    Lint(
                        "observed-missing-retrieved",
                        "provenance_type is 'observed-download' but retrieved is absent; "
                        "'the version I got' is meaningless against a mutable upstream",
                    )
                )
        elif self.retrieved:
            findings.append(
                Lint(
                    "self-deposited-with-retrieved",
                    "provenance_type is 'self-deposited' but retrieved is set; spec §3.1 "
                    "says to omit it (nothing was retrieved from anywhere)",
                )
            )

        redundant = tuple(
            entry.relpath
            for entry in self.files
            if entry.source_path is not None and entry.source_path == entry.relpath
        )
        if redundant:
            findings.append(
                Lint(
                    "redundant-source-path",
                    f"{len(redundant)} entry/entries carry a source_path identical to their "
                    f"relpath. This is legal but changes body_hash relative to the terse form, "
                    f"so the manifest is not interchangeable with one that omits it "
                    f"(docs/decisions.md D-06). Drop them with `chiro remap --tidy`.",
                    redundant[:20],
                )
            )

        if any(entry.source_path is not None for entry in self.files) and not self.source_url:
            findings.append(
                Lint(
                    "source-path-without-source-url",
                    "entries carry source_path but the manifest has no source_url, so fetch "
                    "locations cannot be resolved",
                )
            )

        if self.hash_algorithm != DEFAULT_HASH_ALGORITHM:
            findings.append(
                Lint(
                    "non-default-hash-algorithm",
                    f"hash_algorithm is {self.hash_algorithm!r}; DVC `.dir` interop "
                    f"(`chiro dvc-dir`) is only verified for md5",
                )
            )

        empty = tuple(entry.relpath for entry in self.files if entry.size == 0)
        if empty:
            findings.append(
                Lint(
                    "zero-byte-files",
                    f"{len(empty)} zero-byte file(s); legal, but often the fingerprint of a "
                    f"failed transfer rather than intent",
                    empty[:20],
                )
            )

        findings.extend(
            Lint(warning.code, warning.message, tuple(warning.paths[:20]))
            for warning in lint_paths(entry.relpath for entry in self.files)
        )
        return findings

    # -- serialisation ------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict in the format's field order.

        Entries come out sorted by ``relpath`` (spec §3.2 "Entries MUST be sorted
        by ``relpath`` on disk"), which also makes the output byte-deterministic
        — a precondition for the Layer-3 whole-file hash being reproducible.
        """
        header: dict[str, Any] = {
            "schema": SCHEMA,
            "schema_version": self.schema_version,
            "hash_algorithm": self.hash_algorithm,
            "provenance_type": self.provenance_type,
            "record_accession": self.record_accession,
            "source_url": self.source_url,
            "created": self.created,
            "retrieved": self.retrieved,
            "generator": self.generator,
            "size": self.size,
            "nfiles": self.nfiles,
            "body_hash": self.body_hash,
        }
        out = {name: header[name] for name in HEADER_FIELD_ORDER if header[name] is not None}
        out.update(self.extra)
        out["files"] = [entry.to_dict() for entry in self.files]
        return out

    def to_json(self, *, indent: int | None = 2) -> str:
        """Render to JSON text, with a trailing newline.

        Pretty-printed by default because manifests are shared through Git
        (spec §7) and a one-line array diffs uselessly. The exact serialisation
        is fixed — ``indent=2``, format field order, ``ensure_ascii=False``,
        trailing newline — so that the whole-file hash of a manifest written by
        this library is reproducible. See docs/decisions.md D-09 for why that is
        a weaker guarantee than it looks.
        """
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False) + "\n"

    def to_bytes(self, *, indent: int | None = 2) -> bytes:
        return self.to_json(indent=indent).encode("utf-8")

    def dump(self, path: str | os.PathLike[str], *, indent: int | None = 2) -> Path:
        """Write to *path*, atomically. Returns the path written."""
        target = Path(path)
        if target.parent != Path():
            target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(self.to_bytes(indent=indent))
        temporary.replace(target)
        return target

    # -- deserialisation ---------------------------------------------------- #

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        allow_unknown_fields: bool | None = None,
        check_body_hash: bool = True,
        check_derived: bool = True,
    ) -> Manifest:
        """Parse and validate a manifest document.

        *allow_unknown_fields* defaults to a version-derived policy: strict at
        the ``schema_version`` this reader implements, tolerant for a higher
        *minor* (minor bumps are additive by definition, so refusing them would
        make every future field a breaking change). See docs/decisions.md D-07.
        """
        if not isinstance(data, Mapping):
            raise SchemaError(f"a manifest must be a JSON object, got {type(data).__name__}")

        if data.get("schema") != SCHEMA:
            raise SchemaError(
                f"not a chirograph manifest: expected schema {SCHEMA!r}, got {data.get('schema')!r}"
            )

        version = data.get("schema_version")
        major, minor = _parse_version(version)
        if major != SUPPORTED_MAJOR:
            raise UnsupportedVersion(
                f"schema_version {version!r} has major version {major}; this implementation "
                f"supports major version {SUPPORTED_MAJOR} only"
            )
        if allow_unknown_fields is None:
            allow_unknown_fields = minor > SUPPORTED_MINOR

        missing = REQUIRED_HEADER_FIELDS - data.keys()
        if missing:
            raise SchemaError(
                f"manifest is missing required field(s): {', '.join(sorted(missing))}"
            )
        if "files" not in data:
            raise SchemaError("manifest is missing 'files'")

        unknown = data.keys() - _KNOWN_HEADER_FIELDS
        if unknown and not allow_unknown_fields:
            raise SchemaError(
                f"manifest has unrecognised header field(s) {', '.join(sorted(unknown))}. "
                f"Pass allow_unknown_fields=True to preserve them verbatim."
            )

        algorithm = check_algorithm(_require_str(data, "hash_algorithm"))
        raw_files = data["files"]
        if isinstance(raw_files, (str, bytes)) or not isinstance(raw_files, Sequence):
            raise SchemaError(f"'files' must be an array, got {type(raw_files).__name__}")

        entries = [
            FileEntry.from_dict(
                entry,
                algorithm=algorithm,
                allow_unknown_fields=allow_unknown_fields,
                where=f"files[{index}]",
            )
            for index, entry in enumerate(raw_files)
        ]

        manifest = cls(
            provenance_type=_require_str(data, "provenance_type"),
            files=entries,
            hash_algorithm=algorithm,
            schema_version=str(version),
            record_accession=_optional_str(data, "record_accession"),
            source_url=_optional_str(data, "source_url"),
            created=_require_str(data, "created"),
            retrieved=_optional_str(data, "retrieved"),
            generator=_optional_str(data, "generator"),
            extra={key: data[key] for key in sorted(unknown)},
        )

        if check_derived:
            declared_nfiles = data["nfiles"]
            if isinstance(declared_nfiles, bool) or not isinstance(declared_nfiles, int):
                raise SchemaError(f"nfiles must be an integer, got {declared_nfiles!r}")
            if declared_nfiles != manifest.nfiles:
                raise SchemaError(
                    f"nfiles is {declared_nfiles} but 'files' has {manifest.nfiles} entries"
                )
            declared_size = data["size"]
            if isinstance(declared_size, bool) or not isinstance(declared_size, int):
                raise SchemaError(f"size must be an integer, got {declared_size!r}")
            if declared_size != manifest.size:
                raise SchemaError(
                    f"size is {declared_size} but the entry sizes sum to {manifest.size}"
                )

        if check_body_hash:
            # Validated with the same rule as every other digest, so an uppercase
            # body_hash is reported as "must be lowercase" rather than as a
            # mismatch blaming corruption (docs/decisions.md D-18).
            declared = check_digest(data["body_hash"], algorithm, field="body_hash")
            recomputed = manifest.body_hash
            if declared != recomputed:
                raise BodyHashMismatch(
                    f"body_hash mismatch: manifest declares {declared!r}, recomputed "
                    f"{recomputed!r} over the canonicalised files array. The file list has "
                    f"been truncated, edited, or corrupted — or it was written by an "
                    f"implementation that canonicalises differently "
                    f"(run `chiro test-vectors --check` against it)."
                )

        return manifest

    @classmethod
    def loads(cls, text: str | bytes, **kwargs: Any) -> Manifest:
        if isinstance(text, (bytes, bytearray)):
            text = decode_document(bytes(text))
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"manifest is not valid JSON: {exc}") from exc
        return cls.from_dict(data, **kwargs)

    @classmethod
    def load(cls, path: str | os.PathLike[str], **kwargs: Any) -> Manifest:
        try:
            return cls.loads(Path(path).read_bytes(), **kwargs)
        except ManifestError as exc:
            raise type(exc)(f"{path}: {exc}") from exc

    # -- naming ------------------------------------------------------------- #

    def default_filename(self) -> str:
        """Preferred filename: accession-derived where possible (spec §6).

        ``GSE12345.chiro.json`` beats ``manifest.chiro.json`` — unambiguous in a
        flat deposit and survives being downloaded next to other records'
        manifests.
        """
        stem = self.record_accession or "manifest"
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in stem).strip("-.")
        return f"{safe or 'manifest'}{MANIFEST_SUFFIX}"


#: BOMs we recognise well enough to name in an error. Spec §1.2 permits none of
#: them; UTF-16/32 are listed because ``json.loads`` on *bytes* will happily
#: auto-detect and decode them, so silence here means silently accepting a
#: manifest that is not UTF-8 at all.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\x00\x00\xfe\xff", "UTF-32 big-endian"),
    (b"\xff\xfe\x00\x00", "UTF-32 little-endian"),
    (b"\xef\xbb\xbf", "UTF-8"),
    (b"\xfe\xff", "UTF-16 big-endian"),
    (b"\xff\xfe", "UTF-16 little-endian"),
)


def decode_document(raw: bytes) -> str:
    """Decode manifest bytes as UTF-8, refusing anything else (spec §1.2).

    The single gate for "are these bytes a manifest at all", so the rule and its
    message live in one place rather than being restated by each caller.

    Two traps, both of which have bitten:

    * ``json.loads`` accepts *bytes* and auto-detects UTF-16/UTF-32 from their
      BOMs (RFC 8259 §8.1 notwithstanding). Handing it raw bytes therefore accepts
      manifests in encodings the spec forbids — and re-encoding a manifest changes
      its whole-file hash, so a registry would be storing two different objects
      under one ``chiro://`` reference.
    * ``UnicodeDecodeError`` is a :class:`ValueError`, not a
      :class:`json.JSONDecodeError`, so a caller catching only the latter lets a
      non-UTF-8 body escape as an uncaught error rather than as a finding.
    """
    for marker, name in _BOMS:
        if raw.startswith(marker):
            raise SchemaError(
                f"manifest starts with a {name} byte-order mark; spec §1.2 requires UTF-8 "
                f"with no BOM. A BOM (or a non-UTF-8 encoding) also changes the whole-file "
                f"hash, and so the chiro:// reference, while leaving the manifest looking "
                f"identical."
            )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaError(
            f"manifest is not valid UTF-8 ({exc}); spec §1.2 requires UTF-8 with no BOM"
        ) from exc


def _reject_shadowing(extra: Mapping[str, Any], known: frozenset[str], *, where: str) -> None:
    """Refuse ``extra`` keys that collide with a real field name.

    ``extra`` is written out after the known fields, so a colliding key would
    override the value the object computed — letting ``extra={"relpath": "../x"}``
    emit a path that never saw :func:`validate_path`, or
    ``extra={"body_hash": ...}`` emit a body_hash that is not a hash of the body.
    Unreachable through :meth:`Manifest.from_dict` (which excludes known keys from
    ``extra``), but this is the documented public object model, so the guard
    belongs on the constructor rather than in the loader.
    """
    collisions = sorted(set(extra) & known)
    if collisions:
        raise SchemaError(
            f"{where} has extra field(s) {', '.join(collisions)} that collide with real "
            f"field names. Extra fields are serialised after the known ones, so these "
            f"would silently override validated values."
        )


def normalise_order(files: Sequence[FileEntry]) -> list[FileEntry]:
    """Sort entries into canonical order and reject duplicate ``relpath``.

    Delegates to :func:`chirograph.canonical.normalise_entries` so that the
    ordering used on disk is provably the same one used for hashing, rather than
    a second sort that could drift from it.

    Entries handed in directly may carry non-NFC paths, so they are normalised
    here — by :func:`dataclasses.replace` on the entry, never by reconstructing a
    ``FileEntry`` from the canonicalised row. The row is a flattened mapping that
    includes any ``extra`` fields, so ``FileEntry(**row)`` raised ``TypeError`` for
    exactly the combination of a non-NFC path and a forward-compatible unknown
    field.
    """
    normalised: list[FileEntry] = []
    for index, entry in enumerate(files):
        # Type-check before touching `nfc`, which raises TypeError on a non-string.
        # The rows only reach canonical.py's own checks *after* this loop, so
        # normalising first would let a `relpath=123` escape as TypeError rather
        # than as the SchemaError the rest of the API promises.
        for name in ("relpath", "source_path"):
            value = getattr(entry, name)
            if value is None and name == "source_path":
                continue
            if not isinstance(value, str):
                raise SchemaError(
                    f"files[{index}]: {name!r} must be a string, got {type(value).__name__}"
                )
        relpath = nfc(entry.relpath)
        source_path = None if entry.source_path is None else nfc(entry.source_path)
        if relpath != entry.relpath or source_path != entry.source_path:
            entry = replace(entry, relpath=relpath, source_path=source_path)
        normalised.append(entry)

    # Ordering and duplicate rejection stay in canonical.py, so the on-disk order
    # is provably the order that gets hashed.
    ordered = normalise_entries([entry.to_dict() for entry in normalised])
    by_path = {entry.relpath: entry for entry in normalised}
    return [by_path[str(row["relpath"])] for row in ordered]


def _parse_version(value: object) -> tuple[int, int]:
    if not isinstance(value, str):
        raise SchemaError(f"schema_version must be a string, got {type(value).__name__}")
    parts = value.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise UnsupportedVersion(f"schema_version must look like 'MAJOR.MINOR', got {value!r}")
    return int(parts[0]), int(parts[1])


def _require_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise SchemaError(f"{key} must be a string, got {type(value).__name__}")
    return value


def _optional_str(data: Mapping[str, Any], key: str) -> str | None:
    if key not in data:
        return None
    value = data[key]
    if value is None:
        raise SchemaError(f"{key} is null; the format omits absent fields rather than writing null")
    if not isinstance(value, str):
        raise SchemaError(f"{key} must be a string, got {type(value).__name__}")
    return value


# --------------------------------------------------------------------------- #
# Module-level convenience, mirroring json.load/loads/dump/dumps
# --------------------------------------------------------------------------- #


def load(path: str | os.PathLike[str], **kwargs: Any) -> Manifest:
    """Load and validate a manifest from *path*."""
    return Manifest.load(path, **kwargs)


def loads(text: str | bytes, **kwargs: Any) -> Manifest:
    """Load and validate a manifest from JSON text."""
    return Manifest.loads(text, **kwargs)


def dump(manifest: Manifest, path: str | os.PathLike[str], *, indent: int | None = 2) -> Path:
    """Write *manifest* to *path*."""
    return manifest.dump(path, indent=indent)


def dumps(manifest: Manifest, *, indent: int | None = 2) -> str:
    """Render *manifest* as JSON text."""
    return manifest.to_json(indent=indent)


# Re-exported so callers can introspect the algorithm table without importing
# two modules.
SUPPORTED_ALGORITHMS = tuple(sorted(HASH_ALGORITHMS))
