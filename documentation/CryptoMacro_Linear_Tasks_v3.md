# CryptoMacro Analyst Bot — Linear Project Tasks v3 (Final)

**Project:** CryptoMacro Analyst Bot MVP v2.1
**Timeline:** 12 Weeks (6 Phases)
**Owner:** Romain
**Architecture:** Rust collectors → NATS JetStream → Python normalizer/feature/alerts → TimescaleDB + Redis → Discord bot → FastAPI + React dashboard
**Principle:** Deterministic triggers, LLM summarizes only (never triggers)

---

## Epic 1: Foundation & DevEx

*Phase 0 — Days 1–3. Repo scaffolding, infrastructure, configuration, scope enforcement, contracts.*

---

### F-1: MVP Scope Lock & Non-Goals Checklist

**Goal:** Formalize the MVP boundary as a living document (`SCOPE.md`). Prevents scope creep when deep in implementation.

**Dependencies:** None (first task)

**Requirements:**
- Create `SCOPE.md` at repo root encoding all non-goals from spec Section 1.3:
  - No automatic trading / execution
  - No raw blockchain parsing or address clustering
  - No complex predictive ML (LSTMs, transformers)
  - No news + social sentiment scraping
  - No order book / microstructure analysis
  - No multi-asset portfolio optimization or mobile app
  - No SOL/HYPE on-chain flows (BTC/ETH only)
- Include a "Stretch Goals" section for things that are tempting but explicitly deferred
- Reference this doc in the repo README and in PR templates

**Acceptance Criteria:**
- [ ] `SCOPE.md` exists at repo root with all non-goals listed
- [ ] README links to `SCOPE.md`
- [ ] Every non-goal from spec Section 1.3 is present

**Tests:**
- [ ] Manual review: confirm 1:1 mapping between spec Section 1.3 and `SCOPE.md`

---

### F-2: On-Chain Provider Decision Gate

**Goal:** Choose Glassnode vs CryptoQuant (or alternative) before writing any on-chain code. Hard gate — no on-chain work starts until resolved.

**Dependencies:** None

**Requirements:**
- Evaluate providers against MVP constraint: must supply entity-tagged exchange flows (no clustering)
- Compare: API availability, free tier limits, data resolution (hourly vs daily), BTC + ETH coverage, USD denominated values, latency, cost
- Document decision in `docs/ONCHAIN_PROVIDER.md` with rationale
- Verify API access: get key, make test call, confirm data shape matches `onchain_exchange_flows` schema
- Map provider response fields to DB columns explicitly

**Acceptance Criteria:**
- [ ] `docs/ONCHAIN_PROVIDER.md` exists with provider comparison and final decision
- [ ] Test API call returns entity-tagged exchange flow data for BTC and ETH
- [ ] Data shape confirmed compatible with `onchain_exchange_flows` table schema
- [ ] Field mapping documented (provider field → DB column)
- [ ] API key secured and added to `.env.example` as placeholder

**Tests:**
- [ ] Manual: run test script against chosen provider, verify response contains `inflow`, `outflow`, `netflow` with entity tags

---

### F-3: Docker Compose Infrastructure Setup

**Goal:** Single `docker-compose up` starts all core infrastructure: TimescaleDB, Redis, NATS JetStream.

**Dependencies:** None

**Requirements:**
- TimescaleDB container (latest stable) with `cryptomacro` database, port 5432
- Redis container with persistence (AOF or RDB), port 6379
- NATS container with JetStream enabled, ports 4222 (client) + 8222 (monitoring)
- Named volumes for all stateful services
- Internal Docker network (`cryptomacro-net`)
- `.env.example` with all required environment variables (DB credentials, API key placeholders, Discord token, Claude API key)
- Health checks on all three services

**Acceptance Criteria:**
- [ ] `docker-compose up -d` starts all 3 services without errors
- [ ] `docker-compose ps` shows all services healthy
- [ ] TimescaleDB accepts connections; `SELECT * FROM timescaledb_information.hypertables` works
- [ ] Redis responds to `PING`
- [ ] NATS JetStream reachable at `nats://localhost:4222`; monitoring at `http://localhost:8222`
- [ ] Data survives `docker-compose down && docker-compose up`
- [ ] `.env.example` is complete and documented

**Tests:**
- [ ] Automated: shell script that runs `docker-compose up`, waits for health, runs connectivity checks, tears down

---

## Implementation Notes

**NATS Healthcheck Limitation:**
- NATS does not have a Docker healthcheck in the docker-compose configuration
- **Technical constraint**: NATS official image uses a minimal scratch-based image without shell utilities (no `/bin/sh` for CMD-SHELL healthcheck)
- **Workaround**: NATS health can be monitored via HTTP monitoring endpoint at `http://localhost:8222/varz` instead
- TimescaleDB and Redis both have functional Docker healthchecks as specified
- This is a limitation of the NATS Docker image architecture, not a configuration choice

---

### F-4: Database Schema & Migrations

**Goal:** Full TimescaleDB schema from spec Section 5 — all hypertables, continuous aggregates, indexes.

**Dependencies:** F-3

**Requirements:**
- Numbered, idempotent SQL migration files covering all tables:
  - `market_candles`, `derivatives_metrics`, `macro_data` (5.1)
  - `onchain_exchange_flows`, `onchain_features` (5.2)
  - `computed_features`, `cross_features`, `regime_state`, `alerts`, `analysis_reports` (5.3)
- All time-series tables converted to hypertables
- Continuous aggregates: `candles_5m` and `candles_1h` (Section 5.4)
- Indexes on `(time, symbol)` for per-asset tables
- UUID generation enabled (`gen_random_uuid()`)
- `seed.py` script inserting test fixtures into every table

**Implementation Notes:**

**CRITICAL: TimescaleDB Composite Primary Key Requirement**

TimescaleDB requires the partitioning column (`time`) to be part of any unique constraint or primary key for hypertables. This architectural requirement was discovered during implementation.

**Pattern Applied to All 9 Hypertables:**
- `market_candles`: `PRIMARY KEY (time, symbol, timeframe)` — partitioned by (time, symbol, timeframe) for uniqueness per 1-minute candle
- `derivatives_metrics`: `PRIMARY KEY (time, symbol, exchange)` — partitioned by (time, symbol, exchange) for uniqueness per exchange
- `macro_data`: `PRIMARY KEY (time, indicator, source)` — partitioned by (time, indicator, source) for uniqueness per data source
- `onchain_exchange_flows`: `PRIMARY KEY (time, symbol, exchange, source)` — partitioned by (time, symbol, exchange, source)
- `onchain_features`: `PRIMARY KEY (time, symbol, feature_name)` — partitioned by (time, symbol, feature_name)
- `computed_features`: `PRIMARY KEY (time, symbol, feature_name)` — partitioned by (time, symbol, feature_name)
- `cross_features`: `PRIMARY KEY (time, feature_name)` — partitioned by (time, feature_name) for cross-asset features
- `regime_state`: `PRIMARY KEY (time)` — single regime per timestamp
- `alerts`: `PRIMARY KEY (id, time)` — unique alert ID + time for partitioning

**Why Standalone UUID PRIMARY KEY Doesn't Work:**
TimescaleDB error: `cannot create a unique index without the column "time" (used in partitioning)`. Attempting `id UUID PRIMARY KEY` without including `time` violates TimescaleDB's hypertable constraint requirements.

**Testing Completed:**
- ✅ All 10 migration files applied successfully on fresh TimescaleDB
- ✅ Idempotency verified: migrations run twice with no errors
- ✅ All 9 hypertables created and verified via `timescaledb_information.hypertables`
- ✅ Both continuous aggregates (`candles_5m`, `candles_1h`) created and verified
- ✅ Seed script inserted 989 rows across all 10 tables with no errors
- ✅ All acceptance criteria met

**Deliverables:**
- 10 numbered SQL migration files in `database/migrations/`
- `database/run_migrations.py` — migration runner with verification
- `database/seed.py` — test fixture seeder for all tables

**Acceptance Criteria:**
- [x] Migrations run successfully on fresh TimescaleDB
- [x] Running migrations twice produces no errors (idempotent)
- [x] All time-series tables listed in `timescaledb_information.hypertables`
- [x] `candles_5m` and `candles_1h` visible in `timescaledb_information.continuous_aggregates`
- [x] `seed.py` inserts valid data into every table
- [x] All columns match spec schema exactly (types, defaults, constraints)

**Tests:**
- [x] Automated: fresh DB → apply migrations → verify hypertable + CAGG count
- [x] `seed.py` runs without errors; spot-check 3 tables for correct row count and types

---

### F-5a: Configuration Files — MVP (Phases 1–2 Only)

**Goal:** Create the YAML configs needed for Phase 1–2 work. Ship what you need now, not everything from Section 17.

**Dependencies:** None

**Requirements:**
- `configs/symbols.yaml`: asset list (BTC, ETH, SOL, HYPE), Binance symbols, update cadences per spec Section 2
- `configs/providers.yaml`: data source config (Binance WS URLs, FRED series IDs, Yahoo tickers, on-chain provider placeholder, Coinglass placeholder)
- `configs/thresholds.yaml`: only thresholds used by Phase 1–2 alerts and regime classifier:
  - VOL_EXPANSION, LEADERSHIP_ROTATION, breakout alert thresholds
  - Regime conditions for all 5 regimes
  - Cooldown durations for Phase 1–2 alerts
- Python config loader module that validates configs on startup and rejects malformed input with clear errors
- No thresholds hardcoded in any Phase 1–2 code — everything reads from config

**Implementation Notes:**

**Configuration Architecture**

Created a modular, validated configuration system with three YAML files and a Python loader module. All Phase 1-2 services will read from these configs with no hardcoded values.

**File Structure:**
- `configs/symbols.yaml` — Asset definitions (BTC, ETH, SOL, HYPE) with Binance mappings, update cadences, and on-chain availability flags
- `configs/providers.yaml` — All data source configurations (Binance WS, FRED, Yahoo, Coinglass, on-chain provider)
- `configs/thresholds.yaml` — Phase 1-2 alert thresholds and regime classifier conditions
- `configs/loader.py` — Validation and parsing module with clear error messages
- `tests/test_config_loader.py` — 17 unit tests covering valid and malformed scenarios

**Key Implementation Decisions:**

1. **Phase Scope Enforcement**: `thresholds.yaml` explicitly marked as `"phase: 1-2"` with validation to prevent accidental Phase 3-4 threshold usage before those alerts are implemented. F-5b will add remaining thresholds.

2. **On-Chain Provider Placeholder**: `providers.yaml` includes TBD placeholder awaiting F-2 provider decision, with hard constraint documented (`entity_tagging_required: true` per SCOPE.md).

3. **Comprehensive Validation**: Config loader validates all required fields per F-5a acceptance criteria:
   - FRED series IDs: DFF, DGS2, DGS10, M2SL, CPIAUCSL, PCEPI, ICSA
   - Symbols: BTC, ETH, SOL, HYPE with Binance mappings
   - Phase 1-2 alert types: VOL_EXPANSION, LEADERSHIP_ROTATION, BREAKOUT, REGIME_SHIFT, CORRELATION_BREAK
   - All 5 regime definitions with deterministic conditions

4. **Clear Error Messages**: Malformed configs produce descriptive errors pointing to exact issue:
   - Example: `"symbols.yaml missing required key: 'all_symbols'"`
   - Example: `"FRED series 'DFF' missing (required per F-5a acceptance criteria)"`
   - Example: `"phase must be '1-2' for F-5a (got '3-4')"`

5. **Helper Methods**: Config object provides convenience methods:
   - `get_symbol_list()` → `["BTC", "ETH", "SOL", "HYPE"]`
   - `get_onchain_symbols()` → `["BTC", "ETH"]`
   - `get_alert_threshold(alert_type)` → alert configuration dict
   - `get_regime_config(regime)` → regime conditions and drivers

**Testing Completed:**
- ✅ Config loader CLI test: all 3 YAMLs loaded and validated successfully
- ✅ All 17 unit tests passing (11 valid scenarios + 6 malformed scenarios)
- ✅ FRED series IDs validated per acceptance criteria
- ✅ Symbols configuration covers all 4 assets with correct Binance mappings
- ✅ Error messages descriptive and point to exact validation failure

**Deliverables:**
- 3 YAML configuration files (`symbols.yaml`, `providers.yaml`, `thresholds.yaml`)
- Python config loader module with validation (`configs/loader.py`)
- 17 unit tests, all passing (`tests/test_config_loader.py`)
- CLI tool for config validation (`python configs/loader.py`)

**Acceptance Criteria:**
- [x] All three YAML files parse without errors
- [x] `symbols.yaml` covers all 4 assets with correct Binance mappings
- [x] `providers.yaml` has correct FRED series IDs (DFF, DGS2, DGS10, M2SL, CPIAUCSL, PCEPI, ICSA)
- [x] Config loader validates and rejects malformed configs with clear error messages
- [x] No threshold values hardcoded in Phase 1–2 service code

**Tests:**
- [x] Unit test: config loader against valid YAML → passes
- [x] Unit test: config loader against YAML with missing required field → raises descriptive error

---

### F-5b: Configuration Completeness Pass (Phase 4–5)

**Goal:** Ensure full coverage of spec Section 17 once all alert types and features are implemented.

**Dependencies:** F-5a, AL-8, AL-9 (all alert types implemented)

**Requirements:**
- Add all remaining thresholds from spec Section 17:
  - CROWDED_LEVERAGE, DELEVERAGING_EVENT thresholds
  - EXCHANGE_INFLOW_RISK, NETFLOW_SHIFT thresholds + confirmations
  - Risk score weights (on-chain anomaly, fragility, market structure)
  - Eval windows (4h market, 12h on-chain)
- Audit: every alert and regime task reads from config, zero hardcoded values

**Acceptance Criteria:**
- [ ] Every parameter from spec Section 17 present in `thresholds.yaml`
- [ ] All alert and regime code reads from config (grep for magic numbers returns nothing)
- [ ] Config loader validates all new fields

**Tests:**
- [ ] Automated: grep codebase for hardcoded threshold values → zero matches

---

### F-6: Service Skeletons & Project Structure

