from __future__ import annotations

import json

import discord


async def setup(bot) -> None:
    guild = discord.Object(id=bot.settings.discord_server_id)

    @bot.tree.command(name="regime", description="Show current market regime", guild=guild)
    async def regime_command(interaction: discord.Interaction) -> None:
        if not bot._check_guild(interaction):
            await interaction.response.send_message("Unauthorized.", ephemeral=True)
            return

        await interaction.response.defer()

        regime_raw = await bot.redis.get("regime:latest")

        if not regime_raw:
            await interaction.followup.send("No regime data available.")
            return

        r = json.loads(regime_raw)
        conf = r.get("confidence", 0)

        embed = discord.Embed(title="Current Market Regime", color=0x8B5CF6)
        embed.add_field(name="Regime", value=r.get("regime", "N/A"), inline=True)
        embed.add_field(name="Confidence", value=f"{conf:.0%}", inline=True)

        factors = r.get("factors", {})
        if factors:
            factor_str = "\n".join(f"• {k}: {v}" for k, v in list(factors.items())[:5])
            embed.add_field(name="Factors", value=factor_str, inline=False)

        await interaction.followup.send(embed=embed)
