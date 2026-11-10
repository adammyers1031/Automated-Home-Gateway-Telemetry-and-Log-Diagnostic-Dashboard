"""UTC timestamp helpers shared by the API, simulator and database layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc
ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(UTC)


def to_iso(value: datetime | str) -> str:
    """Render a datetime (or ISO string) as ``YYYY-MM-DDTHH:MM:SSZ``.

    Storing one canonical format keeps SQLite string comparisons
    lexicographically correct across the whole schema.
    """
    if isinstance(value, str):
        value = parse_iso(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime(ISO_FORMAT)


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string, accepting a trailing ``Z``."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def iso_minutes_ago(minutes: int) -> str:
    """ISO timestamp for ``minutes`` before now, for SQL window filters."""
    return to_iso(utcnow() - timedelta(minutes=minutes))
