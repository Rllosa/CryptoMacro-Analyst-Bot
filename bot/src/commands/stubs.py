from __future__ import annotations

import discord

_STUB_MSG = "Not yet available (Phase 2+)"


async def setup(bot) -> None:
    guild = discord.Object(id=bot.settings.discord_server_id)

    @bot.tree.command(name="funding", description="Funding rates summary", guild=guild)
    async def funding(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(_STUB_MSG, ephemeral=True)

    @bot.tree.command(name="brief", description="Latest daily brief", guild=guild)
    async def brief(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(_STUB_MSG, ephemeral=True)

    @bot.tree.command(name="flows", description="On-chain exchange flows", guild=guild)
    async def flows(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(_STUB_MSG, ephemeral=True)

    @bot.tree.command(name="eval", description="Alert evaluation metrics", guild=guild)
    async def eval_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(_STUB_MSG, ephemeral=True)

    @bot.tree.command(name="ask", description="Ask the analyst", guild=guild)
    async def ask(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(_STUB_MSG, ephemeral=True)
