"""Check the documentation against the actual CLI.

Prose drifts from code silently, and a how-to that names a flag which does not
exist is worse than no how-to: it costs the reader trust in the parts that are
right. Five review passes over the implementation found forty-one defects; the
first review of the *documentation* found fabricated flags, fabricated command
signatures and a naming convention that contradicted the spec. So the docs get a
test too.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

DOCS = sorted((Path(__file__).parent.parent / "docs").glob("*.md"))
README = Path(__file__).parent.parent / "README.md"
ALL_MD = [*DOCS, README]

#: Documents that describe unbuilt design. They may name things that do not exist,
#: provided they say so — which :func:`test_aspirational_docs_say_so` checks.
ASPIRATIONAL = {"ideas.md", "manifest-registry-design.md", "resolution-and-config.md"}


def _cli(*args: str) -> str:
    return subprocess.run(
        [sys.executable, "-m", "chirograph.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def _valid_flags() -> dict[str, set[str]]:
    """Every subcommand's real flags, straight from argparse."""
    top = _cli("--help")
    subs = re.findall(r"^\s{4}(\w[\w-]*)\s{2,}", top, re.M)
    assert subs, "could not parse subcommands out of `chiro --help`"
    return {s: set(re.findall(r"(--[a-z][a-z0-9-]*)", _cli(s, "--help"))) for s in subs}


def _invocations(text: str) -> list[tuple[str, str]]:
    """(subcommand, remainder) for each `chiro <sub> …`, continuations joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [
        (m.group(1), m.group(2))
        for line in joined.splitlines()
        for m in re.finditer(r"\bchiro\s+([a-z][a-z-]*)\b(.*)", line)
    ]


VALID = _valid_flags()


@pytest.mark.parametrize("doc", ALL_MD, ids=lambda p: p.name)
def test_documented_flags_exist(doc: Path) -> None:
    """No document may name a flag its subcommand does not have."""
    bad = []
    for sub, rest in _invocations(doc.read_text()):
        if sub not in VALID:
            continue
        # An aspirational doc may propose a flag, but must be flagged as such.
        if doc.name in ASPIRATIONAL:
            continue
        bad += [
            f"chiro {sub} {f}"
            for f in re.findall(r"(--[a-z][a-z0-9-]*)", rest)
            if f not in VALID[sub]
        ]
    assert not bad, f"{doc.name} names flags that do not exist: {sorted(set(bad))}"


@pytest.mark.parametrize("doc", ALL_MD, ids=lambda p: p.name)
def test_documented_subcommands_exist(doc: Path) -> None:
    """Catch a renamed or imagined subcommand."""
    known = set(VALID) | {"--help", "--version"}
    used = {
        m.group(1)
        for m in re.finditer(r"\bchiro\s+([a-z][a-z-]{2,})\b", doc.read_text())
        if m.group(1) not in {"format", "manifests", "json", "tools", "ref"}
    }
    unknown = {u for u in used if u not in known} - {"write", "capture"}
    # Only complain about things that look like subcommands in a command position.
    unknown = {u for u in unknown if re.search(rf"^\s*\$?\s*chiro\s+{u}\b", doc.read_text(), re.M)}
    assert not unknown, f"{doc.name} uses unknown subcommands: {sorted(unknown)}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_archive_urls_use_schemes(doc: Path) -> None:
    """A source-url for an archive with a backend must use its scheme.

    Using the underlying FTP path defeats the point of the plugin and breaks when
    the archive reorganises its directory layout -- GEO's ``GSE111nnn`` sharding
    being exactly the sort of thing that changes.
    """
    raw = re.findall(r"--source-url\s+'([^']+)'", doc.read_text())
    offenders = [u for u in raw if "ftp.ncbi.nlm.nih.gov/geo" in u or "zenodo.org/api" in u]
    assert not offenders, (
        f"{doc.name} uses a raw archive URL where a scheme exists "
        f"(geo:// zenodo:// ega:// bia://): {offenders}"
    )


@pytest.mark.parametrize("doc", ALL_MD, ids=lambda p: p.name)
def test_manifest_outputs_are_not_accession_named(doc: Path) -> None:
    """Spec 1.1: manifests are named after their own digest.

    An example that writes ``GSE000000.chiro.json`` teaches the convention the
    spec replaced -- and it is the convention under which a second observation of
    one accession silently overwrites the first.
    """
    text = doc.read_text()
    offenders = [
        m.group(1)
        for m in re.finditer(r"-o\s+\S*?([A-Za-z][\w.-]*\.chiro\.json)", text)
        if not re.match(r"^(tmp|<digest>|\$D)", m.group(1))
    ]
    # The deposit copy is the documented exception: a human browses that directory.
    allowed = {"GSE000000.chiro.json"} if doc.name == "howto-deposit-data.md" else set()
    offenders = [o for o in offenders if o not in allowed]
    assert not offenders, (
        f"{doc.name} writes accession-named manifests: {sorted(set(offenders))}; "
        "spec 1.1 wants <digest>.chiro.json"
    )


@pytest.mark.parametrize("doc", sorted(ASPIRATIONAL))
def test_aspirational_docs_say_so(doc: str) -> None:
    """A doc describing unbuilt design must declare it in the first 40 lines."""
    text = (Path(__file__).parent.parent / "docs" / doc).read_text()
    head = "\n".join(text.splitlines()[:40]).lower()
    assert any(w in head for w in ("aspirational", "not built", "deferred", "design")), (
        f"{doc} describes unbuilt behaviour without saying so up front"
    )
