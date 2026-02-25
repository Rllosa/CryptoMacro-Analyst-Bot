from __future__ import annotations

import discord


async def setup(bot) -> None:
    guild = discord.Object(id=bot.settings.discord_server_id)

    @bot.tree.command(name="alerts", description="Show recent alerts (last 7 days)", guild=guild)
    async def alerts_command(
        interaction: discord.Interaction,
        count: int = 5,
    ) -> None:
        if not bot._check_guild(interaction):
            await interaction.response.send_message("Unauthorized.", ephemeral=True)
            return

        await interaction.response.defer()

        count = max(1, min(count, 20))

        # TODO(DEL-4): Replace direct DB access with /api/alerts/recent endpoint
        async with bot.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT alert_type, symbol, severity, time
                    FROM alerts
                    WHERE time > NOW() - INTERVAL '7 days'
                    ORDER BY time DESC
                    LIMIT %s
                    """,
                    (count,),
                )
                rows = await cur.fetchall()

        if not rows:
            await interaction.followup.send("No alerts in the last 7 days.")
            return

        embed = discord.Embed(title=f"Last {len(rows)} Alert(s)", color=0x6B7280)
        for alert_type, symbol, severity, ts in rows:
            sym = f" ({symbol})" if symbol else ""
            embed.add_field(
                name=f"{severity} | {alert_type}{sym}",
                value=f"<t:{int(ts.timestamp())}:R>",
                inline=False,
            )

        await interaction.followup.send(embed=embed)
