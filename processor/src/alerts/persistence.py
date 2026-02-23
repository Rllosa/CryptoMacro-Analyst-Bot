from __future__ import annotations

from typing import Any

# Key prefix hoisted at module level — never rebuilt per call
_KEY_PREFIX = "persistence:"


class PersistenceTracker:
    """
    Redis-backed consecutive-cycle counter per alert key.

    Survives service restarts — Redis TTL expires the counter when the
    trigger condition has not been met for 2× the maximum persistence window
    (default 1200s = 20 min = 4 × 5-min cycles).

    Key scheme: persistence:{alert_key}
    Value: integer counter managed via INCR
    TTL: refreshed on every increment while the condition holds

    Mirrors the CooldownRegistry pattern — Redis-backed state is consistent
    across both persistence and cooldown, and both survive process restarts.
    """

    def __init__(self, redis: Any, ttl_secs: int = 1200) -> None:
        self._redis = redis
        self._ttl = ttl_secs

    async def record_met(self, key: str) -> int:
        """
        Increment the consecutive count for key and refresh the TTL.

        Returns the new count. Call when the trigger condition is met.
        The TTL is refreshed on every increment so the key does not expire
        between consecutive cycles while the condition holds.
        """
        full_key = _KEY_PREFIX + key
        count = await self._redis.incr(full_key)
        await self._redis.expire(full_key, self._ttl)
        return int(count)

    async def record_not_met(self, key: str) -> None:
        """
        Delete the persistence key, resetting the consecutive count to zero.

        Call when the trigger condition is NOT met, when the alert fires
        (reset after fire), or when the alert is suppressed by cooldown.
        """
        await self._redis.delete(_KEY_PREFIX + key)

    async def get(self, key: str) -> int:
        """Return the current consecutive count for key (0 if absent)."""
        val = await self._redis.get(_KEY_PREFIX + key)
        return int(val.decode()) if val is not None else 0
