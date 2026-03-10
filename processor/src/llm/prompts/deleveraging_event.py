"""
Deleveraging event prompt template (LLM-4 / SOLO-58).

Builds the system + user prompt for Claude event analysis triggered by
a DELEVERAGING_EVENT alert. Claude is asked to output structured JSON only.

Consumed by llm.event_analyzer.EventAnalyzer.
"""

from __future__ import annotations

SYSTEM = """\
You are a professional crypto macro analyst specialising in derivatives and liquidation cascades.
Your audience is a sophisticated trader who needs rapid, actionable clarity on cascade events.

Tone: direct, numbers-first, no hedging. Include specific USD amounts and percentages where available.
Never make promises about future prices.

IMPORTANT: Respond ONLY with valid JSON — no markdown fences, no preamble, no trailing text.
""".strip()


def build(
    alert_type: str,
    symbol: str | None,
    trigger_values: dict,
    context: dict,
) -> str:
    """Return the user message string for event analysis."""
    lines: list[str] = [
        f"A {alert_type} alert just fired on {symbol or 'MARKET'}. Provide rapid event analysis.\n"
    ]

    # --- Trigger data ---
    lines.append("**Trigger Conditions:**")
    liq = trigger_values.get("liquidations_1h_usd")
    oi_drop = trigger_values.get("oi_drop_1h")
    atr_ratio = trigger_values.get("atr_ratio")
    if liq is not None:
        lines.append(f"- Liquidations (1h): ${liq / 1e6:.1f}M")
    if oi_drop is not None:
        lines.append(f"- OI drop flag (1h): {oi_drop:.0f} (1 = OI fell ≥5%)")
    if atr_ratio is not None:
        lines.append(f"- Candle/ATR ratio: {atr_ratio:.2f}×")
    lines.append("")

    # --- Regime ---
    regime = context.get("regime") or {}
    if regime:
        lines.append(
            f"**Market Regime:** {regime.get('current', 'UNKNOWN')} "
            f"(confidence {regime.get('confidence', 0.0):.0%})"
        )
        lines.append("")

    # --- Per-asset features ---
    features = context.get("features") or {}
    if features:
        lines.append("**Per-Asset Signals:**")
        for asset, feat in features.items():
            r1h = feat.get("r_1h", 0.0)
            rv_z = feat.get("rv_4h_zscore", 0.0)
            lines.append(f"- {asset}: r_1h={r1h:+.2%}, rv_zscore={rv_z:+.2f}")
        lines.append("")

    # --- Derivatives context ---
    derivatives = context.get("derivatives") or {}
    if derivatives:
        lines.append("**Derivatives State:**")
        for asset, deriv in derivatives.items():
            funding_z = deriv.get("funding_zscore")
            liq_val = deriv.get("liquidations_1h_usd")
            oi_d = deriv.get("oi_drop_1h")
            parts = []
            if funding_z is not None:
                parts.append(f"funding_z={funding_z:+.2f}")
            if liq_val is not None:
                parts.append(f"liq=${liq_val / 1e6:.1f}M")
            if oi_d is not None:
                parts.append(f"oi_drop={oi_d:.0f}")
            if parts:
                lines.append(f"- {asset}: {', '.join(parts)}")
        lines.append("")

    # --- Recent alerts ---
    recent_alerts = context.get("recent_alerts") or []
    if recent_alerts:
        lines.append("**Recent Alerts (last 6h):**")
        for alert in recent_alerts[:6]:
            lines.append(
                f"- [{alert.get('severity', '?')}] {alert.get('type', '?')} "
                f"on {alert.get('symbol', '?')} @ {alert.get('fired_at', 'N/A')}"
            )
        lines.append("")

    lines.append(
        'Based on the above, respond with a JSON object containing EXACTLY these keys:\n'
        '{\n'
        '  "summary": "1-2 sentences: what happened and the scale of the event.",\n'
        '  "interpretation": "2-4 sentences: why this matters, what drove the cascade, '
        'and what it signals for positioning. Include specific numbers.",\n'
        '  "watch_next": ["level/condition 1", "level/condition 2", "level/condition 3"],\n'
        '  "similar_historical_events": ["optional ref 1"]\n'
        '}\n\n'
        'Rules:\n'
        '- summary: 1-2 sentences, specific USD amount and % moves.\n'
        '- interpretation: 2-4 sentences, reference specific numbers, no generic statements.\n'
        '- watch_next: 2-4 concrete levels, conditions, or timing triggers to monitor.\n'
        '- similar_historical_events: optional, omit or leave empty if not applicable.\n'
        '- Output ONLY the JSON object. No markdown. No explanation outside the JSON.'
    )

    return "\n".join(lines)
