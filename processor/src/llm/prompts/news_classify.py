"""
News classification prompt template (LLM-2b / SOLO-95).

Builds the system + user prompt for async classification of a single
crypto news headline. Used by NewsClassifier — never in the 5-minute
alert hot path (Rule 1.1 preserved).

Claude is asked to output structured JSON only. NewsClassifier writes
the result to news_signals (DB + Redis). AL-12 evaluator reads that
output deterministically — no LLM in the alert trigger path.
"""

from __future__ import annotations

SYSTEM = """\
You are classifying a crypto news headline for market impact.
Be objective and data-driven. Respond ONLY with valid JSON — no markdown, no explanation outside the JSON.
""".strip()


def build(headline: str, published_at: str, source: str) -> str:
    """Return the user message for a single headline classification."""
    return (
        f'Classify this crypto news headline for market impact.\n\n'
        f'Headline: "{headline}"\n'
        f'Published: {published_at}\n'
        f'Source: {source}\n\n'
        'Respond with ONLY this JSON object:\n'
        '{\n'
        '  "relevant": true | false,\n'
        '  "direction": "bullish" | "bearish" | "neutral" | "ambiguous",\n'
        '  "confidence": "high" | "medium" | "low",\n'
        '  "event_type": "regulatory" | "exploit" | "exchange" | "macro" | "protocol" | "other",\n'
        '  "assets": ["BTC", "ETH"],\n'
        '  "reasoning": "one sentence explaining why this is or is not market-moving"\n'
        '}\n\n'
        'Rules:\n'
        '- relevant=true only if the headline is genuinely market-moving (price, liquidity, risk).\n'
        '- Routine price updates, opinion pieces, and ads are relevant=false.\n'
        '- direction: net market impact if relevant=true; "neutral" if mixed or relevant=false.\n'
        '- confidence: how certain you are given the headline text alone.\n'
        '- assets: list only the assets directly and significantly affected (max 4).\n'
        '- Output ONLY the JSON object. No markdown fences. No preamble.'
    )
