from __future__ import annotations

from src.embeds import format_alert_embed, format_event_analysis_embed


def _base_payload(alert_type: str, severity: str = "HIGH", symbol: str = "btc", **tv_overrides) -> dict:
    return {
        "alert_type": alert_type,
        "severity": severity,
        "symbol": symbol,
        "conditions": {"trigger_values": tv_overrides},
        "message": "test interpretation",
        "time": "2026-02-25T00:00:00Z",
        "cooldown_until": "2026-02-25T01:00:00Z",
    }


def _field_names(embed_dict: dict) -> list[str]:
    return [f["name"] for f in embed_dict.get("fields", [])]


# ── per alert-type tests ─────────────────────────────────────────────────────

def test_vol_expansion_embed():
    payload = _base_payload("VOL_EXPANSION", "HIGH", rv_1h_zscore=2.5, volume_zscore=2.0, direction="up")
    d = format_alert_embed(payload).to_dict()
    assert d["color"] == 0xEF4444
    assert "VOL_EXPANSION" in d["title"]
    names = _field_names(d)
    assert "rv_1h_zscore" in names
    assert "volume_zscore" in names
    assert "Direction" in names


def test_leadership_rotation_embed():
    payload = _base_payload("LEADERSHIP_ROTATION", "MEDIUM", pair="ETH/BTC", rs_zscore=1.8, direction="outperform")
    d = format_alert_embed(payload).to_dict()
    assert d["color"] == 0xF59E0B
    assert "LEADERSHIP_ROTATION" in d["title"]
    names = _field_names(d)
    assert "Pair" in names
    assert "rs_zscore" in names


def test_breakout_embed():
    payload = _base_payload("BREAKOUT", "HIGH", direction="up", level=98000.0, volume_zscore=2.1)
    d = format_alert_embed(payload).to_dict()
    assert "BREAKOUT" in d["title"]
    names = _field_names(d)
    assert "Direction" in names
    assert "Level" in names
    assert "volume_zscore" in names


def test_regime_shift_embed():
    payload = _base_payload(
        "REGIME_SHIFT", "HIGH",
        old_regime="RISK_OFF", new_regime="RISK_ON_TREND", confidence=0.85
    )
    d = format_alert_embed(payload).to_dict()
    assert "REGIME_SHIFT" in d["title"]
    names = _field_names(d)
    assert "Transition" in names
    assert "Confidence" in names
    # Confidence shown as percentage
    confidence_field = next(f for f in d["fields"] if f["name"] == "Confidence")
    assert "85%" in confidence_field["value"]


def test_correlation_break_embed():
    payload = _base_payload("CORRELATION_BREAK", "MEDIUM", pair="BTC/SPX", delta=-0.4, current=0.3, historical=0.7)
    d = format_alert_embed(payload).to_dict()
    assert "CORRELATION_BREAK" in d["title"]
    names = _field_names(d)
    assert "Pair" in names
    assert "Delta" in names
    assert "Current / Historical" in names


def test_crowded_leverage_embed():
    payload = _base_payload("CROWDED_LEVERAGE", "HIGH", funding_zscore=2.2, oi_change_24h=0.15)
    d = format_alert_embed(payload).to_dict()
    assert "CROWDED_LEVERAGE" in d["title"]
    names = _field_names(d)
    assert "funding_zscore" in names
    assert "oi_change_24h" in names


def test_deleveraging_event_embed():
    payload = _base_payload(
        "DELEVERAGING_EVENT", "HIGH",
        liq_1h_usd=5_000_000, oi_drop_pct=0.08, candle_atr_multiple=3.2
    )
    d = format_alert_embed(payload).to_dict()
    assert "DELEVERAGING_EVENT" in d["title"]
    names = _field_names(d)
    assert "liq_1h_usd" in names
    liq_field = next(f for f in d["fields"] if f["name"] == "liq_1h_usd")
    assert "$5.0M" in liq_field["value"]


def test_exchange_inflow_risk_embed():
    payload = _base_payload("EXCHANGE_INFLOW_RISK", "HIGH", inflow_zscore=3.1, netflow_zscore=2.5)
    d = format_alert_embed(payload).to_dict()
    assert "EXCHANGE_INFLOW_RISK" in d["title"]
    names = _field_names(d)
    assert "inflow_zscore" in names
    assert "netflow_zscore" in names


