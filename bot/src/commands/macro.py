from __future__ import annotations

import asyncio
import json

import discord


async def setup(bot) -> None:
    guild = discord.Object(id=bot.settings.discord_server_id)

    @bot.tree.command(name="macro", description="Show macro indicators (VIX, DXY, SPX)", guild=guild)
    async def macro_command(interaction: discord.Interaction) -> None:
        if not bot._check_guild(interaction):
            await interaction.response.send_message("Unauthorized.", ephemeral=True)
            return

        await interaction.response.defer()

        vix_raw, dxy_raw, spx_raw = await asyncio.gather(
            bot.redis.get("macro:latest:vix"),
            bot.redis.get("macro:latest:dxy"),
            bot.redis.get("macro:latest:spx"),
        )

        embed = discord.Embed(title="Macro Indicators", color=0xF59E0B)

        for label, raw in (("VIX", vix_raw), ("DXY", dxy_raw), ("SPX", spx_raw)):
            if raw:
                data = json.loads(raw)
                price = data.get("price") or data.get("close") or data.get("value")
                embed.add_field(
                    name=label,
                    value=f"{price:.2f}" if price is not None else "N/A",
                    inline=True,
                )
            else:
                embed.add_field(name=label, value="No data", inline=True)

        await interaction.followup.send(embed=embed)