**Goal:** Scaffold the full project directory per spec Section 14. Every service runnable as a no-op.

**Dependencies:** F-3, F-5a

**Requirements:**
- Rust project (`collector/`): `Cargo.toml` with tokio, tungstenite, nats, serde. Skeleton `main.rs` connecting to NATS
- Python projects (`processor/`, `analyzer/`, `bot/`, `api/`): each with `pyproject.toml` or `requirements.txt`, skeleton `main.py`
- React project (`dashboard/`): Vite + React scaffold, `package.json`, `App.jsx`, empty view stubs
- `eval/` directory with empty Python files per spec
- `schema/migrations/` with migration files from F-4
- All services have Dockerfiles or are in `docker-compose.yml`

**Acceptance Criteria:**
- [ ] `cargo build` succeeds in `collector/`
- [ ] Each Python service installs deps and runs `main.py` without import errors
- [ ] `npm install && npm run dev` works in `dashboard/`
- [ ] Directory tree matches spec Section 14
- [ ] All services startable (as no-ops) via `docker-compose up`

**Tests:**
- [ ] Automated: CI script that builds all services and verifies exit code 0

---

## Implementation Notes

**Project Structure:**
- Created 6 service directories: collector/ (Rust), processor/, analyzer/, bot/, api/ (Python), dashboard/ (React)
- Moved `database/migrations/` to `schema/migrations/` for better organization
- Created `schema/contracts/` placeholder for F-7 (JSON schemas)
- Created `eval/` directory with 4 placeholder files for evaluation framework (EV-1 through EV-4)

