"""The CLI, driven in-process.

Exit codes get their own attention because they are the contract a registry's CI
depends on: 0 success, 1 a finding, 2 the tool could not run. Conflating 1 and 2
would make a merge gate treat "this manifest is a lie" and "the file was missing"
identically.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chirograph.cli import EXIT_ERROR, EXIT_FINDING, EXIT_OK, main
from chirograph.manifest import Manifest
from chirograph.ref import ChiroRef


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def built(tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    target = tmp_path / "GSE1.chiro.json"
    code, _, _ = run(capsys, "write", str(tree), "--accession", "GSE1", "-o", str(target))
    assert code == EXIT_OK
    return target


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys)
    assert code == EXIT_ERROR
    assert "COMMAND" in out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert "chirograph" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #


def test_write_produces_a_loadable_manifest(built: Path) -> None:
    manifest = Manifest.load(built)
    assert manifest.nfiles == 3
    assert manifest.record_accession == "GSE1"


def test_write_reports_the_reference(tree: Path, tmp_path: Path, capsys) -> None:
    code, _, err = run(capsys, "write", str(tree), "-o", str(tmp_path / "m.chiro.json"))
    assert code == EXIT_OK
    assert "chiro://md5:" in err
    assert "body_hash" in err


def test_write_to_stdout_emits_only_json(tree: Path, capsys) -> None:
    code, out, err = run(capsys, "write", str(tree), "--stdout")
    assert code == EXIT_OK
    assert json.loads(out)["schema"] == "chirograph"
    assert err == ""  # nothing must contaminate a piped manifest


def test_write_dry_run_writes_nothing(tree: Path, tmp_path: Path, capsys) -> None:
    target = tmp_path / "nope.chiro.json"
    code, _, err = run(capsys, "write", str(tree), "-o", str(target), "--dry-run")
    assert code == EXIT_OK
    assert not target.exists()
    assert "dry run" in err


def test_write_needs_exactly_one_source(tree: Path, capsys) -> None:
    code, _, err = run(capsys, "write")
    assert code == EXIT_ERROR
    assert "either a directory or --from-dvc-cache" in err


def test_write_rejects_both_sources(tree: Path, capsys) -> None:
    code, _, _ = run(capsys, "write", str(tree), "--from-dvc-cache", "x.dvc")
    assert code == EXIT_ERROR


def test_write_with_rewrite_rule(tmp_path: Path, capsys) -> None:
    deposit = tmp_path / "deposit"
    deposit.mkdir()
    (deposit / "GSE1_sampleA_barcodes.tsv.gz").write_bytes(b"hello\n")
    target = tmp_path / "out.chiro.json"
    code, _, _ = run(
        capsys,
        "capture",
        str(deposit),
        "--source-url",
        "https://example.org/suppl/",
        "--relpath-from-source",
        r"^GSE1_([^_]+)_(.*)$=>\1/\2",
        "-o",
        str(target),
    )
    assert code == EXIT_OK
    manifest = Manifest.load(target)
    assert manifest.files[0].relpath == "sampleA/barcodes.tsv.gz"
    assert manifest.files[0].source_path == "GSE1_sampleA_barcodes.tsv.gz"


def test_write_bad_rewrite_rule_is_a_usage_error(tree: Path, capsys) -> None:
    code, _, err = run(
        capsys, "write", str(tree), "--relpath-from-source", "no-separator", "--stdout"
    )
    assert code == EXIT_ERROR
    assert "REGEX" in err


def test_write_exclude_flag(tree: Path, capsys) -> None:
    code, out, _ = run(capsys, "write", str(tree), "--stdout", "--exclude", "*.mtx")
    assert code == EXIT_OK
    paths = [entry["relpath"] for entry in json.loads(out)["files"]]
    assert not any(path.endswith(".mtx") for path in paths)


def test_write_sha256(tree: Path, capsys) -> None:
    code, out, _ = run(capsys, "write", str(tree), "--stdout", "--hash-algorithm", "sha256")
    assert code == EXIT_OK
    assert len(json.loads(out)["body_hash"]) == 64


# --------------------------------------------------------------------------- #
# write/capture output policy: no silent clobber, no silent empty manifest
# --------------------------------------------------------------------------- #


def test_write_refuses_to_clobber_existing_output(tree: Path, tmp_path: Path, capsys) -> None:
    target = tmp_path / "m.chiro.json"
    assert run(capsys, "write", str(tree), "-o", str(target))[0] == EXIT_OK
    first = target.read_bytes()
    # A second write to the same path must refuse rather than overwrite.
    code, _, err = run(capsys, "write", str(tree), "--accession", "changed", "-o", str(target))
    assert code == EXIT_ERROR
    assert "already exists" in err
    assert target.read_bytes() == first  # untouched


def test_write_force_overwrites(tree: Path, tmp_path: Path, capsys) -> None:
    target = tmp_path / "m.chiro.json"
    assert run(capsys, "write", str(tree), "-o", str(target))[0] == EXIT_OK
    args = ("write", str(tree), "--accession", "GSE9", "-o", str(target), "--force")
    code, _, _ = run(capsys, *args)
    assert code == EXIT_OK
    assert Manifest.load(target).record_accession == "GSE9"


def test_capture_twice_does_not_clobber_the_drift_snapshot(
    tree: Path, tmp_path: Path, capsys
) -> None:
    # Two observations of one accession would default to the same filename; the
    # second must not destroy the first (which is the drift record).
    out = tmp_path / "GSE1.chiro.json"
    common = ("capture", str(tree), "--source-url", "geo://GSE1", "--accession", "GSE1")
    assert run(capsys, *common, "-o", str(out))[0] == EXIT_OK
    code, _, err = run(capsys, *common, "-o", str(out))
    assert code == EXIT_ERROR
    assert "already exists" in err


def test_clobber_guard_ignores_dry_run_and_stdout(tree: Path, tmp_path: Path, capsys) -> None:
    target = tmp_path / "m.chiro.json"
    assert run(capsys, "write", str(tree), "-o", str(target))[0] == EXIT_OK
    # Neither writes to the existing path, so neither is a clobber.
    assert run(capsys, "write", str(tree), "-o", str(target), "--dry-run")[0] == EXIT_OK
    assert run(capsys, "write", str(tree), "-o", str(target), "--stdout")[0] == EXIT_OK


def test_write_refuses_empty_manifest(tmp_path: Path, capsys) -> None:
    # A directory of manifests: the default *.chiro.json exclude drops everything.
    d = tmp_path / "manifests"
    d.mkdir()
    (d / "a.chiro.json").write_text("{}")
    code, _, err = run(capsys, "write", str(d), "-o", str(tmp_path / "out.chiro.json"))
    assert code == EXIT_ERROR
    assert "empty manifest" in err
    assert not (tmp_path / "out.chiro.json").exists()


def test_write_allow_empty(tmp_path: Path, capsys) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    target = tmp_path / "e.chiro.json"
    code, _, _ = run(capsys, "write", str(d), "-o", str(target), "--allow-empty")
    assert code == EXIT_OK
    assert Manifest.load(target).nfiles == 0


def test_dry_run_reports_empty_without_failing(tmp_path: Path, capsys) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    code, _, err = run(capsys, "write", str(d), "--dry-run")
    assert code == EXIT_OK
    assert "0 file(s)" in err


# --------------------------------------------------------------------------- #
# --name-by-digest (spec §1.1)
# --------------------------------------------------------------------------- #


def test_name_by_digest_filename_equals_reference(tree: Path, tmp_path: Path, capsys) -> None:
    outdir = tmp_path / "manifests"
    code, _, _ = run(capsys, "write", str(tree), "--name-by-digest", "-o", str(outdir))
    assert code == EXIT_OK
    written = list(outdir.glob("*.chiro.json"))
    assert len(written) == 1
    path = written[0]
    digest = path.name.removesuffix(".chiro.json")
    # The filename is the whole-file hash, so it equals the chiro:// reference and
    # is checkable with a plain md5sum.
    assert str(ChiroRef.for_path(path, "md5")) == f"chiro://md5:{digest}"


def test_name_by_digest_defaults_to_cwd(tree: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    code, _, _ = run(capsys, "write", str(tree), "--name-by-digest")
    assert code == EXIT_OK
    assert len(list(tmp_path.glob("*.chiro.json"))) == 1


def test_name_by_digest_is_idempotent_when_content_is_pinned(tree, tmp_path, capsys) -> None:
    outdir = tmp_path / "m"
    common = (
        "write",
        str(tree),
        "--created",
        "2026-09-04T00:00:00Z",
        "--name-by-digest",
        "-o",
        str(outdir),
    )
    assert run(capsys, *common)[0] == EXIT_OK
    assert run(capsys, *common)[0] == EXIT_OK  # same bytes -> same name, no clobber error
    assert len(list(outdir.glob("*.chiro.json"))) == 1


def test_name_by_digest_distinct_observations_coexist(tree, tmp_path, capsys) -> None:
    outdir = tmp_path / "m"
    assert (
        run(
            capsys,
            "write",
            str(tree),
            "--created",
            "2026-01-01T00:00:00Z",
            "--name-by-digest",
            "-o",
            str(outdir),
        )[0]
        == EXIT_OK
    )
    assert (
        run(
            capsys,
            "write",
            str(tree),
            "--created",
            "2026-02-02T00:00:00Z",
            "--name-by-digest",
            "-o",
            str(outdir),
        )[0]
        == EXIT_OK
    )
    assert len(list(outdir.glob("*.chiro.json"))) == 2  # different created -> both kept


def test_name_by_digest_rejects_stdout(tree: Path, capsys) -> None:
    code, _, err = run(capsys, "write", str(tree), "--name-by-digest", "--stdout")
    assert code == EXIT_ERROR
    assert "stdout" in err


def test_name_by_digest_rejects_file_as_output_dir(tree: Path, tmp_path: Path, capsys) -> None:
    afile = tmp_path / "afile"
    afile.write_text("x")
    code, _, err = run(capsys, "write", str(tree), "--name-by-digest", "-o", str(afile))
    assert code == EXIT_ERROR
    assert "directory" in err


def test_capture_requires_source_url(tree: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        main(["capture", str(tree)])


def test_capture_lints_clean(tree: Path, capsys) -> None:
    code, _, err = run(capsys, "capture", str(tree), "--source-url", "https://x/", "--stdout")
    assert code == EXIT_OK
    assert "warning" not in err


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #


def test_verify_clean_tree(built: Path, tree: Path, capsys) -> None:
    code, out, _ = run(capsys, "verify", str(built), "--root", str(tree))
    assert code == EXIT_OK
    assert out.strip().endswith("OK")


def test_verify_detects_tampering(built: Path, tree: Path, capsys) -> None:
    (tree / "sampleA" / "barcodes.tsv").write_bytes(b"HELLO\n")
    code, out, _ = run(capsys, "verify", str(built), "--root", str(tree))
    assert code == EXIT_FINDING
    assert "FAIL hash-mismatch" in out
    assert out.strip().endswith("FAILED")


def test_verify_json_report(built: Path, tree: Path, capsys) -> None:
    code, out, _ = run(capsys, "verify", str(built), "--root", str(tree), "--json")
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["verification_level"] == "tier2b-full"


def test_verify_layer_2_is_tier1(built: Path, tree: Path, capsys) -> None:
    code, out, _ = run(capsys, "verify", str(built), "--root", str(tree), "--layer", "2", "--json")
    assert code == EXIT_OK
    assert json.loads(out)["verification_level"] == "tier1"


def test_verify_strict_makes_extra_files_fail(built: Path, tree: Path, capsys) -> None:
    (tree / "derived.h5ad").write_bytes(b"x")
    code, out, _ = run(capsys, "verify", str(built), "--root", str(tree))
    assert code == EXIT_OK
    code, out, _ = run(capsys, "verify", str(built), "--root", str(tree), "--strict")
    assert code == EXIT_FINDING
    assert "FAIL extra" in out


def test_verify_defaults_the_root_to_the_manifests_directory(tree: Path, capsys) -> None:
    target = tree / ".." / "m.chiro.json"
    run(capsys, "write", str(tree), "-o", str(tree.parent / "m.chiro.json"))
    # The manifest sits next to `data/`, so the default root has no matching files.
    code, _, _ = run(capsys, "verify", str(tree.parent / "m.chiro.json"))
    assert code == EXIT_FINDING  # correct: files are under data/, not alongside
    code, _, _ = run(capsys, "verify", str(tree.parent / "m.chiro.json"), "--root", str(tree))
    assert code == EXIT_OK
    assert target


def test_verify_missing_manifest_is_a_tool_error(capsys) -> None:
    code, _, err = run(capsys, "verify", "/nonexistent/x.chiro.json")
    assert code == EXIT_ERROR
    assert "No such file" in err or "not exist" in err


def test_verify_corrupt_manifest_is_a_finding(tmp_path: Path, capsys) -> None:
    """A manifest that lies about itself is a finding, not a tool failure."""
    target = tmp_path / "bad.chiro.json"
    document = json.loads(Manifest(files=[], created="2026-01-01T00:00:00Z").to_json())
    document["body_hash"] = "0" * 32
    target.write_text(json.dumps(document))
    code, _, err = run(capsys, "verify", str(target))
    assert code == EXIT_FINDING
    assert "BodyHashMismatch" in err


def test_verify_sample(built: Path, tree: Path, capsys) -> None:
    code, out, _ = run(capsys, "verify", str(built), "--root", str(tree), "--sample", "1", "--json")
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["checked"] == 1
    assert payload["verification_level"] == "tier2b-sampled"


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #


def test_validate_ok(built: Path, capsys) -> None:
    code, out, _ = run(capsys, "validate", str(built))
    assert code == EXIT_OK
    assert out.startswith("OK ")


def test_validate_with_jsonschema(built: Path, capsys) -> None:
    pytest.importorskip("jsonschema")
    code, _, _ = run(capsys, "validate", str(built), "--jsonschema")
    assert code == EXIT_OK


def test_validate_missing_optional_dep_is_a_tool_error_not_a_finding(
    built: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A runner without the `schema` extra must not make a good manifest INVALID.

    Exit 1 means "this manifest is a lie" and would block a merge; exit 2 means
    "the tool could not run" and should page whoever owns the runner
    (docs/decisions.md D-16).
    """
    import chirograph.schema as schema_module

    def unavailable(_document: object) -> list[str]:
        raise RuntimeError("needs the `jsonschema` package")

    monkeypatch.setattr(schema_module, "validate_document", unavailable)
    code, out, err = run(capsys, "validate", str(built), "--jsonschema")
    assert code == EXIT_ERROR
    assert "jsonschema" in err
    assert "INVALID" not in out


