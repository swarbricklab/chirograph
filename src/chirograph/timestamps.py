"""ISO-8601 timestamp handling for ``created`` / ``retrieved``.

Policy (docs/decisions.md D-05): a timestamp must carry an explicit UTC offset.
A naive timestamp in a provenance record is worse than no timestamp — the whole
semantic content of an ``observed-download`` manifest is "these were the bytes at
*this instant*" (registry design §3), and "2026-07-01T09:15:00" is an instant
only if you already know which machine wrote it. Any explicit offset is accepted
on read and converted to UTC; output is always ``...Z``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .errors import SchemaError

__all__ = ["format_timestamp", "now_timestamp", "parse_timestamp"]


def now_timestamp() -> str:
    """Return the current time as a UTC ISO-8601 string, second resolution.

    Second resolution rather than microsecond: nothing acts on sub-second
    precision here, and the shorter form is what the spec's examples use.
    """
    return format_timestamp(datetime.now(timezone.utc))


def format_timestamp(moment: datetime) -> str:
    """Render *moment* as ``YYYY-MM-DDTHH:MM:SSZ``."""
    if moment.tzinfo is None:
        raise SchemaError("refusing to format a timezone-naive datetime as a UTC timestamp")
    return moment.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: object, *, field: str) -> datetime:
    """Parse an ISO-8601 timestamp, requiring an explicit offset."""
    if not isinstance(value, str):
        raise SchemaError(f"{field} must be a string, got {type(value).__name__}")
    text = value.strip()
    if not text:
        raise SchemaError(f"{field} must not be empty")
    # datetime.fromisoformat only learned to accept a trailing 'Z' in 3.11.
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise SchemaError(f"{field} {value!r} is not a valid ISO-8601 timestamp: {exc}") from exc
    if parsed.tzinfo is None:
        raise SchemaError(
            f"{field} {value!r} has no timezone offset. Provenance timestamps must be "
            f"unambiguous instants — append 'Z' for UTC."
        )
    return parsed


def canonical_timestamp(value: object, *, field: str) -> str:
    """Parse *value* and re-render it in the canonical ``...Z`` form."""
    return format_timestamp(parse_timestamp(value, field=field))