**Service Skeletons:**
- **collector/** (Rust): Cargo.toml with async-nats 0.38, tokio 1.42, tokio-tungstenite 0.26, serde, tracing. Multi-stage Dockerfile for optimized builds
- **processor/** (Python): NATS-to-TimescaleDB normalizer with nats-py, psycopg3, structlog
- **analyzer/** (Python): Feature engine + regime + alerts with pandas, numpy, ta library, pyyaml for config loading
- **bot/** (Python): Discord bot with discord.py 2.3, anthropic SDK 0.40+ for LLM integration
- **api/** (Python): FastAPI REST API with uvicorn standard server
- **dashboard/** (React): Vite + React 18 with react-router-dom, 6 view stubs (CommandCenter, AssetDetail, MacroDashboard, OnChainIntelligence, IntelligenceCenter, Evaluation), lightweight-charts and recharts for data visualization

**Docker Compose Integration:**
- All 6 services added to docker-compose.yml with proper dependency chains
- collector → depends on NATS
- processor → depends on TimescaleDB + NATS
- analyzer → depends on TimescaleDB + Redis + NATS (mounts configs/ volume read-only)
- bot → depends on TimescaleDB + Redis + NATS
- api → depends on TimescaleDB + Redis, exposes port 8000
- dashboard → depends on api, exposes port 3000 (nginx serves on port 80 internally)

**Verification:**
- All Python services verified with `python3 -m py_compile` - no syntax errors
- Rust collector structure validated (Cargo.toml dependencies defined, builds in Docker)
- docker-compose.yml syntax validated

**Technical Decisions:**
- Used pyproject.toml (PEP 518) for all Python services instead of requirements.txt for modern Python packaging
- Used multi-stage Docker builds for Rust collector to minimize final image size
- Dashboard uses nginx in production Dockerfile for static file serving
- All Python services use python:3.11-slim base image for consistency
- Analyzer service mounts configs/ directory read-only for threshold access

**Phase 0 Gate Verification Fixes (2026-02-16):**

During Phase 0 gate verification (`docker-compose up` full test), encountered and fixed the following issues:

**Fix 1: Docker Build Context Issue**
- **Problem:** analyzer/Dockerfile had `COPY ../configs /app/configs` which failed because Docker build context cannot access parent directories
- **Solution:** Removed the invalid COPY command from analyzer/Dockerfile:19 - configs are already mounted as read-only volumes in docker-compose.yml
- **Files:** analyzer/Dockerfile

**Fix 2: NATS Package Name Correction**
- **Problem:** All Python services used non-existent package `asyncio-nats-client>=2.9.0` (latest version is 0.11.5)
- **Solution:** Updated all pyproject.toml files to use correct modern NATS package: `nats-py>=2.9.0`
- **Files:** analyzer/pyproject.toml, processor/pyproject.toml, bot/pyproject.toml

**Fix 3: Migrations Path Update**
- **Problem:** database/run_migrations.py still referenced old path `database/migrations` after F-6 reorganization
- **Solution:** Updated path to new location `schema/migrations` in run_migrations.py:156
- **Files:** database/run_migrations.py

**Rust Collector Note:**
- Rust collector has unresolved dependency issue: transitive dependency `time-core-0.1.8` requires edition2024 which isn't stable in Cargo 1.84.1
- This is non-blocking for Phase 0 since collector skeleton exists and will be properly implemented in DI-1 (Binance WebSocket Collector)
- Deferred proper fix to DI-1 when implementing actual collector functionality

**Verification Results:**
- ✅ All infrastructure services healthy (TimescaleDB, Redis, NATS)
- ✅ All Python services build and run successfully
- ✅ Migrations applied successfully: 10 migrations, 9 hypertables, 2 CAGGs
- ✅ Config loader tests: 17/17 passing
- ✅ Schema tests: 14/14 passing
- ✅ API responding at http://localhost:8000

**Branch:** romain/fix-phase0-gate-issues

---

### F-7: Schema Contracts (JSON Schemas + Validators)

**Goal:** Prevent silent breaking changes across NATS, DB, API, and LLM boundaries. When you refactor one service, contract tests catch what you broke.

**Dependencies:** F-4, F-6

**Requirements:**
- JSON schema files under `schema/contracts/`:
  - NATS candle message schema (`market.candles.*`)
  - Alert payload schema (`alerts.fired`)
  - Daily brief output schema (spec Section 10.1)
  - Event analysis output schema
  - `/api/health` response schema
- Python validators that test payloads against schemas
- Validators used in tests; optionally wired into runtime for debug builds

**Acceptance Criteria:**
- [ ] Schema files exist under `schema/contracts/`
- [ ] Validators can validate/reject sample payloads
- [ ] Any schema change requires updating corresponding contract tests
- [ ] Schemas match the spec (NATS message matches what collector publishes, alert payload matches what alert engine produces)

**Tests:**
- [ ] Contract tests: validate known-good fixtures against each schema → pass
- [ ] Contract tests: validate intentionally-bad fixtures → fail with descriptive error

---

## Epic 2: Data Ingestion

*Phase 1 (Weeks 1–2) for crypto, Phase 2 (Weeks 3–4) for macro, Phase 3 (Weeks 5–6) for derivatives, Phase 4 (Weeks 7–8) for on-chain.*

---

### DI-0: Capture Real Fixture Data

**Goal:** Record real WS + API responses for deterministic replay tests. ~1 hour of work that pays for itself every time you touch ingestion or features.

**Dependencies:** None (can be done in parallel with F-3)

**Requirements:**
- Record 1 hour of Binance Futures kline messages for BTC and ETH (optionally SOL/HYPE)
- Record 1 sample response each for: FRED API, Yahoo Finance, Coinglass, on-chain provider (once F-2 is done)
- Store under `tests/fixtures/` in a replayable format (JSON lines or raw JSON)
- Document fixture format in `tests/fixtures/README.md`

**Acceptance Criteria:**
- [ ] Fixture files stored under `tests/fixtures/`
- [ ] Binance fixture contains >= 60 minutes of candle data for BTC and ETH
- [ ] Each API fixture contains a valid sample response
- [ ] Fixtures are replayable in integration tests

**Tests:**
- [ ] Replay test: Binance fixture → normalizer writes correct DB rows (validates full chain)

---

### DI-1: Binance WebSocket Collector (Rust)

**Goal:** Real-time Rust collector connecting to Binance Futures WS for BTC, ETH, SOL, HYPE. Normalizes and publishes to NATS JetStream.

**Dependencies:** F-3, F-6, F-7

**Implementation Notes (2026-02-17):**

- **Binary + lib.rs pattern:** Added `src/lib.rs` re-exporting all public modules so `tests/integration.rs` can import crate types (`cryptomacro_collector::models::*`). Required because Rust integration tests can only import library crates, not binaries.
- **TLS: `rustls-tls-native-roots` over `native-tls`:** Pure-Rust TLS avoids an OpenSSL build dependency in the `rust:1.85-slim` Docker image.
- **`time = "=0.3.36"` pin in Cargo.toml:** `async-nats 0.38` pulls `time` transitively. Versions ≥ 0.3.37 require rustc 1.88.0 due to edition2024 changes; Docker image ships rust:1.85. Since `Cargo.lock` is gitignored, the pin must live in `Cargo.toml`.
- **Dockerfile: `rust:1.84-slim` → `rust:1.85-slim`:** Resolves `time-core` edition2024 build failure that was flagged as a blocker in F-6/SOLO-28.
- **Publish every tick, not just closed candles:** The feature engine benefits from in-progress candle updates. The `is_closed` flag is logged at DEBUG so downstream can filter.
- **Heartbeat: warn-and-continue:** Timeout logs WARNING but does not force-disconnect. Binance maintenance windows can cause short silences; a reconnect would just re-open and hit the same silence.
- **15 tests total:** 10 unit tests (models, config, collector modules) + 5 integration tests replaying DI-0 JSONL fixtures. `cargo clippy -- -D warnings` and `cargo fmt --check` both pass.

**Requirements:**
- Connect to `wss://fstream.binance.com` for `btcusdt`, `ethusdt`, `solusdt`, `hypeusdt`
- Subscribe to kline (1m candles) and aggTrade streams
- Parse and normalize into unified schema (timestamp UTC, symbol, OHLCV)
- Published messages must pass NATS candle schema validation (F-7)
- Publish to NATS JetStream subject `market.candles.{symbol}`
- Automatic reconnection with exponential backoff on disconnect
- Heartbeat monitoring: log warning if no message in 30 seconds
- Graceful shutdown on SIGTERM
- If NATS is down: keep reconnecting and log degraded state (no local buffering for MVP)

**Acceptance Criteria:**
- [ ] Connects to all 4 Binance WS streams within 5 seconds
- [ ] Over 1 hour, observed candle count matches expected within ±1% per symbol
- [ ] Any detected gaps are logged with timestamps
- [ ] Disconnecting Binance WS triggers reconnect within 10 seconds
- [ ] Published messages pass candle schema validation (F-7)
- [ ] Heartbeat warnings logged when stream goes silent
- [ ] `Ctrl+C` triggers clean shutdown
- [ ] NATS disconnection logged as degraded; collector keeps running and retries

**Tests:**
- [ ] Integration test: replay fixture (DI-0) → assert NATS output matches schema
- [ ] Integration test: drop network → verify reconnect within 10s
- [ ] Schema validation test: published messages pass contract (F-7)
- [ ] Manual: 1h parity test vs expected candle counts

---

### DI-2: NATS-to-TimescaleDB Normalizer

**Goal:** Python service consuming NATS candle messages and persisting to `market_candles`. Handles gap backfilling on startup.

**Dependencies:** DI-1, F-4

**Requirements:**
- Subscribe to `market.candles.*` NATS subjects
- Batch insert to `market_candles` (batch size configurable, default 100 or 5 seconds)
- Deduplication: skip inserts for existing (time, symbol, exchange) — upsert or conflict-ignore
- Gap detection on startup: query last timestamp per symbol, backfill from Binance REST if gap > 5 minutes
- UTC timestamp normalization
- Connection pooling to TimescaleDB

**Acceptance Criteria:**
- [ ] Candles from all 4 symbols appear in `market_candles` within 10 seconds of publishing
- [ ] Duplicate messages do not create duplicate rows
- [ ] 10-minute outage gap filled from Binance REST on restart
- [ ] Gaps detected and logged with symbol + time range
- [ ] `candles_5m` and `candles_1h` continuous aggregates populated automatically
- [ ] DB connection survives TimescaleDB restart

**Implementation Notes (2026-02-18):**

**Dual-flush policy:** Flush triggered by whichever comes first — `batch_size` messages accumulated (default 100) OR `batch_timeout_secs` elapsed (default 5 s) since last flush. Bounds both write latency and write amplification under variable load.

**At-least-once delivery via JetStream acks:** Messages are NOT acked on DB failure — JetStream redelivers after `ack_wait` expires. Acks fire concurrently via `asyncio.gather` after a successful batch write.

**Single multi-row INSERT:** `upsert_candles` builds one `INSERT INTO market_candles ... VALUES (...),(...) ON CONFLICT (time, symbol, timeframe) DO NOTHING` per flush — one server round-trip regardless of batch size (not `executemany`). Placeholder string `_ROW_PH` hoisted at import time.

**Pydantic validation on every message:** `CandleMessage.model_validate(data)` validates schema; bad-JSON and bad-schema failures are logged with separate event keys so they're distinguishable in log analysis.

**Gap backfill on startup:** `run_backfill` queries `MAX(time)` per symbol, compares to `now`, fetches from Binance Futures REST (`/fapi/v1/klines`) if gap > `gap_threshold_minutes` (default 5 min). All 4 symbols run concurrently via `asyncio.gather`. Paginates with 1 000-kline pages (Binance max), stops when response is shorter than limit.

**NATS connect with exponential backoff:** Retries up to 10 times with `min(2^(attempt-1), 30)` second delay. Same backoff pattern used in `db.py` (`create_pool_with_retry`).

**Tests:**
- [ ] Fixture replay (DI-0): replay recorded candles → DB row count matches expected
- [ ] Duplicate replay: same fixture twice → single row per candle only
- [ ] Integration: insert gap in DB, restart normalizer → verify backfill fires

---

### DI-3: FRED API Collector

> **DEFERRED to Phase 5+** — Full FRED series (M2, Fed Funds, CPI, PCE, Jobless Claims) not needed for MVP. VIX + DXY via Yahoo Finance (DI-4) is sufficient for macro stress. FRED data is weekly/monthly — too lagging for 5-minute alert cycles. Revisit post-MVP if macro granularity becomes necessary.

**Goal:** Collect macro indicators from FRED: Fed Funds Rate, 2Y/10Y Yields, M2, CPI, PCE, Jobless Claims.

**Dependencies:** F-4, F-5a

**Requirements:**
- FRED API client with series IDs from `providers.yaml`
- Polling: daily for rates/yields, weekly for M2/CPI/PCE/Jobless Claims
- Write to `macro_data` with `source = 'fred'`
- Respect FRED rate limits (120 req/min)
- Backfill last 2 years on first run
- Rate-limit/backoff implemented

**Acceptance Criteria:**
- [ ] All 7 FRED series fetched and stored
- [ ] 2-year backfill on first run
- [ ] Subsequent runs fetch only new data (incremental)
- [ ] No 429 errors
- [ ] FRED down → macro degraded state, crypto continues unaffected

**Tests:**
- [ ] Unit test: mock FRED API response → verify correct `macro_data` row structure
- [ ] Mock FRED failure → verify degraded state logged, no crash
- [ ] Manual: verify 2-year backfill by querying earliest timestamp per series

---

### DI-4: Yahoo Finance Collector

**Goal:** Real-time macro market data: DXY, S&P 500, Nasdaq, VIX, Gold.

**Dependencies:** F-4, F-5a

**Requirements:**
- Poll Yahoo Finance for DXY, ^GSPC, ^IXIC, ^VIX, GC=F every 5 minutes during market hours
- Write to `macro_data` with `source = 'yahoo'`
- Handle market closures gracefully (no errors on weekends/holidays, use last known value)
- Backfill 2 years of daily data on first run
- Rate-limit/backoff on 429s and timeouts

**Acceptance Criteria:**
- [ ] All 5 tickers fetched and stored
- [ ] Updates every 5 minutes during US market hours
- [ ] No errors during weekends/holidays
- [ ] Last known value cached in Redis
- [ ] Runs 48 hours locally without repeated failures (solo reliability check)
- [ ] Yahoo down → macro degraded, crypto continues

**Tests:**
- [ ] Unit test: mock Yahoo response → verify `macro_data` row structure
- [ ] Simulate 429/timeout → verify backoff + degrade behavior
- [ ] Manual: run on weekend, confirm no errors

---

### DI-5: Coinglass API Collector

> **PHASE 1.5 — HIGHEST PRIORITY.** Unlocks DELEVERAGING regime (currently dead code — needs `liquidations_1h_usd`, `oi_drop_1h`) and CROWDED_LEVERAGE alert (`funding_zscore > 2.5 + OI spike`). Implement immediately after FE-6.

**Goal:** Derivatives data: funding rates, open interest, liquidations for BTC, ETH, SOL, HYPE.

**Dependencies:** F-4, F-5a

**Data to collect (via Coinglass API):**

| Endpoint | Data | Polling |
|----------|------|---------|
| `/funding` | Funding rate per symbol × exchange | 5 min |
| `/open_interest` | OI in USD per symbol × exchange | 5 min |
| `/liquidation` | Long/short liq volume per symbol | 1 min |

**Exchanges:** Binance, OKX, Bybit

**Requirements:**
- Coinglass API client for BTC, ETH, SOL, HYPE
- Aggregated funding rates across Binance, OKX, Bybit
- Total + per-exchange OI (USD)
- Liquidation data: long/short, size, exchange, timestamp
- Write to `derivatives_metrics`
- Polling: every 5 minutes for funding/OI, 1 minute for liquidations
- Graceful degradation: if unavailable, derivatives features → NaN
- Max 3 consecutive failures before marking degraded

**Acceptance Criteria:**
- [ ] Funding, OI, and liquidation data stored for all 4 assets × 3 exchanges
- [ ] If Coinglass down → derivatives alerts disabled only, everything else continues
- [ ] No API key leakage in logs
- [ ] Data written to `derivatives_metrics` table (not raw storage)

**Tests:**
- [ ] Unit test: mock Coinglass response → verify `derivatives_metrics` row structure
- [ ] Mock failure → verify degraded state logged, derivatives alerts disabled, other alerts unaffected

---

### DI-9: On-Chain Exchange Flow Collector (BTC/ETH Only)

> **Renumbered from DI-6 to DI-9** to avoid collision with Deribit DVOL (now DI-6) and CoinGecko (DI-7), News Feed (DI-8) added in Phase 2. References in OPS-4 and FE-5 updated accordingly.

**Goal:** Integrate the chosen entity-tagged on-chain provider (from F-2) for BTC and ETH exchange flows.

**Dependencies:** F-2 (hard gate), F-4

**Requirements:**
- MVP CONSTRAINT enforced: entity-tagged exchange flows only. No clustering. Hard gate.
- Fetch: exchange inflow, outflow, netflow for BTC and ETH
- Both native units and USD values (schema-level requirement)
- Write to `onchain_exchange_flows`
- Hourly polling (or best available resolution)
- Backfill 90 days on first run
- Graceful degradation if provider API is down

**Acceptance Criteria:**
- [ ] BTC and ETH flows stored in `onchain_exchange_flows`
- [ ] Data is entity-tagged (source field confirms provider, not derived)
- [ ] Both native and USD values present
- [ ] 90-day backfill complete
- [ ] Provider down → on-chain degraded, on-chain alerts disabled only

**Tests:**
- [ ] Integration test: verify API response fields map correctly to DB columns
- [ ] Mock provider down → verify graceful degradation

---

## Epic 3: Storage

*Phase 1. Schema is in F-4; this epic covers performance and query utilities.*

---

### ST-1: TimescaleDB Performance & Query Utilities

**Goal:** Make sure the DB is usable for fast reads and that continuous aggregates refresh automatically.

**Dependencies:** F-4

**Requirements:**
- Verify indexes are hit for common query patterns (EXPLAIN ANALYZE)
- CAGG refresh policies configured (auto-refresh, not manual)
- Query helper functions or SQL snippets for:
  - Latest state per symbol (most recent `computed_features` row)
  - Last 24h candles/features for a symbol
  - Last N alerts with optional type/severity filter
- Compression policies on `market_candles` (compress chunks older than 7 days)
- Retention policy: raw 1-minute candles for 90 days, aggregates indefinitely
- Document policies in `docs/STORAGE.md`

**Acceptance Criteria:**
- [ ] "Latest state per symbol" query runs under 50ms on seeded DB
- [ ] CAGGs refresh automatically without manual intervention
- [ ] Compression policies active (verified with `timescaledb_information.compression_settings`)
- [ ] Retention drop policy active for `market_candles` at 90 days
- [ ] `docs/STORAGE.md` documents all policies and query patterns

**Tests:**
- [ ] Automated: query helpers return expected outputs on seeded DB
- [ ] Manual: insert old test data, trigger compression, verify chunk size reduction

---

## Epic 4: Features & Regime Engine

*Phase 1 (Weeks 1–2) for core features, Phase 2 (Weeks 3–4) for macro + regime.*

---

### FE-1: Feature Engine — Core Indicators (5-Minute Cycle)

**Goal:** Compute all per-asset technical indicators every 5 minutes, write to `computed_features`.

**Dependencies:** DI-1, DI-2, F-4

**Requirements:**
- Scheduled every 5 minutes
- Per symbol, compute from `candles_5m` and `candles_1h`:
  - Returns: `r_5m`, `r_1h`, `r_4h`, `r_1d`
  - Realized volatility: `rv_1h`, `rv_4h`
  - RSI(14), MACD + signal, Bollinger Bands, ATR(14)
  - EMA slope, volume z-score
  - Breakout flags: `breakout_high_4h`, `breakout_low_4h`, `breakout_high_24h`, `breakout_low_24h`
- Write to `computed_features`
- Cache latest snapshot in Redis
- Missing data for one symbol doesn't crash others

**Acceptance Criteria:**
- [ ] New rows in `computed_features` every 5 minutes for all 4 symbols
- [ ] RSI always 0–100
- [ ] Bollinger Bands: `bb_upper > close > bb_lower` under normal conditions
- [ ] Breakout flags correctly detect 4h/24h high/low exceedances
- [ ] Redis contains latest feature snapshot per symbol
- [ ] One symbol missing data doesn't affect others
- [ ] Deterministic: replay same candles → identical features

**Implementation Notes (2026-02-18):**

**Feature engine in `processor/`** (not `analyzer/`): `processor/` owns all data pipeline components — normalizer, feature engine, regime classifier, alert engine. `analyzer/` is LLM-only. `features/` package lives at `processor/src/features/`.

**EAV schema for `computed_features`:** One row per `(time, symbol, feature_name)` — avoids wide-table schema churn when adding or removing features. Single multi-row `INSERT ... ON CONFLICT (time, symbol, feature_name) DO NOTHING` per cycle (not `executemany`). Placeholder string `_FEATURE_PH` hoisted at module level.

**22 features per symbol per cycle:** `r_5m`, `r_1h`, `r_4h`, `r_1d`, `rv_1h`, `rv_4h`, `rsi_14`, `macd`, `macd_signal`, `macd_hist`, `bb_upper`, `bb_middle`, `bb_lower`, `bb_pct_b`, `bb_bandwidth`, `atr_14`, `ema_slope`, `volume_zscore`, `breakout_4h_high`, `breakout_4h_low`, `breakout_24h_high`, `breakout_24h_low`.

**Zero hardcoded thresholds:** All indicator parameters (`rsi_period`, `macd_fast/slow/signal`, `bollinger_period`, `bollinger_std`, `atr_period`, `rv_window_1h`, `rv_window_4h`, `volume_zscore_window`, `breakout_4h_window`, `breakout_24h_window`, `ema_slope_period`) loaded from `configs/thresholds.yaml` via `FeatureParams` frozen dataclass.

**`ta` library for indicators:** `RSIIndicator`, `MACD`, `BollingerBands`, `AverageTrueRange`, `EMAIndicator` from `ta.momentum`, `ta.trend`, `ta.volatility`. Annualised RV: `std(log_returns) * sqrt(105120)` where `105120 = 365 × 24 × 12` (5-min periods per year), hoisted as `_PERIODS_PER_YEAR`.

**NaN filtering before DB insert:** `math.isnan(value)` guard in `_compute_symbol` prevents inserting NULL into the `NOT NULL value` column when insufficient candle history is available (< `bollinger_period` rows).

**Per-symbol `asyncio.gather` with `return_exceptions=True`:** One failing symbol (e.g. insufficient data) never crashes others — graceful degradation per rule 1.3. Failures logged as warnings.

**Redis cache:** Key `features:latest:{symbol_lower}`, TTL = 600 s. Payload: `{"time": iso_str, "features": {name: value}}`. NaN values serialised as `null`.

**Tests:**
- [ ] Golden fixture test: known candle sequence → expected RSI, MACD, BB, ATR values (3 test vectors minimum)
- [ ] Unit test: missing candle data for one symbol → graceful skip, warning logged, other symbols unaffected

---

### FE-2: Feature Engine — Cross-Asset Features

**Goal:** Compute relative strength ratios and leadership metrics.

**Dependencies:** FE-1

**Requirements:**
- `eth_btc_rs`, `sol_btc_rs`, `hype_btc_rs` relative strength ratios
- RS z-scores for leadership rotation detection
- Rolling correlations: `corr_btc_sp500`, `corr_btc_dxy` (stub NaN until macro data available)
- `corr_btc_sp500_7d` (7-day rolling)
- `macro_stress` composite (stub 0.0 until macro integration)
- Write to `cross_features` every 5 minutes

**Acceptance Criteria:**
- [ ] `cross_features` rows generated every 5 minutes
- [ ] RS ratios are valid decimals (NaN only if data missing)
- [ ] Correlation fields NULL until macro data integrated
- [ ] `macro_stress` defaults to 0.0
- [ ] Leadership metrics update every 5 minutes

**Tests:**
- [ ] Golden fixture: known price sequences for BTC/ETH/SOL → expected RS ratios and z-scores
- [ ] Fixture: leadership rotation scenario → expected RS values trigger rotation detection

**Implementation Notes (merged PR #13):**
- **RS formula — return difference (alpha):** `RS[t] = (alt[t]/alt[t-N] - 1) - (btc[t]/btc[t-N] - 1)`. Ratio variant (`alt_return / btc_return`) is numerically unstable when BTC return ≈ 0; alpha never divides by BTC return.
- **Correlation stubs absent from output:** `corr_btc_sp500`, `corr_btc_dxy`, `corr_btc_sp500_7d` emit no rows until FE-3. Absent rows = NULL for consumers (safer than NaN in a NOT NULL column). `macro_stress` stubbed at `0.0` and written as a real row.
- **Single DB round-trip for close fetch:** window function `ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time DESC)` fetches N candles for all 4 symbols in one query.
- **Constant hoisting:** `_ALT_PAIRS` stores pre-built `(symbol, rs_key, zscore_key)` — no f-strings in the compute loop. `_ASSETS_INVOLVED` hoisted at module level. `[_CROSS_PH] * len(rows)` uses list multiplication (O(1)) — rows iterated exactly once.
- **Candle window:** `rs_zscore_window (60) + rs_lookback (20) = 80`, computed once in `__init__`.
- **36 new tests (126 total):** golden fixture vectors with analytically-derived z-score values (≈±2.7429 for surge/decline), symmetry checks, edge cases.

---

### FE-3: Macro Stress Composite (0–100)

> **SIMPLIFIED SCOPE (Phase 2).** Original plan included full FRED macro composite. Scope reduced to VIX + DXY only — FRED series deferred to Phase 5+. Implement after DI-4 (Yahoo Finance) ships.

**Goal:** Compute `macro_stress` (0–100) feeding regime classification (RISK_OFF_STRESS) and alert severity.

**Dependencies:** DI-4, FE-2 (DI-3 dependency removed — FRED deferred)

**Revised inputs (Phase 2 only):**

| Feature | Source | Update freq |
|---------|--------|-------------|
| `vix` | Yahoo Finance DI-4 (^VIX) | daily |
| `dxy_momentum` | Yahoo Finance DI-4 (DX-Y.NYB) | daily |

**Output features:**
- `macro_stress` — composite 0–100: `vix_norm * 0.6 + dxy_stress * 0.4`
- `vix` — raw VIX level (passed through to `cross_features`)
- `dxy_momentum` — 5-day DXY rate-of-change

**Requirements:**
- Weights loaded from `thresholds.yaml`, not hardcoded
- Write to `cross_features.macro_stress` every 5 minutes
- Update `corr_btc_sp500`, `corr_btc_dxy`, `corr_btc_sp500_7d` when data available

**Acceptance Criteria:**
- [ ] `macro_stress` updates every 5 minutes, always 0–100
- [ ] High VIX + DXY spike → stress > 60
- [ ] Calm markets → stress < 30
- [ ] Weights loaded from YAML config
- [ ] Degrades gracefully when Yahoo Finance unavailable

**Tests:**
- [ ] Golden fixture: known VIX + DXY values → expected stress score
- [ ] Unit test: verify weights read from `thresholds.yaml` not hardcoded

---

### FE-4: Derivatives Feature Computation

> **PHASE 1.5** — Consumes Coinglass (DI-5) data. Must be implemented after DI-5 ships. These features activate DELEVERAGING regime and CROWDED_LEVERAGE alert which are currently dead code.

**Goal:** Compute derivatives features from Coinglass data, written to `features:latest:{symbol}` alongside FE-1 features.

**Dependencies:** DI-5 (hard dependency — no data without Coinglass)

**Input tables (written by DI-5):**
- `coinglass_funding_rate` — 8h snapshots per symbol
- `coinglass_open_interest` — 5m snapshots per symbol
- `coinglass_liquidations` — 1h aggregated per symbol

**Output features (added to `features:latest:{symbol}`):**

| Feature | Formula | Purpose |
|---------|---------|---------|
| `funding_zscore` | z-score vs 30-day rolling | CROWDED_LEVERAGE trigger |
| `oi_drop_1h` | % OI drop in last hour (negative = drop) | DELEVERAGING trigger |
| `liquidations_1h_usd` | Total USD liq volume in last hour | DELEVERAGING trigger |

**Requirements:**
- Add to `computed_features` (fill previously NULL derivatives columns)
- Cache in Redis alongside FE-1 features (same `features:latest:{symbol}` key)
- Degrade to NaN when Coinglass unavailable — no crash

**Acceptance Criteria:**
- [ ] All three features computed every 5 minutes
- [ ] Z-scores mathematically correct (verified on sample data)
- [ ] Degrade to NaN without crash when Coinglass unavailable
- [ ] `features:latest:{symbol}` Redis key contains derivatives fields when data available

**Tests:**
- [ ] Golden fixture: known funding/OI/liq sequences → expected z-scores
- [ ] Unit test: empty derivatives data → NaN output, no crash

---

### FE-5: On-Chain Feature Computation (BTC/ETH Only)

**Goal:** Compute on-chain flow features and risk score for BTC and ETH.

**Dependencies:** DI-6

**Requirements:**
- `inflow_zscore`, `netflow_zscore`: z-score vs 30-day rolling
- `netflow_sma_1d`, `netflow_sma_7d`
- `flow_persistence`: categorical — "accumulation", "distribution", "neutral"
- `risk_score`: composite per spec Section 7 (onchain_anomaly + fragility + market_structure)
- Weights loaded from `thresholds.yaml` — configurable, default equal (0.333 each)
- Risk score output includes component breakdown for debugging/display
- Write to `onchain_features`

**Acceptance Criteria:**
- [ ] All features computed hourly (matching provider resolution)
- [ ] Z-scores correct (verified on 3 sample periods)
- [ ] `flow_persistence` correctly categorizes SMA crossover
- [ ] `risk_score` between 0–100, includes component breakdown
- [ ] Weights loaded from YAML, not hardcoded
- [ ] Degrades gracefully if on-chain data unavailable

**Tests:**
- [ ] Golden fixture: known flow sequences → expected z-scores + risk score
- [ ] Unit test: verify risk score weights come from config, not constants

---

### FE-6: Regime Classifier (5 Regimes, Deterministic)

> **DONE — PR #20 merged (2026-02-23).** See implementation notes below. FE-3 dependency removed — `macro_stress` stubbed at `0.0` until FE-3 ships (zero is safe default). INDETERMINATE handling moved to AL-5 (see there).

**Goal:** Rules-based regime classifier with 5 states, evaluating every 5 minutes. Entirely deterministic.

**Dependencies:** FE-1, FE-2 (FE-3 not a hard blocker — macro_stress stubs to 0.0 until FE-3 ships)

**Requirements:**
- 5 regimes: `RISK_ON_TREND`, `RISK_OFF_STRESS`, `CHOP_RANGE`, `VOL_EXPANSION`, `DELEVERAGING`
- All conditions and thresholds from `thresholds.yaml` — no hardcoded values
- Confidence score (0.0–1.0): weighted sum of conditions met + z-score bonus
- Below min_confidence (0.4) → `regime=None` (uncertain); no DB write, Redis still written
- `contributing_factors` JSONB logged per cycle
- Write to `regime_state` every 5 minutes (skipped when uncertain)
- Cache `regime:latest` in Redis (TTL 600s) — always written, even when uncertain
- Tiebreak priority: DELEVERAGING > RISK_OFF_STRESS > VOL_EXPANSION > RISK_ON_TREND > CHOP_RANGE

**Implementation Notes (PR #20):**
- `processor/src/regime/` — 5 files: `__init__.py`, `config.py`, `classifier.py`, `db.py`, `engine.py`
- `RegimeParams` frozen dataclass loads from `thresholds.yaml`; `tight_bb_bandwidth_max: 0.03` added
- `_build_regime_inputs()` maps raw Redis feature names to semantic names (single translation layer)
- `_compute_rv_4h_zscore()` duplicated from `vol_expansion.py` (avoids coupling); in-memory `deque(maxlen=288)`
- FE-3/FE-4 fields (`vix`, `dxy_momentum`, `funding_zscore`, etc.) default to `0.0` until those engines ship
- 18 tests — all pass; wired into `main.py` alongside other engines

**Known issue (FIX-1):** `volatility_regime = "high" if rv_4h_zscore > 0 else "low"` fires ~50% of the time by construction. Fix: `> 0.5`. See FIX-1 task.

**Acceptance Criteria:**
- [x] Regime computed every 5 minutes
- [x] Each regime activates under documented conditions
- [x] Confidence always 0.0–1.0
- [x] Below 0.4 → `regime=None`, DB write skipped, Redis still written
- [x] Redis reflects latest regime with < 1s latency
- [x] Deterministic: same inputs → same regime always

**Tests:**
- [x] 18 tests — unit tests for `_eval_condition`, `classify_regime`, `RegimeParams`, integration tests for `_run_cycle`, DB unit test for `insert_regime`

---

## Epic 5: Alert Engine

*Phase 1 (Weeks 1–2) for market alerts, Phase 3 (Weeks 5–6) for derivatives, Phase 4 (Weeks 7–8) for on-chain.*

---

### AL-1: Alert Engine Core — Cooldowns, Dedup, Persistence, Storage

**Goal:** Shared alert infrastructure used by all 8 alert types. Build once, well.

**Dependencies:** F-4, F-7, FE-1

**Requirements:**
- Cooldown registry: per (type, symbol, direction), durations from `thresholds.yaml`
- Deduplication: same alert signature within cooldown → suppressed
- Persistence: configurable "persist N cycles" — condition must hold for N consecutive 5m cycles before firing
- Alert payload builder: `input_snapshot` JSONB with all feature values at trigger time
- Payloads must pass alert schema validation (F-7)
- Write fired alerts to `alerts` table
- Publish to NATS `alerts.fired`
- Target: < 10 alerts/day average
- Alerts reproducible using stored `input_snapshot` (replay capability)

**Acceptance Criteria:**
- [ ] Cooldown correctly suppresses duplicate alerts within window
- [ ] Persistence requires N consecutive cycles before firing
- [ ] `alerts` rows contain complete `input_snapshot`
- [ ] NATS `alerts.fired` receives payload on fire
- [ ] All cooldown durations loaded from `thresholds.yaml`
- [ ] Published payloads pass schema validation (F-7)

**Tests:**
- [ ] Deterministic test: trigger condition met once then gone → no alert (persistence)
- [ ] Deterministic test: trigger condition met N times → alert fires
- [ ] Deterministic test: alert fires → same condition within cooldown → suppressed
- [ ] Deterministic test: cooldown expires → re-trigger allowed
- [ ] Contract test: alert payload passes schema (F-7)

**Implementation Notes:**

`AlertEngine` is a stateful helper (not a background service) — no `run()` loop. Individual alert evaluators (AL-2+) call `evaluate_and_fire()` once per 5m cycle. This keeps AL-1 generic and alert logic isolated per type.

Evaluation order (fastest rejection first):
1. `not conditions_met` → reset persistence, return False
2. cooldown active in Redis → log suppressed, reset persistence, return False
3. persistence count < required cycles → increment, return False
4. Build payload, validate against F-7 schema → raises on contract violation
5. Insert to DB → publish to NATS → activate cooldown → reset persistence

`CooldownRegistry` is Redis-backed (survives restarts). Key: `cooldown:{type}:{dedup_key}`. Uses `SETEX` with TTL = cooldown_minutes × 60. `PersistenceTracker` is in-memory (resets on restart) — acceptable because persistence is a 10-min safety gate (2 × 5m cycles), not a delivery guarantee. Dedup key is `f"{symbol or '_'}:{direction}"` — combined with `alert_type` forms the full unique signature. NATS uses regular `nc.publish` (not JetStream) for Phase 1; DEL-1 adds the durable consumer. F-7 schema loaded once at import time in `validator.py`, called before every DB write.

---

### AL-2: Alert — VOL_EXPANSION

**Goal:** Fire when realized volatility and volume both spike with breakout confirmation.

**Dependencies:** AL-1, FE-1

**Requirements:**
- Trigger: `rv_1h_zscore >= 2.0` AND `volume_zscore >= 1.5` AND breakout detected
- All thresholds read from `thresholds.yaml` — no hardcoded constants
- Severity escalation to HIGH on multiple confirmations
- Cooldown: 30 minutes
- Persistence: 2 consecutive cycles (10 min)

**Acceptance Criteria:**
- [ ] Fires when all conditions met for 2 consecutive cycles
- [ ] Does NOT fire when only one condition met
- [ ] Does NOT re-fire within 30-minute cooldown
- [ ] Single-cycle spike does NOT trigger
- [ ] Severity escalation behaves per spec

**Tests:**
- [ ] Test vector: rv=2.1, vol=1.6, breakout=true for 2 cycles → FIRES
- [ ] Test vector: rv=2.1, vol=1.0, breakout=true → NO FIRE
- [ ] Test vector: fire → same conditions 20 min later → SUPPRESSED

---

### AL-3: Alert — LEADERSHIP_ROTATION

**Goal:** Detect significant relative strength shifts between assets.

**Dependencies:** AL-1, FE-2

**Requirements:**
- Trigger: RS z-score >= 2.0 for any cross-pair (ETH/BTC, SOL/BTC, HYPE/BTC)
- All thresholds from `thresholds.yaml`
- Cooldown: 120 minutes
- Payload: which asset gaining/losing, magnitude, current regime

**Acceptance Criteria:**
- [x] Fires on RS z-score exceeding threshold
- [x] Cooldown 120 min respected
- [x] Payload identifies rotation direction

**Tests:**
- [x] Test vector: SOL/BTC RS z-score = 2.3 → FIRES with "SOL outperforming BTC"
- [x] Test vector: same signal 60 min later → SUPPRESSED
- [x] Fixture: BTC → alts rotation scenario triggers alert

**Implementation Notes:**

- **No rolling buffer** — FE-2 already computes and caches `eth_btc_rs_zscore`, `sol_btc_rs_zscore`, `hype_btc_rs_zscore` in `cross_features:latest` (Redis, TTL 600s). One Redis read per cycle covers all 3 pairs. This contrasts with AL-2 which needs an in-memory deque to z-score raw rv_1h on the fly.
- **`symbol=None` throughout** — Cross-asset alert. Direction encodes both pair and winner: `"{alt}_over_btc"` (positive z, alt outperforms BTC) or `"btc_over_{alt}"` (negative z). The AlertEngine dedup key uses `(alert_type, symbol=None, direction)`, giving each of the 6 directional slots its own independent cooldown and persistence counter.
- **6 evaluate_and_fire calls per cycle** — 3 pairs × 2 directions, sequential (not gathered). No I/O fan-out since data is already in memory from the single Redis read.
- **Severity always MEDIUM** — No escalation logic in Phase 1. Deferred to EV-3 threshold tuning.
- **E2E verified** — Persistence (2 cycles), NATS payload (`symbol=null`, `severity=MEDIUM`, `leading/lagging` in context), `alert_payload.json` schema pass, DB row with `symbol=NULL`.

---

### AL-4: Alert — BREAKOUT

**Goal:** Alert on price breakouts beyond 4h/24h ranges with volume confirmation.

**Dependencies:** AL-1, FE-1

**Requirements:**
- Trigger on breakout flags from feature engine + volume_zscore > 1.0
- Cooldown and dedup by (type, symbol, direction)
- Severity: 24h breakout = HIGH, 4h breakout = MEDIUM

**Acceptance Criteria:**
- [ ] Fires on confirmed breakout (price + volume)
- [ ] No alert on breakout without volume confirmation
- [ ] Severity correctly differentiated (4h=MEDIUM, 24h=HIGH)

**Tests:**
- [ ] Test vector: breakout_high_24h=true, vol_z=1.5 → HIGH alert
- [ ] Test vector: breakout_high_4h=true, vol_z=0.5 → NO FIRE (volume too low)

---

### AL-5: Alert — REGIME_SHIFT

**Goal:** Fire on confirmed regime transitions AND on INDETERMINATE — extended periods of ambiguous/transitional market structure.

**Dependencies:** AL-1, FE-6

**Requirements:**

**Case 1 — Confirmed regime transition:**
- Trigger: regime changed from one named state to another AND confidence >= 0.5
- Cooldown: 90 minutes
- Payload: old_regime, new_regime, confidence, contributing_factors

**Case 2 — INDETERMINATE:**
- Trigger: `result.regime = None` (uncertain) persists for ≥ N consecutive 5-minute cycles (default N=5, i.e. ≥ 25 minutes)
- `direction = "indeterminate"` in payload
- Severity: MEDIUM
- Separate cooldown bucket from named regime transitions
- `_uncertain_streak: int` counter in `RegimeShiftEvaluator` — increments each cycle with `regime=None`, resets on any named regime
- Does NOT fire on single uncertain cycle (N-cycle gate same as persistence)

**Acceptance Criteria:**
- [ ] Fires on confirmed regime transition at >= 0.5 confidence
- [ ] No alert below 0.5 confidence
- [ ] Fires INDETERMINATE after ≥ 25 min of consecutive uncertain cycles
- [ ] INDETERMINATE has its own cooldown (doesn't block named regime transition alerts)
- [ ] Payload includes old/new regime + drivers (or "indeterminate" direction)

**Tests:**
- [ ] Test vector: CHOP → VOL_EXPANSION at 0.6 → FIRES
- [ ] Test vector: CHOP → VOL_EXPANSION at 0.35 → NO FIRE
- [ ] Test vector: 5 consecutive `regime=None` cycles → INDETERMINATE fires
- [ ] Test vector: 4 consecutive `regime=None` cycles → no fire
- [ ] Fixture: regime transition scenario triggers once then cooldown prevents re-fire

---

### AL-6: Alert — CORRELATION_BREAK

**Goal:** Detect BTC-equity or BTC-DXY correlation breakdown.

**Dependencies:** AL-1, FE-3

**Requirements:**
- Trigger: correlation delta >= 0.3 (30d vs 7d rolling diverges)
- Delta and cooldown from `thresholds.yaml`
- Cooldown: 120 minutes
- Payload: pair, delta, current vs historical

**Acceptance Criteria:**
- [ ] Fires when correlation shifts by 0.3+
- [ ] Identifies direction correctly (increasing or decreasing)
- [ ] Cooldown respected

**Tests:**
- [ ] Test vector: 30d_corr=0.7, 7d_corr=0.3 → FIRES (delta=0.4)
- [ ] Test vector: 30d_corr=0.7, 7d_corr=0.5 → NO FIRE (delta=0.2)

---

### AL-7: Alert — CROWDED_LEVERAGE

**Goal:** Detect dangerously crowded positioning via funding + OI.

**Dependencies:** AL-1, FE-4

**Requirements:**
- Trigger: `funding_zscore >= 2.5` AND `oi_change_24h >= 0.05`
- All thresholds from `thresholds.yaml`
- Cooldown: 60 minutes
- Auto-disabled when derivatives feed is degraded

**Acceptance Criteria:**
- [ ] Fires when funding elevated AND OI growing
- [ ] Does NOT fire on high funding alone
- [ ] Cooldown 60 min respected
- [ ] Automatically disabled when derivatives data unavailable

**Tests:**
- [ ] Test vector: funding_z=2.8, oi_change=0.07 → FIRES
- [ ] Test vector: funding_z=2.8, oi_change=0.02 → NO FIRE
- [ ] Mock derivatives outage → alert disabled, other alerts unaffected

---

### AL-8: Alert — DELEVERAGING_EVENT

**Goal:** Detect cascade liquidation events. Always HIGH severity. Triggers event-specific LLM analysis.

**Dependencies:** AL-1, FE-4

**Requirements:**
- Trigger: `liq_1h_usd >= 50M` AND `oi_drop >= 5%` AND `candle_size >= 2x ATR`
- All thresholds from `thresholds.yaml`
- Cooldown: 30 minutes
- Always severity HIGH
- Triggers event-specific LLM analysis (LLM-3)

**Acceptance Criteria:**
- [ ] Fires on large cascades meeting all 3 conditions
- [ ] Always HIGH severity
- [ ] Triggers LLM event analysis pipeline

**Tests:**
- [ ] Test vector: liq=60M, oi_drop=0.08, candle=2.5xATR → FIRES (HIGH)
- [ ] Test vector: liq=60M, oi_drop=0.03, candle=2.5xATR → NO FIRE (OI missed)
- [ ] Integration: fire mock cascade → alert stored + event analysis triggered

---

### AL-9: Alert — EXCHANGE_INFLOW_RISK (BTC/ETH Only)

**Goal:** Detect large entity-tagged exchange inflows suggesting sell pressure.

**Dependencies:** AL-1, FE-5

**Requirements:**
- Base trigger: `inflow_z >= 2.5` AND `netflow_z >= 2.0` AND entity-tagged confirmed
- Hard gate: refuses non-entity-tagged flow data
- Severity → HIGH if ANY: funding_z >= 2.0 + OI up, OR VOL_EXPANSION active, OR price within 1.5x ATR of support, OR macro_stress >= 60
- Cooldown: 90 minutes
- BTC and ETH only — never fires for SOL or HYPE

**Acceptance Criteria:**
- [ ] Fires when both z-scores exceeded on entity-tagged data
- [ ] Does NOT fire on non-entity-tagged data (hard gate enforced)
- [ ] Severity escalation works for all 4 HIGH conditions
- [ ] Only BTC/ETH — never SOL or HYPE
- [ ] Confirmations escalate severity correctly

**Tests:**
- [ ] Test vector: inflow_z=2.8, netflow_z=2.3, entity=true, funding_z=2.1 → HIGH
- [ ] Test vector: inflow_z=2.8, netflow_z=2.3, entity=false → NO FIRE
- [ ] Test vector: symbol=SOL → NO FIRE regardless of z-scores
- [ ] Unit test: each confirmation path escalates severity independently

---

### AL-10: Alert — NETFLOW_SHIFT (BTC/ETH Only)

**Goal:** Detect structural shifts in exchange netflow direction (accumulation ↔ distribution).

**Dependencies:** AL-1, FE-5

**Requirements:**
- Condition A: SMA crossover (long outflow → short inflow, or vice versa)
- Condition B: 12h consecutive netflow z > 1.0, OR 9/12h positive netflow
- Condition C: SMA_short - SMA_long > DELTA_THRESH
- Fire when 2+ conditions met
- Cooldown: 24 hours (structural signal)
- Implements A/B/C exactly as spec Section 9.3

**Acceptance Criteria:**
- [ ] Fires when 2+ conditions met
- [ ] Does NOT fire on single condition alone
- [ ] 24-hour cooldown enforced
- [ ] Correctly labels accumulation vs distribution

**Tests:**
- [ ] Test vector: conditions A+B met → FIRES
- [ ] Test vector: only condition A met → NO FIRE
- [ ] Fixture: structural flip scenario triggers once then 24h cooldown prevents re-fire
- [ ] Historical validation: verified on at least 1 known accumulation/distribution period

---

### AL-11: Alert Routing Rules

**Goal:** Centralized routing logic determining which alerts go to which Discord channels.

**Dependencies:** AL-1

**Requirements:**
- Route HIGH severity to #alerts-high AND #alerts-all
- Route all alerts to #alerts-all
- Route REGIME_SHIFT to #regime-shifts
- Route EXCHANGE_INFLOW_RISK and NETFLOW_SHIFT to #on-chain
- Route system health events to #system-health
- Routing rules configurable (not hardcoded channel IDs)

**Acceptance Criteria:**
- [ ] Each alert type arrives in the correct channel(s)
- [ ] HIGH alerts appear in both #alerts-high and #alerts-all
- [ ] Routing is testable independently of Discord

**Tests:**
- [ ] Unit test: given alert type + severity → expected channel list
- [ ] Integration: generate mock alerts of each type → assert correct channel routing

---

## Epic 6: LLM Synthesis (Non-Triggering)

*Phase 2 (Weeks 3–4) for daily briefs, Phase 3 (Weeks 5–6) for event analysis.*

*LLM renders deterministic state into language. Never triggers alerts.*

---

### LLM-1: Context Builder

**Goal:** Module that assembles all system state into a structured prompt context for Claude API calls. This is the hardest part of the LLM integration — the API wrapper is trivial by comparison.

**Dependencies:** FE-1, FE-2, FE-6

**Requirements:**
- Assembles: current regime + confidence, all features, last N alerts, macro data, asset prices, on-chain flows (when available)
- Output: structured dict/JSON ready for prompt injection
- Configurable context window (e.g., last 6h of alerts, last 24h of features)
- Handles missing data gracefully (omits unavailable sections rather than erroring)
- Token budget awareness: truncate or summarize if context exceeds model limits

**Acceptance Criteria:**
- [ ] Context includes all available data sections
- [ ] Missing data sections omitted with no errors
- [ ] Context size stays within Claude token limits
- [ ] Output is reproducible given same DB state

**Tests:**
- [ ] Unit test: full data → complete context with all sections
- [ ] Unit test: no derivatives data → context built without derivatives section, no error

---

### LLM-2: Claude Client + Prompt Library

**Goal:** Central LLM client with prompt templates and retry/backoff logic.

**Dependencies:** LLM-1, F-7

**Requirements:**
- Claude API client with configurable model (Sonnet for daily, Opus for weekly)
- Retry with exponential backoff on transient failures
- Timeout handling (30s default)
- Prompt templates under `analyzer/prompts/`:
  - `daily_brief.py`, `event_liq.py`, `event_inflow.py`, `event_macro.py`, `weekly_deep.py`
- Hard rule: LLM output never triggers alerts (enforced architecturally — LLM module has no access to alert engine)

**Acceptance Criteria:**
- [ ] Client retries on transient failures (429, 500, timeout)
- [ ] All prompt templates exist and produce valid prompts
- [ ] LLM module cannot import or call alert engine (architectural enforcement)

**Tests:**
- [ ] Unit test: client retry/backoff behavior on mock failures
- [ ] Unit test: timeout triggers graceful failure, not crash

---

### LLM-3: Daily Brief (9 AM + 7 PM Dubai)

**Goal:** Claude-generated daily brief posted twice daily.

**Dependencies:** LLM-1, LLM-2, F-7

**Requirements:**
- Scheduled at 9 AM and 7 PM Dubai time (UTC+4)
- Output JSON matches spec Section 10.1 schema
- Output validated against daily brief contract schema (F-7)
- Fields: headline, regime_summary, drivers, asset_notes, onchain_intelligence, risks_next_6h, opportunities, key_levels, invalidation_watch, confidence_outlook
- Write to `analysis_reports` with `report_type = 'daily_brief'`
- Track token usage and cost in `tokens_used` and `cost_usd`
- Graceful degradation: if Claude API fails, log error and continue

**Acceptance Criteria:**
- [ ] Generated at 9 AM and 7 PM Dubai time
- [ ] Output JSON passes schema validation (F-7)
- [ ] `analysis_reports` row created with token count and cost
- [ ] Claude API error → logged, system continues, Discord shows "LLM unavailable"
- [ ] Brief contains specific numbers and evidence, not generic statements

**Tests:**
- [ ] Schema validation test: output JSON parsed and all required keys present (F-7 contract)
- [ ] Fallback test: mock Claude API failure → verify system continues and error logged

---

### LLM-4: Event-Triggered Analysis

**Goal:** Immediate Claude analysis on high-severity alerts (e.g., DELEVERAGING_EVENT).

**Dependencies:** LLM-1, LLM-2, AL-8

**Requirements:**
- Triggered by high-severity alerts within 30 seconds
- Context includes: triggering alert, current regime, all features, last 6h of alerts
- Event-specific prompts from prompt library
- Output: what happened, cascade risk assessment, likely next moves
- Validated against event analysis contract schema (F-7)
- Store in `analysis_reports` with `report_type = 'event_analysis'`

**Acceptance Criteria:**
- [ ] Analysis triggered within 30 seconds of high-severity alert
- [ ] References specific numbers from triggering event
- [ ] Stored in `analysis_reports`
- [ ] Posted to Discord with proper formatting
- [ ] If Claude API down → alert still delivered, analysis marked "LLM unavailable"

**Tests:**
- [ ] Integration: fire mock DELEVERAGING_EVENT → analysis generated and stored
- [ ] Fallback: Claude API down → alert delivered, analysis skipped gracefully

---

### LLM-5: Weekly Deep Report (Sunday, Claude Opus)

**Goal:** Sunday deep analysis using Claude Opus for the Intelligence Center.

**Dependencies:** LLM-1, LLM-2

**Implementation Notes:** Lower priority for solo MVP — valuable but not blocking core functionality.

**Requirements:**
- Runs every Sunday at 10 AM Dubai time
- Uses Claude Opus for deeper analysis
- Input: full week of regime history, all alerts, all features, macro events
- Output: week in review, regime transition analysis, alert quality assessment, next week outlook
- Store in `analysis_reports` with `report_type = 'weekly_deep'`
- Post summary to Discord

**Acceptance Criteria:**
- [ ] Generated every Sunday
- [ ] Uses Opus model (verified in `model_used` field)
- [ ] Covers full week comprehensively
- [ ] Stored and accessible in Intelligence Center

**Tests:**
- [ ] Schema validation: all required output fields present
- [ ] Manual: review one generated report for quality and specificity

---

## Epic 7: Delivery (Discord + Dashboard)

*Phase 1 (Weeks 1–2) for Discord, Phase 6 (Weeks 11–12) for dashboard.*

---

### DEL-1: Discord Bot — Core Setup

**Goal:** Discord bot with server structure, slash commands, and alert delivery.

**Dependencies:** AL-1, AL-11

**Requirements:**
- `discord.py` with slash commands
- Channels: #alerts-high, #alerts-all, #daily-brief, #regime-shifts, #on-chain, #bot-commands, #system-health
- Slash commands: `/status` (health), `/alerts` (last N), `/regime` (current), `/brief` (latest), `/macro` (stress + yields), `/funding` (derivatives snapshot), `/flows` (on-chain summary), `/eval` (alert quality), `/ask` (ad-hoc LLM query)
- Subscribe to NATS `alerts.fired` → post rich embeds via routing rules (AL-11)
- Auto-reconnect on Discord connection drop
- Restricted to configured private server (reject commands from unauthorized servers)

**Acceptance Criteria:**
- [ ] Bot online in Discord
- [ ] All slash commands functional
- [ ] Alerts appear in correct channels within 5 seconds of firing
- [ ] Bot reconnects after network interruption
- [ ] Commands rejected from unauthorized servers

**Tests:**
- [ ] E2E: fire test alert → verify embed arrives in correct channel with correct format
- [ ] Manual: test each slash command
- [ ] Unauthorized server → command rejected

---

### DEL-2: Alert Embed Formatter

**Goal:** Consistent, rich embed formatting for all alert types.

**Dependencies:** DEL-1

**Requirements:**
- Embed format per spec Section 11.3
- Color: RED (#EF4444) HIGH, AMBER (#F59E0B) MEDIUM, GRAY (#6B7280) LOW
- Fields: key metrics (inline), interpretation, watch next
- Footer: cooldown info + timestamp
- Consistent formatting across all 8 alert types + system health events

**Acceptance Criteria:**
- [ ] HIGH/MEDIUM/LOW formatting consistent with spec colors
- [ ] All embeds include key metrics and cooldown info
- [ ] Each alert type has type-specific fields (not generic)

**Tests:**
- [ ] Unit test: generate embed for each alert type → verify color, fields, footer

---

### DEL-3: Discord — Daily Brief Delivery

**Goal:** Multi-embed daily brief and regime shift routing to Discord.

**Dependencies:** DEL-1, DEL-2, LLM-3, AL-5

**Requirements:**
- Daily briefs posted as multi-embed messages to #daily-brief (6 embeds per spec 11.4)
- `/brief` command returns latest brief summary

**Acceptance Criteria:**
- [ ] Daily briefs render correctly with all 6 embeds
- [ ] `/brief` returns latest brief

**Tests:**
- [ ] Manual: verify daily brief formatting in Discord

---

### DEL-4: FastAPI Backend — REST Endpoints (MVP)

**Goal:** API endpoints needed for system status and dashboard View 1.

**Dependencies:** F-4, FE-1, FE-6, AL-1

**Implementation Notes:** Start with endpoints needed for View 1. Remaining endpoints (search, eval, macro calendar) added when those views are built.

**Requirements:**
- MVP endpoints:
  - `/api/health` (contract-validated per F-7)
  - `/api/regime/current`
  - `/api/alerts/recent` (last N, with type/severity filters)
  - `/api/market/features` (latest per symbol)
  - `/api/onchain/risk` (latest BTC/ETH risk scores)
  - `/api/macro/current` (latest macro snapshot)
- Additional endpoints (added with corresponding dashboard views):
  - `/api/market/candles`, `/api/regime/history`
  - `/api/alerts/stats`, `/api/onchain/flows`
  - `/api/analysis/latest`, `/api/analysis/search`
  - `/api/macro/calendar`, `/api/eval/metrics`
- Query parameters: date ranges, symbol filters, pagination
- Response caching via Redis where appropriate
- CORS for dashboard origin
- Pydantic request/response models

**Acceptance Criteria:**
- [ ] MVP endpoints return valid JSON with correct data
- [ ] `/api/health` response passes contract schema (F-7)
- [ ] Query parameters filter correctly
- [ ] Response times < 200ms for cached endpoints
- [ ] CORS allows dashboard origin

**Tests:**
- [ ] Contract tests: `/api/health` response validated against schema (F-7)
- [ ] Smoke test: hit each MVP endpoint → 200 with valid JSON
- [ ] Integration: query with filters → verify correct results

---

### DEL-5: FastAPI Backend — WebSocket Real-Time

**Goal:** `/ws/live` WebSocket pushing real-time updates to dashboard.

**Dependencies:** DEL-4

**Requirements:**
- Single WS connection per client at `/ws/live`
- Event types: `price_update` (5s), `feature_update` (5m), `regime_update` (on change), `alert_fired` (instant), `flow_update` (hourly), `macro_update` (5m), `health_update` (30s), `brief_ready` (on generation)
- Subscribe to NATS internally, relay to WS clients
- Handle disconnects gracefully
- Support multiple concurrent clients

**Acceptance Criteria:**
- [ ] WS establishes from browser
- [ ] Price updates every ~5 seconds
- [ ] Alerts pushed instantly
- [ ] 10 concurrent clients all receive updates
- [ ] Client disconnect doesn't crash server

**Tests:**
- [ ] Automated: connect WS client, verify `price_update` received within 10s
- [ ] Manual: connect 10 clients, verify all receive same alert

---

### DEL-6: Dashboard — Shell & Navigation

**Goal:** React dashboard shell: dark theme, sidebar, top bar, 6-view routing.

**Dependencies:** F-6

**Requirements:**
- Dark theme, high-contrast accent colors
- Fixed sidebar: 6 views + system status
- Top bar: regime badge (color pill), system health, Dubai + UTC clocks
- Keyboard navigation: 1–6 for views
- Responsive: desktop (full), tablet (collapsed sidebar), mobile (bottom tabs)
- Vite + React + TailwindCSS + zustand
- Auth strategy: no auth for local dev, JWT required if exposed publicly

**Acceptance Criteria:**
- [ ] All 6 views accessible via sidebar and keyboard
- [ ] Regime badge updates in real-time
- [ ] System health shows green/amber/red per feed
- [ ] Clocks tick correctly
- [ ] Responsive at desktop, tablet, mobile breakpoints
- [ ] If exposed publicly, auth required

**Tests:**
- [ ] Manual: verify all 6 keyboard shortcuts
- [ ] Manual: resize browser → verify responsive behavior at 3 breakpoints

---

### DEL-7: Dashboard — View 1: Command Center (MVP View)

**Goal:** Default overview. Everything at a glance, no scrolling. This is the MVP dashboard deliverable.

**Dependencies:** DEL-4, DEL-5, DEL-6

**Requirements:**
- Row 1: 4 asset price cards (live price, 24h change, sparkline, key metric)
- Row 2: Regime panel + Macro strip (DXY, VIX, S&P, Yields)
- Row 3: Alert feed (last 10) + On-chain summary (BTC/ETH risk scores, flow direction)
- On-chain: BTC/ETH show data, SOL/HYPE show "N/A on-chain"
- All data via WebSocket

**Acceptance Criteria:**
- [ ] 4 asset cards show live prices updating every 5s
- [ ] Regime panel reflects current regime with correct colors
- [ ] Alert feed updates instantly on new alerts
- [ ] No scrolling on 1080p+ display
- [ ] On-chain summary: data for BTC/ETH, "N/A" for SOL/HYPE

**Tests:**
- [ ] Manual: verify live price updates for all 4 assets
- [ ] Manual: fire alert → appears in feed within 5 seconds

---

### DEL-8: Dashboard — View 2: Asset Detail

**Goal:** Deep dive per asset with candlestick chart and feature dashboard.

**Dependencies:** DEL-5, DEL-6

**Priority:** Fast-follow after View 1

**Requirements:**
- Tab bar: BTC | ETH | SOL | HYPE
- TradingView lightweight-charts: candlesticks, volume, Bollinger Bands, alert markers
- Feature grid: RSI, MACD, volatility, funding, OI, breakout status
- On-chain column for BTC/ETH only ("Not available" for SOL/HYPE)
- Time range: 1h, 4h, 1d, 7d, 30d

**Acceptance Criteria:**
- [ ] Candlestick chart renders with real data
- [ ] BB overlay visible
- [ ] Alert markers at trigger timestamps
- [ ] Feature grid updates every 5 min
- [ ] On-chain column: data for BTC/ETH, "Not available" for SOL/HYPE

**Tests:**
- [ ] Manual: switch between assets and time ranges, verify data changes

---

### DEL-9: Dashboard — View 3: Macro Dashboard

**Goal:** Full macro context with indicator charts and correlation analysis.

**Dependencies:** DEL-5, DEL-6

**Priority:** Fast-follow after View 1

**Requirements:**
- Macro charts: DXY, VIX, S&P 500, 2Y/10Y, yield curve spread
- Correlation heatmap (BTC vs equity, BTC vs DXY)
- Macro stress gauge (0–100)
- Historical overlay: stress vs BTC price

**Acceptance Criteria:**
- [ ] All macro indicators chart correctly
- [ ] Heatmap reflects rolling correlations
- [ ] Stress gauge matches `macro_stress` value
- [ ] Interactive charts (zoom, hover tooltips)

**Tests:**
- [ ] Manual: verify all 5 macro charts render with data

---

### DEL-10: Dashboard — View 4: On-Chain Intelligence

**Goal:** BTC and ETH flow analysis page.

**Dependencies:** DEL-5, DEL-6

**Priority:** Fast-follow after View 1

**Requirements:**
- Side-by-side BTC vs ETH flows (tabbed on mobile)
- Per asset: netflow bar chart, z-score timeline, risk gauge, persistence indicator
- Cross-asset flow comparison chart
- Combined risk score bar

**Acceptance Criteria:**
- [ ] BTC/ETH flows side-by-side on desktop
- [ ] Netflow chart distinguishes inflow/outflow
- [ ] Risk gauges update hourly
- [ ] Cross-asset comparison shows correlation/divergence

**Tests:**
- [ ] Manual: verify both assets display correct flow data

---

### DEL-11: Dashboard — View 5: Intelligence Center

**Goal:** LLM analysis browser — searchable archive of all Claude reports.

**Dependencies:** DEL-5, DEL-6

**Priority:** Fast-follow after View 1

**Requirements:**
- Latest daily brief displayed in full
- Searchable archive of `analysis_reports`
- Filters: report type, date range, regime
- Full text search
- Token/cost tracking visible

**Acceptance Criteria:**
- [ ] Latest brief displayed with all fields
- [ ] Search returns relevant results
- [ ] Filters work correctly
- [ ] Token/cost visible per report

**Tests:**
- [ ] Manual: search for a known report, verify it appears

---

### DEL-12: Dashboard — View 6: Evaluation & Performance

**Goal:** Alert quality metrics and detailed alert log.

**Dependencies:** DEL-5, DEL-6, EV-1

**Priority:** Fast-follow after View 1

**Requirements:**
- Quality charts: hit rate over time, by type, by severity, false positive rate
- Alert log table: filterable (date, type, severity, asset, hit/miss)
- CSV export
- Summary stats: total alerts, hit rate, avg move size

**Acceptance Criteria:**
- [ ] Charts render with real evaluation data
- [ ] All filter combinations work
- [ ] CSV export functional
- [ ] Summary stats match manual calculation

**Tests:**
- [ ] Manual: apply filters, verify table updates
- [ ] Manual: export CSV, open in spreadsheet, verify columns

---

### DEL-13: Dashboard — WebSocket Integration

**Goal:** Wire real-time WS updates to all dashboard views via centralized state management.

**Dependencies:** DEL-5, DEL-6

**Requirements:**
- Custom `useWebSocket` hook managing single connection
- Zustand stores updated per event type (marketStore, regimeStore, alertStore, flowStore)
- Auto-reconnect with exponential backoff
- Visual disconnect indicator
- All views react to store updates

**Acceptance Criteria:**
- [ ] Dashboard connects on load, stays connected
- [ ] Prices update every 5s
- [ ] Alerts appear instantly in feed
- [ ] Regime changes reflected across all views
- [ ] Disconnect shows warning, auto-reconnects
- [ ] No memory leaks after 1 hour

**Tests:**
- [ ] Manual: kill WS server, verify warning shown, reconnect on restart
- [ ] Manual: monitor browser memory for 1 hour, verify no leak

---

## Epic 8: Evaluation

*Phase 5 (Weeks 9–10).*

---

### EV-1: Post-Alert Move Tracking

**Goal:** Automatically evaluate alert quality by tracking price moves after each alert.

**Dependencies:** AL-1

**Requirements:**
- Schedule evaluation: +4h for market alerts, +12h for on-chain alerts
- Compute: `followed_by_move` (boolean), `move_size_atr` (ATR multiples)
- Meaningful move = price moved >= 1.0 ATR in alerted direction
- Update `alerts` table with results
- Aggregate metrics: hit rate, FP rate, avg move per alert type
- Pending evaluations survive service restarts
- Results feed `/eval` Discord command and dashboard View 6

**Acceptance Criteria:**
- [ ] Every alert gets evaluation within configured window
- [ ] `followed_by_move` correctly identifies moves >= 1.0 ATR
- [ ] `move_size_atr` mathematically correct
- [ ] Aggregate hit rate computable per alert type
- [ ] Pending evaluations persist across restarts

**Tests:**
- [ ] Deterministic test: known alert + known price move → expected evaluation
- [ ] Integration test: restart service with pending evaluations → they complete

---

### EV-2: Alert Quality Metrics & API

**Goal:** Compute and expose quality metrics for dashboard and threshold tuning.

**Dependencies:** EV-1

**Requirements:**
- Per alert type: hit rate (7d, 30d), FP rate, avg move, alert count
- Per severity: hit rate comparison
- Regime-conditional: hit rate per alert type per regime
- Redis cache (hourly refresh) + `/api/eval/metrics` endpoint
- Weekly summary in `analysis_reports`
- Track threshold changes by config version hash for before/after comparison

**Acceptance Criteria:**
- [ ] All metrics correct
- [ ] Redis cached and up-to-date
- [ ] API endpoint returns structured JSON
- [ ] Weekly summary stored
- [ ] Config version hash tracked alongside metrics

**Tests:**
- [ ] Unit test: known alert history → expected hit rate calculations

---

### EV-3: Threshold Tuning Framework

**Goal:** Tooling to suggest threshold adjustments based on evaluation data.

**Dependencies:** EV-2

**Requirements:**
- Script reads performance, suggests adjustments
- Compares current vs alternative thresholds on historical data
- Output: recommended changes with impact estimates
- Manual application only (no auto-tuning in MVP)

**Acceptance Criteria:**
- [ ] Script produces recommendations with impact estimates
- [ ] No automatic changes

**Tests:**
- [ ] Manual: run script, review recommendation quality

---

### EV-4: Backtesting Framework

**Goal:** Replay historical data through regime classifier and alert engine.

**Dependencies:** FE-6, AL-1

**Requirements:**
- Load historical `computed_features` and `cross_features`
- Run regime + alerts in backtest mode (no side effects)
- Output: simulated alerts vs actual price moves
- Date range selection
- CSV export
- Deterministic: replay produces identical results

**Acceptance Criteria:**
- [ ] Runs over specified date range
- [ ] Produces same alerts as live system for same features (deterministic)
- [ ] CSV includes: time, type, severity, followed_by_move, move_size_atr
- [ ] 30 days completes in < 5 minutes

**Tests:**
- [ ] Deterministic: known feature window → expected backtest alerts

---

## Epic 9: Ops & Reliability

*Cross-cutting. Build incrementally alongside each phase.*

---

### OPS-1: Health Model & `/api/health` Contract

**Goal:** Define the health status model used by all services and the `/api/health` endpoint contract.

**Dependencies:** F-6, F-7

**Requirements:**
- Health status enum: `HEALTHY`, `DEGRADED`, `DOWN` per component
- Components: binance_ws, coinglass, fred, yahoo, onchain_provider, nats, timescaledb, redis, discord, claude_api
- `/api/health` returns JSON matching contract schema (F-7) with per-component status + last update timestamp + degradation reason
- Health checks run every 30 seconds internally
- All degradation paths (OPS-2 through OPS-6) report through this model

**Acceptance Criteria:**
- [ ] `/api/health` returns all components with status
- [ ] Each component has last update timestamp
- [ ] Degraded components include reason string
- [ ] Response passes contract schema (F-7)

**Tests:**
- [ ] Contract test: `/api/health` response shape validated against schema

---

### OPS-2: Macro Degrade Path

**Goal:** When macro feeds (FRED/Yahoo) fail, system continues with crypto-only operation.

**Dependencies:** OPS-1, DI-3, DI-4

**Requirements:**
- FRED/Yahoo down → `macro_stress` uses last known value
- Correlations marked stale
- Health endpoint reflects degraded macro
- Regime classifier continues with `macro_stress = last_known`

**Acceptance Criteria:**
- [ ] FRED down → system continues, macro_stress uses last known
- [ ] Health shows DEGRADED for fred/yahoo
- [ ] Regime classifier still runs

**Tests:**
- [ ] Fault injection: block FRED host → verify degraded state + system continues

---

### OPS-3: Derivatives Degrade Path

**Goal:** When Coinglass fails, derivatives alerts auto-disable, everything else continues.

**Dependencies:** OPS-1, DI-5

**Requirements:**
- Coinglass down → CROWDED_LEVERAGE and DELEVERAGING_EVENT auto-disable
- Derivatives features → NaN
- Health reflects degraded derivatives
- Other alerts continue normally

**Acceptance Criteria:**
- [ ] Coinglass down → derivatives alerts disabled
- [ ] Other alerts unaffected
- [ ] Recovery automatic when Coinglass returns

**Tests:**
- [ ] Fault injection: block Coinglass → verify alerts disabled + health degraded + other alerts working

---

### OPS-4: On-Chain Degrade Path

**Goal:** When on-chain provider fails, flow alerts disable, regime uses market-only signals.

**Dependencies:** OPS-1, DI-9

**Requirements:**
- Provider down → EXCHANGE_INFLOW_RISK and NETFLOW_SHIFT auto-disable
- On-chain features → NaN
- Regime classifier excludes on-chain drivers
- Health reflects degraded on-chain

**Acceptance Criteria:**
- [ ] Provider down → flow alerts disabled
- [ ] Regime still classifies using market data only
- [ ] Auto-recovery when provider returns

**Tests:**
- [ ] Fault injection: block provider host → verify flow alerts disabled + regime continues

---

### OPS-5: LLM Degrade Path

**Goal:** When Claude API fails, rules-based system continues. Briefs show "LLM unavailable."

**Dependencies:** OPS-1, LLM-3

**Requirements:**
- Claude API down → daily brief skipped with "LLM unavailable" note in Discord
- Event-triggered analyses skipped with logged warning
- All alerts continue (LLM never triggers alerts — architectural guarantee)
- Health reflects degraded LLM

**Acceptance Criteria:**
- [ ] Claude API down → alerts still fire normally
- [ ] Discord shows "LLM unavailable" at brief time
- [ ] Recovery: next scheduled brief generates normally

**Tests:**
- [ ] Fault injection: mock Claude API failure → verify alerts continue + brief skipped gracefully

---

### OPS-6: Message Bus Degrade Path

**Goal:** When NATS fails, collector keeps reconnecting. System degrades but doesn't crash.

**Dependencies:** OPS-1

**Implementation Notes:** Hardest degrade path. For MVP: reconnect + log. No local WAL buffering required.

**Requirements:**
- NATS down → collector keeps reconnecting with exponential backoff, logs degraded state
- Processor can fallback to direct DB queries for latest data (if collector wrote before NATS died)
- Health reflects degraded message bus
- System restores automatically when NATS returns

**Acceptance Criteria:**
- [ ] NATS down → collector logs degraded, keeps retrying
- [ ] No crash in any service
- [ ] NATS recovery → message flow resumes automatically
- [ ] Health endpoint shows DEGRADED for nats

**Tests:**
- [ ] Fault injection: `docker stop nats` → verify all services survive, logs show degraded
- [ ] Recovery: `docker start nats` → verify message flow resumes

---

### OPS-7: Monitoring & Observability

**Goal:** Structured logging, health metrics, feed heartbeats across all services.

**Dependencies:** OPS-1

**Requirements:**
- Structured JSON logging across all services with correlation IDs
- Per-service health endpoints
- Key metrics: messages/sec, computation time, alert count, API latency, WS clients
- Feed heartbeat: alert if any feed silent > 2 minutes
- Discord #system-health for critical system alerts

**Acceptance Criteria:**
- [ ] All services produce structured JSON logs
- [ ] Feed silence detected and alerted within 2 minutes
- [ ] System alerts posted to Discord #system-health
- [ ] Key metrics queryable

**Tests:**
- [ ] Stop Binance collector → verify heartbeat alert in Discord #system-health within 2 min

---

### OPS-8: Security Hardening

**Goal:** Secure all secrets, rate-limit external APIs, restrict access.

**Dependencies:** F-3

**Requirements:**
- API keys in `.env` / secret manager, never in code or logs
- Rate limiting on all external API calls
- Discord bot restricted to private server
- Dashboard auth (JWT) if deployed publicly
- No secrets in Docker images or git history

**Acceptance Criteria:**
- [ ] `git log` and Docker images contain zero secrets
- [ ] No 429s from external APIs in production
- [ ] Dashboard requires auth when exposed publicly
- [ ] Discord bot only responds in configured server

**Tests:**
- [ ] Automated: `git log --all -p | grep -i "api_key\|secret\|token"` returns nothing
- [ ] Manual: attempt bot command from unauthorized server → rejected

---

## Epic 10: Quality Assurance

*Cross-cutting. One smoke test to rule them all.*

---

### QA-1: End-to-End Smoke Test

**Goal:** A single command proves the entire chain works: infra → ingest → features → alert → delivery.

**Dependencies:** F-3, F-4, DI-0, DI-1, DI-2, FE-1, AL-1, AL-2, DEL-1

**Requirements:**
- Script/makefile target: `make smoke`
- Starts infrastructure (docker-compose)
- Replays fixtures (DI-0) through the pipeline
- Computes features from fixture data
- Triggers at least 1 deterministic alert (VOL_EXPANSION on crafted fixture)
- Delivers alert to Discord test channel (or logs delivery)
- Stores alert in DB and verifies retrieval
- Tears down cleanly
- Total runtime < 5 minutes

**Acceptance Criteria:**
- [ ] `make smoke` runs end-to-end locally without manual intervention
- [ ] Produces at least 1 deterministic alert + stores in DB
- [ ] Alert delivery verified (Discord or logged)
- [ ] Clean teardown with no orphan containers

**Tests:**
- [ ] The smoke test IS the test
- [ ] Optional: CI job runs smoke test on PRs

---

## Epic 11: Infrastructure Fixes & Threshold Calibration

*Phase 1.5 — architecture gaps and threshold correctness fixes discovered post-implementation.*

---

### INFRA-1: Architecture Gap Fixes

> **DONE — PR #19 merged (2026-02-23).** Four gaps found during code review.

**Goal:** Fix four architectural gaps in the processor service.

**Changes made:**
1. **docker-compose.yml** — Added `redis` to `processor` service `depends_on`. Processor uses Redis heavily but could start before Redis was ready.
2. **CrossFeatureEngine timing** — Replaced wall-clock `datetime.now()` with `time.monotonic()` for cycle-interval tracking. NTP adjustments could cause double-fire or skipped cycles.
3. **PersistenceTracker** — Made Redis-backed (survives restarts). Previously in-memory → reset on every restart, silently losing persistence state.
4. **CI pipeline** — Added `.github/workflows/processor.yml` to run `ruff check` + `pytest` on PRs.

---

### QA-2: Golden-Fixture Snapshot Tests for Alert Evaluators

**Goal:** Freeze exact NATS payload shape for AL-2, AL-3, AL-4. Catch silent payload regressions.

**Dependencies:** AL-2, AL-3, AL-4

**Requirements:**
- For each alert type: a `test_golden_payload_{alert_type}` test that constructs known input, captures NATS payload, and compares against a JSON fixture file under `processor/tests/fixtures/`
- Fixture files committed alongside tests — act as diff guards
- If any NATS payload field changes, at least one golden test fails

**Fixture files:**
- `processor/tests/fixtures/golden_vol_expansion.json`
- `processor/tests/fixtures/golden_leadership_rotation.json`
- `processor/tests/fixtures/golden_breakout.json`

**Acceptance Criteria:**
- [ ] 3 golden tests (one per alert type)
- [ ] Fixtures committed
- [ ] `pytest tests/ -q` passes
- [ ] Any payload field change causes at least one test failure

---

### FIX-1: Fix volatility_regime Threshold

**Goal:** Fix a statistical error in the regime classifier that makes `volatility_regime = "high"` fire ~50% of the time regardless of actual market conditions.

**Dependencies:** FE-6

**Problem:** In `processor/src/regime/classifier.py`, `_build_regime_inputs()` computes:
```python
"volatility_regime": "high" if rv_4h_zscore > 0 else "low"
```
By statistical construction, a z-score > 0 approximately 50% of the time. The field carries no information.

**Fix:** Change threshold to `> 0.5` (or configurable via `thresholds.yaml`):
```python
"volatility_regime": "high" if rv_4h_zscore > params.volatility_regime_threshold else "low"
```

**Impact:** RISK_ON_TREND, RISK_OFF_STRESS, VOL_EXPANSION all use `volatility_regime == "high"` as a condition — all are currently misfiring ~50% of the time.

**Acceptance Criteria:**
- [ ] Threshold moved to `thresholds.yaml` under `regime_classifier.volatility_regime_threshold`
- [ ] Default value: `0.5`
- [ ] Updated tests reflect new threshold
- [ ] No hardcoded value remains in `classifier.py`

---

### FIX-2: Per-Asset Threshold Multipliers

**Goal:** Apply per-asset scaling to alert thresholds. HYPE is 10–20x less liquid than BTC — BTC-calibrated thresholds generate false positives on HYPE.

**Dependencies:** AL-2, AL-3, AL-4, FE-1

**Problem:** All 4 symbols use identical thresholds from `thresholds.yaml`. Normal HYPE volume spikes look like VOL_EXPANSION at BTC-calibrated levels. HYPE candles are routinely 3–5x larger than BTC ATR ratios.

**Solution:** Add `threshold_multiplier` per symbol to `configs/symbols.yaml`:
```yaml
symbols:
  BTC:  { threshold_multiplier: 1.0 }
  ETH:  { threshold_multiplier: 1.2 }
  SOL:  { threshold_multiplier: 1.5 }
  HYPE: { threshold_multiplier: 2.5 }
```

Applied at evaluator layer: each evaluator loads the multiplier for its symbol and scales all threshold comparisons.

**Acceptance Criteria:**
- [ ] `symbols.yaml` has `threshold_multiplier` per asset
- [ ] All Phase 1–2 evaluators (AL-2, AL-3, AL-4) apply multiplier
- [ ] Tests verify HYPE threshold = BTC threshold × 2.5
- [ ] No multiplier hardcoded in evaluator code

---

## Epic 12: Phase 2 Data Sources

*New data providers added in the plan revision. Deribit and CoinGecko are free-tier, no auth.*

---

### DI-6: Deribit DVOL — Implied Volatility Index

**Goal:** Collect BTC and ETH implied volatility index (DVOL) from Deribit. Leading indicator for VOL_EXPANSION regime.

**Dependencies:** F-4

**What is DVOL:** Deribit DVOL is the crypto equivalent of VIX — a real-time implied volatility index for BTC and ETH options. Unlike realized vol (historical), DVOL reflects what the options market expects future volatility to be.

**Why it matters:**
- DVOL spike before price moves = leading indicator for VOL_EXPANSION
- DVOL elevated + funding high = options traders and perp traders both expect volatility → regime confirmation
- Available publicly (no API key required for historical data)

**API:** `GET https://www.deribit.com/api/v2/get_volatility_index_data?currency=BTC&resolution=3600`

**Requirements:**
- Poll hourly for BTC and ETH DVOL
- Write to `macro_data` with `source = 'deribit'`
- Backfill 180 days on first run
- Rate limit: 20 req/s (generous)
- Graceful degradation: DVOL is enhancement, not critical path

**Acceptance Criteria:**
- [ ] BTC and ETH DVOL stored hourly
- [ ] 180-day backfill complete on first run
- [ ] Deribit down → logged, system continues without DVOL
- [ ] Data available in `cross_features` for FE-3 consumption

---

### DI-7: CoinGecko BTC Dominance

**Goal:** Collect BTC market dominance (BTC.D) from CoinGecko. Signals alt season (RISK_ON_TREND) or BTC flight-to-safety.

**Dependencies:** F-4

**Signal interpretation:**
- BTC.D rising = capital rotating from alts to BTC (risk-off within crypto)
- BTC.D falling = alt season, risk appetite high, RISK_ON_TREND signal strengthens
- BTC.D > 60% historically = bear market consolidation in BTC

**API:** `GET https://api.coingecko.com/api/v3/global` — returns `btc_dominance` (float, percentage). Free tier, no API key required.

**Requirements:**
- Poll every 10 minutes (free tier safe limit: ~5–15 req/min)
- Write `btc_dominance` to `cross_features`
- Backfill 180 days via `/coins/bitcoin/market_chart`
- Rate limit: 10 req/min max
- Graceful degradation: non-critical

**Acceptance Criteria:**
- [ ] `btc_dominance` updated every 10 minutes in `cross_features`
- [ ] 180-day backfill complete
- [ ] CoinGecko down → logged, system continues
- [ ] `cross_features:latest` Redis key includes `btc_dominance`

---

### DI-8: News Feed Collector (Cryptopanic)

**Goal:** Poll for high-importance crypto news headlines and store for async LLM classification (LLM-2b).

**Dependencies:** F-4

**Architecture note:** Headlines feed LLM-2b (async classifier) → structured JSON → AL-12 (deterministic evaluator). LLM-2b is **never** in the 5-minute alert trigger path (Rule 1.1 preserved).

**API:** `GET https://cryptopanic.com/api/v1/posts/?filter=hot&currencies=BTC,ETH,SOL`

**Pre-filters (before LLM):**
- `filter=hot` (trending posts only)
- `min_votes: 30` (community vote gate)
- `kind: news | analysis` (no social posts)
- `max_age_minutes: 30` (skip stale headlines)

**Requirements:**
- Poll every 5 minutes
- Deduplicate by headline ID (don't re-classify seen headlines)
- Store raw headlines with metadata (votes, timestamp, currencies)
- Pass new headlines to LLM-2b via internal queue or Redis pub/sub
- Rate limit: 5 req/s (free tier)
- Graceful degradation: news is enhancement, not critical

**Acceptance Criteria:**
- [ ] New headlines stored every 5 minutes
- [ ] No duplicate classification of same headline
- [ ] Stale headlines (> 30 min) skipped
- [ ] LLM-2b notified of new headlines

---

### DI-10: Coinglass Liquidation Heatmap Collector

**Goal:** Fetch price-level liquidation cluster data for BTC, ETH, SOL, HYPE from Coinglass. The heatmap shows where significant long/short positions would be force-liquidated at given price levels — providing forward-looking cascade risk context for LLM-3b's positioning bias output.

**Dependencies:** F-4, DI-5 (same Coinglass API key infrastructure)

**Blocks:** LLM-3b (SOLO-97)

**API:** `GET https://open-api-v4.coinglass.com/api/futures/liquidation/map`

**Data captured per symbol:**

| Field | Description |
|-------|-------------|
| `price_level` | Price band (USD) |
| `long_liq_usd` | USD value of longs liquidated at this level |
| `short_liq_usd` | USD value of shorts liquidated at this level |
| `total_liq_usd` | Combined liquidation value at this level |
| `captured_at` | Snapshot timestamp |

**Requirements:**
- Poll every 15 minutes (heatmap is slow-moving; no need for 5-min cadence)
- Fetch for all 4 symbols: BTC, ETH, SOL, HYPE
- Store top-N price levels by total liquidation value (N=20 per symbol)
- Write to `liquidation_heatmap` TimescaleDB table (new table, idempotent migration)
- Cache latest snapshot per symbol to Redis: `heatmap:latest:{sym.lower()}usdt` (TTL 1800s)
- Same Coinglass API key as DI-5 — reuse client, add endpoint only
- Graceful degradation: if Coinglass unavailable → LLM-3b omits heatmap context, no crash
- Rate limit: honour same 3-failure circuit breaker as DI-5

**DB schema (`liquidation_heatmap`):**
```sql
CREATE TABLE IF NOT EXISTS liquidation_heatmap (
    time            TIMESTAMPTZ NOT NULL,
    symbol          TEXT        NOT NULL,
    price_level     NUMERIC     NOT NULL,
    long_liq_usd    NUMERIC,
    short_liq_usd   NUMERIC,
    total_liq_usd   NUMERIC,
    PRIMARY KEY (time, symbol, price_level)
);
SELECT create_hypertable('liquidation_heatmap', 'time', if_not_exists => TRUE);
```

**Redis payload (JSON, TTL 1800s):**
```json
{
  "captured_at": "2026-02-25T12:00:00Z",
  "symbol": "BTC",
  "levels": [
    { "price": 88000, "long_liq_usd": 142000000, "short_liq_usd": 8000000 },
    { "price": 85000, "long_liq_usd": 380000000, "short_liq_usd": 12000000 }
  ]
}
```

**LLM-3b usage:** Positioning bias prompt includes the top-3 highest-density liquidation levels per symbol. LLM can cite cascade risk zones ("$85K long cluster — large liquidation magnet") in its directional analysis.

**Acceptance Criteria:**
- [ ] Heatmap data stored every 15 minutes for all 4 assets
- [ ] `liquidation_heatmap` migration is idempotent (`IF NOT EXISTS`)
- [ ] Redis key `heatmap:latest:{sym}usdt` written with TTL 1800s
- [ ] Coinglass down → LLM-3b skips heatmap section, no alert disruption
- [ ] No API key logged

**Tests:**
- [ ] Unit: mock Coinglass response → verify top-N levels selected and stored correctly
- [ ] Unit: empty response → graceful no-op, no crash
- [ ] Mock failure → Redis key absent → LLM-3b context builder handles missing key cleanly

---

## Epic 13: News Classification & Positioning Bias

---

### LLM-2b: Async News Classifier

**Goal:** Classify news headlines from DI-8 into structured market signals. Runs async — never in the 5-minute trigger hot path.

**Architecture invariant:** LLM classifies headline → writes structured JSON to `news_signals` Redis key (TTL 2h) → AL-12 (deterministic evaluator) reads it. LLM never directly triggers an alert.

**Dependencies:** DI-8, LLM-2

**Classification output schema:**
```json
{
  "headline": "...",
  "relevant": true,
  "direction": "bearish",
  "confidence": "high",
  "event_type": "regulatory",
  "assets_affected": ["BTC", "ETH"],
  "classified_at": "2026-02-24T12:00:00Z"
}
```

**Requirements:**
- Batch-classifies new headlines from DI-8 queue
- Runs on separate async cycle (not 5-minute feature cycle)
- Only classifies high-importance headlines (pre-filtered by DI-8)
- Writes to `news_signals:{headline_id}` Redis key (TTL 2h)
- If Claude API unavailable → headlines skipped, no crash

**Acceptance Criteria:**
- [ ] Headlines classified within 60 seconds of arrival
- [ ] Structured JSON written to Redis with correct schema
- [ ] Claude API outage → system continues, news alerts paused only
- [ ] LLM-2b has no import path to alert engine (Rule 1.1 architectural enforcement)

---

### AL-12: NEWS_EVENT Alert Evaluator

**Goal:** Deterministic evaluator that reads structured news signals from LLM-2b and fires NEWS_EVENT alerts. No LLM in this path.

**Dependencies:** AL-1, LLM-2b

**Trigger conditions (all must be true):**
```yaml
news_event:
  min_confidence: "high"       # Only fire on high-confidence LLM classifications
  max_age_minutes: 20          # Ignore stale signals
  relevant: true               # LLM must have flagged as market-relevant
  cooldown_minutes: 60         # Per asset, per direction
```

**Severity mapping:**
- `event_type: regulatory` → HIGH
- `event_type: macro` → HIGH (if assets_affected includes BTC)
- `event_type: exchange` → MEDIUM
- `event_type: protocol` → MEDIUM

**Payload includes:** headline, direction, event_type, assets_affected, LLM confidence

**Acceptance Criteria:**
- [ ] Fires only on `high` confidence classifications
- [ ] Does NOT fire on stale signals (> 20 min)
- [ ] Cooldown respected per asset/direction
- [ ] Severity correctly mapped from event_type
- [ ] No LLM import in this file (deterministic rules only)

**Tests:**
- [ ] Test vector: `confidence=high`, `relevant=true`, `age=5min` → FIRES
- [ ] Test vector: `confidence=medium` → NO FIRE
- [ ] Test vector: `age=25min` → NO FIRE (stale)
- [ ] Test vector: fired → same event 30 min later → SUPPRESSED

---

### LLM-3b: Positioning Bias Section in Daily Brief

**Goal:** Add a "POSITIONING BIAS" section to the daily brief (LLM-3) that explicitly states the current market direction in actionable terms.

**Dependencies:** LLM-3, FE-6

**Direction mapping (deterministic, computed before LLM call):**

| Regime | Base direction label |
|--------|---------------------|
| RISK_ON_TREND | BULLISH |
| RISK_OFF_STRESS | BEARISH |
| VOL_EXPANSION | derived from btc_trend sign (see below) |
| DELEVERAGING | BEARISH — active unwind |
| CHOP_RANGE | NEUTRAL — wait |
| INDETERMINATE | UNCLEAR — transitioning |

**VOL_EXPANSION special case:** Direction derived from `btc_trend` (1h return):
- `btc_trend > 0.005` → "VOLATILE — bullish expansion"
- `btc_trend < -0.005` → "VOLATILE — bearish expansion"
- else → "VOLATILE — direction unclear"

**Confidence qualification (3 tiers, thresholds in `thresholds.yaml`):**
- `≥ 0.80` → "Strongly" / "confirmed" prefix
- `0.60–0.79` → plain label (no qualifier)
- `0.40–0.59` → "Cautiously" / "tentative" prefix

**Implementation:** `context_builder.py` computes `base_label` + `qualified_label` deterministically before the LLM call. LLM receives `qualified_label` as a fact and writes the explanation — it cannot override the label.

**Acceptance Criteria:**
- [ ] POSITIONING BIAS section present in all daily briefs
- [ ] Direction label always derived from regime, never LLM-invented
- [ ] Confidence qualification thresholds loaded from `thresholds.yaml`
- [ ] VOL_EXPANSION direction uses `btc_trend` sign correctly

**Tests:**
- [ ] Unit test: each regime → expected base direction label
- [ ] Unit test: VOL_EXPANSION + btc_trend=0.01 → "bullish expansion"
- [ ] Unit test: confidence=0.85 → "Strongly" prefix applied
- [ ] Unit test: confidence=0.45 → "Cautiously" / "Tentative" prefix applied

---

## MVP Execution Order (Solo)

Sequenced for fastest time-to-value. Working alerts by end of week 2.

```
Week 0 (Days 1-3): Foundation
  F-1 → F-3 → F-4 → F-5a → F-6 → F-7
  DI-0 (capture fixtures in parallel)
  F-2 (provider research, doesn't block Phase 1-3)

Weeks 1-2: Crypto Alerts End-to-End
  DI-1 → DI-2 → FE-1 → FE-2
  AL-1 → AL-2 → AL-3 → AL-4 → AL-11
  DEL-1 → DEL-2
  QA-1 (smoke test proves chain works)
  OPS-1 (health model — needed by everything)

Phase 1.5 (Week 2-3): Derivatives & Threshold Fixes
  DI-5 (Coinglass — HIGHEST PRIORITY, unlocks DELEVERAGING + CROWDED_LEVERAGE)
  FE-4 (funding_zscore, oi_drop_1h, liquidations_1h_usd)
  FIX-1 (fix volatility_regime threshold in classifier.py: > 0 → > 0.5)
  FIX-2 (per-asset threshold multipliers in symbols.yaml — HYPE=2.5x, SOL=1.5x)

Weeks 3-4: Macro + Regime + LLM
  DI-4 → DI-6 (Deribit DVOL) → DI-7 (CoinGecko BTC.D) → DI-8 (News Feed)
  FE-3 (VIX + DXY only; DI-3/FRED deferred to Phase 5+)
  FE-6 [DONE] → AL-5 → AL-6 → AL-12 (News Event)
  LLM-1 → LLM-2 → LLM-2b (async news classifier) → LLM-3 → LLM-3b → DEL-3
  OPS-2 (macro degrade path)

Weeks 5-6: Derivatives Alerts
  AL-7 (CROWDED_LEVERAGE) → AL-8 (DELEVERAGING_EVENT)
  LLM-4 (event analysis)
  OPS-3 (derivatives degrade path)

Weeks 7-8: On-Chain
  F-2 (finalize if not done) → DI-9 (On-Chain Exchange Flow) → FE-5
  AL-9 → AL-10
  F-5b (config completeness pass)
  OPS-4 (on-chain degrade path)

Weeks 9-10: Evaluation + Hardening
  EV-1 → EV-2 → EV-3 → EV-4
  OPS-5 → OPS-6 → OPS-7 → OPS-8
  LLM-5 (weekly deep report)
  DI-3 (FRED — evaluate after EV-3 threshold tuning; may not be needed)

Weeks 11-12: Dashboard
  DEL-4 → DEL-5 → DEL-6 → DEL-7 (MVP view)
  DEL-13 (WebSocket integration)
  DEL-8 → DEL-9 → DEL-10 → DEL-11 → DEL-12 (fast-follows)
  ST-1 (storage optimization)
  INFRA-1 (golden fixtures + regression suite)
```

---

## Task Summary

| Epic | Tasks | Phase |
|------|-------|-------|
| 1. Foundation & DevEx | F-1 through F-7 (8 tasks, with F-5 split) | Phase 0 |
| 2. Data Ingestion | DI-0 through DI-9 (10 tasks; DI-3 deferred) | Phases 1–4 |
| 3. Storage | ST-1 (1 task) | Phase 1 |
| 4. Features & Regime | FE-1 through FE-6 (6 tasks) | Phases 1–2 |
| 5. Alert Engine | AL-1 through AL-12 (12 tasks) | Phases 1–4 |
| 6. LLM Synthesis | LLM-1, LLM-2, LLM-2b, LLM-3, LLM-3b, LLM-4, LLM-5 (7 tasks) | Phases 2–3 |
| 7. Delivery | DEL-1 through DEL-13 (13 tasks) | Phases 1, 6 |
| 8. Evaluation | EV-1 through EV-4 (4 tasks) | Phase 5 |
| 9. Ops & Reliability | OPS-1 through OPS-8 (8 tasks) | Cross-cutting |
| 10. Quality Assurance | QA-1, QA-2 (2 tasks) | Cross-cutting |
| 11. Infrastructure & Fixes | INFRA-1, FIX-1, FIX-2 (3 tasks) | Phase 1.5 |
| **Total** | **74 tasks** | |

---

*End of Tasks — CryptoMacro Analyst Bot MVP v2.1 (v3 Final — updated Phase 1.5 plan, 74 tasks)*