def test_validate_reports_lint_warnings(tmp_path: Path, capsys) -> None:
    manifest = Manifest(
        provenance_type="observed-download",
        files=[],
        created="2026-01-01T00:00:00Z",
    )
    target = manifest.dump(tmp_path / "warn.chiro.json")
    code, out, _ = run(capsys, "validate", str(target))
    assert code == EXIT_OK  # warnings are not failures...
    assert "observed-missing-source-url" in out
    code, _, _ = run(capsys, "validate", str(target), "--strict-lint")
    assert code == EXIT_FINDING  # ...unless asked


def test_validate_multiple_and_json(built: Path, tmp_path: Path, capsys) -> None:
    """Malformed JSON is a *finding* about the document, not a tool failure.

    `chiro verify` already exited 1 for the same file. The two commands
    disagreeing would make a CI gate's behaviour depend on which one it called
    (docs/decisions.md D-16).
    """
    broken = tmp_path / "broken.chiro.json"
    broken.write_text("{not json")
    code, out, _ = run(capsys, "validate", str(built), str(broken), "--json")
    assert code == EXIT_FINDING
    payload = json.loads(out)
    assert payload["ok"] is False
    assert len(payload["manifests"]) == 2
    # The good manifest must still be reported, not lost to the bad one.
    assert payload["manifests"][0]["ok"] is True


