#!/usr/bin/env python3
"""
Contract Tests for Schema Validation

Tests that validate known-good fixtures pass and intentionally-bad fixtures fail.
These tests prevent silent breaking changes across service boundaries.

Usage:
    pytest tests/test_schema_contracts.py -v
"""

import sys
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from schema.validator import (
    validate_nats_candle,
    validate_alert,
    validate_daily_brief,
    validate_event_analysis,
    validate_health_response,
    ValidationError,
)


class TestNATSCandleContract:
    """Tests for NATS candle message schema"""

    def test_valid_candle_message(self):
        """Valid candle message should pass validation"""
        valid_candle = {
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "timeframe": "1m",
            "time": "2026-02-16T12:00:00Z",
            "open": 50000.0,
            "high": 50100.0,
            "low": 49900.0,
            "close": 50050.0,
            "volume": 125.5,
            "quote_volume": 6275000.0,
            "trades": 1523
        }
        # Should not raise
        validate_nats_candle(valid_candle)

    def test_missing_required_field(self):
        """Missing required field should fail validation"""
        invalid_candle = {
            "symbol": "BTCUSDT",
            "exchange": "binance",
            # Missing "timeframe"
            "time": "2026-02-16T12:00:00Z",
            "open": 50000.0,
            "high": 50100.0,
            "low": 49900.0,
            "close": 50050.0,
            "volume": 125.5,
            "quote_volume": 6275000.0
        }
        with pytest.raises(ValidationError):
            validate_nats_candle(invalid_candle)

    def test_negative_price(self):
        """Negative price should fail validation"""
        invalid_candle = {
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "timeframe": "1m",
            "time": "2026-02-16T12:00:00Z",
            "open": -50000.0,  # Invalid: negative
            "high": 50100.0,
            "low": 49900.0,
            "close": 50050.0,
            "volume": 125.5,
            "quote_volume": 6275000.0
        }
        with pytest.raises(ValidationError):
            validate_nats_candle(invalid_candle)

    def test_invalid_symbol_format(self):
        """Invalid symbol format should fail validation"""
        invalid_candle = {
            "symbol": "btc",  # Invalid: not matching pattern
            "exchange": "binance",
            "timeframe": "1m",
            "time": "2026-02-16T12:00:00Z",
            "open": 50000.0,
            "high": 50100.0,
            "low": 49900.0,
            "close": 50050.0,
            "volume": 125.5,
            "quote_volume": 6275000.0
        }
        with pytest.raises(ValidationError):
            validate_nats_candle(invalid_candle)


class TestAlertContract:
    """Tests for alert payload schema"""

    def test_valid_alert_payload(self):
        """Valid alert payload should pass validation"""
        valid_alert = {
            "alert_id": "550e8400-e29b-41d4-a716-446655440000",
            "alert_type": "VOL_EXPANSION",
            "symbol": "BTC",
            "severity": "HIGH",
            "time": "2026-02-16T12:00:00Z",
            "conditions": {
                "rv_1h_zscore": 2.5,
                "volume_zscore": 2.0,
                "breakout_type": "24h"
            },
            "context": {
                "regime": "VOL_EXPANSION",
                "regime_confidence": 0.85
            },
            "message": "BTC volatility expansion with breakout confirmation",
            "cooldown_until": "2026-02-16T12:30:00Z"
        }
        # Should not raise
        validate_alert(valid_alert)

    def test_regime_shift_null_symbol(self):
        """REGIME_SHIFT with null symbol should pass validation"""
        valid_alert = {
            "alert_id": "550e8400-e29b-41d4-a716-446655440001",
            "alert_type": "REGIME_SHIFT",
            "symbol": None,  # null for market-wide alerts
            "severity": "HIGH",
            "time": "2026-02-16T12:00:00Z",
            "conditions": {
                "old_regime": "RISK_ON_TREND",
                "new_regime": "VOL_EXPANSION",
                "confidence": 0.75
            },
            "context": {
                "regime": "VOL_EXPANSION",
                "regime_confidence": 0.75
            },
            "message": "Regime shift from RISK_ON_TREND to VOL_EXPANSION"
        }
        # Should not raise
        validate_alert(valid_alert)

    def test_invalid_alert_type(self):
        """Invalid alert type should fail validation"""
        invalid_alert = {
            "alert_id": "550e8400-e29b-41d4-a716-446655440000",
            "alert_type": "INVALID_TYPE",  # Not in enum
            "symbol": "BTC",
            "severity": "HIGH",
            "time": "2026-02-16T12:00:00Z",
            "conditions": {},
            "context": {}
        }
        with pytest.raises(ValidationError):
            validate_alert(invalid_alert)

    def test_invalid_severity(self):
        """Invalid severity should fail validation"""
        invalid_alert = {
            "alert_id": "550e8400-e29b-41d4-a716-446655440000",
            "alert_type": "VOL_EXPANSION",
            "symbol": "BTC",
            "severity": "CRITICAL",  # Not in enum
            "time": "2026-02-16T12:00:00Z",
            "conditions": {},
            "context": {}
        }
        with pytest.raises(ValidationError):
            validate_alert(invalid_alert)


