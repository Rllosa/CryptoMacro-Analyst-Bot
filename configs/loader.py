#!/usr/bin/env python3
"""
Configuration Loader for CryptoMacro Analyst Bot.

Loads and validates all YAML configuration files:
- symbols.yaml
- providers.yaml
- thresholds.yaml

Provides validated configuration objects and clear error messages for malformed configs.

Usage:
    from configs.loader import load_config

    config = load_config()
    symbols = config.symbols
    providers = config.providers
    thresholds = config.thresholds
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class ConfigurationError(Exception):
    """Raised when configuration is invalid or malformed."""
    pass


class Config:
    """Validated configuration object containing all loaded configs."""

    def __init__(self, symbols: Dict[str, Any], providers: Dict[str, Any], thresholds: Dict[str, Any]):
        self.symbols = symbols
        self.providers = providers
        self.thresholds = thresholds

    def get_symbol_list(self) -> List[str]:
        """Get list of all configured symbols."""
        return self.symbols.get("all_symbols", [])

    def get_onchain_symbols(self) -> List[str]:
        """Get list of symbols with on-chain data available."""
        return self.symbols.get("onchain_symbols", [])

    def get_asset_config(self, symbol: str) -> Dict[str, Any]:
        """Get configuration for a specific asset symbol."""
        assets = self.symbols.get("assets", {})
        if symbol not in assets:
            raise ConfigurationError(f"Asset '{symbol}' not found in symbols configuration")
        return assets[symbol]

    def get_alert_threshold(self, alert_type: str) -> Dict[str, Any]:
        """Get threshold configuration for a specific alert type."""
        alert_type_key = alert_type.lower()
        if alert_type_key not in self.thresholds:
            raise ConfigurationError(f"Alert type '{alert_type}' not found in thresholds configuration")
        return self.thresholds[alert_type_key]

    def get_regime_config(self, regime: str) -> Dict[str, Any]:
        """Get configuration for a specific regime."""
        regime_classifier = self.thresholds.get("regime_classifier", {})
        regimes = regime_classifier.get("regimes", {})
        if regime not in regimes:
            raise ConfigurationError(f"Regime '{regime}' not found in regime_classifier configuration")
        return regimes[regime]


def _load_yaml_file(file_path: Path) -> Dict[str, Any]:
    """
    Load and parse a YAML file.

    Args:
        file_path: Path to YAML file

    Returns:
        Parsed YAML as dictionary

    Raises:
        ConfigurationError: If file doesn't exist or YAML is malformed
    """
    if not file_path.exists():
        raise ConfigurationError(f"Configuration file not found: {file_path}")

    try:
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
            if data is None:
                raise ConfigurationError(f"Configuration file is empty: {file_path}")
            return data
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Failed to parse YAML in {file_path}: {e}")
    except Exception as e:
        raise ConfigurationError(f"Failed to read {file_path}: {e}")


def _validate_symbols_config(config: Dict[str, Any]) -> None:
    """
    Validate symbols.yaml configuration.

    Args:
        config: Parsed symbols configuration

    Raises:
        ConfigurationError: If configuration is invalid
    """
    # Check required top-level keys
    required_keys = ["version", "assets", "all_symbols", "onchain_symbols"]
    for key in required_keys:
        if key not in config:
            raise ConfigurationError(f"symbols.yaml missing required key: '{key}'")

    # Validate all_symbols list
    all_symbols = config["all_symbols"]
    if not isinstance(all_symbols, list) or len(all_symbols) == 0:
        raise ConfigurationError("symbols.yaml 'all_symbols' must be a non-empty list")

    # Validate that all symbols are defined in assets
    assets = config.get("assets", {})
    for symbol in all_symbols:
        if symbol not in assets:
            raise ConfigurationError(f"Symbol '{symbol}' in all_symbols but not defined in assets")

    # Validate each asset has required fields
    required_asset_fields = ["name", "symbol", "binance_symbol", "update_cadences", "properties"]
    for symbol, asset_config in assets.items():
        for field in required_asset_fields:
            if field not in asset_config:
                raise ConfigurationError(
                    f"Asset '{symbol}' missing required field: '{field}'"
                )

        # Validate binance_symbol is a string
        if not isinstance(asset_config["binance_symbol"], str):
            raise ConfigurationError(f"Asset '{symbol}' binance_symbol must be a string")

        # Validate properties
        props = asset_config["properties"]
        if "onchain_available" not in props:
            raise ConfigurationError(
                f"Asset '{symbol}' missing 'onchain_available' in properties"
            )

    # Validate onchain_symbols match assets with onchain_available=true
    onchain_symbols = config["onchain_symbols"]
    for symbol in onchain_symbols:
        if symbol not in assets:
            raise ConfigurationError(f"On-chain symbol '{symbol}' not defined in assets")
        if not assets[symbol]["properties"].get("onchain_available", False):
            raise ConfigurationError(
                f"Symbol '{symbol}' in onchain_symbols but onchain_available=false"
            )


def _validate_providers_config(config: Dict[str, Any]) -> None:
    """
    Validate providers.yaml configuration.

    Args:
        config: Parsed providers configuration

    Raises:
        ConfigurationError: If configuration is invalid
    """
    # Check required top-level keys
    required_keys = ["version", "binance", "fred", "yahoo_finance", "coinglass", "onchain_provider"]
    for key in required_keys:
        if key not in config:
            raise ConfigurationError(f"providers.yaml missing required key: '{key}'")

    # Validate Binance configuration
    binance = config["binance"]
    if "websocket" not in binance or "base_url" not in binance["websocket"]:
        raise ConfigurationError("providers.yaml: binance.websocket.base_url is required")

    # Validate FRED configuration and series IDs
    fred = config["fred"]
    required_fred_series = ["DFF", "DGS2", "DGS10", "M2SL", "CPIAUCSL", "PCEPI", "ICSA"]
    fred_series = fred.get("series", {})
    for series_id in required_fred_series:
        if series_id not in fred_series:
            raise ConfigurationError(
                f"providers.yaml: FRED series '{series_id}' missing (required per F-5a acceptance criteria)"
            )
        series_config = fred_series[series_id]
        if "series_id" not in series_config:
            raise ConfigurationError(
                f"providers.yaml: FRED series '{series_id}' missing 'series_id' field"
            )

    # Validate Yahoo Finance configuration
    yahoo = config["yahoo_finance"]
    if "tickers" not in yahoo:
        raise ConfigurationError("providers.yaml: yahoo_finance.tickers is required")

    # Validate Coinglass configuration
    coinglass = config["coinglass"]
    if "base_url" not in coinglass:
        raise ConfigurationError("providers.yaml: coinglass.base_url is required")


def _validate_thresholds_config(config: Dict[str, Any]) -> None:
    """
    Validate thresholds.yaml configuration.

    Args:
        config: Parsed thresholds configuration

    Raises:
        ConfigurationError: If configuration is invalid
    """
    # Check required top-level keys
    required_keys = ["version", "phase"]
    for key in required_keys:
        if key not in config:
            raise ConfigurationError(f"thresholds.yaml missing required key: '{key}'")

    # Validate phase is "1-2" per F-5a requirements
    if config["phase"] != "1-2":
        raise ConfigurationError(
            f"thresholds.yaml: phase must be '1-2' for F-5a (got '{config['phase']}')"
        )

    # Validate Phase 1-2 alert types are present
    required_alert_types = [
        "vol_expansion",
        "leadership_rotation",
        "breakout",
        "regime_shift",
        "correlation_break"
    ]
    for alert_type in required_alert_types:
        if alert_type not in config:
            raise ConfigurationError(
                f"thresholds.yaml missing Phase 1-2 alert type: '{alert_type}'"
            )

        # Validate alert has conditions
        alert_config = config[alert_type]
        if "conditions" not in alert_config and alert_type != "regime_shift":
            raise ConfigurationError(
                f"thresholds.yaml: alert type '{alert_type}' missing 'conditions'"
            )

        # Validate cooldown
        if "cooldown_minutes" not in alert_config:
            raise ConfigurationError(
                f"thresholds.yaml: alert type '{alert_type}' missing 'cooldown_minutes'"
            )

    # Validate regime_classifier configuration
    if "regime_classifier" not in config:
        raise ConfigurationError("thresholds.yaml missing 'regime_classifier'")

    regime_classifier = config["regime_classifier"]
    if "regimes" not in regime_classifier:
        raise ConfigurationError("thresholds.yaml: regime_classifier missing 'regimes'")

    # Validate all 5 regimes are defined
    required_regimes = [
        "RISK_ON_TREND",
        "RISK_OFF_STRESS",
        "CHOP_RANGE",
        "VOL_EXPANSION",
        "DELEVERAGING"
    ]
    regimes = regime_classifier["regimes"]
    for regime in required_regimes:
        if regime not in regimes:
            raise ConfigurationError(
                f"thresholds.yaml: regime_classifier missing regime: '{regime}'"
            )

        # Validate regime has primary_condition
        regime_config = regimes[regime]
        if "primary_condition" not in regime_config:
            raise ConfigurationError(
                f"thresholds.yaml: regime '{regime}' missing 'primary_condition'"
            )


def load_config(config_dir: Optional[Path] = None) -> Config:
    """
    Load and validate all configuration files.

    Args:
        config_dir: Path to configs directory (defaults to ./configs relative to project root)

    Returns:
        Validated Config object

    Raises:
        ConfigurationError: If any configuration file is missing or invalid
    """
    # Determine config directory
    if config_dir is None:
        # Default: configs/ directory relative to project root
        project_root = Path(__file__).parent.parent
        config_dir = project_root / "configs"

    if not config_dir.exists():
        raise ConfigurationError(f"Configuration directory not found: {config_dir}")

    # Load YAML files
    symbols_path = config_dir / "symbols.yaml"
    providers_path = config_dir / "providers.yaml"
    thresholds_path = config_dir / "thresholds.yaml"

    try:
        symbols = _load_yaml_file(symbols_path)
        providers = _load_yaml_file(providers_path)
        thresholds = _load_yaml_file(thresholds_path)
    except ConfigurationError as e:
        print(f"❌ Configuration loading failed: {e}", file=sys.stderr)
        raise

    # Validate configurations
    try:
        _validate_symbols_config(symbols)
        _validate_providers_config(providers)
        _validate_thresholds_config(thresholds)
    except ConfigurationError as e:
        print(f"❌ Configuration validation failed: {e}", file=sys.stderr)
        raise

    print("✅ All configurations loaded and validated successfully")
    return Config(symbols=symbols, providers=providers, thresholds=thresholds)


def main():
    """CLI entry point for testing configuration loading."""
    print("🔧 CryptoMacro Configuration Loader")
    print("=" * 60)

    try:
        config = load_config()

        print("\n📊 Loaded Configuration Summary:")
        print(f"   Assets: {', '.join(config.get_symbol_list())}")
        print(f"   On-chain assets: {', '.join(config.get_onchain_symbols())}")
        print(f"   Thresholds phase: {config.thresholds.get('phase')}")
        print(f"   Regime classifier: {len(config.thresholds.get('regime_classifier', {}).get('regimes', {}))} regimes")

        print("\n✅ Configuration is valid and ready for use")

    except ConfigurationError as e:
        print(f"\n❌ Configuration Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
