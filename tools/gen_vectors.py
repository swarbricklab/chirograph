#!/usr/bin/env python3
"""Generate the frozen canonicalisation test vectors.

**Run this once.** The output is a *freeze*: once published, the expected values
in ``src/chirograph/vectors/canonicalisation.json`` must never change, because
other implementations are checking themselves against them. Regenerating after
an implementation change would silently move the goalposts — which is the exact
failure the vectors exist to prevent. ``tests/test_vectors.py`` reads the
checked-in file and asserts the implementation still reproduces it; it does not
regenerate.

The vectors file is written with ``ensure_ascii=True`` so that it is pure ASCII
and there is no ambiguity whatsoever about which codepoints a case means — the
NFC/NFD cases would be unreadable and un-reviewable otherwise.

Independent cross-checks (so the vectors are not merely self-consistent):

* ``dvc_dir_hash`` values are checked against hashes produced by a real
  ``dvc add`` in ``tests/test_dvc_interop.py``.
* ``canonical`` is carried as a literal string, so any hash in the file can be
  re-derived with ``printf '%s' "<canonical>" | md5sum`` by hand, with no
  chirograph code involved. ``tools/check_vectors_independently.sh`` does that.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from chirograph.canonical import (
    canonical_document_text,
    canonical_text,
    compute_body_hash,
    dvc_dir_bytes,
    dvc_dir_hash,
)
from chirograph.hashing import hash_bytes

MD5_A = "b1946ac92492d2347c6235b4d2611184"  # md5 of b"hello\n"
MD5_B = "591785b794601e212b260e25925636fd"  # md5 of b"world\n"
MD5_C = "5eb63bbbe01eeed093cb22bb8f5acdc3"  # md5 of b"hello world"
SHA_A = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"

# --------------------------------------------------------------------------- #
# Canonicalisation cases
# --------------------------------------------------------------------------- #
# Each case is (name, why-it-exists, hash_algorithm, input-array).
# The expected values are computed below; the *inputs* and the *reasons* are the
# hand-authored part.

CASES: list[tuple[str, str, str, list[dict[str, object]]]] = [
    (
        "empty",
        "A manifest may legitimately describe zero files. The canonical form of an "
        "empty array is '[]' — two bytes, not the empty string.",
        "md5",
        [],
    ),
    (
        "single-minimal",
        "The smallest real case: one entry with only the three required fields. "
        "Keys come out alphabetically (md5, relpath, size), not in declaration order.",
        "md5",
        [{"relpath": "a.txt", "md5": MD5_A, "size": 6}],
    ),
    (
        "unsorted-input",
        "Canonicalisation sorts; the on-disk order of the input must not affect the "
        "hash. Same expected output as 'sorted-input'.",
        "md5",
        [
            {"relpath": "sub/b.txt", "md5": MD5_B, "size": 6},
            {"relpath": "a.txt", "md5": MD5_A, "size": 6},
        ],
    ),
    (
        "sorted-input",
        "The same entries already in order. Must hash identically to 'unsorted-input'.",
        "md5",
        [
            {"relpath": "a.txt", "md5": MD5_A, "size": 6},
            {"relpath": "sub/b.txt", "md5": MD5_B, "size": 6},
        ],
    ),
    (
        "key-order-scrambled",
        "Keys given in a deliberately odd order. sort_keys=True must make this "
        "identical to 'single-minimal'.",
        "md5",
        [{"size": 6, "md5": MD5_A, "relpath": "a.txt"}],
    ),
    (
        "sort-hyphen-before-slash",
        "THE sort trap. The sort key is the joined POSIX path as a plain string, not "
        "a tuple of components. '-' is U+002D and '/' is U+002F, so 'a-b/x' sorts "
        "BEFORE 'a/b'. An implementation that splits into components first, or that "
        "sorts by (dirname, basename), gets the opposite order and a different hash.",
        "md5",
        [
            {"relpath": "a/b", "md5": MD5_A, "size": 1},
            {"relpath": "a-b/x", "md5": MD5_B, "size": 1},
            {"relpath": "a.b", "md5": MD5_C, "size": 1},
        ],
    ),
    (
        "sort-case-sensitive",
        "Sorting is by codepoint, not case-folded or locale-aware. All ASCII "
        "uppercase precedes all ASCII lowercase, so 'B.txt' sorts before 'a.txt'. A "
        "locale-aware sort (or Python's `sorted(key=str.lower)`) reverses this.",
        "md5",
        [
            {"relpath": "a.txt", "md5": MD5_A, "size": 1},
            {"relpath": "B.txt", "md5": MD5_B, "size": 1},
            {"relpath": "_under.txt", "md5": MD5_C, "size": 1},
        ],
    ),
    (
        "nfd-input-normalised",
        "Input carries a decomposed e-acute (e + U+0301, as macOS produces). The "
        "canonical form carries the composed U+00E9. An implementation that skips "
        "NFC normalisation produces a manifest that a Linux-built one cannot match.",
        "md5",
        [{"relpath": "cafe\u0301.txt", "md5": MD5_A, "size": 1}],
    ),
    (
        "nfc-then-sort-ordering-trap",
        "THE order-of-operations trap. Normalise BEFORE sorting. In NFD, "
        "'cafe\\u0301' sorts before 'caff' (compare 'e' U+0065 with 'f' U+0066). "
        "After NFC it is 'caf\\u00e9', which sorts AFTER 'caff' (U+00E9 > U+0066). "
        "Sort-then-normalise yields the reverse order and a different hash.",
        "md5",
        [
            {"relpath": "cafe\u0301", "md5": MD5_A, "size": 1},
            {"relpath": "caff", "md5": MD5_B, "size": 1},
        ],
    ),
    (
        "non-ascii-literal-in-canonical",
        "ensure_ascii=False: non-ASCII codepoints appear as literal UTF-8 in the "
        "canonical bytes, NOT as \\uXXXX escapes. Contrast the dvc_dir field of this "
        "same case, which does escape them — that difference is precisely why "
        "body_hash is not a DVC .dir hash.",
        "md5",
        [
            {"relpath": "caf\u00e9.txt", "md5": MD5_A, "size": 1},
            {"relpath": "na\u00efve\u2014dash.txt", "md5": MD5_B, "size": 1},
        ],
    ),
    (
        "astral-plane-codepoint",
        "A codepoint above U+FFFF (U+1F9EC DNA DOUBLE HELIX), which is one "
        "4-byte UTF-8 sequence but a surrogate PAIR in UTF-16. Implementations that "
        "sort or length-check in UTF-16 code units diverge here.",
        "md5",
        [
            {"relpath": "\U0001f9ec-sample/reads.fq.gz", "md5": MD5_A, "size": 1},
            {"relpath": "zz-sample/reads.fq.gz", "md5": MD5_B, "size": 1},
        ],
    ),
    (
        "source-path-relocation",
        "Spec §8.1: a flat GEO deposit reorganised into a per-sample tree. "
        "source_path is included in the canonical form and sorts after 'size' "
        "alphabetically.",
        "md5",
        [
            {
                "relpath": "sampleA/barcodes.tsv.gz",
                "md5": MD5_A,
                "size": 20481,
                "source_path": "GSE000000_sampleA_barcodes.tsv.gz",
            },
            {
                "relpath": "sampleA/matrix.mtx.gz",
                "md5": MD5_B,
                "size": 88420471,
                "source_path": "GSE000000_sampleA_matrix.mtx.gz",
            },
        ],
    ),
    (
        "redundant-source-path",
        "A source_path identical to its relpath. Canonicalisation does NOT drop it, "
        "so this hashes DIFFERENTLY from 'terse-no-source-path' below even though "
        "the two describe the same fetch. Writers omit redundant source_path; "
        "canonicalisers must not remove it, or two implementations disagree about "
        "which normalisations to apply. See docs/decisions.md D-06.",
        "md5",
        [{"relpath": "a.txt", "md5": MD5_A, "size": 6, "source_path": "a.txt"}],
    ),
    (
        "terse-no-source-path",
        "The same fetch expressed tersely. Deliberately paired with "
        "'redundant-source-path' to pin that the two body_hash values differ.",
        "md5",
        [{"relpath": "a.txt", "md5": MD5_A, "size": 6}],
    ),
    (
        "extension-fields",
        "All DVC-compat and supplementary fields present at once, to pin their key "
        "ordering (etag, isexec, md5, relpath, size, source_path, version_id) and "
        "that booleans serialise as bare true/false.",
        "md5",
        [
            {
                "relpath": "bin/tool",
                "md5": MD5_A,
                "size": 1024,
                "source_path": "dist/tool",
                "isexec": True,
                "version_id": "3HL4kqtJlcpXroDTDmjVBH40Nrjfkd",
                "etag": "9b2cf535f27731c974343645a3985328-2",
            },
            {"relpath": "bin/other", "md5": MD5_B, "size": 0, "isexec": False},
        ],
    ),
    (
        "file-version-in-body",
        "(1.1) A file_version entry field. It is an ordinary scalar entry field, so it "
        "enters canonicalisation and IS covered by body_hash, sorting between 'etag' and "
        "'isexec'. Paired with 'terse-no-source-path' (the same entry without it) to pin "
        "that adding the 1.1 field changes body_hash — i.e. it really enters the body.",
        "md5",
        [{"relpath": "a.txt", "md5": MD5_A, "size": 6, "file_version": "2026-07-30T09:02:00Z"}],
    ),
    (
        "large-size-beyond-double",
        "2**53 + 1 = 9007199254740993 bytes (~9 PB). Exactly representable as a JSON "
        "integer but NOT as an IEEE-754 double: a parser that routes integers "
        "through a float writes 9007199254740992 and gets a different hash. Real "
        "genomics archives are not yet this big, but a JSON parser choosing floats "
        "is a live hazard in several languages.",
        "md5",
        [{"relpath": "huge.bin", "md5": MD5_A, "size": 9007199254740993}],
    ),
    (
        "zero-size",
        "A zero-byte file. size serialises as bare 0, and the md5 is the md5 of the empty string.",
        "md5",
        [{"relpath": "empty.txt", "md5": "d41d8cd98f00b204e9800998ecf8427e", "size": 0}],
    ),
    (
        "duplicate-md5-two-paths",
        "Identical content at two paths — legal, and mirrors DVC's content-addressed "
        "dedup (one cache object, two paths). Not a duplicate entry: relpath is what "
        "must be unique.",
        "md5",
        [
            {"relpath": "copies/one.txt", "md5": MD5_A, "size": 6},
            {"relpath": "copies/two.txt", "md5": MD5_A, "size": 6},
        ],
    ),
    (
        "path-with-url-unsafe-characters",
        "Spaces, '#', '%', '?' and '+' in paths. These are legal filenames and must "
        "pass through canonicalisation untouched — percent-encoding belongs to URL "
        "resolution, not to the manifest.",
        "md5",
        [
            {"relpath": "run 1/a#b.txt", "md5": MD5_A, "size": 1},
            {"relpath": "run 1/c%20d.txt", "md5": MD5_B, "size": 1},
            {"relpath": "run 1/e?f+g.txt", "md5": MD5_C, "size": 1},
        ],
    ),
    (
        "deep-nesting",
        "A long path, to pin that nothing collapses or normalises interior components.",
        "md5",
        [
            {
                "relpath": "a/b/c/d/e/f/g/h/i/j/k/leaf.txt",
                "md5": MD5_A,
                "size": 1,
            }
        ],
    ),
    (
        "sha256-algorithm",
        "hash_algorithm sha256. The wire key stays 'md5' (spec §3.2 defines it as "
        "'hash of the file's bytes, per hash_algorithm'); only the digest length and "
        "the algorithm used for body_hash change.",
        "sha256",
        [{"relpath": "a.txt", "md5": SHA_A, "size": 5}],
    ),
]


def _many_entries() -> list[dict[str, object]]:
    """200 entries in an adversarial order, to catch sort instability.

    Generated rather than written out, but the *expected* canonical string is
    frozen in the output file just like every other case, so the generator being
    programmatic does not weaken the freeze.
    """
    entries = []
    for index in range(200):
        # Interleave two naming schemes whose orders differ, and emit in an order
        # that is neither.
        stem = f"s{index % 20:02d}" if index % 3 else f"S{index % 20:02d}"
        entries.append(
            {
                "relpath": f"{stem}/part-{index:03d}.dat",
                "md5": hash_bytes(str(index).encode(), "md5"),
                "size": index * 1024,
            }
        )
    entries.reverse()
    return entries


CASES.append(
    (
        "many-entries-adversarial-order",
        "200 entries interleaving upper- and lower-case stems, supplied in reverse. "
        "Catches sort instability and any accidental dependence on input order.",
        "md5",
        _many_entries(),
    )
)


# --------------------------------------------------------------------------- #
# Path-safety cases
# --------------------------------------------------------------------------- #

PATH_ACCEPT = [
    ["a.txt", "the ordinary case"],
    ["sub/dir/a.txt", "nested"],
    [".hidden", "a leading dot is a normal filename, not a traversal"],
    ["..hidden", "TWO leading dots followed by more characters is a filename, not '..'"],
    ["a..b/c", "dots in the middle of a component"],
    ["a-b/x", "hyphen sorts before slash but is perfectly legal"],
    ["caf\u00e9.txt", "non-ASCII (precomposed U+00E9)"],
    ["cafe\u0301.txt", "non-ASCII (decomposed; accepted, then normalised to NFC)"],
    ["\U0001f9ec.txt", "astral-plane codepoint"],
    ["run 1/a#b%c?d.txt", "URL-unsafe but filesystem-legal characters"],
    [
        "sample:1/a.txt",
        "a colon is legal on POSIX away from position 1; only a leading DRIVE "
        "prefix like 'C:' is rejected",
    ],
    ["x.tar.gz", "compound extension"],
]

PATH_REJECT = [
    ["", "empty"],
    ["/abs/path", "absolute POSIX path - would escape the import target"],
    ["a//b", "empty interior component; ambiguous on materialisation"],
    ["a/", "trailing separator; this names a directory, not a file"],
    ["..", "the parent directory"],
    [".", "the current directory"],
    ["a/../b", "interior traversal - the classic Zip-Slip shape"],
    ["../a", "leading traversal"],
    ["a/..", "trailing traversal"],
    ["./a", "a '.' component"],
    ["a/./b", "an interior '.' component"],
    ["C:/data", "Windows drive-absolute"],
    ["c:x", "Windows drive-relative"],
    ["a\\b", "backslash: a separator on one platform and a filename character on another"],
    ["a\u0000b", "NUL byte"],
    ["a\nb", "newline; a control character, and it breaks every line-oriented tool"],
    ["a\tb", "tab; control character"],
    ["a\u007fb", "DEL; control character"],
    ["a" * 4097, "longer than the 4096-byte path limit"],
    ["x" * 256, "component longer than the 255-byte limit"],
]


# --------------------------------------------------------------------------- #
# Whole-manifest cases
# --------------------------------------------------------------------------- #

GOOD_MANIFEST = {
    "schema": "chirograph",
    "schema_version": "1.0",
    "hash_algorithm": "md5",
    "provenance_type": "observed-download",
    "record_accession": "GSE111111",
    "source_url": "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE111nnn/GSE111111/suppl/",
    "created": "2026-07-01T09:15:00Z",
    "retrieved": "2026-07-01T09:15:00Z",
    "generator": "chirograph 0.1.0",
    "size": 6,
    "nfiles": 1,
    "body_hash": "",  # filled in below
    "files": [{"relpath": "GSE111111_processed.h5ad", "md5": MD5_A, "size": 6}],
}
GOOD_MANIFEST["body_hash"] = compute_body_hash(GOOD_MANIFEST["files"], "md5")  # type: ignore[arg-type]

# A valid 1.1 document: the optional staleness fields present (spec §8). Separate
# from GOOD_MANIFEST so the frozen 1.0 vector that references it is never disturbed.
MINOR_1_MANIFEST = {
    "schema": "chirograph",
    "schema_version": "1.1",
    "hash_algorithm": "md5",
    "provenance_type": "observed-download",
    "record_accession": "GSE111111",
    "concept": "geo:GSE111111",
    "source_url": "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE111nnn/GSE111111/suppl/",
    "created": "2026-09-04T00:00:00Z",
    "retrieved": "2026-09-04T00:00:00Z",
    "record_version": "2026-07-30",
    "generator": "chirograph 0.1.0",
    "size": 6,
    "nfiles": 1,
    "body_hash": "",  # filled in below
    "files": [
        {
            "relpath": "GSE111111_processed.h5ad",
            "md5": MD5_A,
            "size": 6,
            "file_version": "2026-07-30T09:02:00Z",
        }
    ],
}
MINOR_1_MANIFEST["body_hash"] = compute_body_hash(MINOR_1_MANIFEST["files"], "md5")  # type: ignore[arg-type]

# --------------------------------------------------------------------------- #
# Container (2.0) canonicalisation cases — the two-key {containers, files} form.
# Each case is (name, why, hash_algorithm, containers, files). Unlike the files-
# only cases there is no dvc_dir (DVC .dir drops containers entirely).
# --------------------------------------------------------------------------- #

CONTAINER_CASES: list[tuple[str, str, str, list[dict[str, object]], list[dict[str, object]]]] = [
    (
        "container-body",
        "(2.0) A ZIP container plus one member carrying crc32. body_hash is over the "
        "two-key {containers, files} document (container-model §4): sort_keys fixes the "
        "top-level order as 'containers' then 'files', each array pre-sorted by its own "
        "key, container fields sorted alphabetically (format, md5, size, source_root).",
        "md5",
        [{"source_root": "data.zip", "format": "zip", "md5": MD5_C, "size": 15728640}],
        [
            {
                "relpath": "sampleA/matrix.mtx",
                "md5": MD5_A,
                "size": 6,
                "source_path": "data.zip/sampleA/matrix.mtx",
                "crc32": "df2441b0",
            }
        ],
    ),
    (
        "container-multi-sorted",
        "(2.0) Two containers given out of order, to pin that containers sort by "
        "source_root just as files sort by relpath.",
        "md5",
        [
            {"source_root": "z.zip", "format": "zip", "md5": MD5_B},
            {"source_root": "a.tar.gz", "format": "tar.gz", "md5": MD5_C},
        ],
        [
            {"relpath": "one", "md5": MD5_A, "size": 6, "source_path": "z.zip/one"},
            {"relpath": "two", "md5": MD5_B, "size": 6, "source_path": "a.tar.gz/two"},
        ],
    ),
]

# A valid 2.0 document: a container block + a member. Separate from the 1.x
# manifests so their frozen vectors are never disturbed.
MINOR_2_MANIFEST = {
    "schema": "chirograph",
    "schema_version": "2.0",
    "hash_algorithm": "md5",
    "provenance_type": "observed-download",
    "record_accession": "10.5281/zenodo.7654321",
    "source_url": "zenodo://10.5281/zenodo.7654321",
    "created": "2026-09-06T00:00:00Z",
    "retrieved": "2026-09-06T00:00:00Z",
    "generator": "chirograph 0.1.0",
    "size": 6,
    "nfiles": 1,
    "body_hash": "",  # filled in below
    "containers": [{"source_root": "data.zip", "format": "zip", "md5": MD5_C, "size": 15728640}],
    "files": [
        {
            "relpath": "sampleA/matrix.mtx",
            "md5": MD5_A,
            "size": 6,
            "source_path": "data.zip/sampleA/matrix.mtx",
            "crc32": "df2441b0",
        }
    ],
}
MINOR_2_MANIFEST["body_hash"] = compute_body_hash(
    MINOR_2_MANIFEST["files"],  # type: ignore[arg-type]
    "md5",
    containers=MINOR_2_MANIFEST["containers"],  # type: ignore[arg-type]
)


def _container_mutate(**changes: object) -> dict[str, object]:
    """A copy of MINOR_2_MANIFEST with *changes* applied (for reject cases).

    body_hash need not be recomputed: the container/member rules are enforced
    during construction (``Manifest.__post_init__``), before the body_hash check.
    """
    document = json.loads(json.dumps(MINOR_2_MANIFEST))
    for key, value in changes.items():
        document[key] = value
    return document


def _mutate(**changes: object) -> dict[str, object]:
    document = json.loads(json.dumps(GOOD_MANIFEST))
    for key, value in changes.items():
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
    return document


BAD_MANIFESTS: list[dict[str, object]] = [
    {
        "name": "wrong-schema-token",
        "why": "The internal schema field is the fallback identifier; a reader must key on it.",
        "error": "SchemaError",
        "document": _mutate(schema="tessera"),
    },
    {
        "name": "future-major-version",
        "why": "A MAJOR version beyond what the reader implements (major 2) means it "
        "cannot be trusted to interpret the document. (2.0 is now a supported major — "
        "the container block — so the too-new major is 3.)",
        "error": "UnsupportedVersion",
        "document": _mutate(schema_version="3.0"),
    },
    {
        "name": "etag-algorithm",
        "why": "hash_algorithm 'etag' is reserved but rejected in v1 (docs/decisions.md D-04).",
        "error": "UnsupportedAlgorithm",
        "document": _mutate(hash_algorithm="etag"),
    },
    {
        "name": "body-hash-mismatch",
        "why": "The load-bearing check: a files array that does not match its body_hash.",
        "error": "BodyHashMismatch",
        "document": _mutate(body_hash="0" * 32),
    },
    {
        "name": "nfiles-disagrees",
        "why": "Cheap sanity check from spec §9 step 4.",
        "error": "SchemaError",
        "document": _mutate(nfiles=2),
    },
    {
        "name": "size-disagrees",
        "why": "Cheap sanity check from spec §9 step 4.",
        "error": "SchemaError",
        "document": _mutate(size=99),
    },
    {
        "name": "traversal-relpath",
        "why": "Path-traversal safety on READ, because observed-download manifests are "
        "untrusted third-party descriptions.",
        "error": "PathSafetyError",
        "document": _mutate(
            files=[{"relpath": "../../etc/passwd", "md5": MD5_A, "size": 6}],
            body_hash=compute_body_hash(
                [{"relpath": "../../etc/passwd", "md5": MD5_A, "size": 6}], "md5"
            ),
        ),
    },
    {
        "name": "duplicate-relpath",
        "why": "relpath is an entry's identity; duplicates leave no canonical ordering.",
        "error": "SchemaError",
        "document": _mutate(
            nfiles=2,
            size=12,
            files=[
                {"relpath": "a.txt", "md5": MD5_A, "size": 6},
                {"relpath": "a.txt", "md5": MD5_B, "size": 6},
            ],
        ),
    },
    {
        "name": "naive-timestamp",
        "why": "A provenance timestamp without an offset is not an instant "
        "(docs/decisions.md D-05).",
        "error": "SchemaError",
        "document": _mutate(retrieved="2026-07-01T09:15:00"),
    },
    {
        "name": "unknown-entry-field",
        "why": "Unknown fields are covered by body_hash, so tolerating them in one "
        "implementation and dropping them in another makes one manifest hash two ways "
        "(docs/decisions.md D-07).",
        "error": "SchemaError",
        "document": _mutate(
            files=[{"relpath": "a.txt", "md5": MD5_A, "size": 6, "mtime": 1234567890}]
        ),
    },
    {
        "name": "null-instead-of-absent",
        "why": "The format omits absent fields rather than writing null; accepting both "
        "spellings would give one manifest two canonical forms.",
        "error": "SchemaError",
        "document": _mutate(record_accession=None) | {"record_accession": None},
    },
    {
        "name": "float-size",
        "why": "1.0 and 1 are the same number but different bytes.",
        "error": "SchemaError",
        "document": _mutate(files=[{"relpath": "a.txt", "md5": MD5_A, "size": 6.0}]),
    },
    {
        "name": "digest-wrong-length",
        "why": "A 32-character digest under sha256 means the manifest was assembled from "
        "md5s under the wrong header — a mass verification failure waiting to happen.",
        "error": "SchemaError",
        "document": _mutate(hash_algorithm="sha256"),
    },
    {
        "name": "crc32-without-container",
        "why": "(2.0) A member carrying crc32 but no container to anchor it can never be "
        "confirmed (container-model §8).",
        "error": "SchemaError",
        "document": _mutate(
            files=[{"relpath": "a.txt", "md5": MD5_A, "size": 6, "crc32": "deadbeef"}]
        ),
    },
    {
        "name": "nested-containers",
        "why": "(2.0) One source_root nested under another is ambiguous for member "
        "matching and is forbidden (container-model §8).",
        "error": "SchemaError",
        "document": _container_mutate(
            containers=[
                {"source_root": "data.zip", "format": "zip", "md5": MD5_C},
                {"source_root": "data.zip/inner", "format": "zip", "md5": MD5_B},
            ],
            files=[
                {
                    "relpath": "x",
                    "md5": MD5_A,
                    "size": 6,
                    "source_path": "data.zip/inner/x",
                }
            ],
        ),
    },
    {
        "name": "orphan-container",
        "why": "(2.0) A declared container with no member under its source_root (spec §8).",
        "error": "SchemaError",
        "document": _container_mutate(
            containers=[
                {"source_root": "data.zip", "format": "zip", "md5": MD5_C},
                {"source_root": "empty.zip", "format": "zip", "md5": MD5_B},
            ]
        ),
    },
]


def build() -> dict[str, object]:
    cases = []
    for name, why, algorithm, entries in CASES:
        canonical = canonical_text(entries)
        raw = canonical.encode("utf-8")
        dvc_bytes = dvc_dir_bytes(entries, algorithm)
        cases.append(
            {
                "name": name,
                "why": why,
                "hash_algorithm": algorithm,
                "input": entries,
                "canonical": canonical,
                "canonical_utf8_bytes": len(raw),
                "canonical_sha256": hash_bytes(raw, "sha256"),
                "body_hash": compute_body_hash(entries, algorithm),
                "dvc_dir": dvc_bytes.decode("utf-8"),
                "dvc_dir_hash": dvc_dir_hash(entries, algorithm),
            }
        )

    container_cases = []
    for name, why, algorithm, containers, entries in CONTAINER_CASES:
        canonical = canonical_document_text(containers, entries)
        raw = canonical.encode("utf-8")
        container_cases.append(
            {
                "name": name,
                "why": why,
                "hash_algorithm": algorithm,
                "containers": containers,
                "files": entries,
                "canonical": canonical,
                "canonical_utf8_bytes": len(raw),
                "canonical_sha256": hash_bytes(raw, "sha256"),
                "body_hash": compute_body_hash(entries, algorithm, containers=containers),
            }
        )

    return {
        "format": "chirograph-test-vectors",
        "format_version": "1.0",
        "spec_version": "2.0",
        "spec_sections": ["§3.2", "§4", "§8", "§9"],
        "$comment": (
            "FROZEN. These are conformance vectors for the .chiro.json canonicalisation "
            "and validation rules. Once published they must never change: other "
            "implementations check themselves against them. If a value here is wrong, "
            "publish a corrected vector set under a new spec_version rather than editing "
            "these in place. "
            "The file is deliberately pure ASCII (\\uXXXX escapes) so that the NFC/NFD "
            "cases are unambiguous about which codepoints they mean. "
            "'canonical' is the exact string that gets UTF-8 encoded and hashed, carried "
            "literally so that a mismatching implementation can diff the STRING rather "
            "than stare at two digests. Any body_hash here can be re-derived with no "
            "chirograph code at all: printf '%s' \"<canonical>\" | md5sum. "
            "'dvc_dir' / 'dvc_dir_hash' are the DERIVED DVC .dir interop form (spec §0), "
            "which uses DIFFERENT serialisation (default separators, ensure_ascii=True, "
            "no size field) and therefore a different hash — see docs/decisions.md D-01."
        ),
        "canonicalisation": cases,
        "container_canonicalisation": container_cases,
        "path_safety": {
            "$comment": (
                "Paths that must be accepted and rejected on READ as well as write "
                "(spec §3.2). Rejection is a security boundary: a consumer materialises "
                "relpath to disk, and observed-download manifests describe other "
                "people's deposits."
            ),
            "accept": [{"path": path, "why": why} for path, why in PATH_ACCEPT],
            "reject": [{"path": path, "why": why} for path, why in PATH_REJECT],
        },
        "manifests": {
            "$comment": (
                "Whole-document cases. 'valid' must load; each entry in 'invalid' must "
                "be refused with the named error class."
            ),
            "valid": [
                {
                    "name": "observed-download-terse",
                    "why": "Spec §8.2: layout preserved, so no source_path and the body is "
                    "DVC-.dir-shaped.",
                    "document": GOOD_MANIFEST,
                },
                {
                    "name": "minor-1-staleness-fields",
                    "why": "(1.1) The optional staleness fields (spec §8): concept and "
                    "record_version in the header, file_version per entry. Must load, and "
                    "schema_version is 1.1 because 1.1 fields are present.",
                    "document": MINOR_1_MANIFEST,
                },
                {
                    "name": "minor-2-container",
                    "why": "(2.0) A container block plus a ZIP member with crc32. Must load; "
                    "body_hash is over the two-key {containers, files} document.",
                    "document": MINOR_2_MANIFEST,
                },
            ],
            "invalid": BAD_MANIFESTS,
        },
    }


def main() -> None:
    out = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src"
        / "chirograph"
        / "vectors"
        / "canonicalisation.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and "--force" not in sys.argv:
        sys.exit(
            f"{out} already exists. These vectors are FROZEN — regenerating them would "
            f"move the goalposts for every other implementation checking itself against "
            f"them. Pass --force only if you are deliberately publishing a new "
            f"spec_version."
        )
    out.write_text(json.dumps(build(), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