class TestDailyBriefContract:
    """Tests for daily brief schema"""

    def test_valid_daily_brief(self):
        """Valid daily brief should pass validation"""
        valid_brief = {
            "report_id": "550e8400-e29b-41d4-a716-446655440000",
            "report_type": "daily_brief",
            "generated_at": "2026-02-16T09:00:00Z",
            "time_range": {
                "start": "2026-02-15T21:00:00Z",
                "end": "2026-02-16T09:00:00Z"
            },
            "regime_summary": {
                "current_regime": "RISK_ON_TREND",
                "confidence": 0.75,
                "transitions": [],
                "analysis": "Market continues in risk-on mode with low volatility."
            },
            "alert_summary": {
                "total_alerts": 3,
                "by_type": {"VOL_EXPANSION": 2, "BREAKOUT": 1},
                "by_severity": {"HIGH": 1, "MEDIUM": 2, "LOW": 0},
                "notable_alerts": []
            },
            "market_summary": {
                "assets": {
                    "BTC": {"price_change_pct": 2.5, "volume_change_pct": 15.0, "volatility_regime": "low"},
                    "ETH": {"price_change_pct": 3.2, "volume_change_pct": 20.0, "volatility_regime": "low"}
                },
                "correlations": {
                    "btc_spx": 0.65,
                    "btc_dxy": -0.45
                }
            },
            "key_insights": [
                "BTC maintaining strong uptrend with low volatility",
                "ETH outperforming with leadership rotation signals"
            ],
            "watch_list": [
                "Watch $50,000 resistance level for BTC",
                "Monitor funding rates for overheating signals"
            ],
            "llm_metadata": {
                "model": "claude-sonnet-4-5-20250929",
                "tokens_used": 1523,
                "cost_usd": 0.045,
                "generation_time_ms": 2341
            }
        }
        # Should not raise
        validate_daily_brief(valid_brief)

    def test_missing_key_insights(self):
        """Missing required key_insights should fail validation"""
        invalid_brief = {
            "report_id": "550e8400-e29b-41d4-a716-446655440000",
            "report_type": "daily_brief",
            "generated_at": "2026-02-16T09:00:00Z",
            "time_range": {"start": "2026-02-15T21:00:00Z", "end": "2026-02-16T09:00:00Z"},
            "regime_summary": {"current_regime": "RISK_ON_TREND", "confidence": 0.75},
            "alert_summary": {"total_alerts": 0, "by_type": {}, "by_severity": {"HIGH": 0, "MEDIUM": 0, "LOW": 0}},
            "market_summary": {"assets": {}},
            # Missing "key_insights"
            "watch_list": [],
            "llm_metadata": {"model": "claude-sonnet-4-5-20250929", "tokens_used": 100, "cost_usd": 0.01}
        }
        with pytest.raises(ValidationError):
            validate_daily_brief(invalid_brief)


