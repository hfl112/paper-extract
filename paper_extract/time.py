from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    """Current UTC time as ISO 8601 with a trailing Z, second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp_from_iso(iso: str) -> str:
    """Compact timestamp for filenames, e.g. 2026-07-05T19:42:00Z -> 20260705T194200Z."""
    return iso.replace("-", "").replace(":", "")


def timestamp() -> str:
    """Compact UTC timestamp for filenames."""
    return stamp_from_iso(utc_now())
