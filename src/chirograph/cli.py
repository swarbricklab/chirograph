"""``chiro`` / ``chirograph`` command-line interface.

Exit codes are part of the contract, because this is meant to run in CI (a
registry's merge gate, registry design §6):

* ``0`` — success; whatever was asked for held.
* ``1`` — a *finding*: bytes disagree with a manifest, or a manifest is invalid.
  The tool worked; the answer is "no".
* ``2`` — the tool could not do the job: bad usage, missing file, unreadable
  input.

Built on :mod:`argparse` rather than a nicer library so that the package keeps
zero required dependencies (docs/decisions.md D-08).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from .constants import (
    DEFAULT_HASH_ALGORITHM,
    HASH_ALGORITHMS,
    PROVENANCE_OBSERVED,
    PROVENANCE_SELF,
    PROVENANCE_TYPES,
)
from .diff import diff_manifests
from .errors import ChirographError
from .manifest import Manifest, decode_document, read_manifest_bytes
from .ref import ChiroRef, canonical_ref
from .rewrite import parse_rules
from .version import __version__

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_ERROR = 2

PROGRAM = "chiro"


# --------------------------------------------------------------------------- #
# Small output helpers
# --------------------------------------------------------------------------- #


def human_bytes(count: int) -> str:
    """Render a byte count for humans, keeping the exact value alongside."""
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    value = float(count)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{count} ({value:.1f} {units[index]})" if index else f"{count} B"


def emit_json(payload: object, stream: TextIO) -> None:
    """Write *payload* as JSON.

    .. rubric:: Filenames that are not valid UTF-8

    A path whose bytes are not valid UTF-8 reaches us as unpaired surrogates
    (Python's ``surrogateescape``). Such a path is emitted as a ``\\uDCxx`` escape:
    syntactically valid JSON, and losslessly recoverable by a Python consumer, but
    RFC 8259 §8.2 marks unpaired surrogates as not interoperable — Go's
    ``encoding/json`` substitutes U+FFFD and ``serde_json`` rejects them.

    A previous version rewrote them to a literal-backslash spelling to satisfy
    RFC 8259. That was worse. The result could not be turned back into a filename,
    so a consumer could no longer act on the report; and a directory holding both
    a file with an invalid byte and one *literally* named with those six characters
    collapsed to one indistinguishable string, so a consumer that deduplicates saw
    one extra file where two existed. Losing recoverability and uniqueness to gain
    cross-language politeness is the wrong trade for a report whose whole purpose is
    to name a file precisely.

    Consumers in other languages should treat such entries as opaque. The registry's
    drift-report format is where this needs a real answer (docs/ideas.md): it should
    carry the raw bytes in a dedicated, explicitly-encoded field rather than trying
    to squeeze them into a JSON string.
    """
    json.dump(payload, stream, indent=2, ensure_ascii=False, default=str)
    stream.write("\n")


class _SurrogateSafeStream:
    """A stream proxy that survives text containing unpaired surrogates.

    Filenames whose bytes are not valid UTF-8 come back from the filesystem as
    surrogates, and they end up *inside error messages* — which is exactly when
    you most want the message. Writing one to a strict UTF-8 stream raises
    ``UnicodeEncodeError``, so reporting the problem crashed on the problem.

    Wrapping the CLI's two streams once covers every call site, including ones
    added later, and is why commands take ``out``/``err`` as parameters rather than
    touching :data:`sys.stdout` directly.
    """

    __slots__ = ("_stream",)

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        try:
            return self._stream.write(text)
        except UnicodeEncodeError:
            # backslashreplace keeps the filename legible and copy-pasteable.
            return self._stream.write(text.encode("utf-8", "backslashreplace").decode("utf-8"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _Progress:
    """Minimal stderr progress, only when attached to a terminal."""

    def __init__(self, label: str, enabled: bool) -> None:
        self.label = label
        self.enabled = enabled and sys.stderr.isatty()
        self._last = -1

    def files(self, done: int, total: int, *_: int) -> None:
        if not self.enabled or total == 0:
            return
        percent = done * 100 // total
        if percent != self._last:
            self._last = percent
            sys.stderr.write(f"\r{self.label}: {done}/{total} ({percent}%)")
            sys.stderr.flush()

    def done(self) -> None:
        if self.enabled and self._last >= 0:
            sys.stderr.write("\n")
            sys.stderr.flush()


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=("Chirograph — portable, content-addressed manifests for scientific datasets."),
        epilog=(
            "Exit status: 0 success, 1 a finding (bytes or manifest disagree), "
            "2 the tool could not run. See docs/chiro-format-spec.md for the format."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"chirograph {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    _add_write(subparsers)
    _add_capture(subparsers)
    _add_verify(subparsers)
    _add_validate(subparsers)
    _add_info(subparsers)
    _add_ls(subparsers)
    _add_diff(subparsers)
    _add_ref(subparsers)
    _add_remap(subparsers)
    _add_dvc_dir(subparsers)
    _add_schema(subparsers)
    _add_test_vectors(subparsers)
    return parser


def _add_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help=(
            "where to write the manifest. Defaults to <accession>.chiro.json in the "
            "current directory, or manifest.chiro.json without an accession."
        ),
    )
    parser.add_argument(
        "--stdout", action="store_true", help="write the manifest to stdout instead of a file"
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        metavar="N",
        help=(
            "JSON indentation (default 2). Use 0 for a single line. Note this changes "
            "the whole-file hash, and therefore the chiro:// reference."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do the work and report, but write nothing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "overwrite the output file if it already exists (default: refuse). "
            "A manifest is often a drift snapshot, so overwriting one can destroy "
            "the record it exists to keep."
        ),
    )


def _add_allow_empty(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "write a manifest even if no files were found (default: refuse). "
            "An empty manifest usually means the default excludes dropped "
            "everything — e.g. a directory of *.chiro.json files."
        ),
    )


def _add_header_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--accession", metavar="ID", help="record_accession (e.g. GSE12345)")
    parser.add_argument("--source-url", metavar="URL", help="base location of the data")
    parser.add_argument("--created", metavar="TS", help="override created (ISO 8601 with offset)")
    parser.add_argument("--generator", metavar="STR", help="override the generator string")
    parser.add_argument(
        "--hash-algorithm",
        choices=sorted(HASH_ALGORITHMS),
        default=None,
        help=(
            "hash algorithm (default md5, which keeps DVC .dir interop). Defaults to "
            "None internally so that 'not given' is distinguishable from an explicit "
            "'md5' — --from-dvc-cache must not reject a sha256 .dvc for disagreeing "
            "with a value the user never typed."
        ),
    )


def _add_walk_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help=(
            "exclude paths matching GLOB, in addition to the defaults. A pattern with "
            "'/' matches the whole relative path; without, it matches any component. "
            "Repeatable."
        ),
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="do not apply the built-in excludes (.git, .dvc, *.chiro.json, ...)",
    )
    parser.add_argument("--no-hidden", action="store_true", help="skip dot-files and dot-dirs")
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="follow directory symlinks (cycle-guarded). Off by default: a link out of "
        "the tree silently pulls in bytes you did not mean to publish.",
    )
    parser.add_argument(
        "--skip-unreadable",
        action="store_true",
        help="record unreadable files as skipped instead of failing. Off by default, "
        "because a manifest that omits a file it could not read is a lie.",
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=None, metavar="N", help="parallel hashing workers"
    )
    parser.add_argument(
        "--relpath-from-source",
        action="append",
        default=[],
        metavar="RE=>REPL",
        help=(
            "the tree is the DEPOSIT layout: walked path becomes source_path, and this "
            "rule derives relpath. Repeatable; first match wins. Example: "
            "'^GSE1_([^_]+)_(.*)$=>\\1/\\2'"
        ),
    )
    parser.add_argument(
        "--source-from-relpath",
        action="append",
        default=[],
        metavar="RE=>REPL",
        help=(
            "the tree is the WORKSPACE layout: walked path becomes relpath, and this "
            "rule derives source_path. Repeatable; first match wins."
        ),
    )


def _add_write(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "write",
        help="build a manifest from a directory tree or a DVC cache",
        description=(
            "Walk a directory, hash every file, and write a manifest. With "
            "--from-dvc-cache, transcribe hashes DVC has already computed instead "
            "(no re-reading of the data)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("directory", nargs="?", help="directory to describe")
    parser.add_argument(
        "--provenance",
        choices=PROVENANCE_TYPES,
        default=PROVENANCE_SELF,
        help="provenance_type",
    )
    parser.add_argument(
        "--retrieved",
        metavar="TS",
        help="when the bytes were fetched (observed-download only)",
    )
    _add_header_flags(parser)
    _add_walk_flags(parser)
    dvc = parser.add_argument_group("DVC transcription")
    dvc.add_argument(
        "--from-dvc-cache",
        metavar="PATH",
        help="a .dvc file or dvc.lock to transcribe instead of walking a directory",
    )
    dvc.add_argument("--dvc-out", metavar="PATH", help="which output to use, if several")
    dvc.add_argument(
        "--cache-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="extra DVC cache root to search. Repeatable.",
    )
    dvc.add_argument(
        "--allow-legacy-md5",
        action="store_true",
        help="transcribe DVC 2.x-era md5s. UNSAFE unless every file is binary: those "
        "hashes were computed after CRLF->LF conversion for text files.",
    )
    dvc.add_argument(
        "--prefer-workspace-sizes",
        action="store_true",
        help="when cache and workspace disagree on a size, believe the workspace",
    )
    _add_output_flags(parser)
    _add_allow_empty(parser)
    parser.set_defaults(func=cmd_write)


def _add_capture(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "capture",
        help="write an observed-download manifest for a dataset you fetched",
        description=(
            "Like `write --provenance observed-download`, but requires --source-url and "
            "defaults --retrieved to now: the two fields whose absence makes an observed "
            "manifest meaningless (spec §5)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("directory", help="directory holding the fetched data")
    parser.add_argument("--source-url", required=True, metavar="URL", help="where it came from")
    parser.add_argument(
        "--retrieved",
        metavar="TS",
        help="when the fetch happened (default: now — pass this explicitly if the "
        "download finished earlier, or the provenance is wrong)",
    )
    parser.add_argument("--accession", metavar="ID", help="record_accession")
    parser.add_argument("--created", metavar="TS", help="override created")
    parser.add_argument("--generator", metavar="STR", help="override the generator string")
    parser.add_argument(
        "--hash-algorithm", choices=sorted(HASH_ALGORITHMS), default=None, help="hash algorithm"
    )
    _add_walk_flags(parser)
    _add_output_flags(parser)
    _add_allow_empty(parser)
    parser.set_defaults(func=cmd_capture)


def _add_verify(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "verify",
        help="check bytes against a manifest (Layers 1 and 2)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("manifest", help="manifest to verify against")
    parser.add_argument(
        "--root",
        metavar="DIR",
        help="directory holding the data (default: the manifest's own directory)",
    )
    parser.add_argument(
        "--layer",
        choices=["all", "1", "2"],
        default="all",
        help="'2' checks only the manifest's internal integrity and reads no data",
    )
    parser.add_argument(
        "--source-layout",
        action="store_true",
        help="look for files by source_path (deposit layout) rather than relpath",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--sample",
        type=int,
        metavar="N",
        help="check N files, chosen deterministically from the body_hash",
    )
    group.add_argument(
        "--sample-fraction", type=float, metavar="F", help="check a fraction (0 < F <= 1)"
    )
    parser.add_argument(
        "--check-extra",
        action="store_true",
        help="also report files present under the root but absent from the manifest",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat extra files as a failure (implies --check-extra)",
    )
    parser.add_argument("-j", "--jobs", type=int, default=None, metavar="N")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("-q", "--quiet", action="store_true", help="only report failures")
    parser.set_defaults(func=cmd_verify)


def _add_validate(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "validate",
        help="check a manifest's own integrity and hygiene, reading no data",
        description=(
            "Everything checkable without the data: JSON well-formedness, schema shape, "
            "path safety, derived-field agreement, body_hash, plus lint warnings. This is "
            "a registry's Tier-1 gate (registry design §6) and the cheap-checks-first "
            "defence of §8."
        ),
    )
    parser.add_argument("manifest", nargs="+", help="manifest(s) to validate")
    parser.add_argument(
        "--jsonschema",
        action="store_true",
        help="additionally validate against the published JSON Schema (needs the "
        "'schema' extra). Note the schema cannot express the integrity constraints.",
    )
    parser.add_argument(
        "--strict-lint", action="store_true", help="treat lint warnings as failures"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.set_defaults(func=cmd_validate)


def _add_info(subparsers: Any) -> None:
    parser = subparsers.add_parser("info", help="summarise a manifest")
    parser.add_argument("manifest")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd_info)


def _add_ls(subparsers: Any) -> None:
    parser = subparsers.add_parser("ls", help="list the files a manifest describes")
    parser.add_argument("manifest")
    parser.add_argument("-l", "--long", action="store_true", help="show size and hash")
    parser.add_argument(
        "--source", action="store_true", help="show deposit paths (source_path) instead"
    )
    parser.add_argument("--urls", action="store_true", help="show resolved fetch URLs")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd_ls)


def _add_diff(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "diff",
        help="compare two manifests",
        description=(
            "Comparing two observed-download manifests of the same accession, taken at "
            "different times, is an upstream-drift report (registry design §7b)."
        ),
    )
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="exit 1 when the manifests differ (for use in CI)",
    )
    parser.set_defaults(func=cmd_diff)


def _add_ref(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "ref",
        help="print or check a manifest's chiro:// reference",
        description=(
            "The reference is the manifest's whole-file hash (Layer 3). Because it is a "
            "hash of bytes, re-indenting a manifest changes it — use --canonical for a "
            "reformat-invariant address. See docs/decisions.md D-09."
        ),
    )
    parser.add_argument("manifest")
    parser.add_argument(
        "--algorithm", choices=sorted(HASH_ALGORITHMS), default="md5", help="hash algorithm"
    )
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="address the canonical document form instead of the raw bytes",
    )
    parser.add_argument("--shard", action="store_true", help="print the registry shard path too")
    parser.add_argument(
        "--check", metavar="REF", help="verify the manifest matches REF; exit 1 if not"
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help=(
            "skip confirming the file is a valid manifest before minting a reference. "
            "Only for hashing a file you already know is one — a reference asserts "
            "'this is manifest X', so by default that is checked."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd_ref)


def _add_remap(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "remap",
        help="rewrite the paths or header of an existing manifest",
        description=(
            "Changes relpath/source_path or header fields without re-reading any data. "
            "The hashes are untouched, so the result still describes the same bytes."
        ),
    )
    parser.add_argument("manifest")
    parser.add_argument(
        "--relpath-from-source",
        action="append",
        default=[],
        metavar="RE=>REPL",
        help="derive a new relpath from each entry's source path",
    )
    parser.add_argument(
        "--source-from-relpath",
        action="append",
        default=[],
        metavar="RE=>REPL",
        help="derive a new source_path from each entry's relpath",
    )
    parser.add_argument(
        "--tidy",
        action="store_true",
        help="drop any source_path identical to its relpath (changes body_hash — "
        "docs/decisions.md D-06)",
    )
    parser.add_argument("--set-source-url", metavar="URL")
    parser.add_argument("--set-accession", metavar="ID")
    parser.add_argument("--set-provenance", choices=PROVENANCE_TYPES)
    _add_output_flags(parser)
    parser.set_defaults(func=cmd_remap)


def _add_dvc_dir(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "dvc-dir",
        help="emit the equivalent DVC .dir object and its hash",
        description=(
            "DVC interop. The body of a manifest is a superset of a DVC .dir, but a .dir "
            "carries no sizes and DVC serialises it differently, so body_hash is NOT a "
            ".dir hash. This command produces the real thing, letting a DVC user check a "
            "manifest with DVC's own logic. See docs/decisions.md D-01."
        ),
    )
    parser.add_argument("manifest")
    parser.add_argument("--hash-only", action="store_true", help="print just the hash")
    parser.add_argument("-o", "--output", metavar="PATH", help="write the .dir bytes to PATH")
    parser.set_defaults(func=cmd_dvc_dir)


def _add_schema(subparsers: Any) -> None:
    parser = subparsers.add_parser("schema", help="print the published JSON Schema")
    parser.add_argument(
        "--path", action="store_true", help="print the schema's filesystem path instead"
    )
    parser.set_defaults(func=cmd_schema)


def _add_test_vectors(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "test-vectors",
        help="print or self-check the frozen conformance vectors",
        description=(
            "The vectors pin canonicalisation across implementations. If you are writing "
            "a second implementation, `chiro test-vectors > vectors.json` and make your "
            "code reproduce every 'canonical' string exactly — a near-miss produces "
            "non-matching hashes silently (spec §4)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check this implementation against the vectors and exit non-zero on failure",
    )
    parser.add_argument("--path", action="store_true", help="print the vectors' path instead")
    parser.set_defaults(func=cmd_test_vectors)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _excludes_for(args: argparse.Namespace) -> list[str] | None:
    from .walk import DEFAULT_EXCLUDES

    if args.no_default_excludes:
        return list(args.exclude)
    return [*DEFAULT_EXCLUDES, *args.exclude]


def _guard_no_clobber(target: Path, force: bool) -> None:
    """Refuse to overwrite an existing manifest unless ``--force``.

    Raises :class:`FileExistsError`, which the top-level handler maps to exit 2
    (the tool declined to run) rather than 1 (a finding). Manifests are often
    drift snapshots, and content-addressed names (spec §1.1) let successive
    observations of one accession coexist instead of clobbering.
    """
    if target.exists() and not force:
        raise FileExistsError(
            f"{target} already exists; refusing to overwrite (use --force). "
            "If this is a new observation, give it its own name — content-addressed "
            "names (spec §1.1) keep successive snapshots from colliding."
        )


def _write_output(manifest: Manifest, args: argparse.Namespace, out: TextIO) -> Path | None:
    indent = args.indent if args.indent > 0 else None
    if args.dry_run:
        return None
    if args.stdout:
        out.write(manifest.to_json(indent=indent))
        return None
    target = Path(args.output) if args.output else Path(manifest.default_filename())
    _guard_no_clobber(target, args.force)
    manifest.dump(target, indent=indent)
    return target


def _report_build(result: Any, target: Path | None, args: argparse.Namespace, err: TextIO) -> None:
    """Write the build summary and every diagnostic to stderr.

    Under ``--stdout`` the informational summary is dropped but **notes and lint
    warnings are not**. They go to stderr, which cannot contaminate the manifest on
    stdout, and suppressing them meant that `chiro write … --stdout > m.chiro.json`
    — the obvious idiom — silently discarded things like "the .dvc's declared size
    disagrees with the recovered sizes; one of them is stale".
    """
    manifest = result.manifest
    lines: list[str] = []
    if not args.stdout:
        lines += [
            f"{manifest.nfiles} file(s), {human_bytes(manifest.size)}",
            f"body_hash {manifest.hash_algorithm}:{manifest.body_hash}",
        ]
        if target is not None:
            reference = ChiroRef.for_path(target, manifest.hash_algorithm)
            lines.insert(0, f"wrote {target}")
            lines.append(f"reference {reference}")
        elif args.dry_run:
            lines.insert(0, "dry run: nothing written")
    for note in getattr(result, "notes", []):
        lines.append(f"note: {note}")
    for finding in manifest.lint():
        lines.append(f"warning: {finding}")
    walk = getattr(result, "walk", None)
    if walk is not None and walk.skipped:
        lines.append(f"skipped {len(walk.skipped)} entry/entries (use --json for detail)")
    if lines:
        err.write("\n".join(lines) + "\n")


def _finalize_build(result: Any, args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    """Guard, write, and report a freshly built manifest. Shared by write/capture.

    Refuses to write an empty (zero-file) manifest unless ``--allow-empty``: such a
    manifest verifies clean and attests nothing, so emitting one silently is a
    booby trap. The commonest cause is the default excludes removing every
    candidate — e.g. running ``chiro write`` over a directory of ``*.chiro.json``.
    ``--dry-run`` writes nothing, so it only reports (the summary's "0 file(s)"
    makes the emptiness visible); the guard is a write-policy check, like the
    no-clobber guard, and neither fires when nothing is written.
    """
    if result.manifest.nfiles == 0 and not args.allow_empty and not args.dry_run:
        err.write(
            "no files to include; refusing to write an empty manifest (use "
            "--allow-empty). The default excludes drop *.chiro.json, VCS metadata "
            "and caches — check whether they removed everything.\n"
        )
        return EXIT_ERROR
    target = _write_output(result.manifest, args, out)
    _report_build(result, target, args, err)
    return EXIT_OK


def cmd_write(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    if bool(args.directory) == bool(args.from_dvc_cache):
        err.write("give either a directory or --from-dvc-cache, not both or neither\n")
        return EXIT_ERROR

    # Refuse an existing output before doing the expensive walk/hash, not after.
    if args.output and not args.dry_run and not args.stdout:
        _guard_no_clobber(Path(args.output), args.force)

    if args.from_dvc_cache:
        from .dvc import manifest_from_dvc

        result = manifest_from_dvc(
            args.from_dvc_cache,
            out_path=args.dvc_out,
            cache_dirs=args.cache_dir,
            provenance_type=args.provenance,
            source_url=args.source_url,
            record_accession=args.accession,
            created=args.created,
            retrieved=args.retrieved,
            generator=args.generator,
            # Only forward an explicitly-chosen algorithm. The parser's default is
            # md5, and forwarding that would reject every sha256 .dvc for
            # disagreeing with a value the user never typed.
            hash_algorithm=args.hash_algorithm,
            allow_legacy_md5=args.allow_legacy_md5,
            prefer_workspace_sizes=args.prefer_workspace_sizes,
        )
        result.notes.insert(
            0,
            f"transcribed {result.out.path!r} from {args.from_dvc_cache} without rehashing; "
            f"sizes recovered from: {result.size_source}",
        )
        return _finalize_build(result, args, out, err)

    from .writer import build_from_directory

    progress = _Progress("hashing", not args.stdout)
    built = build_from_directory(
        args.directory,
        provenance_type=args.provenance,
        hash_algorithm=args.hash_algorithm or DEFAULT_HASH_ALGORITHM,
        source_url=args.source_url,
        record_accession=args.accession,
        created=args.created,
        retrieved=args.retrieved,
        generator=args.generator,
        relpath_from_source=parse_rules(args.relpath_from_source),
        source_from_relpath=parse_rules(args.source_from_relpath),
        excludes=_excludes_for(args),
        include_hidden=not args.no_hidden,
        follow_symlinks=args.follow_symlinks,
        skip_unreadable=args.skip_unreadable,
        jobs=args.jobs,
        progress=progress.files,
    )
    progress.done()
    return _finalize_build(built, args, out, err)


def cmd_capture(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from .writer import capture_from_directory

    if args.output and not args.dry_run and not args.stdout:
        _guard_no_clobber(Path(args.output), args.force)

    progress = _Progress("hashing", not args.stdout)
    result = capture_from_directory(
        args.directory,
        source_url=args.source_url,
        retrieved=args.retrieved,
        hash_algorithm=args.hash_algorithm or DEFAULT_HASH_ALGORITHM,
        record_accession=args.accession,
        created=args.created,
        generator=args.generator,
        relpath_from_source=parse_rules(args.relpath_from_source),
        source_from_relpath=parse_rules(args.source_from_relpath),
        excludes=_excludes_for(args),
        include_hidden=not args.no_hidden,
        follow_symlinks=args.follow_symlinks,
        skip_unreadable=args.skip_unreadable,
        jobs=args.jobs,
        progress=progress.files,
    )
    progress.done()
    return _finalize_build(result, args, out, err)


def cmd_verify(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from .verify import verify_tree

    path = Path(args.manifest)
    manifest = Manifest.load(path)
    root = Path(args.root) if args.root else (path.parent if path.parent != Path() else Path())

    progress = _Progress("verifying", not args.json and not args.quiet)
    report = verify_tree(
        manifest,
        root,
        layer=args.layer,
        use_source_layout=args.source_layout,
        sample=args.sample,
        sample_fraction=args.sample_fraction,
        check_extra=args.check_extra or args.strict,
        jobs=args.jobs,
        progress=progress.files,
    )
    progress.done()

    failed = not report.ok or (args.strict and bool(report.extra_files))
    if args.json:
        payload = report.to_dict()
        payload["strict"] = args.strict
        payload["ok"] = not failed
        emit_json(payload, out)
    else:
        if not args.quiet:
            for line in report.explain():
                out.write(line + "\n")
        for result in report.failures:
            detail = result.detail or ""
            extra = ""
            if result.status == "hash-mismatch":
                extra = f" expected {result.expected_md5} got {result.actual_md5}"
            elif result.status == "size-mismatch":
                extra = f" expected {result.expected_size} got {result.actual_size}"
            out.write(f"FAIL {result.status:<14} {result.relpath}{extra} {detail}".rstrip() + "\n")
        for extra_path in report.extra_files:
            label = "FAIL extra" if args.strict else "extra"
            out.write(f"{label}          {extra_path}\n")
        out.write(("FAILED" if failed else "OK") + "\n")
    return EXIT_FINDING if failed else EXIT_OK


def _as_messages(value: object) -> list[str]:
    """Narrow a report field back to the list of strings it always is."""
    return [str(item) for item in value] if isinstance(value, list) else []


def cmd_validate(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from .schema import validate_document

    # A missing optional dependency means the tool cannot perform a check that was
    # asked for — exit 2 — and must NOT be reported as the manifest being invalid.
    # Getting this wrong would make a CI gate block a merge over a perfectly good
    # manifest whose only problem was the runner's install (docs/decisions.md D-16).
    if args.jsonschema:
        try:
            validate_document({})
        except RuntimeError as exc:
            err.write(f"{PROGRAM}: {exc}\n")
            return EXIT_ERROR

    reports: list[dict[str, object]] = []
    worst = EXIT_OK
    for name in args.manifest:
        entry: dict[str, object] = {"path": name}
        try:
            raw = read_manifest_bytes(name)
        except OSError as exc:
            # Cannot read the file: a tool failure, not a verdict on the manifest, so
            # it is reported as an error rather than as INVALID (docs/decisions.md
            # D-16).
            entry.update(ok=False, unreadable=True, errors=[f"unreadable: {exc}"])
            reports.append(entry)
            worst = max(worst, EXIT_ERROR)
            continue

        # A BOM, a non-UTF-8 body and malformed JSON are all *findings* about the
        # document, not tool failures — `chiro verify` already treated them that
        # way, and the two commands disagreeing on the exit code for the same file
        # would make a CI gate's behaviour depend on which one it happened to call.
        #
        # `decode_document` is the shared gate, so `validate` and `load` cannot
        # drift apart on what counts as a manifest. It also matters that the decode
        # happens *before* `json.loads`: handing raw bytes to json.loads lets it
        # auto-detect UTF-16, and a UnicodeDecodeError is a ValueError rather than a
        # JSONDecodeError, so it would escape this handler and abort the batch.
        # `ValueError` rather than `json.JSONDecodeError`: json.loads raises plain
        # ValueError for an integer past the 4300-digit conversion limit, and
        # RecursionError for a deeply-nested array. Both are properties of the
        # document, so both belong here — catching only JSONDecodeError left them
        # aborting the batch, which is the exact defect this handler exists to fix.
        try:
            document = json.loads(decode_document(raw))
        except ChirographError as exc:
            entry.update(ok=False, errors=[f"{type(exc).__name__}: {exc}"])
            reports.append(entry)
            worst = max(worst, EXIT_FINDING)
            continue
        except (ValueError, RecursionError) as exc:
            entry.update(ok=False, errors=[f"SchemaError: not valid JSON: {exc}"])
            reports.append(entry)
            worst = max(worst, EXIT_FINDING)
            continue

        errors: list[str] = []
        warnings: list[str] = []
        if args.jsonschema:
            errors.extend(f"schema: {message}" for message in validate_document(document))

        try:
            manifest = Manifest.from_dict(document)
        except ChirographError as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        else:
            warnings.extend(str(finding) for finding in manifest.lint())
            entry["nfiles"] = manifest.nfiles
            entry["size"] = manifest.size
            entry["body_hash"] = manifest.body_hash
            # From the bytes already read: a second open would be a TOCTOU window
            # and could raise outside this loop's handler, aborting the batch.
            entry["reference"] = str(ChiroRef.for_bytes(raw, manifest.hash_algorithm))

        failed = bool(errors) or (args.strict_lint and bool(warnings))
        entry.update(ok=not failed, errors=errors, warnings=warnings)
        reports.append(entry)
        if failed:
            worst = max(worst, EXIT_FINDING)

    if args.json:
        emit_json({"ok": worst == EXIT_OK, "manifests": reports}, out)
    else:
        for entry in reports:
            if entry["ok"]:
                status = "OK"
            elif entry.get("unreadable"):
                status = "ERROR"  # the tool could not read it; not a verdict
            else:
                status = "INVALID"
            out.write(f"{status} {entry['path']}\n")
            for message in _as_messages(entry.get("errors")):
                out.write(f"  error: {message}\n")
            for message in _as_messages(entry.get("warnings")):
                out.write(f"  warning: {message}\n")
    return worst


def cmd_info(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    path = Path(args.manifest)
    # One read. Reading twice was harmless only while special files were refused:
    # once pipes were allowed again (D-20), the first read drained the pipe and the
    # second returned nothing, so `reference` became md5 of the empty string — a
    # wrong chiro:// address in an otherwise-correct report, at exit 0.
    raw = read_manifest_bytes(path)
    manifest = loads_named(raw, path)
    reference = ChiroRef.for_bytes(raw, manifest.hash_algorithm)
    payload: dict[str, object] = {
        "path": str(path),
        "schema_version": manifest.schema_version,
        "hash_algorithm": manifest.hash_algorithm,
        "provenance_type": manifest.provenance_type,
        "record_accession": manifest.record_accession,
        "source_url": manifest.source_url,
        "created": manifest.created,
        "retrieved": manifest.retrieved,
        "generator": manifest.generator,
        "nfiles": manifest.nfiles,
        "size": manifest.size,
        "body_hash": manifest.body_hash,
        "reference": str(reference),
        "canonical_reference": str(canonical_ref(manifest)),
        "registry_shard_path": reference.shard_path,
        "dvc_dir_hash": manifest.dvc_dir_hash(),
        "relocated_entries": sum(1 for entry in manifest.files if entry.relocated),
        "lint": [finding.to_dict() for finding in manifest.lint()],
    }
    if args.json:
        emit_json(payload, out)
        return EXIT_OK

    width = max(len(key) for key in payload)
    for key, value in payload.items():
        if key == "lint":
            continue
        if key == "size" and isinstance(value, int):
            value = human_bytes(value)
        if value is None:
            continue
        out.write(f"{key:<{width}}  {value}\n")
    for finding in manifest.lint():
        out.write(f"warning: {finding}\n")
    return EXIT_OK


def cmd_ls(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    manifest = Manifest.load(args.manifest)
    if args.json:
        emit_json(
            [
                {
                    **entry.to_dict(),
                    "url": manifest.resolve_url(entry),
                }
                for entry in manifest.files
            ],
            out,
        )
        return EXIT_OK

    for entry in manifest.files:
        name = entry.source if args.source else entry.relpath
        if args.urls:
            name = manifest.resolve_url(entry) or name
        if args.long:
            out.write(f"{entry.md5}  {entry.size:>14}  {name}\n")
        else:
            out.write(name + "\n")
    return EXIT_OK


def cmd_diff(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    old = Manifest.load(args.old)
    new = Manifest.load(args.new)
    result = diff_manifests(old, new)

    if args.json:
        emit_json(result.to_dict(), out)
    else:
        for key, (before, after) in result.header_changes.items():
            out.write(f"header {key}: {before!r} -> {after!r}\n")
        for old_path, new_path in result.renamed:
            out.write(f"renamed {old_path} -> {new_path}\n")
        for change in result.changed:
            if change.kind == "content":
                assert change.old and change.new
                out.write(
                    f"changed {change.relpath}: {change.old.md5} -> {change.new.md5} "
                    f"({change.old.size} -> {change.new.size} bytes)\n"
                )
            else:
                out.write(f"{change.kind} {change.relpath}\n")
        for entry in result.removed:
            out.write(f"removed {entry.relpath} ({entry.size} bytes)\n")
        for entry in result.added:
            out.write(f"added   {entry.relpath} ({entry.size} bytes)\n")
        out.write(result.summary() + "\n")
        if result.content_changed and old.provenance_type == PROVENANCE_OBSERVED:
            out.write(
                "note: both sides are observed snapshots, so content changes are most "
                "likely upstream drift rather than an error. Nothing should be deleted: "
                "the older manifest remains a valid record of the bytes that were "
                "analysed (registry design §7b).\n"
            )
    if args.exit_code and not result.identical:
        return EXIT_FINDING
    return EXIT_OK


def loads_named(raw: bytes, path: Path) -> Manifest:
    """Parse *raw*, prefixing errors with *path*.

    `Manifest.load` does this, but it also re-reads the file. Parsing bytes we
    already hold keeps the single read (no TOCTOU) while keeping the filename in the
    message, which matters when looping over many manifests.
    """
    try:
        return Manifest.loads(raw)
    except ChirographError as exc:
        raise type(exc)(f"{path}: {exc}") from exc


def load_manifest_named(path: Path) -> Manifest:
    """Load from *path* with a single read, errors prefixed with the filename."""
    return loads_named(read_manifest_bytes(path), path)


def cmd_ref(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    path = Path(args.manifest)

    # `--no-validate` only means anything on the default path: `--canonical` must
    # parse to canonicalise, and `--check` deliberately never validates (it asks a
    # byte-identity question). Accepting the flag and ignoring it is the defect this
    # function had twice already.
    if args.no_validate and (args.canonical or args.check):
        other = "--canonical" if args.canonical else "--check"
        err.write(f"--no-validate does not apply with {other}\n")
        return EXIT_ERROR

    # Compute the reference ONCE, before branching, so every path — plain,
    # --canonical and --check — agrees on what is being referenced. An earlier
    # version computed it inside the `--check` branch, which silently ignored
    # `--canonical` and produced both false OK and false MISMATCH for exactly the
    # combination where a reformat-invariant address matters.
    if args.canonical:
        reference = canonical_ref(load_manifest_named(path), args.algorithm)
    elif args.check or args.no_validate:
        # No parse needed, so stream the hash instead of holding the whole file:
        # `chiro ref` is run over large manifests and `--no-validate` exists to be
        # cheap.
        reference = ChiroRef.for_path(path, args.algorithm)
    else:
        # Read the bytes once, then validate *those bytes* and hash *those bytes*.
        # Validating via one open and hashing via another is a real TOCTOU: swap the
        # file between them and the minted chiro:// address names content that was
        # never checked. (This is purely about correctness — an earlier comment here
        # claimed it "halves the work", which measurement disproved: the run is
        # dominated by the parse, and dropping the second read saves ~0.02 s of ~10 s
        # on a 17 MB manifest.)
        #
        # Confirm the bytes *are* a manifest before minting a reference to them: the
        # reference asserts "this is manifest X", and producing one for a UTF-16 file
        # or a truncated blob would let a registry store something that is not a
        # manifest under a chiro:// address. `ChiroRef.for_bytes` stays a pure hash,
        # the right library primitive.
        raw = read_manifest_bytes(path)
        loads_named(raw, path)
        reference = ChiroRef.for_bytes(raw, args.algorithm)

    if args.check:
        expected = ChiroRef.parse(args.check)
        if expected.algorithm != reference.algorithm:
            err.write(
                f"reference algorithm {expected.algorithm} differs from the one requested "
                f"({reference.algorithm}); re-run with --algorithm {expected.algorithm}\n"
            )
            return EXIT_ERROR
        matched = expected.digest == reference.digest
        if args.json:
            emit_json({"ok": matched, "expected": str(expected), "actual": str(reference)}, out)
        else:
            out.write(
                (f"OK {reference}" if matched else f"MISMATCH expected {expected} got {reference}")
                + "\n"
            )
        return EXIT_OK if matched else EXIT_FINDING

    if args.json:
        emit_json({"reference": str(reference), "shard_path": reference.shard_path}, out)
    else:
        out.write(str(reference) + "\n")
        if args.shard:
            out.write(reference.shard_path + "\n")
    return EXIT_OK


def cmd_remap(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from .manifest import FileEntry
    from .rewrite import try_apply_rules

    manifest = Manifest.load(args.manifest)
    to_relpath = parse_rules(args.relpath_from_source)
    to_source = parse_rules(args.source_from_relpath)
    if to_relpath and to_source:
        err.write("give either --relpath-from-source or --source-from-relpath, not both\n")
        return EXIT_ERROR

    entries: list[FileEntry] = []
    untouched = 0
    for entry in manifest.files:
        relpath, source_path = entry.relpath, entry.source_path
        # `try_apply_rules` returns None when no rule matched, and that distinction
        # is load-bearing here. Treating "no match" as "returned the input" would
        # overwrite relpath with source_path (or delete source_path) for every
        # entry the rules do not cover — silently replacing the workspace layout
        # with the deposit layout, or losing the fetch location entirely.
        rewritten = False
        if to_relpath:
            candidate = try_apply_rules(to_relpath, entry.source, field="relpath")
            if candidate is not None:
                relpath, source_path, rewritten = candidate, entry.source, True
        elif to_source:
            candidate = try_apply_rules(to_source, entry.relpath, field="source_path")
            if candidate is not None:
                source_path, rewritten = candidate, True
        if not rewritten and (to_relpath or to_source):
            untouched += 1
        # Drop a source_path that says nothing, keeping the terse form terse
        # (spec §3.2). Only when asked, or when a rule just rewrote this entry:
        # silently tidying an entry nothing touched would change body_hash (D-06).
        if source_path == relpath and (args.tidy or rewritten):
            source_path = None
        entries.append(
            FileEntry(
                relpath=relpath,
                md5=entry.md5,
                size=entry.size,
                source_path=source_path,
                isexec=entry.isexec,
                version_id=entry.version_id,
                etag=entry.etag,
                extra=entry.extra,
            )
        )

    updated = manifest.with_files(entries)
    if args.set_source_url:
        updated.source_url = args.set_source_url
    if args.set_accession:
        updated.record_accession = args.set_accession
    if args.set_provenance:
        updated.provenance_type = args.set_provenance

    target = _write_output(updated, args, out)
    if not args.stdout:
        if target:
            err.write(f"wrote {target}\n")
        err.write(
            f"body_hash {manifest.body_hash} -> {updated.body_hash}\n"
            f"the chiro:// reference changes with it; the per-file hashes do not.\n"
        )
    # Warnings and the untouched count go to stderr regardless of --stdout: they
    # cannot contaminate a piped manifest, and suppressing them is how a silent
    # no-op gets mistaken for a successful rewrite.
    if untouched:
        err.write(
            f"note: {untouched} of {len(entries)} entry/entries matched no rule and were "
            f"left unchanged\n"
        )
    for finding in updated.lint():
        err.write(f"warning: {finding}\n")
    return EXIT_OK


def cmd_dvc_dir(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from .canonical import dvc_dir_bytes

    manifest = Manifest.load(args.manifest)
    entries = [entry.to_dict() for entry in manifest.files]
    digest = manifest.dvc_dir_hash()
    if args.hash_only:
        out.write(digest + "\n")
        return EXIT_OK
    payload = dvc_dir_bytes(entries, manifest.hash_algorithm)
    if args.output:
        Path(args.output).write_bytes(payload)
        err.write(f"wrote {args.output}\n")
    else:
        out.write(payload.decode("utf-8") + "\n")
    err.write(
        f"DVC .dir hash: {digest}\n"
        f"(this is what a .dvc would record as '{digest}.dir'; it is NOT the manifest's "
        f"body_hash {manifest.body_hash} — see docs/decisions.md D-01)\n"
    )
    return EXIT_OK


def cmd_schema(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from .schema import schema_path, schema_text

    out.write(str(schema_path()) + "\n" if args.path else schema_text())
    return EXIT_OK


def cmd_test_vectors(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from .vectors import check_vectors, vectors_path

    if args.path:
        out.write(str(vectors_path()) + "\n")
        return EXIT_OK
    if not args.check:
        out.write(vectors_path().read_text(encoding="utf-8"))
        return EXIT_OK

    result = check_vectors()
    for failure in result.failures:
        out.write(f"FAIL {failure}\n")
    out.write(f"{result.passed}/{result.total} conformance checks passed\n")
    return EXIT_OK if result.ok else EXIT_FINDING


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return EXIT_ERROR

    # Wrapped once here, so no individual command has to think about it.
    out: TextIO = _SurrogateSafeStream(sys.stdout)  # type: ignore[assignment]
    err: TextIO = _SurrogateSafeStream(sys.stderr)  # type: ignore[assignment]

    try:
        return int(args.func(args, out, err))
    except ChirographError as exc:
        err.write(f"{PROGRAM}: {type(exc).__name__}: {exc}\n")
        # A manifest that does not match its bytes or its own hashes is a
        # *finding*, not a tool failure — CI wants to tell those apart.
        from .errors import IntegrityError, ManifestError

        if isinstance(exc, (IntegrityError, ManifestError)):
            return EXIT_FINDING
        return EXIT_ERROR
    except FileNotFoundError as exc:
        err.write(f"{PROGRAM}: {exc.strerror}: {exc.filename}\n")
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        err.write(f"{PROGRAM}: {exc}\n")
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover
        err.write("\ninterrupted\n")
        return 130
    except BrokenPipeError:  # pragma: no cover - `chiro ls | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
