from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot import CryptoMacroBot
from src.config import BotSettings


def _make_settings(**overrides) -> BotSettings:
    defaults: dict = dict(
        discord_bot_token="test_token",
        discord_server_id=123456789,
        discord_channel_alerts_high=111,
        discord_channel_alerts_all=222,
        discord_channel_daily_brief=333,
        discord_channel_regime_shifts=444,
        discord_channel_onchain=555,
        discord_channel_bot_commands=666,
        discord_channel_system_health=777,
        db_dsn="postgresql://test",
        redis_url="redis://localhost",
        nats_url="nats://localhost",
    )
    defaults.update(overrides)
    return BotSettings(_env_file=None, **defaults)


def _make_bot(**setting_overrides) -> CryptoMacroBot:
    settings = _make_settings(**setting_overrides)
    return CryptoMacroBot(
        settings=settings,
        pool=AsyncMock(),
        redis=AsyncMock(),
        nc=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_shutdown_before_start():
    """request_shutdown() sets the shutdown event; no errors raised."""
    bot = _make_bot()
    assert not bot._shutdown_event.is_set()
    bot.request_shutdown()
    assert bot._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_alert_routed_to_correct_channels():
    """VOL_EXPANSION HIGH → sent to alerts_all and alerts_high; ack called once."""
    bot = _make_bot()

    mock_all = AsyncMock()
    mock_high = AsyncMock()

    def _get_channel(cid):
        return {222: mock_all, 111: mock_high}.get(cid)

    bot.get_channel = MagicMock(side_effect=_get_channel)

    msg = AsyncMock()
    msg.data = json.dumps(
        {
            "alert_type": "VOL_EXPANSION",
            "severity": "HIGH",
            "symbol": "btc",
            "conditions": {"trigger_values": {}},
            "message": "test",
            "time": "2026-02-25T00:00:00Z",
            "cooldown_until": "2026-02-25T01:00:00Z",
        }
    ).encode()

    await bot._on_alert(msg)

    mock_all.send.assert_called_once()
    mock_high.send.assert_called_once()
    msg.ack.assert_called_once()


@pytest.mark.asyncio
async def test_unauthorized_server_rejected():
    """_check_guild() returns False when guild_id doesn't match settings."""
    bot = _make_bot()
    interaction = MagicMock()
    interaction.guild_id = 999999999  # not discord_server_id
    assert not bot._check_guild(interaction)


@pytest.mark.asyncio
async def test_missing_channel_no_crash():
    """get_channel returning None for all channels → _on_alert completes without error."""
    bot = _make_bot()
    bot.get_channel = MagicMock(return_value=None)

    msg = AsyncMock()
    msg.data = json.dumps(
        {
            "alert_type": "VOL_EXPANSION",
            "severity": "HIGH",
            "symbol": "btc",
            "conditions": {},
            "message": "test",
            "time": "2026-02-25T00:00:00Z",
            "cooldown_until": "2026-02-25T01:00:00Z",
        }
    ).encode()

    # Must not raise — logs a warning instead
    await bot._on_alert(msg)


@pytest.mark.asyncio
async def test_nats_failure_bot_stays_online(caplog):
    """NATS exception → DEGRADED logged, shutdown event not set."""
    bot = _make_bot()
    bot.nc.jetstream = MagicMock(side_effect=Exception("connection refused"))

    with caplog.at_level(logging.WARNING):
        await bot.start_nats_listener()

    assert "DEGRADED" in caplog.text
    assert not bot._shutdown_event.is_set()
