from __future__ import annotations

import asyncio
import json

import discord


async def setup(bot) -> None:
    guild = discord.Object(id=bot.settings.discord_server_id)

    @bot.tree.command(name="status", description="Show current system and market status", guild=guild)
    async def status_command(interaction: discord.Interaction) -> None:
        if not bot._check_guild(interaction):
            await interaction.response.send_message("Unauthorized.", ephemeral=True)
            return

        await interaction.response.defer()

        features_raw, regime_raw = await asyncio.gather(
            bot.redis.get("features:latest:btc"),
            bot.redis.get("regime:latest"),
        )

        embed = discord.Embed(title="System Status", color=0x3B82F6)

        if features_raw:
            f = json.loads(features_raw)
            rv = f.get("rv_1h_zscore")
            vol = f.get("volume_zscore")
            embed.add_field(name="BTC rv_1h_zscore", value=f"{rv:.2f}" if rv is not None else "N/A", inline=True)
            embed.add_field(name="BTC volume_zscore", value=f"{vol:.2f}" if vol is not None else "N/A", inline=True)
        else:
            embed.add_field(name="BTC Features", value="No data", inline=False)

        if regime_raw:
            r = json.loads(regime_raw)
            conf = r.get("confidence", 0)
            embed.add_field(name="Regime", value=r.get("regime", "N/A"), inline=True)
            embed.add_field(name="Confidence", value=f"{conf:.0%}", inline=True)
        else:
            embed.add_field(name="Regime", value="No data", inline=False)

        await interaction.followup.send(embed=embed)
