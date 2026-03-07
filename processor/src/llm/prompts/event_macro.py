"""
Macro event prompt template (LLM-2 / SOLO-56).

Builds the system + user prompt for event-triggered analysis of macro regime
shifts — VIX spikes, DXY breakouts, or macro_stress threshold breaches that
trigger a REGIME_SHIFT or CORRELATION_BREAK alert.

Consumed by LLM-4 (event-triggered analysis).
"""

from __future__ import annotations

SYSTEM = """\
You are a professional crypto macro analyst. Your audience is a sophisticated
trader who tracks cross-asset correlations and macro regime dynamics.
Be concise and data-driven. Max ~300 words. No price predictions.
""".strip()


def build(context: dict) -> str:
    """Return the user message for a macro event analysis."""
    lines: list[str] = []

    alert_type = context.get("alert_type", "MACRO_EVENT")
    lines.append(f"A **{alert_type}** event was detected.\n")

    # Current regime
    regime = (context.get("regime") or {})
    if regime:
        lines.append(
            f"Current regime: **{regime.get('current', 'UNKNOWN')}** "
            f"(confidence {regime.get('confidence', 0.0):.0%})\n"
        )
        transitions = regime.get("recent_transitions") or []
        if transitions:
            last = transitions[0]
            lines.append(
                f"Recent transition: {last.get('from', '?')} → {last.get('to', '?')} "
                f"at {last.get('at', 'N/A')}\n"
            )

    # Macro snapshot
    cross = context.get("cross_features") or {}
    if cross:
        lines.append("**Macro Snapshot:**")
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
        lines.append(", ".join(parts) + "\n")

    lines.append(
        "Provide a brief analysis of this macro event: What drove the regime shift? "
        "How are crypto assets positioned relative to the macro backdrop? "
        "What are the key risks and watchpoints for the next 24h? "
        "Sections: **Event Summary**, **Macro Context**, **Crypto Implications**, **Key Watchpoints**."
    )

    return "\n".join(lines)
