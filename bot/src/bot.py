from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import discord
from discord.ext import commands

from config import BotSettings
from embeds import format_alert_embed
from routing import AlertRouter

logger = logging.getLogger(__name__)


class CryptoMacroBot(commands.Bot):
    def __init__(self, settings: BotSettings, pool, redis, nc) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.pool = pool
        self.redis = redis
        self.nc = nc
        self._router = AlertRouter()
        self._shutdown_event = asyncio.Event()

    async def setup_hook(self) -> None:
        from commands.alerts import setup as setup_alerts
        from commands.macro import setup as setup_macro
        from commands.regime import setup as setup_regime
        from commands.status import setup as setup_status
        from commands.stubs import setup as setup_stubs

        await setup_status(self)
        await setup_alerts(self)
        await setup_regime(self)
        await setup_macro(self)
        await setup_stubs(self)

    async def on_ready(self) -> None:
        guild = discord.Object(id=self.settings.discord_server_id)
        synced = await self.tree.sync(guild=guild)
        logger.info("Synced %d slash commands to guild %d", len(synced), self.settings.discord_server_id)

    def _check_guild(self, interaction: discord.Interaction) -> bool:
        return interaction.guild_id == self.settings.discord_server_id

    def _get_channel(self, name: str) -> Optional[discord.TextChannel]:
        channel_id = getattr(self.settings, f"discord_channel_{name}", None)
        if channel_id is None:
            return None
        return self.get_channel(channel_id)  # type: ignore[return-value]

    async def start_nats_listener(self) -> None:
        try:
            js = self.nc.jetstream()
            sub = await js.subscribe(
                "alerts.fired",
                durable="discord-alerts-consumer",
                stream="ALERTS",
            )
            logger.info("NATS JetStream listener started (durable=discord-alerts-consumer)")
            async for msg in sub.messages:
                await self._on_alert(msg)
        except Exception as e:
            logger.warning(
                "NATS listener DEGRADED: %s — bot stays online, slash commands work", e
            )

    async def _on_alert(self, msg) -> None:
        try:
            payload = json.loads(msg.data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Invalid alert message encoding: %s", e)
            await msg.ack()
            return

        try:
            await msg.ack()
            channel_names = self._router.get_channels(
                payload.get("alert_type", ""),
                payload.get("severity", ""),
            )
            embed = self._build_embed(payload)
            channels = [c for name in channel_names if (c := self._get_channel(name)) is not None]
            if channels:
                await asyncio.gather(*[ch.send(embed=embed) for ch in channels])
            else:
                logger.warning("No Discord channels resolved for alert: %s", payload.get("alert_type"))
        except Exception as e:
            logger.error("Error processing alert: %s", e)

    def _build_embed(self, payload: dict) -> discord.Embed:
        return format_alert_embed(payload)

    def request_shutdown(self) -> None:
        self._shutdown_event.set()
        asyncio.create_task(self.close())
