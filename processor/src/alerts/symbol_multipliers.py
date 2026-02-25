"""
Per-asset threshold multipliers — loaded from symbols.yaml.

Each asset declares a `threshold_multiplier` in its `properties:` block.
Evaluators multiply base thresholds by this value before comparing, making the
effective threshold = base_threshold × multiplier. A higher multiplier makes the
threshold stricter (requires a proportionally larger signal to fire).
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class SymbolMultipliers:
    """Maps short symbol name (BTC, ETH, ...) to threshold_multiplier."""

    multipliers: dict[str, float]

    def get(self, symbol: str) -> float:
        """Return multiplier for symbol; defaults to 1.0 for unknown symbols."""
        return self.multipliers.get(symbol, 1.0)

    @classmethod
    def load(cls, symbols_path: str) -> SymbolMultipliers:
        with open(symbols_path) as f:
            cfg = yaml.safe_load(f)
        # Key by binance_symbol (e.g. "BTCUSDT") — that's what alert evaluators use.
        multipliers = {
            asset["binance_symbol"]: asset["properties"]["threshold_multiplier"]
            for asset in cfg["assets"].values()
        }
        return cls(multipliers=multipliers)