def test_validate_unreadable_file_is_a_tool_error(tmp_path: Path, capsys) -> None:
    code, _, _ = run(capsys, "validate", str(tmp_path / "nope.chiro.json"))
    assert code == EXIT_ERROR


def test_validate_and_verify_agree_on_a_bom(built: Path, tmp_path: Path, capsys) -> None:
    """Both must call it a finding, and both must mention the BOM."""
    bommed = tmp_path / "bom.chiro.json"
    bommed.write_bytes(b"\xef\xbb\xbf" + built.read_bytes())

    code, out, _ = run(capsys, "validate", str(bommed))
    assert code == EXIT_FINDING
    assert "BOM" in out

    code, _, err = run(capsys, "verify", str(bommed))
    assert code == EXIT_FINDING
    assert "BOM" in err


# --------------------------------------------------------------------------- #
# info / ls / diff / ref / remap / dvc-dir
# --------------------------------------------------------------------------- #


def test_info(built: Path, capsys) -> None:
    code, out, _ = run(capsys, "info", str(built))
    assert code == EXIT_OK
    assert "body_hash" in out
    assert "registry_shard_path" in out
    assert "dvc_dir_hash" in out


def test_info_json(built: Path, capsys) -> None:
    _, out, _ = run(capsys, "info", str(built), "--json")
    payload = json.loads(out)
    assert payload["nfiles"] == 3
    assert payload["reference"].startswith("chiro://md5:")
    assert payload["canonical_reference"] != payload["reference"]


def test_ls(built: Path, capsys) -> None:
    code, out, _ = run(capsys, "ls", str(built))
    assert code == EXIT_OK
    assert out.splitlines() == [
        "sampleA/barcodes.tsv",
        "sampleA/matrix.mtx",
        "sampleB/barcodes.tsv",
    ]


def test_ls_long_and_json(built: Path, capsys) -> None:
    code, out, _ = run(capsys, "ls", str(built), "-l")
    assert code == EXIT_OK
    assert "b1946ac92492d2347c6235b4d2611184" in out
    code, out, _ = run(capsys, "ls", str(built), "--json")
    assert len(json.loads(out)) == 3


def test_diff(built: Path, tree: Path, tmp_path: Path, capsys) -> None:
    (tree / "sampleA" / "barcodes.tsv").write_bytes(b"CHANGED\n")
    later = tmp_path / "later.chiro.json"
    run(capsys, "write", str(tree), "--accession", "GSE1", "-o", str(later))

    code, out, _ = run(capsys, "diff", str(built), str(later))
    assert code == EXIT_OK  # no --exit-code
    assert "changed sampleA/barcodes.tsv" in out

    code, _, _ = run(capsys, "diff", str(built), str(later), "--exit-code")
    assert code == EXIT_FINDING


