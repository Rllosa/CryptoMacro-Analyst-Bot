#!/usr/bin/env python3
"""
API Sample Response Capture Script

Captures sample responses from external APIs for fixture testing:
- FRED API (macro economic data)
- Yahoo Finance (market data)
- Coinglass (derivatives data)
- CryptoQuant (on-chain data)

Output: JSON files under tests/fixtures/api_samples/
"""

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests library not installed")
    print("Install with: pip install requests")
    sys.exit(1)

# Output directory
OUTPUT_DIR = Path(__file__).parent / "api_samples"

# API endpoints and sample queries
# Note: These require API keys from .env (except Yahoo Finance)
APIS = {
    "fred": {
        "name": "FRED (Federal Reserve Economic Data)",
        "url": "https://api.stlouisfed.org/fred/series/observations",
        "params": {
            "series_id": "DFF",  # Federal Funds Rate
            "api_key": "PLACEHOLDER",  # Replace with actual key
            "file_type": "json",
            "limit": 10,
        },
        "note": "Requires FRED API key (free at https://fred.stlouisfed.org/docs/api/api_key.html)",
    },
    "yahoo_finance": {
        "name": "Yahoo Finance",
        "url": "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD",
        "params": {
            "interval": "1d",
            "range": "5d",
        },
        "note": "No API key required (public endpoint)",
    },
    "coinglass": {
        "name": "Coinglass (Derivatives Data)",
        "url": "https://open-api.coinglass.com/public/v2/indicator/funding_usd_history",
        "params": {
            "symbol": "BTC",
            "interval": "h1",
        },
        "headers": {
            "coinglassSecret": "PLACEHOLDER",  # Replace with actual key
        },
        "note": "Requires Coinglass API key (https://www.coinglass.com/pricing)",
    },
    "cryptoquant": {
        "name": "CryptoQuant (On-Chain Data)",
        "url": "https://api.cryptoquant.com/v1/btc/exchange-flows/inflow",
        "params": {
            "exchange": "binance",
            "window": "hour",
            "limit": 10,
        },
        "headers": {
            "Authorization": "Bearer PLACEHOLDER",  # Replace with actual key
        },
        "note": "Requires CryptoQuant API key (https://cryptoquant.com/pricing)",
    },
}


def capture_api_sample(api_id: str, config: dict) -> bool:
    """Capture a sample API response and save to file."""

    print(f"\n{'='*60}")
    print(f"Capturing: {config['name']}")
    print(f"{'='*60}")
    print(f"URL: {config['url']}")
    print(f"Note: {config['note']}")

    # Check if API key is placeholder
    requires_key = "PLACEHOLDER" in str(config.get("params", {})) or "PLACEHOLDER" in str(config.get("headers", {}))
    if requires_key:
        print()
        print("⚠️  API key is PLACEHOLDER - this call will likely fail")
        print("   Update the API key in this script or set environment variables")
        print("   Proceeding anyway to capture error response format...")

    try:
        # Make API request
        print()
        print("Making API request...")
        response = requests.get(
            config["url"],
            params=config.get("params", {}),
            headers=config.get("headers", {}),
            timeout=30,
        )

        # Save response
        output_file = OUTPUT_DIR / f"{api_id}_sample.json"
        result = {
            "api": api_id,
            "name": config["name"],
            "url": config["url"],
            "captured_at": datetime.utcnow().isoformat() + "Z",
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
        }

        with open(output_file, "w") as f:
            json.dump(result, f, indent=2)

        if response.status_code == 200:
            print(f"✓ Success! Status: {response.status_code}")
            print(f"  Saved to: {output_file}")
            return True
        else:
            print(f"⚠️  API returned status {response.status_code}")
            print(f"  Response saved to: {output_file}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"✗ Request failed: {e}")

        # Save error response
        output_file = OUTPUT_DIR / f"{api_id}_error.json"
        error_result = {
            "api": api_id,
            "name": config["name"],
            "url": config["url"],
            "captured_at": datetime.utcnow().isoformat() + "Z",
            "error": str(e),
        }

        with open(output_file, "w") as f:
            json.dump(error_result, f, indent=2)

        print(f"  Error details saved to: {output_file}")
        return False

    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False


def main():
    """Capture sample responses from all APIs."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("API Sample Response Capture")
    print("=" * 60)
    print()
    print(f"Output directory: {OUTPUT_DIR}")
    print()
    print("⚠️  IMPORTANT: This script requires API keys for most services")
    print("   Update the PLACEHOLDER values in APIS dict or set environment variables")
    print()

    success_count = 0
    total_count = len(APIS)

    for api_id, config in APIS.items():
        if capture_api_sample(api_id, config):
            success_count += 1

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Successfully captured: {success_count}/{total_count}")
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    if success_count < total_count:
        print("⚠️  Some API calls failed (likely due to missing/invalid API keys)")
        print("   This is expected if you haven't configured API keys yet")
        print("   Error responses are saved for reference")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("⚠️  Capture interrupted by user")
        sys.exit(1)
