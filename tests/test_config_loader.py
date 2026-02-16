#!/usr/bin/env python3
"""
Unit tests for configuration loader.

Tests both valid and malformed configuration scenarios per F-5a acceptance criteria.

Usage:
    pytest tests/test_config_loader.py -v
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

# Import the config loader
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from configs.loader import load_config, ConfigurationError


class TestConfigLoaderValid:
    """Test config loader with valid configurations."""

    def test_load_valid_configs(self):
        """Test that valid configuration files load successfully."""
        config = load_config()

        assert config is not None
        assert config.symbols is not None
        assert config.providers is not None
        assert config.thresholds is not None

    def test_get_symbol_list(self):
        """Test retrieving symbol list."""
        config = load_config()
        symbols = config.get_symbol_list()

        assert isinstance(symbols, list)
        assert len(symbols) == 4
        assert "BTC" in symbols
        assert "ETH" in symbols
        assert "SOL" in symbols
        assert "HYPE" in symbols

    def test_get_onchain_symbols(self):
        """Test retrieving on-chain symbols (BTC and ETH only)."""
        config = load_config()
        onchain_symbols = config.get_onchain_symbols()

        assert isinstance(onchain_symbols, list)
        assert len(onchain_symbols) == 2
        assert "BTC" in onchain_symbols
        assert "ETH" in onchain_symbols
        assert "SOL" not in onchain_symbols  # Per SCOPE.md
        assert "HYPE" not in onchain_symbols  # Per SCOPE.md

    def test_get_asset_config(self):
        """Test retrieving individual asset configuration."""
        config = load_config()

        btc_config = config.get_asset_config("BTC")
        assert btc_config["name"] == "Bitcoin"
        assert btc_config["binance_symbol"] == "BTCUSDT"
        assert btc_config["properties"]["onchain_available"] is True

        sol_config = config.get_asset_config("SOL")
        assert sol_config["name"] == "Solana"
        assert sol_config["properties"]["onchain_available"] is False

    def test_get_asset_config_invalid_symbol(self):
        """Test retrieving config for non-existent symbol raises error."""
        config = load_config()

        with pytest.raises(ConfigurationError, match="Asset 'INVALID' not found"):
            config.get_asset_config("INVALID")

    def test_get_alert_threshold(self):
        """Test retrieving alert threshold configuration."""
        config = load_config()

        vol_expansion = config.get_alert_threshold("vol_expansion")
        assert "conditions" in vol_expansion
        assert vol_expansion["conditions"]["rv_1h_zscore"] == 2.0
        assert vol_expansion["cooldown_minutes"] == 30

    def test_get_regime_config(self):
        """Test retrieving regime configuration."""
        config = load_config()

        risk_on = config.get_regime_config("RISK_ON_TREND")
        assert "primary_condition" in risk_on
        assert "key_drivers" in risk_on

    def test_symbols_yaml_has_all_required_fields(self):
        """Test symbols.yaml contains all required fields per F-5a."""
        config = load_config()

        # Check top-level keys
        assert "version" in config.symbols
        assert "assets" in config.symbols
        assert "all_symbols" in config.symbols
        assert "onchain_symbols" in config.symbols

        # Check each asset has required fields
        for symbol in config.get_symbol_list():
            asset = config.get_asset_config(symbol)
            assert "name" in asset
            assert "symbol" in asset
            assert "binance_symbol" in asset
            assert "update_cadences" in asset
            assert "properties" in asset

    def test_providers_yaml_has_fred_series(self):
        """Test providers.yaml contains all required FRED series per F-5a acceptance criteria."""
        config = load_config()

        required_series = ["DFF", "DGS2", "DGS10", "M2SL", "CPIAUCSL", "PCEPI", "ICSA"]
        fred_series = config.providers["fred"]["series"]

        for series_id in required_series:
            assert series_id in fred_series, f"Missing required FRED series: {series_id}"
            assert "series_id" in fred_series[series_id]

    def test_thresholds_yaml_phase_1_2_alerts(self):
        """Test thresholds.yaml contains Phase 1-2 alert thresholds."""
        config = load_config()

        # Check phase
        assert config.thresholds["phase"] == "1-2"

        # Check Phase 1-2 alert types
        required_alerts = [
            "vol_expansion",
            "leadership_rotation",
            "breakout",
            "regime_shift",
            "correlation_break"
        ]

        for alert_type in required_alerts:
            assert alert_type in config.thresholds, f"Missing Phase 1-2 alert type: {alert_type}"

    def test_thresholds_yaml_regime_classifier(self):
        """Test thresholds.yaml contains all 5 regime definitions."""
        config = load_config()

        regime_classifier = config.thresholds["regime_classifier"]
        regimes = regime_classifier["regimes"]

        required_regimes = [
            "RISK_ON_TREND",
            "RISK_OFF_STRESS",
            "CHOP_RANGE",
            "VOL_EXPANSION",
            "DELEVERAGING"
        ]

        for regime in required_regimes:
            assert regime in regimes, f"Missing regime: {regime}"
            assert "primary_condition" in regimes[regime]


class TestConfigLoaderMalformed:
    """Test config loader with malformed configurations."""

    def test_missing_config_file(self):
        """Test that missing config file raises clear error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            # Create only one of the three required files
            (config_dir / "symbols.yaml").write_text("version: '1.0'\n")

            with pytest.raises(ConfigurationError, match="Configuration file not found"):
                load_config(config_dir)

    def test_empty_yaml_file(self):
        """Test that empty YAML file raises clear error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            (config_dir / "symbols.yaml").write_text("")
            (config_dir / "providers.yaml").write_text("version: '1.0'\n")
            (config_dir / "thresholds.yaml").write_text("version: '1.0'\n")

            with pytest.raises(ConfigurationError, match="Configuration file is empty"):
                load_config(config_dir)

    def test_malformed_yaml_syntax(self):
        """Test that malformed YAML syntax raises clear error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            # Create YAML with truly invalid syntax (unclosed quote)
            (config_dir / "symbols.yaml").write_text("version: '1.0\nassets:\n  BTC: unclosed quote\n")
            (config_dir / "providers.yaml").write_text("version: '1.0'\n")
            (config_dir / "thresholds.yaml").write_text("version: '1.0'\n")

            with pytest.raises(ConfigurationError, match="Failed to parse YAML"):
                load_config(config_dir)

    def test_missing_required_key_symbols(self):
        """Test that missing required key in symbols.yaml raises clear error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            # Missing 'all_symbols' key
            symbols_config = {
                "version": "1.0",
                "assets": {
                    "BTC": {
                        "name": "Bitcoin",
                        "symbol": "BTC",
                        "binance_symbol": "BTCUSDT",
                        "update_cadences": {},
                        "properties": {"onchain_available": True}
                    }
                },
                "onchain_symbols": ["BTC"]
            }

            (config_dir / "symbols.yaml").write_text(yaml.dump(symbols_config))
            (config_dir / "providers.yaml").write_text("version: '1.0'\nbinance: {}\nfred: {}\nyahoo_finance: {}\ncoinglass: {}\nonchain_provider: {}\n")
            (config_dir / "thresholds.yaml").write_text("version: '1.0'\nphase: '1-2'\n")

            with pytest.raises(ConfigurationError, match="symbols.yaml missing required key: 'all_symbols'"):
                load_config(config_dir)

    def test_missing_fred_series(self):
        """Test that missing required FRED series raises clear error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            # Create valid symbols.yaml
            symbols_config = {
                "version": "1.0",
                "assets": {"BTC": {
                    "name": "Bitcoin",
                    "symbol": "BTC",
                    "binance_symbol": "BTCUSDT",
                    "update_cadences": {},
                    "properties": {"onchain_available": True}
                }},
                "all_symbols": ["BTC"],
                "onchain_symbols": ["BTC"]
            }

            # Missing DFF series in FRED config
            providers_config = {
                "version": "1.0",
                "binance": {"websocket": {"base_url": "wss://test"}},
                "fred": {
                    "base_url": "https://api.stlouisfed.org",
                    "series": {
                        "DGS2": {"series_id": "DGS2"},
                        # Missing DFF, DGS10, M2SL, CPIAUCSL, PCEPI, ICSA
                    }
                },
                "yahoo_finance": {"tickers": {}},
                "coinglass": {"base_url": "https://test"},
                "onchain_provider": {}
            }

            (config_dir / "symbols.yaml").write_text(yaml.dump(symbols_config))
            (config_dir / "providers.yaml").write_text(yaml.dump(providers_config))
            (config_dir / "thresholds.yaml").write_text("version: '1.0'\nphase: '1-2'\n")

            with pytest.raises(ConfigurationError, match="FRED series .* missing"):
                load_config(config_dir)

    def test_wrong_phase_in_thresholds(self):
        """Test that incorrect phase in thresholds.yaml raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            symbols_config = {
                "version": "1.0",
                "assets": {"BTC": {
                    "name": "Bitcoin",
                    "symbol": "BTC",
                    "binance_symbol": "BTCUSDT",
                    "update_cadences": {},
                    "properties": {"onchain_available": True}
                }},
                "all_symbols": ["BTC"],
                "onchain_symbols": ["BTC"]
            }

            providers_config = {
                "version": "1.0",
                "binance": {"websocket": {"base_url": "wss://test"}},
                "fred": {
                    "base_url": "https://api.stlouisfed.org",
                    "series": {
                        "DFF": {"series_id": "DFF"},
                        "DGS2": {"series_id": "DGS2"},
                        "DGS10": {"series_id": "DGS10"},
                        "M2SL": {"series_id": "M2SL"},
                        "CPIAUCSL": {"series_id": "CPIAUCSL"},
                        "PCEPI": {"series_id": "PCEPI"},
                        "ICSA": {"series_id": "ICSA"}
                    }
                },
                "yahoo_finance": {"tickers": {}},
                "coinglass": {"base_url": "https://test"},
                "onchain_provider": {}
            }

            # Wrong phase (should be "1-2" for F-5a)
            thresholds_config = {
                "version": "1.0",
                "phase": "3-4"  # Wrong phase
            }

            (config_dir / "symbols.yaml").write_text(yaml.dump(symbols_config))
            (config_dir / "providers.yaml").write_text(yaml.dump(providers_config))
            (config_dir / "thresholds.yaml").write_text(yaml.dump(thresholds_config))

            with pytest.raises(ConfigurationError, match="phase must be '1-2' for F-5a"):
                load_config(config_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