def test_diff_identical(built: Path, capsys) -> None:
    code, out, _ = run(capsys, "diff", str(built), str(built), "--exit-code")
    assert code == EXIT_OK
    assert "identical" in out


def test_ref_and_check(built: Path, capsys) -> None:
    code, out, _ = run(capsys, "ref", str(built))
    assert code == EXIT_OK
    reference = out.strip()
    assert reference.startswith("chiro://md5:")

    code, out, _ = run(capsys, "ref", str(built), "--check", reference)
    assert code == EXIT_OK
    assert out.startswith("OK")

    code, out, _ = run(capsys, "ref", str(built), "--check", "chiro://md5:" + "0" * 32)
    assert code == EXIT_FINDING
    assert "MISMATCH" in out


def test_ref_shard(built: Path, capsys) -> None:
    code, out, _ = run(capsys, "ref", str(built), "--shard")
    assert code == EXIT_OK
    assert out.splitlines()[1].startswith("manifests/")


def test_ref_canonical_differs(built: Path, capsys) -> None:
    _, plain, _ = run(capsys, "ref", str(built))
    _, canonical, _ = run(capsys, "ref", str(built), "--canonical")
    assert plain.strip() != canonical.strip()


def test_ref_algorithm_mismatch_is_a_usage_error(built: Path, capsys) -> None:
    code, _, err = run(capsys, "ref", str(built), "--check", "chiro://sha256:" + "0" * 64)
    assert code == EXIT_ERROR
    assert "--algorithm sha256" in err


def test_remap(built: Path, tmp_path: Path, capsys) -> None:
    out_path = tmp_path / "remapped.chiro.json"
    code, _, err = run(
        capsys,
        "remap",
        str(built),
        "--source-from-relpath",
        r"^(.*)/(.*)$=>GSE1_\1_\2",
        "-o",
        str(out_path),
    )
    assert code == EXIT_OK
    assert "body_hash" in err
    manifest = Manifest.load(out_path)
    assert manifest.entry("sampleA/barcodes.tsv").source_path == "GSE1_sampleA_barcodes.tsv"


def test_remap_tidy_drops_redundant_source_paths(tmp_path: Path, capsys) -> None:
    from chirograph.manifest import FileEntry

    manifest = Manifest(
        files=[FileEntry("a.txt", "b1946ac92492d2347c6235b4d2611184", 6, source_path="a.txt")],
        created="2026-01-01T00:00:00Z",
    )
    source = manifest.dump(tmp_path / "in.chiro.json")
    target = tmp_path / "out.chiro.json"
    code, _, _ = run(capsys, "remap", str(source), "--tidy", "-o", str(target))
    assert code == EXIT_OK
    assert Manifest.load(target).files[0].source_path is None


def test_remap_rejects_both_directions(built: Path, capsys) -> None:
    code, _, err = run(
        capsys,
        "remap",
        str(built),
        "--relpath-from-source",
        r"^(.*)$=>x/\1",
        "--source-from-relpath",
        r"^(.*)$=>y/\1",
        "--stdout",
    )
    assert code == EXIT_ERROR
    assert "not both" in err


def test_dvc_dir_hash_only(built: Path, capsys) -> None:
    code, out, _ = run(capsys, "dvc-dir", str(built), "--hash-only")
    assert code == EXIT_OK
    assert len(out.strip()) == 32


def test_dvc_dir_explains_that_it_is_not_body_hash(built: Path, capsys) -> None:
    code, out, err = run(capsys, "dvc-dir", str(built))
    assert code == EXIT_OK
    assert json.loads(out)  # valid JSON array
    assert "NOT the manifest's" in err


# --------------------------------------------------------------------------- #
# schema / test-vectors
# --------------------------------------------------------------------------- #


def test_schema_command(capsys) -> None:
    code, out, _ = run(capsys, "schema")
    assert code == EXIT_OK
    assert json.loads(out)["title"].startswith("Chirograph manifest")


def test_schema_path(capsys) -> None:
    code, out, _ = run(capsys, "schema", "--path")
    assert code == EXIT_OK
    assert Path(out.strip()).is_file()


def test_test_vectors_dump(capsys) -> None:
    code, out, _ = run(capsys, "test-vectors")
    assert code == EXIT_OK
    assert json.loads(out)["format"] == "chirograph-test-vectors"


def test_test_vectors_check_passes(capsys) -> None:
    """The shipped conformance suite must pass against its own implementation."""
    code, out, _ = run(capsys, "test-vectors", "--check")
    assert code == EXIT_OK
    assert "conformance checks passed" in out
    assert "FAIL" not in out


# --------------------------------------------------------------------------- #
# End-to-end story
# --------------------------------------------------------------------------- #


