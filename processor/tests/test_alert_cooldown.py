"""
Unit tests for CooldownRegistry (alerts/cooldown.py).

All Redis I/O is replaced with AsyncMock — zero network calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from alerts.cooldown import CooldownRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PREFIX = "cooldown:"


def _make_registry() -> tuple[CooldownRegistry, AsyncMock]:
    redis = AsyncMock()
    return CooldownRegistry(redis), redis


# ---------------------------------------------------------------------------
# is_active tests
# ---------------------------------------------------------------------------


def test_is_active_returns_false_when_key_absent() -> None:
    registry, redis = _make_registry()
    redis.exists.return_value = 0

    result = asyncio.run(registry.is_active("VOL_EXPANSION", "BTCUSDT:up"))

    assert result is False
    redis.exists.assert_called_once_with("cooldown:VOL_EXPANSION:BTCUSDT:up")


def test_is_active_returns_true_when_key_present() -> None:
    registry, redis = _make_registry()
    redis.exists.return_value = 1

    result = asyncio.run(registry.is_active("VOL_EXPANSION", "BTCUSDT:up"))

    assert result is True


def test_is_active_different_types_use_different_keys() -> None:
    registry, redis = _make_registry()
    redis.exists.return_value = 0

    asyncio.run(registry.is_active("VOL_EXPANSION", "BTCUSDT:up"))
    asyncio.run(registry.is_active("BREAKOUT", "BTCUSDT:up"))

    calls = [str(c) for c in redis.exists.call_args_list]
    assert calls[0] != calls[1]


def test_is_active_different_dedup_keys_use_different_redis_keys() -> None:
    registry, redis = _make_registry()
    redis.exists.return_value = 0

    asyncio.run(registry.is_active("VOL_EXPANSION", "BTCUSDT:up"))
    asyncio.run(registry.is_active("VOL_EXPANSION", "ETHUSDT:up"))

    first_call_key = redis.exists.call_args_list[0][0][0]
    second_call_key = redis.exists.call_args_list[1][0][0]
    assert first_call_key != second_call_key


# ---------------------------------------------------------------------------
# activate tests
# ---------------------------------------------------------------------------


def test_activate_calls_setex_with_correct_key_and_ttl() -> None:
    registry, redis = _make_registry()

    asyncio.run(registry.activate("VOL_EXPANSION", "BTCUSDT:up", 30))

    redis.setex.assert_called_once_with("cooldown:VOL_EXPANSION:BTCUSDT:up", 30 * 60, "1")


def test_activate_ttl_scales_with_minutes() -> None:
    registry, redis = _make_registry()

    asyncio.run(registry.activate("REGIME_SHIFT", "_:regime", 120))

    _, ttl, _ = redis.setex.call_args[0]
    assert ttl == 120 * 60


def test_activate_market_wide_dedup_key() -> None:
    """Market-wide alerts use '_' as the symbol portion of the dedup key."""
    registry, redis = _make_registry()

    asyncio.run(registry.activate("REGIME_SHIFT", "_:regime", 60))

    key = redis.setex.call_args[0][0]
    assert key == "cooldown:REGIME_SHIFT:_:regime"


def test_key_prefix_constant_used_in_both_methods() -> None:
    """is_active and activate must construct identical key formats."""
    registry, redis = _make_registry()
    redis.exists.return_value = 0

    asyncio.run(registry.is_active("BREAKOUT", "SOLUSDT:down"))
    asyncio.run(registry.activate("BREAKOUT", "SOLUSDT:down", 45))

    is_active_key = redis.exists.call_args[0][0]
    activate_key = redis.setex.call_args[0][0]
    assert is_active_key == activate_key
