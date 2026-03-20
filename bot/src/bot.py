from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import discord
from discord.ext import commands

from config import BotSettings
from embeds import format_alert_embed, format_event_analysis_embed
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
        from commands.brief import setup as setup_brief
        from commands.macro import setup as setup_macro
        from commands.regime import setup as setup_regime
        from commands.status import setup as setup_status
        from commands.stubs import setup as setup_stubs

        await setup_status(self)
        await setup_alerts(self)
        await setup_regime(self)
        await setup_macro(self)
        await setup_stubs(self)
        await setup_brief(self)

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

    async def start_ops_listener(self) -> None:
        try:
            js = self.nc.jetstream()
            sub = await js.subscribe(
                "ops.health",
                durable="discord-ops-health-consumer",
                stream="OPS_HEALTH",
            )
            logger.info("NATS ops listener started (durable=discord-ops-health-consumer)")
            async for msg in sub.messages:
                await self._on_ops_health(msg)
        except Exception as e:
            logger.warning(
                "NATS ops listener DEGRADED: %s — bot stays online, slash commands work", e
            )

    async def _on_ops_health(self, msg) -> None:
        try:
            payload = json.loads(msg.data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Invalid ops.health message encoding: %s", e)
            await msg.ack()
            return

        try:
            await msg.ack()
            channel = self._get_channel("system_health")
            if channel is None:
                logger.warning("discord_channel_system_health not configured")
                return
            embed = self._build_ops_embed(payload)
            await channel.send(embed=embed)
        except Exception as e:
            logger.error("Error posting ops health event: %s", e)

    def _build_ops_embed(self, payload: dict) -> discord.Embed:
        _STATUS_COLORS = {
            "HEALTHY": 0x22C55E,   # green — recovery
            "DEGRADED": 0xF97316,  # orange
            "DOWN": 0xEF4444,      # red
        }
        status = payload.get("status", "DEGRADED")
        color = _STATUS_COLORS.get(status, 0xA3A3A3)
        component = payload.get("component", "unknown")
        reason = payload.get("reason", "")
        timestamp = (payload.get("timestamp") or "")[:16].replace("T", " ")

        icon = {"DOWN": "🔴", "DEGRADED": "🟠", "HEALTHY": "🟢"}.get(status, "⚪")
        title = f"{icon} {component} → {status}"
        embed = discord.Embed(title=title, color=color)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"{timestamp} UTC")
        return embed

    async def start_brief_listener(self) -> None:
        try:
            js = self.nc.jetstream()
            sub = await js.subscribe(
                "reports.daily_brief",
                durable="discord-daily-brief-consumer",
                stream="DAILY_BRIEF",
            )
            logger.info("NATS brief listener started (durable=discord-daily-brief-consumer)")
            async for msg in sub.messages:
                await self._on_brief(msg)
        except Exception as e:
            logger.warning(
                "NATS brief listener DEGRADED: %s — bot stays online, slash commands work", e
            )

    async def _on_brief(self, msg) -> None:
        try:
            payload = json.loads(msg.data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Invalid brief message encoding: %s", e)
            await msg.ack()
            return

        try:
            await msg.ack()
            channel = self._get_channel("daily_brief")
            if channel is None:
                logger.warning("discord_channel_daily_brief not configured")
                return
            embed = self._build_brief_embed(payload)
            await channel.send(embed=embed)
        except Exception as e:
            logger.error("Error posting daily brief: %s", e)

    def _build_brief_embed(self, payload: dict) -> discord.Embed:
        _REGIME_COLORS = {
            "RISK_ON_TREND": 0x22C55E,    # green
            "RISK_OFF_STRESS": 0xEF4444,  # red
            "CHOP_RANGE": 0xA3A3A3,       # grey
            "VOL_EXPANSION": 0xF97316,    # orange
            "DELEVERAGING": 0xDC2626,     # dark red
        }
        regime_summary = payload.get("regime_summary") or {}
        current_regime = regime_summary.get("current_regime", "CHOP_RANGE")
        color = _REGIME_COLORS.get(current_regime, 0xA3A3A3)

        generated_at = payload.get("generated_at", "")[:16].replace("T", " ")
        title = f"Daily Brief — {generated_at} UTC"

        embed = discord.Embed(
            title=title,
            description=(regime_summary.get("analysis") or "")[:4000],
            color=color,
        )

        key_insights = payload.get("key_insights") or []
        if key_insights:
            embed.add_field(
                name="Key Insights",
                value="\n".join(f"• {i}" for i in key_insights),
                inline=False,
            )

        watch_list = payload.get("watch_list") or []
        if watch_list:
            embed.add_field(
                name="Watch List",
                value="\n".join(f"• {w}" for w in watch_list),
                inline=False,
            )

        llm_meta = payload.get("llm_metadata") or {}
        embed.set_footer(
            text=f"{llm_meta.get('model', 'claude')} · {llm_meta.get('tokens_used', 0):,} tokens"
        )

        return embed

    async def start_event_analysis_listener(self) -> None:
        try:
            js = self.nc.jetstream()
            sub = await js.subscribe(
                "events.analysis",
                durable="discord-event-analysis-consumer",
                stream="EVENT_ANALYSIS",
            )
            logger.info("NATS event analysis listener started (durable=discord-event-analysis-consumer)")
            async for msg in sub.messages:
                await self._on_event_analysis(msg)
        except Exception as e:
            logger.warning(
                "NATS event analysis listener DEGRADED: %s — bot stays online, slash commands work", e
            )

    async def _on_event_analysis(self, msg) -> None:
        try:
            payload = json.loads(msg.data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Invalid event analysis message encoding: %s", e)
            await msg.ack()
            return

        try:
            await msg.ack()
            channel = self._get_channel("event_analysis")
            if channel is None:
                logger.warning("discord_channel_event_analysis not configured")
                return
            embed = format_event_analysis_embed(payload)
            await channel.send(embed=embed)
        except Exception as e:
            logger.error("Error posting event analysis: %s", e)

    def request_shutdown(self) -> None:
        self._shutdown_event.set()
        asyncio.create_task(self.close())
