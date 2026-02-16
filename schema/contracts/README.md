# Schema Contracts

JSON Schema definitions for all data contracts in the CryptoMacro system.

These schemas define the shape and validation rules for messages passed between services (via NATS), API responses, and generated reports. Contract tests ensure we catch breaking changes early.

## Available Schemas

### 1. NATS Candle Message (`nats_candle_message.json`)

**Subject:** `market.candles.{symbol}` (e.g., `market.candles.BTCUSDT`)

**Published by:** `collector` service
**Consumed by:** `processor` service

Defines the structure for real-time market candle data from Binance WebSocket.

**Required Fields:**
- `symbol` (string): Trading pair (e.g., "BTCUSDT"), must match pattern `^[A-Z]{3,10}USDT$`
- `exchange` (string): Exchange name, currently only "binance"
- `timeframe` (string): One of "1m", "5m", "1h"
- `time` (string): ISO 8601 UTC timestamp (e.g., "2026-02-16T12:00:00Z")
- `open`, `high`, `low`, `close` (number): OHLC prices, must be > 0
- `volume` (number): Base asset volume, must be >= 0
- `quote_volume` (number): Quote asset volume in USD, must be >= 0

**Optional Fields:**
- `trades` (integer): Number of trades in this candle

---

### 2. Alert Payload (`alert_payload.json`)

**Subject:** `alerts.fired`

**Published by:** `analyzer` service
**Consumed by:** `bot` service

Defines the structure for triggered alerts sent to Discord.

**Alert Types:**
- `VOL_EXPANSION` - Volatility spike detected
- `LEADERSHIP_ROTATION` - Market leadership change
- `BREAKOUT` - Price breakout from range
- `REGIME_SHIFT` - Market regime change (bull/bear/neutral)
- `CORRELATION_BREAK` - Asset correlation breakdown
- `CROWDED_LEVERAGE` - Excessive leverage detected
- `DELEVERAGING_EVENT` - Forced liquidations cascade
- `EXCHANGE_INFLOW_RISK` - Large exchange inflows
- `NETFLOW_SHIFT` - On-chain netflow change

**Required Fields:**
- `alert_type` (string): One of the alert types above
- `severity` (string): One of "LOW", "MEDIUM", "HIGH"
- `timestamp` (string): ISO 8601 UTC timestamp
- `message` (string): Human-readable alert message

**Optional Fields:**
- `symbol` (string or null): Trading pair affected (null for market-wide alerts like REGIME_SHIFT)
- `data` (object): Additional context data

---

### 3. Daily Brief (`daily_brief.json`)

**Published by:** `analyzer` service (2x per day: 9 AM + 7 PM Dubai time)
**Consumed by:** `bot` service

Defines the structure for scheduled market summary reports.

**Required Sections:**
- `report_time` (string): ISO 8601 timestamp of report generation
- `regime_summary` (object): Current market regime assessment
- `alert_summary` (object): Summary of recent alerts
- `market_summary` (object): Overall market conditions
- `key_insights` (array): 3-5 bullet point insights
- `watch_list` (array): Assets to monitor closely
- `llm_metadata` (object): Tracks LLM model, tokens, cost, generation time

---

### 4. Event Analysis (`event_analysis.json`)

**Published by:** `analyzer` service (triggered by HIGH severity alerts)
**Consumed by:** `bot` service

Defines the structure for deep-dive analysis of significant market events.

**Required Sections:**
- `report_time` (string): ISO 8601 timestamp
- `trigger_alert` (object): The HIGH severity alert that triggered this analysis
- `context` (object): Market context and conditions
- `analysis` (object): Deep-dive analysis with summary, interpretation, and watch_next fields
- `llm_metadata` (object): Tracks LLM model, tokens, cost, generation time

---

### 5. Health Response (`health_response.json`)

**Endpoint:** `GET /api/health`

**Published by:** `api` service
**Consumed by:** Monitoring systems, dashboards

Defines the structure for the health check API response.

**Status Values:**
- `HEALTHY` - All systems operational
- `DEGRADED` - Some components experiencing issues
- `DOWN` - Critical systems offline