def test_full_deposit_workflow(tmp_path: Path, capsys) -> None:
    """The workflow the plan cares about: reorganise, publish, verify, detect drift."""
    deposit = tmp_path / "deposit"
    deposit.mkdir()
    (deposit / "GSE7_sampleA_barcodes.tsv.gz").write_bytes(b"barcodes\n")
    (deposit / "GSE7_sampleA_matrix.mtx.gz").write_bytes(b"matrix\n")
    manifest_path = tmp_path / "GSE7.chiro.json"

    # 1. Capture what was downloaded, reorganised into a workspace layout.
    code, _, _ = run(
        capsys,
        "capture",
        str(deposit),
        "--source-url",
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE7nnn/GSE7/suppl/",
        "--accession",
        "GSE7",
        "--relpath-from-source",
        r"^GSE7_([^_]+)_(.*)$=>\1/\2",
        "-o",
        str(manifest_path),
    )
    assert code == EXIT_OK

    # 2. Tier-1: the manifest is internally sound.
    assert run(capsys, "validate", str(manifest_path))[0] == EXIT_OK

    # 3. Tier-2b: the bytes match, checked in the deposit layout.
    assert (
        run(capsys, "verify", str(manifest_path), "--root", str(deposit), "--source-layout")[0]
        == EXIT_OK
    )

    # 4. The manifest has a stable, self-validating address.
    _, reference, _ = run(capsys, "ref", str(manifest_path))
    assert run(capsys, "ref", str(manifest_path), "--check", reference.strip())[0] == EXIT_OK

    # 5. Upstream mutates. Verification notices, and says why.
    (deposit / "GSE7_sampleA_matrix.mtx.gz").write_bytes(b"matrix v2\n")
    code, out, _ = run(
        capsys, "verify", str(manifest_path), "--root", str(deposit), "--source-layout"
    )
    assert code == EXIT_FINDING
    assert "upstream record mutated" in out

    # 6. Capturing again and diffing produces the drift report.
    later = tmp_path / "GSE7-later.chiro.json"
    run(
        capsys,
        "capture",
        str(deposit),
        "--source-url",
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE7nnn/GSE7/suppl/",
        "--accession",
        "GSE7",
        "--relpath-from-source",
        r"^GSE7_([^_]+)_(.*)$=>\1/\2",
        "-o",
        str(later),
    )
    code, out, _ = run(capsys, "diff", str(manifest_path), str(later), "--exit-code")
    assert code == EXIT_FINDING
    assert "changed sampleA/matrix.mtx.gz" in out
    assert "upstream drift" in out

    # 7. The old manifest is still valid — nothing was destroyed.
    assert run(capsys, "validate", str(manifest_path))[0] == EXIT_OK


# --------------------------------------------------------------------------- #
# Regressions from the adversarial review
# --------------------------------------------------------------------------- #


def three_entry_manifest(tmp_path: Path) -> Path:
    """One relocated entry, one redundant source_path, one terse."""
    from chirograph.manifest import FileEntry

    manifest = Manifest(
        source_url="https://ftp.example.org/geo/GSE1/suppl/",
        created="2026-01-01T00:00:00Z",
        files=[
            FileEntry("sampleA/matrix.mtx", "1" * 32, 10, source_path="GSE1_sampleA_matrix.mtx"),
            FileEntry("readme.txt", "2" * 32, 20, source_path="readme.txt"),
            FileEntry("other.bin", "3" * 32, 30),
        ],
    )
    return manifest.dump(tmp_path / "in.chiro.json")


def layout_of(path: Path) -> list[tuple[str, str | None]]:
    return [
        (entry["relpath"], entry.get("source_path"))
        for entry in json.loads(path.read_text())["files"]
    ]


def test_remap_leaves_entries_alone_when_no_rule_matches(tmp_path: Path, capsys) -> None:
    """The destructive case: relpath was being overwritten with source_path.

    `apply_rules` returns its input when nothing matches, and remap assigned that
    unconditionally — so a non-matching rule silently replaced every entry's
    workspace layout with its deposit layout.
    """
    source = three_entry_manifest(tmp_path)
    before = layout_of(source)

    target = tmp_path / "out.chiro.json"
    code, _, err = run(
        capsys, "remap", str(source), "--relpath-from-source", r"^ZZZ_(.*)$=>\1", "-o", str(target)
    )
    assert code == EXIT_OK
    assert layout_of(target) == before
    assert "matched no rule" in err


def test_remap_does_not_delete_source_paths_when_no_rule_matches(tmp_path: Path, capsys) -> None:
    """The mirror case: losing source_path means losing the fetch location."""
    source = three_entry_manifest(tmp_path)
    before = layout_of(source)

    target = tmp_path / "out.chiro.json"
    code, _, _ = run(
        capsys, "remap", str(source), "--source-from-relpath", r"^ZZZ_(.*)$=>\1", "-o", str(target)
    )
    assert code == EXIT_OK
    assert layout_of(target) == before


def test_remap_still_rewrites_the_entries_that_do_match(tmp_path: Path, capsys) -> None:
    source = three_entry_manifest(tmp_path)
    target = tmp_path / "out.chiro.json"
    code, _, err = run(
        capsys,
        "remap",
        str(source),
        "--relpath-from-source",
        r"^GSE1_([^_]+)_(.*)$=>reorg/\1/\2",
        "-o",
        str(target),
    )
    assert code == EXIT_OK
    assert ("reorg/sampleA/matrix.mtx", "GSE1_sampleA_matrix.mtx") in layout_of(target)
    # ...and only those entries.
    assert ("readme.txt", "readme.txt") in layout_of(target)
    assert ("other.bin", None) in layout_of(target)
    assert "2 of 3" in err


def test_stdout_still_reports_notes_and_warnings(tree: Path, capsys) -> None:
    """`chiro write … --stdout > m.chiro.json` must not swallow diagnostics.

    They go to stderr, which cannot contaminate the piped manifest.
    """
    (tree / "empty_dir").mkdir()
    (tree / "NUL.txt").write_bytes(b"x")
    code, out, err = run(capsys, "write", str(tree), "--stdout")
    assert code == EXIT_OK
    assert json.loads(out)["schema"] == "chirograph"  # stdout is still pure JSON
    assert "warning:" in err
    assert "windows-reserved" in err


