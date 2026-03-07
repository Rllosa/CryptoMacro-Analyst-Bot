from __future__ import annotations

import discord


async def setup(bot) -> None:
    guild = discord.Object(id=bot.settings.discord_server_id)

    @bot.tree.command(name="brief", description="Generate a market brief now", guild=guild)
    async def brief_command(interaction: discord.Interaction) -> None:
        if not bot._check_guild(interaction):
            await interaction.response.send_message("Unauthorized.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await bot.nc.publish("briefs.request", b"{}")
        await interaction.followup.send(
            "Brief requested — will appear in #daily-brief in ~30 seconds.",
            ephemeral=True,
        )
