from __future__ import annotations


class PersistenceTracker:
    """
    In-memory consecutive-cycle counter per alert key.

    Resets on service restart — acceptable because persistence is a 10-minute
    safety gate (2 × 5m cycles), not a delivery guarantee. The worst case on
    restart is N extra cycles before re-fire, which is preferable to the
    complexity of Redis-backed state for a short-horizon counter.

    All methods are synchronous (no I/O).
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def record_met(self, key: str) -> int:
        """
        Increment the consecutive count for key.

        Returns the new count. Call this when the trigger condition is met.
        """
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def record_not_met(self, key: str) -> None:
        """
        Reset the consecutive count for key to zero.

        Call this when the trigger condition is NOT met, when the alert fires
        (reset after fire), or when the alert is suppressed by cooldown.
        """
        self._counts[key] = 0

    def get(self, key: str) -> int:
        """Return the current consecutive count for key (0 if never seen)."""
        return self._counts.get(key, 0)
