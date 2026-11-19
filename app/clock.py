"""The one clock the order rules read, so tests can fix the time.

Call it as `clock.utcnow()`; importing the function itself would bypass a patch.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