def test_netflow_shift_embed():
    payload = _base_payload("NETFLOW_SHIFT", "MEDIUM", conditions_met=3, direction="outflow")
    d = format_alert_embed(payload).to_dict()
    assert "NETFLOW_SHIFT" in d["title"]
    names = _field_names(d)
    assert "conditions_met" in names
    assert "Direction" in names


# ── edge case tests ──────────────────────────────────────────────────────────

def test_symbol_none_no_none_in_title():
    payload = _base_payload("VOL_EXPANSION", "HIGH")
    payload["symbol"] = None
    d = format_alert_embed(payload).to_dict()
    assert "(None)" not in d["title"]
    assert "VOL_EXPANSION" in d["title"]


def test_empty_trigger_values_no_error():
    payload = {
        "alert_type": "VOL_EXPANSION",
        "severity": "HIGH",
        "symbol": "btc",
        "conditions": {},
        "message": "",
        "time": "2026-02-25T00:00:00Z",
        "cooldown_until": "2026-02-25T01:00:00Z",
    }
    d = format_alert_embed(payload).to_dict()
    assert "VOL_EXPANSION" in d["title"]
    # All fields default to N/A — no KeyError raised
    for field in d.get("fields", []):
        assert field["value"] != ""


# ── event analysis embed tests ───────────────────────────────────────────────

def _base_event_analysis_payload(**overrides) -> dict:
    base = {
        "report_id": "test-uuid",
        "report_type": "event_analysis",
        "generated_at": "2026-03-19T12:00:00+00:00",
        "trigger_alert": {
            "alert_id": "alert-uuid",
            "alert_type": "DELEVERAGING_EVENT",
            "symbol": "BTCUSDT",
            "severity": "HIGH",
            "time": "2026-03-19T12:00:00+00:00",
            "conditions": {"liq_1h_usd": 60_000_000},
        },
        "context": {
            "regime": {"current": "VOL_EXPANSION", "confidence": 0.78},
            "recent_alerts": [],
            "features": {},
        },
        "analysis": {
            "summary": "BTC cascade: $60M liquidated in 1 hour.",
            "interpretation": (
                "Forced deleveraging confirmed by OI drop. Funding z-score was 2.5 "
                "before the cascade, indicating crowded positioning."
            ),
            "watch_next": ["BTC $80k support", "OI recovery within 2h"],
        },
        "llm_metadata": {
            "model": "claude-sonnet-4-6",
            "tokens_used": 420,
            "cost_usd": 0.003,
            "generation_time_ms": 1800,
        },
    }
    base.update(overrides)
    return base


def test_event_analysis_embed_structure():
    """Title contains alert type and symbol; description leads with bold summary."""
    d = format_event_analysis_embed(_base_event_analysis_payload()).to_dict()
    assert "DELEVERAGING_EVENT" in d["title"]
    assert "BTCUSDT" in d["title"]
    assert d["color"] == 0xEF4444  # HIGH severity = red
    # Description: summary bold first, then interpretation
    assert "**BTC cascade" in d["description"]
    assert "Forced deleveraging" in d["description"]


def test_event_analysis_embed_watch_next_and_regime():
    """Watch Next bullets and Regime field are present."""
    d = format_event_analysis_embed(_base_event_analysis_payload()).to_dict()
    names = [f["name"] for f in d.get("fields", [])]
    assert "Watch Next" in names
    assert "Regime" in names
    watch_field = next(f for f in d["fields"] if f["name"] == "Watch Next")
    assert "• BTC $80k support" in watch_field["value"]
    regime_field = next(f for f in d["fields"] if f["name"] == "Regime")
    assert "VOL_EXPANSION" in regime_field["value"]
    assert "78%" in regime_field["value"]


def test_event_analysis_embed_fallback_text():
    """LLM unavailable fallback summary still renders without error."""
    payload = _base_event_analysis_payload()
    payload["analysis"]["summary"] = "LLM unavailable — event analysis could not be generated."
    payload["analysis"]["interpretation"] = (
        "Claude API was unreachable when this alert fired. "
        "The alert itself was delivered and stored in the alerts table."
    )
    payload["analysis"]["watch_next"] = ["Review raw trigger values in trigger_alert.conditions"]
    d = format_event_analysis_embed(payload).to_dict()
    assert "LLM unavailable" in d["description"]
    assert "Watch Next" in [f["name"] for f in d.get("fields", [])]