def test_surrogate_filename_names_the_file_and_is_a_finding(tmp_path: Path, capsys) -> None:
    """A latin-1 filename is routine in real deposits, and used to crash."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "ok.txt").write_bytes(b"hello\n")
    bad = bytes(root) + b"/caf\xe9.txt"
    try:
        with open(bad, "wb") as handle:
            handle.write(b"x\n")
    except OSError:  # pragma: no cover - filesystem refuses the name
        pytest.skip("filesystem will not create a non-UTF-8 filename")

    code, _, err = run(capsys, "write", str(root), "--stdout")
    assert code == EXIT_FINDING
    assert "surrogate" in err
    assert "caf" in err  # the offending file is named


def test_validate_batch_survives_one_bad_manifest(built: Path, tmp_path: Path, capsys) -> None:
    """A surrogate used to abort the loop before anything was reported."""
    from chirograph.canonical import compute_body_hash

    files = [{"relpath": "a\udcffb", "md5": "b1946ac92492d2347c6235b4d2611184", "size": 6}]
    bad = tmp_path / "surr.chiro.json"
    bad.write_text(
        json.dumps(
            {
                "schema": "chirograph",
                "schema_version": "1.0",
                "hash_algorithm": "md5",
                "provenance_type": "self-deposited",
                "created": "2026-01-01T00:00:00Z",
                "size": 6,
                "nfiles": 1,
                "body_hash": "0" * 32,
                "files": files,
            }
        )
    )
    assert compute_body_hash  # imported for the reader's benefit
    code, out, _ = run(capsys, "validate", str(built), str(bad))
    assert code == EXIT_FINDING
    assert out.startswith("OK ")  # the good one is reported first, not lost
    assert "INVALID" in out


def test_validate_rejects_every_non_utf8_encoding(built: Path, tmp_path: Path, capsys) -> None:
    """And `verify` must give the same verdict for the same file."""
    text = built.read_text(encoding="utf-8")
    for name, raw in [
        ("utf16", text.encode("utf-16")),
        ("utf32", text.encode("utf-32")),
        ("bom8", b"\xef\xbb\xbf" + text.encode("utf-8")),
        ("latin1", text.replace("sampleA", "café").encode("latin-1")),
    ]:
        target = tmp_path / f"{name}.chiro.json"
        target.write_bytes(raw)
        validate_code, _, _ = run(capsys, "validate", str(target))
        verify_code, _, _ = run(capsys, "verify", str(target))
        assert validate_code == EXIT_FINDING, name
        assert verify_code == EXIT_FINDING, name


def test_validate_batch_survives_a_non_utf8_manifest(built: Path, tmp_path: Path, capsys) -> None:
    """A UnicodeDecodeError escaped the per-manifest handler and killed the run."""
    bad = tmp_path / "latin1.chiro.json"
    bad.write_bytes(built.read_text(encoding="utf-8").replace("sampleA", "café").encode("latin-1"))
    code, out, _ = run(capsys, "validate", str(built), str(bad), str(built))
    assert code == EXIT_FINDING
    # Every manifest gets a verdict; none is lost to the bad one.
    assert len([line for line in out.splitlines() if line[:2] in ("OK", "IN")]) == 3


def test_check_extra_tolerates_an_unrepresentable_stray_file(tmp_path: Path, capsys) -> None:
    """It only enumerates names, so it must not discard a passing report.

    Building a manifest from such a tree must still fail loudly, though — the two
    cases want opposite behaviour, which is why the walk takes a flag.
    """
    root = tmp_path / "dep"
    root.mkdir()
    (root / "a.txt").write_bytes(b"listed\n")
    target = tmp_path / "m.chiro.json"
    assert run(capsys, "write", str(root), "-o", str(target))[0] == EXIT_OK

    try:
        with open(bytes(root) + b"/caf\xe9.txt", "wb") as handle:
            handle.write(b"stray\n")
    except OSError:  # pragma: no cover
        pytest.skip("filesystem will not create a non-UTF-8 filename")

    # Passes, and names the stray as an extra.
    code, out, _ = run(capsys, "verify", str(target), "--root", str(root), "--check-extra")
    assert code == EXIT_OK
    assert "caf" in out

    # --strict asked for exact correspondence, so an unlistable extra is a failure.
    code, _, _ = run(capsys, "verify", str(target), "--root", str(root), "--strict")
    assert code == EXIT_FINDING

    # Writing a manifest of the same tree still refuses.
    assert run(capsys, "write", str(root), "--stdout")[0] == EXIT_FINDING


# --------------------------------------------------------------------------- #
# Regressions from the third review pass
# --------------------------------------------------------------------------- #


def test_ref_refuses_to_mint_a_reference_for_a_non_manifest(
    built: Path, tmp_path: Path, capsys
) -> None:
    """A reference asserts "this is manifest X", so the file must be one.

    The previous commit fixed `validate` for non-UTF-8 input but left `chiro ref`
    hashing raw bytes — the one command whose whole job is producing the reference a
    registry would key on.
    """
    utf16 = tmp_path / "utf16.chiro.json"
    utf16.write_bytes(built.read_text(encoding="utf-8").encode("utf-16"))
    junk = tmp_path / "junk.chiro.json"
    junk.write_text("{not json")

    assert run(capsys, "ref", str(built))[0] == EXIT_OK
    for bad in (utf16, junk):
        code, _, err = run(capsys, "ref", str(bad))
        assert code == EXIT_FINDING
        assert "SchemaError" in err

    # The escape hatch still gives the raw byte hash, for a file already known good.
    code, out, _ = run(capsys, "ref", str(utf16), "--no-validate")
    assert code == EXIT_OK
    assert out.startswith("chiro://md5:")


def test_validate_batch_survives_json_value_errors(built: Path, tmp_path: Path, capsys) -> None:
    """json.loads raises plain ValueError for a huge integer, not JSONDecodeError."""
    bigint = tmp_path / "bigint.chiro.json"
    bigint.write_text('{"schema":"chirograph","n":' + "9" * 5000 + "}")
    code, out, _ = run(capsys, "validate", str(bigint), str(built))
    assert code == EXIT_FINDING
    assert "INVALID" in out
    assert "OK " in out  # the good manifest is still reported


def test_validate_labels_a_tool_failure_as_error_not_invalid(tmp_path: Path, capsys) -> None:
    """ "INVALID" reads as a verdict on the manifest; an unreadable file is not one."""
    code, out, _ = run(capsys, "validate", str(tmp_path / "missing.chiro.json"))
    assert code == EXIT_ERROR
    assert out.startswith("ERROR ")
    assert "INVALID" not in out


# --------------------------------------------------------------------------- #
# Regressions from the fourth review pass
# --------------------------------------------------------------------------- #


def test_streaming_a_manifest_from_a_pipe_works(built: Path, capsys) -> None:
    """`chiro validate <(curl ...)` and `... | chiro validate /dev/stdin`.

    These are the idioms for checking a manifest you never wrote to disk — exactly
    what a registry's CI wants. An earlier attempt to stop a writerless FIFO
    blocking refused every non-regular file, which broke both, because process
    substitution *is* a FIFO. There is no guard that stops the hang and keeps these
    working, and keeping them wins: pointing any tool at a writerless FIFO blocks
    (`cat`, `jq`, `md5sum` all do), so that behaviour is unsurprising and explicitly
    requested by the path the user typed.
    """
    import subprocess
    import sys

    for argv in (["validate"], ["info"], ["ls"], ["ref"]):
        result = subprocess.run(
            [sys.executable, "-m", "chirograph.cli", *argv, "/dev/stdin"],
            input=built.read_bytes(),
            capture_output=True,
        )
        assert result.returncode == EXIT_OK, (argv, result.stderr.decode())


def test_a_directory_is_still_refused_with_a_clear_message(tmp_path: Path, capsys) -> None:
    code, out, _ = run(capsys, "validate", str(tmp_path))
    assert code == EXIT_ERROR
    assert "is a directory" in out


def test_ref_hashes_the_bytes_it_validated(built: Path, tmp_path: Path, capsys) -> None:
    """No TOCTOU: validating via one open and hashing via another let a swap
    between them mint a reference naming content that was never checked.

    Checked here by construction rather than by racing: the reference must equal the
    hash of the bytes on disk at the moment of the single read.
    """
    import hashlib

    code, out, _ = run(capsys, "ref", str(built))
    assert code == EXIT_OK
    expected = hashlib.md5(built.read_bytes(), usedforsecurity=False).hexdigest()
    assert out.strip() == f"chiro://md5:{expected}"


def test_ref_check_is_a_byte_question_not_a_validity_one(
    built: Path, tmp_path: Path, capsys
) -> None:
    """`--check REF` asks "did I fetch the right manifest?".

    Validating on that path reported failure for a byte-perfect download of a
    manifest that merely has a finding, and collapsed "wrong download" and "invalid
    manifest" into one exit code.
    """
    import hashlib

    stale = tmp_path / "stale.chiro.json"
    document = json.loads(built.read_text())
    document["body_hash"] = "0" * 32
    stale.write_text(json.dumps(document, indent=2) + "\n")
    digest = hashlib.md5(stale.read_bytes(), usedforsecurity=False).hexdigest()

    # The bytes match, so --check succeeds even though the manifest is invalid.
    code, out, _ = run(capsys, "ref", str(stale), "--check", f"chiro://md5:{digest}")
    assert code == EXIT_OK
    assert out.startswith("OK")

    # A real byte mismatch is still a finding, and --json still emits.
    code, out, _ = run(capsys, "ref", str(stale), "--check", "chiro://md5:" + "0" * 32, "--json")
    assert code == EXIT_FINDING
    assert json.loads(out)["ok"] is False

    # Plain `ref` still refuses it — that IS the validity question.
    assert run(capsys, "ref", str(stale))[0] == EXIT_FINDING


@pytest.mark.parametrize(
    "argv", [["ref"], ["info"], ["ls"], ["verify"], ["dvc-dir"], ["validate"]], ids=lambda a: a[0]
)
def test_no_command_leaks_a_traceback_on_pathological_json(
    tmp_path: Path, capsys, argv: list[str]
) -> None:
    """`json.loads` raises plain ValueError for a huge int and RecursionError for
    deep nesting. Translating both in `Manifest.loads` covers every command at once.
    """
    for name, text in (
        ("deep.chiro.json", "[" * 100_000 + "]" * 100_000),
        ("bigint.chiro.json", '{"schema":"chirograph","n":' + "9" * 5000 + "}"),
    ):
        target = tmp_path / name
        target.write_text(text)
        code, out, err = run(capsys, *argv, str(target))
        assert code == EXIT_FINDING, (name, argv)
        assert "Traceback" not in out + err


def test_canonical_and_no_validate_are_mutually_exclusive(built: Path, capsys) -> None:
    """--canonical must parse to canonicalise, so the flag cannot be honoured."""
    code, _, err = run(capsys, "ref", str(built), "--canonical", "--no-validate")
    assert code == EXIT_ERROR
    assert "--no-validate does not apply" in err


def test_json_output_keeps_a_non_utf8_filename_recoverable(tmp_path: Path, capsys) -> None:
    """The escape must round-trip back to the real filename.

    An earlier version rewrote surrogates to a literal-backslash spelling to satisfy
    RFC 8259; the result could not be turned back into a path, so a consumer could
    not act on the report, and two distinct files collapsed to one string.
    """
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.txt").write_bytes(b"listed\n")
    target = tmp_path / "m.chiro.json"
    assert run(capsys, "write", str(root), "-o", str(target))[0] == EXIT_OK
    try:
        with open(bytes(root) + b"/stray\xff.dat", "wb") as handle:
            handle.write(b"x\n")
    except OSError:  # pragma: no cover
        pytest.skip("filesystem will not create a non-UTF-8 filename")

    code, out, _ = run(
        capsys, "verify", str(target), "--root", str(root), "--check-extra", "--json"
    )
    assert code == EXIT_OK
    names = json.loads(out)["extra_files"]
    assert len(names) == 1
    # The decisive property: the reported name locates the actual file.
    assert (root / names[0]).exists(), names


# --------------------------------------------------------------------------- #
# Regressions from the fifth review pass
# --------------------------------------------------------------------------- #


def test_canonical_check_compares_the_canonical_address(
    built: Path, tmp_path: Path, capsys
) -> None:
    """`--check` used to be evaluated before `--canonical`, so it silently ignored it.

    That produced both a false MISMATCH (for the canonical address the user asked
    about) and a false OK (for the whole-file address they did not) — and made
    `--canonical`'s only real use, a reformat-invariant comparison, unavailable.
    """
    _, plain, _ = run(capsys, "ref", str(built))
    _, canonical, _ = run(capsys, "ref", str(built), "--canonical")
    assert plain.strip() != canonical.strip()

    assert run(capsys, "ref", str(built), "--canonical", "--check", canonical.strip())[0] == EXIT_OK
    assert (
        run(capsys, "ref", str(built), "--canonical", "--check", plain.strip())[0] == EXIT_FINDING
    )

    # The point of --canonical: re-indenting must not change the answer.
    reindented = tmp_path / "reindented.chiro.json"
    reindented.write_text(json.dumps(json.loads(built.read_text()), indent=8) + "\n")
    assert run(capsys, "ref", str(built), "--check", plain.strip())[0] == EXIT_OK
    assert run(capsys, "ref", str(reindented), "--check", plain.strip())[0] == EXIT_FINDING
    assert (
        run(capsys, "ref", str(reindented), "--canonical", "--check", canonical.strip())[0]
        == EXIT_OK
    )


def test_info_on_a_stream_reports_the_right_reference(built: Path, capsys) -> None:
    """`cmd_info` read the file twice; on a pipe the first read drained it.

    The second read then returned nothing, so `reference` was md5 of the empty
    string — a wrong chiro:// address inside an otherwise-correct report, at exit 0.
    """
    import subprocess
    import sys

    expected = run(capsys, "ref", str(built))[1].strip()
    result = subprocess.run(
        [sys.executable, "-m", "chirograph.cli", "info", "/dev/stdin", "--json"],
        input=built.read_bytes(),
        capture_output=True,
    )
    assert result.returncode == EXIT_OK, result.stderr.decode()
    assert json.loads(result.stdout)["reference"] == expected


def test_no_validate_is_refused_where_it_cannot_apply(built: Path, capsys) -> None:
    """`--canonical` must parse, and `--check` deliberately never validates.

    Accepting the flag and ignoring it is the defect this function had twice.
    """
    for extra in (["--canonical"], ["--check", "chiro://md5:" + "0" * 32]):
        code, _, err = run(capsys, "ref", str(built), *extra, "--no-validate")
        assert code == EXIT_ERROR
        assert "--no-validate does not apply" in err


def test_ref_errors_name_the_file(built: Path, tmp_path: Path, capsys) -> None:
    """Parsing bytes we already hold lost the filename that `Manifest.load` adds."""
    broken = tmp_path / "trunc.chiro.json"
    broken.write_text('{"schema": "chirograph",')
    for command in ("ref", "info", "ls"):
        code, _, err = run(capsys, command, str(broken))
        assert code == EXIT_FINDING
        assert "trunc.chiro.json" in err, command


# --------------------------------------------------------------------------- #
# `chiro ref` flag matrix
# --------------------------------------------------------------------------- #
#
# Two review rounds found a regression in `cmd_ref` immediately after it was
# rewritten, both times a flag being silently ignored on one branch. Reviewing the
# function again would not stop a third; enumerating the combinations does.


REF_FLAG_MATRIX = [
    ([], "whole-file"),
    (["--shard"], "whole-file"),
    (["--json"], "whole-file"),
    (["--no-validate"], "whole-file"),
    (["--shard", "--json"], "whole-file"),
    (["--canonical"], "canonical"),
    (["--canonical", "--shard"], "canonical"),
    (["--canonical", "--json"], "canonical"),
]


def reference_from(out: str, flags: list[str]) -> str:
    """Pull the reference out of whichever output shape the flags asked for."""
    if "--json" in flags:
        return str(json.loads(out)["reference"])
    return out.splitlines()[0]


@pytest.mark.parametrize("flags,which", REF_FLAG_MATRIX, ids=lambda v: "+".join(v) if v else "bare")
def test_ref_flag_matrix_addresses_the_right_thing(
    built: Path, capsys, flags: list[str], which: str
) -> None:
    """Every combination must report the address its flags imply.

    `--canonical` selects the reformat-invariant address; everything else the
    whole-file one. A flag being ignored on one branch is exactly how this function
    broke twice.
    """
    whole = run(capsys, "ref", str(built))[1].splitlines()[0]
    canonical = run(capsys, "ref", str(built), "--canonical")[1].splitlines()[0]
    assert whole != canonical

    code, out, _ = run(capsys, "ref", str(built), *flags)
    assert code == EXIT_OK
    expected = canonical if which == "canonical" else whole
    assert reference_from(out, flags) == expected, (flags, out)


@pytest.mark.parametrize("flags,which", REF_FLAG_MATRIX, ids=lambda v: "+".join(v) if v else "bare")
def test_ref_shard_is_honoured_wherever_it_is_accepted(
    built: Path, capsys, flags: list[str], which: str
) -> None:
    code, out, _ = run(capsys, "ref", str(built), *flags)
    assert code == EXIT_OK
    digest = reference_from(out, flags).removeprefix("chiro://md5:")
    shard = f"manifests/{digest[:2]}/{digest[2:]}.chiro.json"
    if "--json" in flags:
        assert json.loads(out)["shard_path"] == shard
    elif "--shard" in flags:
        assert out.splitlines()[1] == shard
    else:
        assert len(out.splitlines()) == 1  # nothing extra printed


@pytest.mark.parametrize("flags", [[], ["--canonical"]], ids=["whole-file", "canonical"])
def test_ref_check_agrees_with_ref_for_the_same_flags(
    built: Path, capsys, flags: list[str]
) -> None:
    """`--check` must compare the address the *same* flags would have produced."""
    own = run(capsys, "ref", str(built), *flags)[1].splitlines()[0]
    other_flags = ["--canonical"] if not flags else []
    other = run(capsys, "ref", str(built), *other_flags)[1].splitlines()[0]

    assert run(capsys, "ref", str(built), *flags, "--check", own)[0] == EXIT_OK
    assert run(capsys, "ref", str(built), *flags, "--check", other)[0] == EXIT_FINDING
    # ...and --json reports the same verdict on both paths.
    code, out, _ = run(capsys, "ref", str(built), *flags, "--check", own, "--json")
    assert code == EXIT_OK
    assert json.loads(out)["ok"] is True


@pytest.mark.parametrize("algorithm", ["md5", "sha256"])
def test_ref_algorithm_is_honoured_on_every_path(built: Path, capsys, algorithm: str) -> None:
    lengths = {"md5": 32, "sha256": 64}
    for flags in ([], ["--canonical"], ["--no-validate"]):
        if "--no-validate" in flags and algorithm == "sha256":
            pass  # still valid: the algorithm applies to the whole-file hash
        code, out, _ = run(capsys, "ref", str(built), "--algorithm", algorithm, *flags)
        assert code == EXIT_OK, flags
        reference = out.splitlines()[0]
        assert reference.startswith(f"chiro://{algorithm}:"), (flags, reference)
        assert len(reference.split(":")[-1]) == lengths[algorithm]


def test_ref_rejects_a_malformed_check_argument(built: Path, capsys) -> None:
    for bad in ("not-a-ref", "chiro://md5:xyz", "md5:" + "0" * 32, "chiro://" + "0" * 32):
        code, _, err = run(capsys, "ref", str(built), "--check", bad)
        assert code == EXIT_ERROR, bad
        assert "RefError" in err or "malformed" in err
