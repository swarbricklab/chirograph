"""Transcribing a DVC cache into a manifest — ``chiro write --from-dvc-cache``.

This is meant to be near-free: DVC has already hashed everything, so building a
manifest should be a transcription rather than a re-read of the dataset. Three
things get in the way, and all three are handled here rather than left to
surprise the user.

**1. A DVC ``.dir`` has no sizes.** Checked against dvc 3.66.1 / dvc-data 3.18.2:
a ``.dir`` entry is ``{"md5", "relpath"}``. Size lives in DVC's ``Meta``, which is
only serialised in the ``with_meta=True`` variant — and that variant is not what
DVC writes to the cache. Our format requires ``size`` per entry, so it has to
come from somewhere. In order of preference:

  a. ``stat`` the **cache object** (``…/files/md5/ab/cdef…``). Authoritative
     (the object is content-addressed, so its size is the file's size) and works
     even when the workspace has never been checked out.
  b. ``stat`` the **workspace file**. Needs a checkout, but works when the cache
     has been garbage-collected or the data is only linked from a remote.

  Both are pure ``stat`` calls, so the operation stays near-free either way.
  Where both are available they are cross-checked, because a disagreement means
  the workspace has drifted from the cache and the transcription would otherwise
  bake in whichever one we happened to consult.

**2. Legacy ``md5-dos2unix`` hashes.** DVC ≤2.x hashed *text* files after
converting CRLF to LF, so the recorded md5 is not the md5 of the bytes on disk.
Transcribing those would produce a manifest that fails verification on exactly
the files whose line endings differ — a subtle, delayed, confusing failure. They
are refused by default.

**3. Cache layout moved.** DVC 3.x uses ``cache/files/md5/ab/cdef…``; 2.x used
``cache/ab/cdef…``. Both are searched, along with any ``cache.dir`` configured in
``.dvc/config``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .constants import DEFAULT_HASH_ALGORITHM, PROVENANCE_SELF
from .errors import DvcError
from .manifest import FileEntry, Manifest
from .paths import validate_path
from .timestamps import now_timestamp
from .version import GENERATOR

__all__ = [
    "DvcOut",
    "DvcTranscription",
    "find_cache_dirs",
    "find_dvc_root",
    "manifest_from_dvc",
    "read_dvc_file",
]

#: Hash names DVC may record. Only plain md5 is transcribable as-is.
_PLAIN = "md5"
_LEGACY = "md5-dos2unix"


def _load_yaml(path: Path) -> object:
    """Parse a ``.dvc`` / ``dvc.lock`` file.

    PyYAML is an optional extra rather than a hard dependency: chirograph is the
    reference implementation of a *file format*, and ``archivefs`` should be able
    to depend on it without inheriting anything (docs/decisions.md D-08). A
    hand-rolled parser for "the subset of YAML DVC happens to emit" was the
    tempting alternative and is a bug farm — DVC is free to add a quoted key or a
    flow mapping at any point.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise DvcError(
            f"reading {path.name} needs PyYAML, which chirograph does not require by default. "
            f"Install it with `pip install 'chirograph[dvc]'` (or `conda install pyyaml`)."
        ) from exc
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DvcError(f"{path}: not valid YAML: {exc}") from exc


@dataclass(frozen=True)
class DvcOut:
    """One ``outs:`` entry from a ``.dvc`` file or ``dvc.lock`` stage."""

    path: str
    md5: str
    hash_name: str
    size: int | None = None
    nfiles: int | None = None
    source: Path | None = None

    @property
    def is_dir(self) -> bool:
        return self.md5.endswith(".dir")

    @property
    def oid(self) -> str:
        """The cache object id, without the ``.dir`` suffix."""
        return self.md5[: -len(".dir")] if self.is_dir else self.md5

    @property
    def legacy(self) -> bool:
        return self.hash_name == _LEGACY


@dataclass
class DvcTranscription:
    """A transcribed manifest plus how each size was obtained."""

    manifest: Manifest
    out: DvcOut
    size_source: str = "unknown"
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Reading .dvc / dvc.lock
# --------------------------------------------------------------------------- #


