"""Access to the published JSON Schema, and validation against it.

The schema exists for editors, CI, and other languages — anything that wants to
check a manifest's *shape* without a chirograph implementation. It deliberately
cannot check the things that actually matter:

* ``size == sum(files[].size)`` and ``nfiles == len(files)``
* ``relpath`` uniqueness
* ``body_hash`` being the hash of the canonicalised ``files`` array

JSON Schema has no arithmetic, no cross-field aggregation, and no hashing. So
schema validity is a necessary condition, never a sufficient one — which is why
:func:`chirograph.load` does its own checking rather than delegating here, and
why ``jsonschema`` is an optional extra rather than a dependency.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = ["SCHEMA_FILENAME", "schema_document", "schema_path", "schema_text", "validate_document"]

SCHEMA_FILENAME = "chirograph-1.0.schema.json"


def schema_path() -> Path:
    """Filesystem path to the published JSON Schema."""
    return Path(__file__).resolve().parent / "schemas" / SCHEMA_FILENAME


def schema_text() -> str:
    """The JSON Schema as text."""
    return schema_path().read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def schema_document() -> Mapping[str, Any]:
    """The JSON Schema as a parsed document."""
    result: Mapping[str, Any] = json.loads(schema_text())
    return result


def validate_document(document: Mapping[str, Any]) -> list[str]:
    """Validate *document* against the JSON Schema, returning error strings.

    An empty list means "shape is fine", not "manifest is fine" — see the module
    docstring. Requires the ``schema`` extra.
    """
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RuntimeError(
            "JSON Schema validation needs the `jsonschema` package, which chirograph does "
            "not require by default. Install it with `pip install 'chirograph[schema]'`. "
            "Note that `chiro validate` already checks everything the schema can, plus the "
            "integrity constraints the schema cannot express."
        ) from exc

    validator_cls = jsonschema.validators.validator_for(schema_document())
    validator = validator_cls(schema_document())
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    ]
