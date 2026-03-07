"""
Liquidation event prompt template (LLM-2 / SOLO-56).

Builds the system + user prompt for event-triggered analysis of a
DELEVERAGING_EVENT alert (large liquidation cascade).

Consumed by LLM-4 (event-triggered analysis).
"""

from __future__ import annotations

SYSTEM = """\
You are a professional crypto macro analyst specialising in derivatives markets.
Your audience is a sophisticated trader. Be concise and data-driven.
Output: concise markdown, max ~300 words. No price predictions.
""".strip()


def build(context: dict) -> str:
    """Return the user message for a liquidation event analysis."""
    lines: list[str] = []

    # Event-specific fields injected by the caller alongside ContextBuilder output
    asset = context.get("asset", "UNKNOWN")
    liq_usd = context.get("liquidations_1h_usd", 0.0)
    oi_drop = context.get("oi_drop_1h", 0.0)
    funding_z = context.get("funding_zscore", 0.0)

    lines.append(
        f"A DELEVERAGING_EVENT alert fired for **{asset}**.\n"
        f"- Liquidations (1h): ${liq_usd / 1e6:.1f}M\n"
        f"- OI drop (1h): {oi_drop:.2%}\n"
        f"- Funding rate zscore: {funding_z:+.2f}\n"
    )

    # Regime context
    regime = (context.get("regime") or {})
    if regime:
        lines.append(
            f"Current regime: **{regime.get('current', 'UNKNOWN')}** "
            f"(confidence {regime.get('confidence', 0.0):.0%})\n"
        )

    # Cross-asset stress
    cross = context.get("cross_features") or {}
    if cross:
        parts = []
        if cross.get("macro_stress") is not None:
            parts.append(f"macro_stress={cross['macro_stress']:.1f}/100")
        if cross.get("vix") is not None:
            parts.append(f"VIX={cross['vix']:.1f}")
        if parts:
            lines.append("Macro context: " + ", ".join(parts) + "\n")

    lines.append(
        "Provide a brief analysis: What does this liquidation cascade signal? "
        "Is this a local flush or the start of broader deleveraging? "
        "What should the trader watch for next? "
        "Sections: **Event Summary**, **Market Context**, **What to Watch**."
    )

    return "\n".join(lines)
