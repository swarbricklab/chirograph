"""Version reporting, and the ``generator`` string written into manifests."""

from __future__ import annotations

__all__ = ["GENERATOR", "__version__", "version"]

_FALLBACK = "0.1.0"


def _detect() -> str:
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _metadata_version
    except ImportError:  # pragma: no cover - Python < 3.8
        return _FALLBACK
    try:
        return _metadata_version("chirograph")
    except PackageNotFoundError:  # pragma: no cover - running from a source tree
        return _FALLBACK


__version__: str = _detect()

#: Value written to a manifest's ``generator`` field (spec §3.1). Tool *and*
#: version, because the field's only job is to let us reconstruct which
#: implementation wrote a manifest when one turns out to be wrong.
GENERATOR: str = f"chirograph {__version__}"


def version() -> str:
    return __version__
