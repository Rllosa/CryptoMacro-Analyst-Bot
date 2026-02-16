# Test Fixtures

Real data captured from external APIs and WebSocket streams for deterministic replay testing.

## Purpose

Fixtures enable:
- **Deterministic tests** — Same input always produces same output
- **Offline development** — Work without live API access
- **Fast test execution** — No network calls in test suite
- **Integration validation** — Verify full pipeline with real-world data

## Directory Structure

```
tests/fixtures/
├── README.md                          # This file
├── capture_binance_klines.py          # Script to capture Binance kline data
├── capture_api_samples.py             # Script to capture API sample responses
├── binance/                           # Binance WebSocket kline data
│   ├── btcusdt_1m.jsonl              # BTC 1-minute klines
│   ├── btcusdt_5m.jsonl              # BTC 5-minute klines
│   ├── btcusdt_1h.jsonl              # BTC 1-hour klines
│   ├── ethusdt_1m.jsonl              # ETH 1-minute klines
│   ├── ethusdt_5m.jsonl              # ETH 5-minute klines
│   ├── ethusdt_1h.jsonl              # ETH 1-hour klines
│   ├── solusdt_1m.jsonl              # SOL 1-minute klines
│   ├── solusdt_5m.jsonl              # SOL 5-minute klines
│   ├── solusdt_1h.jsonl              # SOL 1-hour klines
│   ├── hypeusdt_1m.jsonl             # HYPE 1-minute klines
│   ├── hypeusdt_5m.jsonl             # HYPE 5-minute klines
│   └── hypeusdt_1h.jsonl             # HYPE 1-hour klines
└── api_samples/                       # Sample API responses
    ├── fred_sample.json              # FRED API response
    ├── yahoo_finance_sample.json     # Yahoo Finance response
    ├── coinglass_sample.json         # Coinglass API response
    └── cryptoquant_sample.json       # CryptoQuant API response
```

## Fixture Formats

### Binance Kline Data (JSONL)

Format: JSON Lines (one JSON object per line)

Each line is a Binance kline message from the WebSocket stream:

```json
{
  "e": "kline",
  "E": 1708097520000,
  "s": "BTCUSDT",
  "k": {
    "t": 1708097460000,
    "T": 1708097519999,
    "s": "BTCUSDT",
    "i": "1m",
    "f": 1234567890,
    "L": 1234567900,
    "o": "50000.00",
    "c": "50050.00",
    "h": "50100.00",
    "l": "49900.00",
    "v": "125.50",
    "n": 1523,
    "x": false,
    "q": "6275000.00",
    "V": "62.75",
    "Q": "3137500.00",
    "B": "0"
  }
}
```

**Key Fields:**
- `e`: Event type ("kline")
- `E`: Event time (milliseconds)
- `s`: Symbol (e.g., "BTCUSDT")
- `k`: Kline data object
  - `t`: Kline start time (milliseconds)
  - `T`: Kline close time (milliseconds)
  - `s`: Symbol
  - `i`: Interval (e.g., "1m", "5m", "1h")
  - `o`: Open price
  - `c`: Close price
  - `h`: High price
  - `l`: Low price
  - `v`: Base asset volume
  - `q`: Quote asset volume
  - `n`: Number of trades
  - `x`: Is kline closed? (false = in progress, true = final)

**Usage:**
```python
import json

# Read kline fixture
with open("tests/fixtures/binance/btcusdt_1m.jsonl", "r") as f:
    for line in f:
        kline = json.loads(line)
        # Process kline...
```

---

### API Sample Responses (JSON)

Format: JSON object with metadata and response

```json
{
  "api": "fred",
  "name": "FRED (Federal Reserve Economic Data)",
  "url": "https://api.stlouisfed.org/fred/series/observations",
  "captured_at": "2026-02-16T13:00:00Z",
  "status_code": 200,
  "headers": {
    "content-type": "application/json",
    "...": "..."
  },
  "response": {
    "observations": [
      {
        "date": "2026-02-15",
        "value": "4.58"
      }
    ]
  }
}
```

**Fields:**
- `api`: API identifier (e.g., "fred", "yahoo_finance", "coinglass", "cryptoquant")
- `name`: Human-readable API name
- `url`: API endpoint called
- `captured_at`: Timestamp when data was captured (ISO 8601 UTC)
- `status_code`: HTTP status code
- `headers`: Response headers
- `response`: Actual API response body

**Usage:**
```python
import json

# Read API sample
with open("tests/fixtures/api_samples/fred_sample.json", "r") as f:
    sample = json.load(f)
    api_response = sample["response"]
    # Process response...
```

---

## Capturing New Fixtures

### 1. Binance Kline Data

Captures 1 hour of live kline data for BTC, ETH, SOL, HYPE (1m, 5m, 1h intervals).

**Requirements:**
- Python 3.11+
- `websockets` library: `pip install websockets`

**Run:**
```bash
cd tests/fixtures
python3 capture_binance_klines.py
```