def read_dvc_file(path: str | os.PathLike[str]) -> list[DvcOut]:
    """Read the ``outs`` of a ``.dvc`` file or a ``dvc.lock``."""
    target = Path(path)
    if not target.is_file():
        raise DvcError(f"{target} does not exist")
    document = _load_yaml(target)
    if not isinstance(document, dict):
        raise DvcError(f"{target}: expected a YAML mapping at the top level")

    if "stages" in document:  # dvc.lock
        outs: list[DvcOut] = []
        stages = document.get("stages") or {}
        if not isinstance(stages, dict):
            raise DvcError(f"{target}: 'stages' is not a mapping")
        for stage_name, stage in stages.items():
            if not isinstance(stage, dict):
                continue
            for raw in stage.get("outs") or []:
                outs.append(_parse_out(raw, target, label=f"stages.{stage_name}.outs"))
        if not outs:
            raise DvcError(f"{target}: no stage outputs found")
        return outs

    raw_outs = document.get("outs")
    if not raw_outs:
        raise DvcError(
            f"{target}: no 'outs' found. `--from-dvc-cache` expects a .dvc file or a dvc.lock."
        )
    if not isinstance(raw_outs, list):
        raise DvcError(f"{target}: 'outs' is not a list")
    return [_parse_out(raw, target, label="outs") for raw in raw_outs]


def _parse_out(raw: object, source: Path, *, label: str) -> DvcOut:
    if not isinstance(raw, dict):
        raise DvcError(f"{source}: {label} entry is not a mapping")

    # DVC 3.x records `hash: md5`. DVC 2.x recorded nothing, and its md5s are the
    # dos2unix variety for text files. Absence therefore means legacy, not
    # "default" — getting this backwards produces manifests that fail
    # verification on text files only.
    hash_name = raw.get("hash")
    if hash_name is None:
        hash_name = _LEGACY
    if not isinstance(hash_name, str):
        raise DvcError(f"{source}: {label} 'hash' is not a string")

    digest = raw.get("md5") or raw.get("sha256") or raw.get("hash_value")
    if not isinstance(digest, str):
        raise DvcError(
            f"{source}: {label} entry for {raw.get('path')!r} has no md5. Outputs tracked "
            f"only on a remote with a non-md5 hash cannot be transcribed."
        )
    path = raw.get("path")
    if not isinstance(path, str):
        raise DvcError(f"{source}: {label} entry has no 'path'")

    size = raw.get("size")
    nfiles = raw.get("nfiles")
    return DvcOut(
        path=path,
        md5=digest,
        hash_name=hash_name if hash_name != "md5" else _PLAIN,
        size=size if isinstance(size, int) else None,
        nfiles=nfiles if isinstance(nfiles, int) else None,
        source=source,
    )


# --------------------------------------------------------------------------- #
# Locating the cache
# --------------------------------------------------------------------------- #


def find_dvc_root(start: Path) -> Path | None:
    """Walk up from *start* looking for a ``.dvc`` directory."""
    for candidate in [start, *start.parents]:
        if (candidate / ".dvc").is_dir():
            return candidate
    return None


def _configured_cache_dir(dvc_root: Path) -> Path | None:
    """Read ``cache.dir`` out of ``.dvc/config`` (and ``config.local``).

    A hand-rolled INI read rather than :mod:`configparser`, because DVC's config
    uses ``['cache']``-style section names with quotes in some places and we only
    want one key. Failure here is not fatal — the default location is tried too.
    """
    for name in ("config.local", "config"):
        config = dvc_root / ".dvc" / name
        if not config.is_file():
            continue
        section = None
        try:
            lines = config.read_text(encoding="utf-8").splitlines()
        except OSError:  # pragma: no cover
            continue
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().strip("\"'")
            elif section == "cache" and "=" in stripped:
                key, _, value = stripped.partition("=")
                if key.strip() == "dir":
                    raw = value.strip().strip("\"'")
                    path = Path(raw)
                    return path if path.is_absolute() else (dvc_root / ".dvc" / raw).resolve()
    return None


def find_cache_dirs(dvc_root: Path, extra: Iterable[Path] = ()) -> list[Path]:
    """Candidate cache roots, most likely first.

    Both the DVC 3.x layout (``cache/files/md5``) and the 2.x one (``cache``) are
    returned, because a repo may have been migrated and still hold objects under
    the old paths.
    """
    candidates: list[Path] = [Path(p) for p in extra]
    configured = _configured_cache_dir(dvc_root)
    for base in filter(None, [configured, dvc_root / ".dvc" / "cache"]):
        candidates.append(base / "files" / "md5")  # DVC 3.x
        candidates.append(base)  # DVC 2.x
    seen: set[Path] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        resolved = candidate
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)
    return ordered


