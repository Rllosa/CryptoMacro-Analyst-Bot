from __future__ import annotations

import discord

_HIGH = 0xEF4444
_MEDIUM = 0xF59E0B
_LOW = 0x6B7280

_SEVERITY_COLORS = {
    "HIGH": _HIGH,
    "MEDIUM": _MEDIUM,
    "LOW": _LOW,
}


def format_alert_embed(payload: dict) -> discord.Embed:
    alert_type = payload.get("alert_type", "UNKNOWN")
    severity = payload.get("severity", "UNKNOWN")
    symbol = payload.get("symbol")

    color = _SEVERITY_COLORS.get(severity, _LOW)

    title = f"{severity} | {alert_type}"
    if symbol is not None:
        title += f" ({symbol})"

    embed = discord.Embed(title=title, color=color)

    conditions = payload.get("conditions") or {}
    tv = conditions.get("trigger_values") or {}

    _add_type_fields(embed, alert_type, tv)

    message = payload.get("message", "")
    if message:
        embed.add_field(name="Interpretation", value=message, inline=False)

    time_str = payload.get("time", "")
    cooldown_str = payload.get("cooldown_until", "")
    embed.set_footer(text=f"Cooldown until {cooldown_str} | {time_str}")

    return embed


def _add_type_fields(embed: discord.Embed, alert_type: str, tv: dict) -> None:
    if alert_type == "VOL_EXPANSION":
        embed.add_field(name="rv_1h_zscore", value=str(tv.get("rv_1h_zscore", "N/A")), inline=True)
        embed.add_field(name="volume_zscore", value=str(tv.get("volume_zscore", "N/A")), inline=True)
        embed.add_field(name="Direction", value=str(tv.get("direction", "N/A")), inline=True)

    elif alert_type == "LEADERSHIP_ROTATION":
        embed.add_field(name="Pair", value=str(tv.get("pair", "N/A")), inline=True)
        embed.add_field(name="rs_zscore", value=str(tv.get("rs_zscore", "N/A")), inline=True)
        embed.add_field(name="Direction", value=str(tv.get("direction", "N/A")), inline=True)

    elif alert_type == "BREAKOUT":
        embed.add_field(name="Direction", value=str(tv.get("direction", "N/A")), inline=True)
        embed.add_field(name="Level", value=str(tv.get("level", "N/A")), inline=True)
        embed.add_field(name="volume_zscore", value=str(tv.get("volume_zscore", "N/A")), inline=True)

    elif alert_type == "REGIME_SHIFT":
        old_regime = tv.get("old_regime", "N/A")
        new_regime = tv.get("new_regime", tv.get("direction", "INDETERMINATE"))
        confidence = tv.get("confidence")
        conf_str = f"{confidence:.0%}" if isinstance(confidence, float) else "INDETERMINATE"
        embed.add_field(name="Transition", value=f"{old_regime} → {new_regime}", inline=True)
        embed.add_field(name="Confidence", value=conf_str, inline=True)

    elif alert_type == "CORRELATION_BREAK":
        embed.add_field(name="Pair", value=str(tv.get("pair", "N/A")), inline=True)
        embed.add_field(name="Delta", value=str(tv.get("delta", "N/A")), inline=True)
        embed.add_field(name="Current / Historical", value=f"{tv.get('current', 'N/A')} / {tv.get('historical', 'N/A')}", inline=True)

    elif alert_type == "CROWDED_LEVERAGE":
        embed.add_field(name="funding_zscore", value=str(tv.get("funding_zscore", "N/A")), inline=True)
        embed.add_field(name="oi_change_24h", value=str(tv.get("oi_change_24h", "N/A")), inline=True)

    elif alert_type == "DELEVERAGING_EVENT":
        liq = tv.get("liq_1h_usd")
        liq_str = f"${liq/1e6:.1f}M" if isinstance(liq, (int, float)) else "N/A"
        embed.add_field(name="liq_1h_usd", value=liq_str, inline=True)
        embed.add_field(name="oi_drop%", value=str(tv.get("oi_drop_pct", "N/A")), inline=True)
        embed.add_field(name="candle_atr_multiple", value=str(tv.get("candle_atr_multiple", "N/A")), inline=True)

    elif alert_type == "EXCHANGE_INFLOW_RISK":
        embed.add_field(name="inflow_zscore", value=str(tv.get("inflow_zscore", "N/A")), inline=True)
        embed.add_field(name="netflow_zscore", value=str(tv.get("netflow_zscore", "N/A")), inline=True)

    elif alert_type == "NETFLOW_SHIFT":
        embed.add_field(name="conditions_met", value=str(tv.get("conditions_met", "N/A")), inline=True)
        embed.add_field(name="Direction", value=str(tv.get("direction", "N/A")), inline=True)


def format_event_analysis_embed(payload: dict) -> discord.Embed:
    """Format a LLM-4 F-7 event analysis envelope as a Discord embed.

    Layout:
      Title:       ⚡ Event Analysis — {alert_type} ({symbol})
      Description: **{summary}**\\n\\n{interpretation}   (up to 4096 chars)
      Field:       Watch Next — bullet list
      Field:       Regime — current + confidence (inline)
      Footer:      model · tokens · generation_time | fire_time UTC
    """
    trigger = payload.get("trigger_alert") or {}
    alert_type = trigger.get("alert_type", "UNKNOWN")
    symbol = trigger.get("symbol")
    severity = trigger.get("severity", "HIGH")
    fire_time = (trigger.get("time") or "")[:16].replace("T", " ")

    analysis = payload.get("analysis") or {}
    summary = analysis.get("summary", "")
    interpretation = analysis.get("interpretation", "")
    watch_next = analysis.get("watch_next") or []

    context = payload.get("context") or {}
    regime = context.get("regime") or {}
    regime_name = regime.get("current") or "Unknown"
    confidence = regime.get("confidence")

    llm_meta = payload.get("llm_metadata") or {}

    color = _SEVERITY_COLORS.get(severity, _HIGH)

    title = f"⚡ Event Analysis — {alert_type}"
    if symbol:
        title += f" ({symbol})"

    description = f"**{summary}**\n\n{interpretation}"
    embed = discord.Embed(title=title, description=description[:4096], color=color)

    if watch_next:
        embed.add_field(
            name="Watch Next",
            value="\n".join(f"• {w}" for w in watch_next),
            inline=False,
        )

    regime_str = regime_name
    if isinstance(confidence, float):
        regime_str += f" ({confidence:.0%})"
    embed.add_field(name="Regime", value=regime_str, inline=True)

    model = llm_meta.get("model", "claude")
    tokens = llm_meta.get("tokens_used", 0)
    gen_ms = llm_meta.get("generation_time_ms", 0)
    embed.set_footer(text=f"{model} · {tokens:,} tokens · {gen_ms}ms | {fire_time} UTC")

    return embed
