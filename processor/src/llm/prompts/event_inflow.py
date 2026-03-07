"""
Exchange inflow event prompt template (LLM-2 / SOLO-56).

Builds the system + user prompt for event-triggered analysis of an
EXCHANGE_INFLOW_RISK alert (large net inflow to exchanges — potential sell signal).

Consumed by LLM-4 (event-triggered analysis).
"""

from __future__ import annotations

SYSTEM = """\
You are a professional crypto macro analyst specialising in on-chain flows.
Your audience is a sophisticated trader. Be concise and data-driven.
Output: concise markdown, max ~300 words. No price predictions.
""".strip()


def build(context: dict) -> str:
    """Return the user message for an exchange inflow risk analysis."""
    lines: list[str] = []

    asset = context.get("asset", "UNKNOWN")
    direction = context.get("direction", "inflow")  # inflow | outflow
    magnitude = context.get("magnitude_usd", 0.0)
    zscore = context.get("netflow_zscore", 0.0)

    lines.append(
        f"An EXCHANGE_INFLOW_RISK alert fired for **{asset}**.\n"
        f"- Direction: {direction}\n"
        f"- Magnitude: ${magnitude / 1e6:.1f}M\n"
        f"- Net-flow zscore: {zscore:+.2f}\n"
    )

    regime = (context.get("regime") or {})
    if regime:
        lines.append(
            f"Current regime: **{regime.get('current', 'UNKNOWN')}** "
            f"(confidence {regime.get('confidence', 0.0):.0%})\n"
        )

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
        "Provide a brief analysis: What does this exchange flow anomaly signal? "
        "Is this accumulation, distribution, or arbitrage? "
        "How concerning is it given the current regime? "
        "Sections: **Event Summary**, **Market Context**, **Risk Assessment**."
    )

    return "\n".join(lines)