def _cache_object(cache_dirs: Sequence[Path], oid: str, *, suffix: str = "") -> Path | None:
    for cache in cache_dirs:
        candidate = cache / oid[:2] / (oid[2:] + suffix)
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- #
# Transcription
# --------------------------------------------------------------------------- #


def manifest_from_dvc(
    dvc_file: str | os.PathLike[str],
    *,
    out_path: str | None = None,
    cache_dirs: Iterable[str | os.PathLike[str]] = (),
    workspace_root: str | os.PathLike[str] | None = None,
    provenance_type: str = PROVENANCE_SELF,
    source_url: str | None = None,
    record_accession: str | None = None,
    retrieved: str | None = None,
    generator: str | None = None,
    allow_legacy_md5: bool = False,
    prefer_workspace_sizes: bool = False,
) -> DvcTranscription:
    """Build a manifest from a DVC-tracked output without rehashing anything.

    *out_path* selects one output when the file describes several (a ``dvc.lock``
    typically does).
    """
    target = Path(dvc_file)
    outs = read_dvc_file(target)

    if out_path is not None:
        matching = [out for out in outs if out.path == out_path]
        if not matching:
            available = ", ".join(sorted(out.path for out in outs))
            raise DvcError(f"{target}: no output with path {out_path!r}. Available: {available}")
        out = matching[0]
    elif len(outs) == 1:
        out = outs[0]
    else:
        available = ", ".join(sorted(o.path for o in outs))
        raise DvcError(
            f"{target} declares {len(outs)} outputs; choose one with --dvc-out. "
            f"Available: {available}"
        )

    if out.legacy and not allow_legacy_md5:
        raise DvcError(
            f"{target}: output {out.path!r} carries "
            f"{'a ' + _LEGACY if out.hash_name == _LEGACY else 'no'} hash marker, which means "
            f"DVC 2.x-era hashing. Those md5s were computed after CRLF→LF conversion for files "
            f"DVC classified as text, so they are NOT the md5 of the bytes on disk, and a "
            f"manifest built from them will fail verification on exactly those files. "
            f"Re-add the output with a current DVC (`dvc add --force`) to get plain md5s, or "
            f"pass --allow-legacy-md5 if you know every file is binary."
        )

    dvc_root = find_dvc_root(target.resolve().parent)
    if dvc_root is None:
        raise DvcError(
            f"no .dvc directory found at or above {target.parent}; "
            f"pass --cache-dir to point at the cache explicitly"
        )
    caches = find_cache_dirs(dvc_root, extra=[Path(p) for p in cache_dirs])

    workspace = (
        Path(workspace_root) if workspace_root is not None else (target.resolve().parent / out.path)
    )

    notes: list[str] = []
    if out.is_dir:
        entries, size_source, more = _entries_for_directory(
            out, caches, workspace, prefer_workspace_sizes
        )
    else:
        entries, size_source, more = _entries_for_file(
            out, caches, workspace, prefer_workspace_sizes
        )
    notes.extend(more)

    if out.nfiles is not None and out.nfiles != len(entries):
        notes.append(
            f"{target.name} declares nfiles={out.nfiles} but the .dir lists {len(entries)} "
            f"entries; using the .dir"
        )
    total = sum(entry.size for entry in entries)
    if out.size is not None and out.size != total:
        notes.append(
            f"{target.name} declares size={out.size} but the recovered sizes sum to {total}. "
            f"One of them is stale — verify with `chiro verify` before relying on this manifest."
        )

    manifest = Manifest(
        provenance_type=provenance_type,
        files=entries,
        hash_algorithm=DEFAULT_HASH_ALGORITHM,
        source_url=source_url,
        record_accession=record_accession,
        created=now_timestamp(),
        retrieved=retrieved,
        generator=generator if generator is not None else GENERATOR,
    )
    return DvcTranscription(manifest=manifest, out=out, size_source=size_source, notes=notes)


def _entries_for_file(
    out: DvcOut,
    caches: Sequence[Path],
    workspace: Path,
    prefer_workspace_sizes: bool,
) -> tuple[list[FileEntry], str, list[str]]:
    """A ``.dvc`` tracking a single file."""
    relpath = validate_path(Path(out.path).name)
    size, source, notes = _recover_size(
        relpath, out.oid, caches, workspace, prefer_workspace_sizes, declared=out.size
    )
    return [FileEntry(relpath=relpath, md5=out.oid, size=size)], source, notes


