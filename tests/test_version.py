"""The version fallback must not drift from pyproject.

``version.py`` reports ``importlib.metadata.version("chirograph")`` when the
package is installed, and a hardcoded ``_FALLBACK`` when it is not (a source-tree
run). The fallback is a literal — nothing at import time can read pyproject — so
it silently went stale across the 0.1.0 -> 0.2.0 release. This test turns that
"remember to bump it" into a failure.
"""

from __future__ import annotations

import re
from pathlib import Path

from chirograph.version import _FALLBACK

_PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _pyproject_version() -> str:
    # Regex rather than tomllib so this runs on the 3.10 floor (tomllib is 3.11+)
    # without pulling in a toml parser.
    match = re.search(r'^version = "([^"]+)"', _PYPROJECT.read_text(), re.MULTILINE)
    assert match, 'could not find `version = "..."` in pyproject.toml'
    return match.group(1)


def test_fallback_matches_pyproject_version() -> None:
    assert _pyproject_version() == _FALLBACK, (
        f"version.py _FALLBACK ({_FALLBACK!r}) is out of sync with pyproject "
        f"({_pyproject_version()!r}); bump _FALLBACK on release."
    )


def test_fallback_is_a_release_number() -> None:
    # Guard against a placeholder like "0.0.0" or a dev suffix slipping in.
    assert re.fullmatch(r"\d+\.\d+\.\d+", _FALLBACK), f"{_FALLBACK!r} is not a plain release number"
