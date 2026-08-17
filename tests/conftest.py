"""Shared fixtures."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

MD5_HELLO = "b1946ac92492d2347c6235b4d2611184"  # md5(b"hello\n")
MD5_WORLD = "591785b794601e212b260e25925636fd"  # md5(b"world\n")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small directory tree with known content."""
    root = tmp_path / "data"
    (root / "sampleA").mkdir(parents=True)
    (root / "sampleB").mkdir(parents=True)
    (root / "sampleA" / "barcodes.tsv").write_bytes(b"hello\n")
    (root / "sampleA" / "matrix.mtx").write_bytes(b"world\n")
    (root / "sampleB" / "barcodes.tsv").write_bytes(b"hello\n")  # same content, other path
    return root


@pytest.fixture
def entries() -> list[dict[str, object]]:
    return [
        {"relpath": "sampleA/barcodes.tsv", "md5": MD5_HELLO, "size": 6},
        {"relpath": "sampleA/matrix.mtx", "md5": MD5_WORLD, "size": 6},
    ]


def have_dvc() -> bool:
    return shutil.which("dvc") is not None


requires_dvc = pytest.mark.skipif(not have_dvc(), reason="dvc executable not available")


def dvc_run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a command in *cwd*, with DVC's telemetry off.

    ``DVC_NO_ANALYTICS`` matters for more than politeness: without it every
    invocation may try to reach the network, which on a firewalled HPC node turns
    a 3-second test into a timeout.
    """
    env = {**os.environ, "DVC_NO_ANALYTICS": "1", "DVC_TEST": "1"}
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, env=env)


@pytest.fixture(scope="session")
def _dvc_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build one real DVC repository per session.

    ``dvc init`` plus ``dvc add`` costs ~6 seconds, so building it per test would
    dominate the suite. Each test gets a copy instead (see :func:`dvc_repo`), which
    keeps the tests independent without paying DVC's startup cost eighteen times.
    """
    if not have_dvc():
        pytest.skip("dvc executable not available")
    repo = tmp_path_factory.mktemp("dvc-template")
    dvc_run(repo, "git", "init", "-q", ".")
    dvc_run(repo, "dvc", "init", "-q", "--no-scm")

    data = repo / "data"
    (data / "sub").mkdir(parents=True)
    (data / "a.txt").write_bytes(b"hello\n")
    (data / "sub" / "b.txt").write_bytes(b"world\n")
    (data / "sub" / "binary.bin").write_bytes(bytes(range(256)) * 8)
    dvc_run(repo, "dvc", "add", "data")
    return repo


@pytest.fixture
def dvc_repo(_dvc_template: Path, tmp_path: Path) -> Path:
    """A writable copy of the session's real DVC repository.

    Uses the actual ``dvc`` CLI rather than a fake cache, because the entire point
    of the interop tests is to check our understanding of DVC's on-disk format
    against DVC itself.
    """
    repo = tmp_path / "repo"
    shutil.copytree(_dvc_template, repo, symlinks=True)
    return repo
