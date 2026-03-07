"""
Daily Brief prompt template (LLM-2 / SOLO-56, updated LLM-3 / SOLO-57).

Builds the system + user prompt for the twice-daily Claude market brief
(09:00 and 19:00 Dubai time).

Claude is asked to output structured JSON only. The scheduler (LLM-3) wraps
Claude's output in the full F-7 envelope and validates against the schema.

Consumed by LLM-3 (daily brief scheduler).
"""

from __future__ import annotations

SYSTEM = """\
You are a professional crypto macro analyst. Your audience is a sophisticated
trader who already understands market mechanics — do not over-explain basics.

Tone: direct, signal-focused, no fluff. Use specific numbers where available.
Never make promises about future prices.

IMPORTANT: Respond ONLY with valid JSON — no markdown fences, no preamble, no trailing text.
""".strip()


def build(context: dict) -> str:
    """Return the user message string for the daily brief."""
    lines: list[str] = ["Produce a concise crypto macro daily brief based on the following data.\n"]

    # --- Regime ---
    regime = context.get("regime") or {}
    if regime:
        lines.append(
            f"**Market Regime:** {regime.get('current', 'UNKNOWN')} "
            f"(confidence {regime.get('confidence', 0.0):.0%}, as of {regime.get('as_of', 'N/A')})"
        )
        transitions = regime.get("recent_transitions") or []
        if transitions:
            last = transitions[0]
            lines.append(
                f"Last transition: {last.get('from', '?')} → {last.get('to', '?')} "
                f"at {last.get('at', 'N/A')} (confidence {last.get('confidence', 0.0):.0%})"
            )
        lines.append("")

    # --- Per-asset features ---
    features = context.get("features") or {}
    if features:
        lines.append("**Per-Asset Signals:**")
        for asset, feat in features.items():
            r1h = feat.get("r_1h", 0.0)
            rsi = feat.get("rsi_14", 50.0)
            rv_z = feat.get("rv_4h_zscore", 0.0)
            vol_z = feat.get("volume_zscore", 0.0)
            lines.append(
                f"- {asset}: r_1h={r1h:+.2%}, RSI={rsi:.1f}, "
                f"rv_zscore={rv_z:+.2f}, vol_zscore={vol_z:+.2f}"
            )
        lines.append("")

    # --- Macro / cross-asset ---
    cross = context.get("cross_features") or {}
    if cross:
        lines.append("**Macro Backdrop:**")
        macro_stress = cross.get("macro_stress")
        vix = cross.get("vix")
        dxy = cross.get("dxy_momentum")
        eth_btc = cross.get("eth_btc_rs")
        parts = []
        if macro_stress is not None:
            parts.append(f"macro_stress={macro_stress:.1f}/100")
        if vix is not None:
            parts.append(f"VIX={vix:.1f}")
        if dxy is not None:
            parts.append(f"DXY_mom={dxy:+.2f}")
        if eth_btc is not None:
            parts.append(f"ETH/BTC_rs={eth_btc:+.2f}")
        lines.append(", ".join(parts))
        lines.append("")

    # --- Derivatives ---
    derivatives = context.get("derivatives") or {}
    if derivatives:
        lines.append("**Derivative Positioning:**")
        for asset, deriv in derivatives.items():
            funding_z = deriv.get("funding_zscore")
            liq = deriv.get("liquidations_1h_usd")
            oi_drop = deriv.get("oi_drop_1h")
            parts = []
            if funding_z is not None:
                parts.append(f"funding_zscore={funding_z:+.2f}")
            if liq is not None:
                parts.append(f"liq_1h=${liq / 1e6:.1f}M")
            if oi_drop is not None:
                parts.append(f"oi_drop_1h={oi_drop:.2%}")
            if parts:
                lines.append(f"- {asset}: {', '.join(parts)}")
        lines.append("")

    # --- Recent alerts ---
    recent_alerts = context.get("recent_alerts") or []
    if recent_alerts:
        lines.append("**Notable Alerts (last 6h):**")
        for alert in recent_alerts[:10]:
            lines.append(
                f"- [{alert.get('severity', '?')}] {alert.get('type', '?')} "
                f"on {alert.get('symbol', '?')}: {alert.get('title', '')} "
                f"@ {alert.get('fired_at', 'N/A')}"
            )
        lines.append("")

    # --- Data freshness notice ---
    sa = context.get("sections_available") or {}
    missing = [k for k, v in sa.items() if not v]
    if missing:
        lines.append(f"_Data unavailable: {', '.join(missing)}. Brief is partial._")
        lines.append("")

    lines.append(
        'Based on the above data, respond with a JSON object containing EXACTLY these keys:\n'
        '{\n'
        '  "regime_analysis": "2-4 sentences analysing the current regime, what drove it, '
        'and what it means for positioning. Include specific numbers.",\n'
        '  "key_insights": ["insight 1", "insight 2", "insight 3"],\n'
        '  "watch_list": ["watchpoint 1", "watchpoint 2", "watchpoint 3"]\n'
        '}\n\n'
        'Rules:\n'
        '- regime_analysis: 2-4 sentences, specific numbers, no generic statements.\n'
        '- key_insights: 1-5 strings, each a crisp actionable insight with numbers.\n'
        '- watch_list: 1-5 strings, each a concrete level, event, or condition to monitor.\n'
        '- Output ONLY the JSON object. No markdown. No explanation outside the JSON.'
    )

    return "\n".join(lines)