def _entries_for_directory(
    out: DvcOut,
    caches: Sequence[Path],
    workspace: Path,
    prefer_workspace_sizes: bool,
) -> tuple[list[FileEntry], str, list[str]]:
    """A ``.dvc`` tracking a directory, via its ``.dir`` cache object."""
    dir_object = _cache_object(caches, out.oid, suffix=".dir")
    if dir_object is None:
        searched = "\n  ".join(str(cache) for cache in caches)
        raise DvcError(
            f"cache object {out.oid}.dir not found. Searched:\n  {searched}\n"
            f"Run `dvc fetch {out.path}` (or `dvc pull`) to populate the cache, or pass "
            f"--cache-dir."
        )
    try:
        listing = json.loads(dir_object.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DvcError(f"{dir_object}: unreadable .dir object: {exc}") from exc
    if not isinstance(listing, list):
        raise DvcError(f"{dir_object}: expected a JSON array")

    entries: list[FileEntry] = []
    notes: list[str] = []
    sources: set[str] = set()
    for index, row in enumerate(listing):
        if not isinstance(row, dict):
            raise DvcError(f"{dir_object}: entry {index} is not an object")
        digest = row.get("md5")
        raw_relpath = row.get("relpath")
        if not isinstance(digest, str) or not isinstance(raw_relpath, str):
            raise DvcError(f"{dir_object}: entry {index} lacks md5/relpath")
        relpath = validate_path(raw_relpath, field=f"{dir_object.name}[{index}].relpath")

        declared = row.get("size") if isinstance(row.get("size"), int) else None
        size, source, more = _recover_size(
            relpath, digest, caches, workspace, prefer_workspace_sizes, declared=declared
        )
        sources.add(source)
        notes.extend(more)
        entries.append(
            FileEntry(
                relpath=relpath,
                md5=digest,
                size=size,
                isexec=row.get("isexec") if isinstance(row.get("isexec"), bool) else None,
                version_id=row.get("version_id")
                if isinstance(row.get("version_id"), str)
                else None,
            )
        )

    combined = "+".join(sorted(sources)) if sources else "none"
    return entries, combined, notes


def _recover_size(
    relpath: str,
    oid: str,
    caches: Sequence[Path],
    workspace: Path,
    prefer_workspace_sizes: bool,
    *,
    declared: int | None,
) -> tuple[int, str, list[str]]:
    """Find one entry's byte size without reading the file.

    The ``.dir`` object DVC hashes carries no sizes (see the module docstring), so
    the size is recovered by ``stat``. Both available sources are cross-checked
    when both exist, because a silent disagreement between the cache and the
    workspace is precisely the situation in which picking one at random produces a
    plausible-looking, wrong manifest.
    """
    notes: list[str] = []
    if declared is not None:
        # A `.dir` written with meta, or a single-file `.dvc` with `size:`.
        return declared, "dvc-metadata", notes

    cache_size: int | None = None
    cache_object = _cache_object(caches, oid)
    if cache_object is not None:
        try:
            cache_size = cache_object.stat().st_size
        except OSError:  # pragma: no cover
            cache_size = None

    workspace_size: int | None = None
    candidate = workspace / relpath if workspace.is_dir() else workspace
    try:
        if candidate.is_file():
            workspace_size = candidate.stat().st_size
    except OSError:  # pragma: no cover
        workspace_size = None

    if cache_size is not None and workspace_size is not None and cache_size != workspace_size:
        notes.append(
            f"{relpath}: cache object is {cache_size} bytes but the workspace file is "
            f"{workspace_size}; the workspace has drifted from the cache. Using the "
            f"{'workspace' if prefer_workspace_sizes else 'cache'} size — run `dvc status` "
            f"and `chiro verify` before trusting this manifest."
        )

    order = (
        (workspace_size, "workspace", cache_size, "cache")
        if prefer_workspace_sizes
        else (cache_size, "cache", workspace_size, "workspace")
    )
    if order[0] is not None:
        return order[0], order[1], notes
    if order[2] is not None:
        return order[2], order[3], notes

    raise DvcError(
        f"cannot determine the size of {relpath!r}: it is neither in the DVC cache "
        f"(looked for object {oid}) nor checked out in the workspace at "
        f"{workspace / relpath}. A DVC .dir records no sizes, so one of the two must be "
        f"present. Run `dvc fetch`/`dvc pull` to populate the cache, or `dvc checkout` to "
        f"materialise the workspace."
    )