**Duration:** 1 hour (60 minutes)

**Output:** 12 JSONL files in `binance/` directory

**What it does:**
1. Connects to Binance Futures WebSocket
2. Subscribes to kline streams for all symbols and timeframes
3. Writes messages to JSONL files in real-time
4. Runs for exactly 1 hour, then exits

**Progress:**
```
Connecting to Binance WebSocket...
Symbols: BTCUSDT, ETHUSDT, SOLUSDT, HYPEUSDT
Timeframes: 1m, 5m, 1h
Capture duration: 60 minutes

✓ Connected to Binance WebSocket
Capturing data until 2026-02-16 14:00:00 UTC...

[60s] BTCUSDT 1m: 10 messages | 59.0 min remaining
[120s] ETHUSDT 1m: 10 messages | 58.0 min remaining
...
```

---

### 2. API Sample Responses

Captures sample responses from external APIs.

**Requirements:**
- Python 3.11+
- `requests` library: `pip install requests`
- **API keys** (optional, but recommended):
  - FRED API key: https://fred.stlouisfed.org/docs/api/api_key.html (free)
  - Coinglass API key: https://www.coinglass.com/pricing
  - CryptoQuant API key: https://cryptoquant.com/pricing
  - Yahoo Finance: No key required

**Run:**
```bash
cd tests/fixtures
python3 capture_api_samples.py
```

**Duration:** ~10 seconds

**Output:** 4 JSON files in `api_samples/` directory

**Before running:**
1. Edit `capture_api_samples.py`
2. Replace `PLACEHOLDER` values with actual API keys
3. Or set environment variables (if script supports it)

**What it does:**
1. Makes one API call to each service
2. Saves response (or error) to JSON file
3. Includes metadata: timestamp, status code, headers

**Note:** Script will run even without API keys, but will save error responses. This is useful for understanding error formats.

---

## Using Fixtures in Tests

### Integration Test Example

```python
import json
from pathlib import Path

def test_normalizer_with_real_binance_data():
    """Test normalizer with captured Binance kline data."""

    # Load fixture
    fixture_path = Path("tests/fixtures/binance/btcusdt_1m.jsonl")
    klines = []
    with open(fixture_path, "r") as f:
        for line in f:
            klines.append(json.loads(line))

    # Replay through normalizer
    from processor.normalizer import process_binance_kline

    results = []
    for kline_msg in klines:
        result = process_binance_kline(kline_msg)
        results.append(result)

    # Assertions
    assert len(results) == len(klines)
    assert all(r["symbol"] == "BTCUSDT" for r in results)
    # ... more assertions
```

### API Mock Example

```python
import json
from pathlib import Path
from unittest.mock import patch

def test_fred_collector_with_fixture():
    """Test FRED collector using captured API response."""

    # Load fixture
    fixture_path = Path("tests/fixtures/api_samples/fred_sample.json")
    with open(fixture_path, "r") as f:
        sample = json.load(f)

    # Mock API call to return fixture data
    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = sample["response"]
        mock_get.return_value.status_code = sample["status_code"]

        # Run collector
        from collector.fred import fetch_fred_data
        result = fetch_fred_data("DFF")

        # Assertions
        assert result is not None
        # ... more assertions
```

---

## Fixture Maintenance

### When to Refresh Fixtures

- **Binance klines:** Refresh if market structure changes significantly (e.g., new assets, different volatility patterns)
- **API samples:** Refresh if API response format changes (schema updates, new fields)
- **Frequency:** Every 3-6 months, or when tests start failing due to schema drift

### Fixture Size

- **Binance klines:** ~60 messages per symbol per timeframe per hour = ~720 messages total
- **API samples:** 1 response per API = 4 files
- **Total size:** ~1-5 MB (compressed: ~200-500 KB)

Fixtures are committed to git for reproducibility. They're small enough to not cause repo bloat.

---

## Troubleshooting

### "websockets library not installed"

```bash
pip install websockets
```

### "requests library not installed"

```bash
pip install requests
```

### Binance WebSocket connection fails

- Check internet connection
- Verify Binance Futures is accessible (not blocked)
- Try again in a few minutes (temporary network issue)

### API calls return errors

- **Expected:** If API keys are not configured
- **Solution:** Update `capture_api_samples.py` with real API keys
- **Workaround:** Error responses are still saved and can be used for error handling tests

### JSONL files are empty

- Script may have exited early (check console output)
- Network issue during capture
- Re-run capture script

---

## Related Files

- [schema/contracts/nats_candle_message.json](../../schema/contracts/nats_candle_message.json) — Schema for NATS candle messages (derived from Binance klines)
- [tests/test_schema_contracts.py](../test_schema_contracts.py) — Contract tests that validate message schemas
- [.claude/rules.md](../../.claude/rules.md) — Section 5.2 on fixture testing philosophy

---

*Fixtures captured on 2026-02-16 as part of DI-0 (Capture Real Fixture Data)*
