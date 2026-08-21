"""Time helpers.

All timestamps in NEXORA are stored and compared in **UTC**. Storing local
time is a classic source of bugs: it jumps backwards during daylight-saving
transitions, and it makes rows written by servers in different regions
incomparable.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """The current time as a timezone-aware UTC datetime.

    Note `datetime.now(timezone.utc)`, not the deprecated `utcnow()`, which
    returns a *naive* datetime that merely happens to hold UTC -- and then
    silently compares wrongly against aware datetimes.
    """
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Return `value` as timezone-aware UTC.

    Some database drivers return naive datetimes even for columns declared
    with a timezone -- SQLite always does. Comparing a naive datetime with an
    aware one raises TypeError, so anything read from the database is
    normalised through here before use.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