**Components Monitored:**
- External data sources: `binance_ws`, `coinglass`, `fred`, `yahoo_finance`, `onchain_provider`
- Internal services: `timescaledb`, `redis`, `nats`
- External services: `discord`, `claude_api`

---

## Usage

### Python Validation

```python
from schema.validator import validate_nats_candle, validate_alert, ValidationError

# Validate a NATS candle message
try:
    validate_nats_candle({
        "symbol": "BTCUSDT",
        "exchange": "binance",
        "timeframe": "1m",
        "time": "2026-02-16T12:00:00Z",
        "open": 50000.0,
        "high": 50100.0,
        "low": 49900.0,
        "close": 50050.0,
        "volume": 125.5,
        "quote_volume": 6275000.0
    })
    print("✓ Valid candle message")
except ValidationError as e:
    print(f"✗ Invalid: {e}")

# Validate an alert payload
validate_alert({
    "alert_type": "VOL_EXPANSION",
    "severity": "HIGH",
    "symbol": "BTCUSDT",
    "timestamp": "2026-02-16T14:23:00Z",
    "message": "Volatility spike detected on BTCUSDT"
})
```

### Generic Validator

```python
from schema.validator import validate

# Validate any schema type by name
validate(payload, "nats_candle")
validate(payload, "alert")
validate(payload, "daily_brief")
validate(payload, "event_analysis")
validate(payload, "health_response")
```

### Available Validators

- `validate_nats_candle(payload)`
- `validate_alert(payload)`
- `validate_daily_brief(payload)`
- `validate_event_analysis(payload)`
- `validate_health_response(payload)`

---

## Contract Tests

Contract tests verify that:
1. Valid payloads pass validation
2. Invalid payloads fail validation with clear error messages
3. Schema changes don't break existing consumers

Run all contract tests:

```bash
pytest tests/test_schema_contracts.py -v
```

Run specific test class:

```bash
pytest tests/test_schema_contracts.py::TestNATSCandleContract -v
pytest tests/test_schema_contracts.py::TestAlertContract -v
```

---

## Schema Evolution Guidelines

When modifying schemas:

1. **Never remove required fields** - This breaks existing publishers
2. **Never change field types** - This breaks all consumers
3. **Adding optional fields is safe** - Existing publishers still work
4. **Making required fields optional is safe** - Existing publishers still work
5. **Making optional fields required is BREAKING** - Old publishers will fail validation

If you must make breaking changes:
1. Version the schema (e.g., `nats_candle_message_v2.json`)
2. Update publishers and consumers in lockstep
3. Keep old schema around during transition period

---

## Schema Validation in Production

### Collector Service
```python
# Before publishing to NATS
from schema.validator import validate_nats_candle

candle_data = {...}  # From Binance WebSocket
validate_nats_candle(candle_data)  # Raises ValidationError if invalid
await nc.publish(f"market.candles.{symbol}", json.dumps(candle_data))
```

### Analyzer Service
```python
# Before publishing alerts
from schema.validator import validate_alert

alert = {...}  # Generated alert
validate_alert(alert)  # Ensure contract compliance
await nc.publish("alerts.fired", json.dumps(alert))
```

### API Service
```python
# Before returning health response
from schema.validator import validate_health_response

health_data = {...}  # Collected health metrics
validate_health_response(health_data)  # Validate before returning
return JSONResponse(content=health_data)
```

---

## Why Schema Contracts Matter

Without schema contracts, you get:
- Silent data corruption (missing fields, wrong types)
- Runtime errors in downstream services
- Difficult debugging ("Why is the bot failing?")
- Slow iteration (fear of breaking changes)

With schema contracts, you get:
- **Early detection** - Catch issues at publish time, not consume time
- **Clear contracts** - Every service knows exactly what to expect
- **Safe refactoring** - Contract tests prevent breaking changes
- **Better debugging** - Validation errors tell you exactly what's wrong

---

## Related Files

- [schema/validator.py](../validator.py) - Python validation functions
- [tests/test_schema_contracts.py](../../tests/test_schema_contracts.py) - Contract test suite
- [.claude/rules.md](../../.claude/rules.md) - See F-7 for implementation details
