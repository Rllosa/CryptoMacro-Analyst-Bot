from __future__ import annotations

from typing import Any

# Key prefix hoisted at module level — never rebuilt per call
_KEY_PREFIX = "cooldown:"


class CooldownRegistry:
    """
    Redis-backed cooldown tracker per alert (type, dedup_key) pair.

    Survives service restarts — Redis TTL enforces the cooldown window
    even if the processor process is restarted mid-cooldown.

    Key scheme: cooldown:{alert_type}:{dedup_key}
    Value: "1" (presence is what matters, not the value)
    TTL: cooldown_minutes * 60 seconds (set at activation time)
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def is_active(self, alert_type: str, dedup_key: str) -> bool:
        """Return True if the cooldown key exists in Redis (alert is suppressed)."""
        key = _KEY_PREFIX + alert_type + ":" + dedup_key
        return await self._redis.exists(key) > 0

    async def activate(self, alert_type: str, dedup_key: str, minutes: int) -> None:
        """Set cooldown for (alert_type, dedup_key) with TTL = minutes * 60 seconds."""
        key = _KEY_PREFIX + alert_type + ":" + dedup_key
        await self._redis.setex(key, minutes * 60, "1")
