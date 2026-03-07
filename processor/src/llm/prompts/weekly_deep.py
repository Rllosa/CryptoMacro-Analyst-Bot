"""
Weekly deep report prompt template (LLM-2 / SOLO-56).

Builds the system + user prompt for the Sunday Claude Opus deep-dive report
(LLM-5). Broader retrospective than the daily brief — uses full context
including all regime transition history.

Consumed by LLM-5 (weekly deep report scheduler).
max_tokens recommendation: 4096.
"""

from __future__ import annotations

SYSTEM = """\
You are a senior crypto macro strategist producing a weekly intelligence report.
Your audience is a sophisticated trader reviewing the past 7 days of market
structure. Be analytical, reference specific data points, and draw actionable
conclusions.

Output format: structured markdown with clear section headers.
Tone: authoritative but direct. Max ~1200 words. No price predictions.
""".strip()


def build(context: dict) -> str:
    """Return the user message string for the weekly deep report."""
    lines: list[str] = [
        "Produce a weekly deep market intelligence report based on the following data.\n"
    ]

    # --- Regime (current + full transition history) ---
    regime = context.get("regime") or {}
    if regime:
        lines.append(
            f"**Current Regime:** {regime.get('current', 'UNKNOWN')} "
            f"(confidence {regime.get('confidence', 0.0):.0%})"
        )
        transitions = regime.get("recent_transitions") or []
        if transitions:
            lines.append("\n**Regime Transitions (last 24h):**")
            for t in transitions:
                lines.append(
                    f"- {t.get('from', '?')} → {t.get('to', '?')} "
                    f"@ {t.get('at', 'N/A')} (confidence {t.get('confidence', 0.0):.0%})"
                )
        lines.append("")

    # --- Per-asset features ---
    features = context.get("features") or {}
    if features:
        lines.append("**Current Asset Signals:**")
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
        btc_dom = cross.get("btc_dominance")
        parts = []
        if macro_stress is not None:
            parts.append(f"macro_stress={macro_stress:.1f}/100")
        if vix is not None:
            parts.append(f"VIX={vix:.1f}")
        if dxy is not None:
            parts.append(f"DXY_mom={dxy:+.2f}")
        if eth_btc is not None:
            parts.append(f"ETH/BTC_rs={eth_btc:+.2f}")
        if btc_dom is not None:
            parts.append(f"BTC.D={btc_dom:.1%}")
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

    # --- Alerts summary ---
    recent_alerts = context.get("recent_alerts") or []
    if recent_alerts:
        lines.append("**Alert Activity (last 6h):**")
        for alert in recent_alerts:
            lines.append(
                f"- [{alert.get('severity', '?')}] {alert.get('type', '?')} "
                f"on {alert.get('symbol', '?')}: {alert.get('title', '')} "
                f"@ {alert.get('fired_at', 'N/A')}"
            )
        lines.append("")

    # --- Data freshness ---
    sa = context.get("sections_available") or {}
    missing = [k for k, v in sa.items() if not v]
    if missing:
        lines.append(f"_Data unavailable: {', '.join(missing)}. Report is partial._\n")

    lines.append(
        "Based on the above, write a comprehensive weekly deep report with these sections: "
        "**Executive Summary** (3–4 sentences), "
        "**Regime Analysis** (regime evolution, confidence trends, what drove transitions), "
        "**Asset Performance Review** (BTC, ETH, SOL, HYPE — key themes), "
        "**Macro & Cross-Asset Dynamics** (VIX, DXY, correlations), "
        "**Derivatives & Positioning** (leverage, funding, liquidation risk), "
        "**Notable Alert Activity** (patterns in what fired and why), "
        "**Strategic Outlook** (key watchpoints for the coming week, positioning bias with rationale)."
    )

    return "\n".join(lines)