class TestEventAnalysisContract:
    """Tests for event analysis schema"""

    def test_valid_event_analysis(self):
        """Valid event analysis should pass validation"""
        valid_analysis = {
            "report_id": "550e8400-e29b-41d4-a716-446655440000",
            "report_type": "event_analysis",
            "generated_at": "2026-02-16T12:05:00Z",
            "trigger_alert": {
                "alert_id": "550e8400-e29b-41d4-a716-446655440001",
                "alert_type": "BREAKOUT",
                "symbol": "BTC",
                "severity": "HIGH",
                "time": "2026-02-16T12:00:00Z",
                "conditions": {"breakout_type": "24h", "volume_zscore": 2.5}
            },
            "context": {
                "regime": {"current": "VOL_EXPANSION", "confidence": 0.8},
                "recent_alerts": [
                    {"alert_type": "VOL_EXPANSION", "symbol": "BTC", "time": "2026-02-16T11:30:00Z"}
                ],
                "features": {"rv_4h_zscore": 2.1},
                "price_context": {"current_price": 51000, "r_4h": 2.0, "r_24h": 4.5}
            },
            "analysis": {
                "summary": "BTC breaks out above 24h range with high volume confirmation",
                "interpretation": "This breakout follows increasing volatility and strong volume, suggesting continuation rather than a false break.",
                "watch_next": [
                    "Monitor $52,000 resistance",
                    "Watch for funding rate elevation"
                ]
            },
            "llm_metadata": {
                "model": "claude-sonnet-4-5-20250929",
                "tokens_used": 856,
                "cost_usd": 0.025,
                "generation_time_ms": 1245
            }
        }
        # Should not raise
        validate_event_analysis(valid_analysis)


class TestHealthResponseContract:
    """Tests for /api/health response schema"""

    def test_valid_health_response_healthy(self):
        """Valid HEALTHY health response should pass validation"""
        valid_health = {
            "status": "HEALTHY",
            "timestamp": "2026-02-16T12:00:00Z",
            "components": {
                "binance_ws": {"status": "HEALTHY", "latency_ms": 15.2},
                "timescaledb": {"status": "HEALTHY", "latency_ms": 2.1},
                "redis": {"status": "HEALTHY", "latency_ms": 0.8},
                "nats": {"status": "HEALTHY", "latency_ms": 1.2}
            },
            "degraded_features": [],
            "uptime_seconds": 86400
        }
        # Should not raise
        validate_health_response(valid_health)

    def test_valid_health_response_degraded(self):
        """Valid DEGRADED health response should pass validation"""
        valid_health = {
            "status": "DEGRADED",
            "timestamp": "2026-02-16T12:00:00Z",
            "components": {
                "binance_ws": {"status": "HEALTHY"},
                "coinglass": {"status": "DOWN", "message": "API timeout after 5 retries"},
                "timescaledb": {"status": "HEALTHY"},
                "redis": {"status": "HEALTHY"},
                "nats": {"status": "HEALTHY"}
            },
            "degraded_features": ["derivatives_alerts", "funding_rate_tracking"]
        }
        # Should not raise
        validate_health_response(valid_health)

    def test_invalid_status(self):
        """Invalid status should fail validation"""
        invalid_health = {
            "status": "OK",  # Not in enum
            "timestamp": "2026-02-16T12:00:00Z",
            "components": {
                "binance_ws": {"status": "HEALTHY"},
                "timescaledb": {"status": "HEALTHY"},
                "redis": {"status": "HEALTHY"},
                "nats": {"status": "HEALTHY"}
            }
        }
        with pytest.raises(ValidationError):
            validate_health_response(invalid_health)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
