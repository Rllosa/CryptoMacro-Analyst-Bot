# CryptoMacro Analyst Bot — Project Rules

> These rules govern all development on the CryptoMacro Analyst Bot.
> Claude must read and follow this file before writing any code, creating any branch, or making any commit.

---

## 1. Architecture Invariants

These are non-negotiable. Violating any of these is a blocking issue regardless of context.

### 1.1 Deterministic Core
- The regime classifier and all 8 alert triggers are **pure rules and math**. No randomness, no ML inference, no LLM calls in the trigger path.
- Given identical `computed_features` + `cross_features` + `onchain_features` input, the regime and alert outputs must be **byte-identical** every time.
- All thresholds, weights, and conditions are loaded from `configs/thresholds.yaml`. **Zero hardcoded numeric thresholds** in application code. Constants like `0`, `1`, `100` for normalization bounds are fine; alert/regime thresholds are not.

### 1.2 LLM Never Triggers
- The LLM layer (`analyzer/`) has **no import path** to the alert engine (`processor/alerts/`). This is enforced architecturally, not by convention.
- LLM output is written to `analysis_reports` and posted to Discord. It never writes to `alerts`, `regime_state`, or any table that feeds the alert engine.
- If Claude API is down, the entire alert pipeline continues unaffected. The only visible impact is missing briefs.

### 1.3 Graceful Degradation
- Every external dependency failure disables **only** the features that depend on it. Nothing else.
- The system must **never crash** due to an external service being unavailable. "Never crash" means no panics (Rust), no unhandled exceptions (Python), no white screen (React).
- Degradation state is always reflected in `/api/health` and the Discord #system-health channel.

### 1.4 Entity-Tagged On-Chain Hard Gate
- On-chain exchange flow data must come from a provider that supplies **entity-tagged** flows. No address clustering, no wallet graph inference, no pattern matching on raw transactions.
- If the data source cannot confirm entity tagging, the on-chain pipeline does not activate. This is a hard gate, not a soft preference.

### 1.5 Asset Scope
- Market data (price, derivatives, features, alerts): **BTC, ETH, SOL, HYPE**
- On-chain data (exchange flows, flow features, on-chain alerts): **BTC, ETH only**
- On-chain columns/panels for SOL and HYPE display "N/A" or "Not available for this asset" — never empty, never hidden.

---

## 2. Git Workflow

### 2.1 Branch Management
- **Always create a new branch** for each Linear task.
- Branch naming convention: `romain/{epic-prefix}-{task-id}-{short-description}`
  - Examples:
    - `romain/f-3-docker-compose-setup`
    - `romain/di-1-binance-ws-collector`
    - `romain/al-2-vol-expansion-alert`
    - `romain/ops-4-onchain-degrade-path`
- **Never commit directly to `main`.** All changes go through branches and PRs.
- One branch per task. Do not combine multiple tasks into a single branch unless they are explicitly co-dependent and small (e.g., F-5a config + F-7 schema stubs).

### 2.2 Commit Messages
- Format: `{TASK-ID}: Brief description of change`
  - Examples:
    - `F-3: Add docker-compose with TimescaleDB, Redis, NATS`
    - `DI-1: Implement Binance WS reconnect with exponential backoff`
    - `AL-1: Add cooldown registry with YAML-driven durations`
- **Never mention Claude or AI** in commit messages, PR descriptions, or code comments.
- Keep commits atomic: one logical change per commit. A task may have multiple commits.
- Write commit messages in imperative mood ("Add X", "Fix Y", not "Added X", "Fixes Y").

### 2.3 Pull Request Process

**Never merge without explicit user confirmation.** Create the PR, post the link, and wait.

**Never push code without explicit user permission.** Always ask before `git push` or `git commit`.

PR description template:

```markdown
## Summary
[1–2 sentence overview of what this PR delivers]

## Task
[Link to Linear task] — Closes {TASK-ID}

## Changes Made
- [Bullet list of concrete changes, grouped by file/module]

## Architecture Decisions
[Any non-obvious decisions made during implementation. Why this approach over alternatives.]

## How to Test

### Automated Tests
```bash
# Commands to run automated tests
```

### Manual Verification
1. [Step-by-step manual testing instructions]
2. [Include expected outputs]

## Acceptance Criteria Checklist
- [ ] [Copy acceptance criteria from task]
- [ ] [Each one checked off]

## Test Checklist
- [ ] [Copy test items from task]
- [ ] [Each one verified]

## Files Changed
- `path/to/file` — [brief description of change]

## Dependencies
- [Other tasks/PRs this depends on]
- [Other tasks/PRs that depend on this]

## Next Steps
[What comes after this PR in the execution order]
```

### 2.4 PR Review Checklist (Self-Review Before Submitting)
Before creating a PR, verify:
- [ ] No hardcoded thresholds (grep for magic numbers)
- [ ] No secrets in code, logs, or comments
- [ ] No `TODO` or `FIXME` without a linked task ID
- [ ] All new code has corresponding tests (per task definition)
- [ ] Contract schemas (F-7) still pass if message formats changed
- [ ] `make smoke` still passes (once QA-1 exists)
- [ ] Degradation paths still work if this changes data flow

### 2.5 Linear Integration Workflow

Every task follows a strict Linear status progression tied to git workflow stages:

#### Status Progression

1. **Task Started** → Update Linear to **"In Progress"**
   - When: Immediately after creating the task branch and beginning work
   - Action: `mcp__linear-server__update_issue` with `state: "In Progress"`

2. **PR Created** → Update Linear to **"In Review"**
   - When: After creating the pull request (locally or on GitHub)
   - Action: `mcp__linear-server__update_issue` with `state: "In Review"`

3. **PR Merged** → Update Linear to **"Done"**
   - When: After PR is merged to main (with explicit user confirmation)
   - Action: `mcp__linear-server__update_issue` with `state: "Done"`

#### Available Linear Statuses
- **Backlog** (unstarted) — Default state for all tasks
- **Todo** (unstarted) — Ready to start
- **In Progress** (started) — Work in progress, branch created
- **In Review** (started) — PR created, awaiting review
- **Done** (completed) — PR merged, task complete
- **Canceled** (canceled) — Task abandoned
- **Duplicate** (canceled) — Duplicate of another task

#### Workflow Example

```bash
# 1. Start F-3 task
git checkout -b romain/f-3-docker-compose-setup
# → Update SOLO-24 to "In Progress"

# 2. Complete work and commit
git commit -m "F-3: Add docker-compose with TimescaleDB, Redis, NATS"

# 3. Create PR (ask user first)
gh pr create --title "F-3: Docker Compose Infrastructure Setup" ...
# → Update SOLO-24 to "In Review"

# 4. Merge PR (after user approval)
gh pr merge
# → Update SOLO-24 to "Done"
```

#### Rules
- **Never skip status updates.** Every task must progress through these states.
- **Always update status immediately** after the triggering action (branch creation, PR creation, merge).
- **Never mark a task "Done"** until the PR is actually merged to main.
- **If a task is abandoned mid-work**, update to "Canceled" with a comment explaining why.

---

## 3. Code Standards

### 3.1 Rust (Collector Service)
- **Edition:** 2021+
- **Async runtime:** tokio (multi-threaded)
- **Error handling:** Use `anyhow` for application errors, `thiserror` for library errors. No `.unwrap()` in production code — use `.expect("descriptive message")` only where panic is truly impossible, or propagate with `?`.
- **Logging:** `tracing` crate with structured JSON output. Every log line must include: timestamp, level, service name, and relevant context (symbol, message type, etc.).
- **Naming:** snake_case for functions/variables, PascalCase for types/structs, SCREAMING_SNAKE_CASE for constants.
- **Dependencies:** Pin exact versions in `Cargo.toml` for reproducible builds.
- **Formatting:** `cargo fmt` before every commit. `cargo clippy -- -D warnings` must pass with zero warnings.
- **Tests:** `#[cfg(test)]` modules in the same file for unit tests. Integration tests in `tests/` directory.

### 3.2 Python (Processor, Analyzer, Bot, API, Eval)
- **Version:** 3.11+
- **Type hints:** Required on all function signatures. Use `from __future__ import annotations` for forward references.
- **Error handling:** Never bare `except:`. Always catch specific exceptions. Log the exception with traceback before re-raising or degrading.
- **Logging:** `structlog` with JSON output. Same structured format as Rust: timestamp, level, service, context.
- **Naming:** snake_case for functions/variables/modules, PascalCase for classes, UPPER_SNAKE_CASE for constants.
- **Imports:** stdlib → third-party → local, separated by blank lines. No wildcard imports (`from x import *`).
- **Formatting:** `ruff format` before every commit. `ruff check` must pass with zero errors.
- **Async:** Use `asyncio` for I/O-bound services (normalizer, API, bot). Feature engine and alert engine can be synchronous if simpler.
- **Dependencies:** Pin versions in `requirements.txt` or `pyproject.toml`. Use `pip install --break-system-packages` in Docker only.
- **Tests:** `pytest` with fixtures. Test files mirror source structure: `processor/alerts/vol_expansion.py` → `tests/processor/alerts/test_vol_expansion.py`.
- **No ORM for TimescaleDB.** Use raw SQL via `asyncpg` or `psycopg`. TimescaleDB-specific features (hypertables, continuous aggregates, compression) don't map well to ORMs.

### 3.3 React / TypeScript (Dashboard)
- **Framework:** React 18+ with Vite
- **Language:** TypeScript (strict mode). No `any` types unless absolutely unavoidable and commented with justification.
- **State management:** zustand for global stores (marketStore, regimeStore, alertStore, flowStore). React state for component-local state only.
- **Styling:** TailwindCSS utility classes. No CSS modules, no styled-components. Dark theme as default.
- **Components:** Functional components only. No class components.
- **Naming:** PascalCase for components, camelCase for functions/variables, UPPER_SNAKE_CASE for constants.
- **File structure:** One component per file. File name matches component name: `AssetCard.tsx`.
- **Charts:** TradingView lightweight-charts for candlesticks. Recharts for data visualization. No mixing chart libraries within the same view.
- **WebSocket:** Single connection via custom `useWebSocket` hook. Never open multiple WS connections.
- **Formatting:** Prettier + ESLint before every commit.

### 3.4 SQL (Migrations & Queries)
- **All SQL keywords UPPERCASE:** `SELECT`, `FROM`, `WHERE`, `CREATE TABLE`, etc.
- **Table names:** snake_case, plural for collections (`market_candles`, `alerts`), singular for state (`regime_state`).
- **Column names:** snake_case, descriptive (`funding_zscore` not `fz`).
- **Migrations:** Numbered sequentially (`001_create_market_candles.sql`, `002_create_derivatives_metrics.sql`). Each migration is idempotent (use `IF NOT EXISTS`).
- **No raw string interpolation in queries.** Always use parameterized queries (`$1`, `$2` for asyncpg; `%s` for psycopg). This is a security requirement.
- **Indexes:** Every time-series query pattern must have a supporting index. Verify with `EXPLAIN ANALYZE` before marking a query as "fast enough."

### 3.5 YAML (Configuration)
- **Comments required** for every non-obvious parameter explaining: what it controls, valid range, units.
- **No anchors/aliases** (`&` / `*`) — keep configs flat and readable.
- **Consistent structure:** Group by domain (regime, alerts, risk_score, eval).

---

## 4. Configuration Rules

### 4.1 Threshold Management
- Every alert threshold, regime condition, risk score weight, cooldown duration, and eval window is defined in `configs/thresholds.yaml`.
- Application code reads thresholds at startup and on config reload. **Never inline a threshold value.**
- To verify compliance: `grep -rn` for numeric constants in alert/regime code should return zero threshold-like values.
- When adding a new threshold, also add it to the config validation schema.

### 4.2 Secrets Management
- All API keys, tokens, and credentials live in `.env` (local) or a secret manager (production).
- `.env` is in `.gitignore`. Only `.env.example` is committed (with placeholder values, never real keys).
- Secrets must **never** appear in: code, comments, commit messages, PR descriptions, log output, Docker images, or test fixtures.
- Before every commit, mentally verify: "Could someone reconstruct my API keys from this diff?"

### 4.3 Provider Configuration
- Data source URLs, API endpoints, polling intervals, and rate limits are in `configs/providers.yaml`.
- Changing a provider (e.g., Glassnode → CryptoQuant) should require only config changes and a new adapter, not changes to feature computation or alert logic.

---

## 5. Testing Standards

### 5.1 Testing Philosophy
- **Alerts and regime are the most critical code.** They get the most tests.
- **Golden fixtures** are the primary testing strategy for deterministic components: known input → known expected output, verified by hand once, automated forever.
- **Contract tests** prevent silent breaking changes across service boundaries.
- **Fault injection tests** prove degradation paths work.
- Don't chase 100% coverage. Test the things that would hurt most if wrong.

### 5.2 Test Categories

#### Golden Fixture Tests (Feature Engine, Regime, Alerts)
- Store known input data under `tests/fixtures/golden/`.
- Each fixture includes: input data (candles, features), expected output (computed features, regime, alerts), and a human-readable explanation of why this output is correct.
- Run as part of `pytest` / `cargo test`. These are the first tests to write and the last to delete.

#### Contract Tests (Cross-Service Boundaries)
- JSON schemas under `schema/contracts/` define the shape of: NATS messages, alert payloads, LLM output, API responses.
- Every service that produces or consumes a contract-defined message must have a test that validates against the schema.
- Changing a schema requires updating all consuming tests. This is intentional friction.

#### Deterministic Alert Test Vectors
- For each alert type, maintain a test vector file with scenarios:
  - Trigger scenario (all conditions met) → expected: FIRES with correct severity
  - Partial condition (one condition missing) → expected: NO FIRE
  - Cooldown scenario (re-trigger within cooldown) → expected: SUPPRESSED
  - Persistence scenario (condition met once then gone) → expected: NO FIRE
- These are the most important tests in the project. If alert tests fail, nothing ships.

#### Fault Injection Tests (Degradation Paths)
- For each OPS task (OPS-2 through OPS-6), there is a corresponding test that:
  - Simulates the dependency failure (block host, kill container, mock API error)
  - Verifies the correct components degrade
  - Verifies unrelated components continue normally
  - Verifies recovery when the dependency returns

#### Integration / Replay Tests (Data Pipeline)
- Use recorded fixtures (DI-0) to replay real data through the pipeline.
- Assert: correct DB row count, correct message schema, correct feature values.

#### Smoke Test (QA-1)
- `make smoke` runs the full chain: infrastructure → fixture replay → features → alert → delivery.
- Must pass before any PR to `main` is merged.

### 5.3 Test Naming Convention
- Python: `test_{what}_{scenario}_{expected_outcome}`
  - Example: `test_vol_expansion_all_conditions_met_fires_medium`
  - Example: `test_vol_expansion_missing_volume_no_fire`
  - Example: `test_cooldown_retrigger_within_window_suppressed`
- Rust: `#[test] fn {what}_{scenario}_{expected_outcome}()`
  - Example: `fn binance_ws_reconnect_after_disconnect_succeeds()`

### 5.4 What Does NOT Need Automated Tests (Solo Pragmatism)
- Dashboard visual layout (manual verification is fine)
- Discord embed formatting (manual verification)
- One-time migration scripts (verified on run)
- LLM output quality (schema validation yes, "is the brief insightful" no)

---

## 6. Error Handling & Logging

### 6.1 Error Handling Strategy
- **External API calls:** Always wrap in try/catch with timeout. Log the error. Degrade gracefully. Never let an API failure propagate to crash the service.
- **Database operations:** Use connection pools with retry logic. If the DB is unreachable after N retries, log CRITICAL and set health to DOWN (don't crash the process — it should keep trying to reconnect).
- **NATS operations:** If publish fails, log WARNING and continue. The message is lost but the service survives. If subscribe fails, retry with backoff.
- **Feature computation:** If data is missing for one symbol, log WARNING and skip that symbol. Process remaining symbols normally.
- **Never swallow errors silently.** Every caught exception must produce a log line.

### 6.2 Logging Standards
- **Format:** Structured JSON on all services. Fields: `timestamp` (ISO 8601 UTC), `level`, `service`, `message`, and context-specific fields.
- **Levels:**
  - `ERROR`: Something failed that should not have. Requires investigation.
  - `WARNING`: Expected failure, handled gracefully (API timeout, missing data, degradation).
  - `INFO`: Normal operation milestones (service started, config loaded, alert fired, brief generated).
  - `DEBUG`: Detailed diagnostic info (feature values, query timings). Off in production by default.
- **Correlation IDs:** When a candle flows from collector → normalizer → feature engine → alert engine, use a shared trace ID so you can follow the chain in logs.
- **Never log secrets.** Sanitize API keys, tokens, and credentials from all log output. When logging API responses, redact authorization headers.
- **Alert-specific logging:** Every alert fire must log: alert_type, symbol, severity, trigger conditions met (with values), cooldown status, and whether it was suppressed.

---

## 7. Database Rules

### 7.1 Schema Discipline
- The schema in `schema/migrations/` is the single source of truth. No ad-hoc `ALTER TABLE` in application code.
- Every schema change goes through a new numbered migration file.
- Migrations are idempotent: running them twice produces no errors and no side effects.

### 7.2 Query Patterns
- **Read-heavy queries** (latest features, current regime, recent alerts) should hit Redis cache first, DB second.
- **Write paths** go directly to TimescaleDB. Redis is updated after successful DB write.
- **Time-range queries** must always include a `WHERE time >= $1 AND time < $2` clause. Never do full table scans on hypertables.
- **Aggregation queries** should use continuous aggregates (`candles_5m`, `candles_1h`) instead of computing on raw data.

### 7.3 TimescaleDB-Specific
- All time-series tables are hypertables. Never create a regular table for time-series data.
- Compression policies are configured in ST-1, not in application code.
- Continuous aggregate refresh policies run automatically. Application code should never manually refresh aggregates.

---

## 8. Service Communication

### 8.1 NATS JetStream
- **Subject naming:** `{domain}.{entity}.{symbol}` — e.g., `market.candles.btcusdt`, `alerts.fired`
- **Message format:** JSON, validated against contract schemas (F-7).
- **Delivery guarantee:** At-least-once. Consumers must handle duplicate messages (upsert/dedup on write).
- **Consumer groups:** Each logical consumer has a named durable consumer. This ensures messages aren't lost during consumer restarts.

### 8.2 Redis
- **Key naming:** `{domain}:{entity}:{identifier}` — e.g., `features:latest:btcusdt`, `regime:current`, `health:binance_ws`
- **TTL:** All cached values must have a TTL. Feature snapshots: 10 minutes. Regime: 10 minutes. Health: 2 minutes. No immortal keys.
- **Serialization:** JSON for complex objects, raw values for simple scalars.

### 8.3 WebSocket (Dashboard)
- Single endpoint: `/ws/live`
- JSON messages with `{ "type": "...", "data": { ... } }` envelope.
- Client sends no messages (read-only stream). Server pushes events.
- Reconnect with exponential backoff: 1s, 2s, 4s, 8s, max 30s.

---

## 9. Project Structure Rules

### 9.1 Directory Ownership
Each service owns its directory and does not import from other services:
- `collector/` (Rust) — data ingestion only. No feature computation, no alerts.
- `processor/` (Python) — normalizer, feature engine, regime classifier, alert engine. Core deterministic pipeline.
- `analyzer/` (Python) — LLM client, context builder, prompt templates. **Cannot import from `processor/alerts/`.**
- `bot/` (Python) — Discord bot. Consumes NATS events and DB reads. No computation.
- `api/` (Python) — FastAPI backend. Reads DB and Redis. No computation.
- `dashboard/` (React) — Frontend. Communicates only via REST API and WebSocket.
- `eval/` (Python) — Evaluation scripts. Reads from DB. Can import from `processor/` for replay.

### 9.2 Shared Code
- `schema/contracts/` — JSON schemas shared across services.
- `configs/` — YAML configuration shared across services.
- `tests/fixtures/` — Test data shared across test suites.
- No other shared code. If two services need the same logic, it's a sign the logic belongs in one service and the other should consume its output.

### 9.3 File Size Limits
- No single source file over **500 lines**. If it's getting long, split by responsibility.
- No single function over **80 lines**. If it's getting long, extract helpers.
- These are guidelines, not hard blocks — but if you're over the limit, you should have a good reason.

---

## 10. Deployment & Operations

### 10.1 Docker
- Every service has a Dockerfile or is defined in `docker-compose.yml`.
- `docker-compose up` starts the complete system in development mode.
- Docker images must not contain secrets. Use environment variables or mounted secret files.
- Pin base image versions (e.g., `python:3.11-slim`, not `python:latest`).

### 10.2 Environment Parity
- Development, staging, and production use the same Docker images with different `.env` files.
- No "works on my machine" code. If it runs in Docker, it runs everywhere.

### 10.3 Health Checks
- Every service exposes a health endpoint or responds to a health check mechanism.
- Docker health checks configured for all services in `docker-compose.yml`.
- `/api/health` is the aggregated system health view.

---

## 11. Documentation Rules

### 11.1 Required Documentation
- `README.md` — Project overview, quickstart, links to SCOPE.md and other docs.
- `SCOPE.md` — MVP boundaries and non-goals (F-1).
- `docs/ONCHAIN_PROVIDER.md` — Provider decision and rationale (F-2).
- `docs/STORAGE.md` — TimescaleDB policies and query patterns (ST-1).
- `schema/contracts/README.md` — Schema contract descriptions.
- `tests/fixtures/README.md` — Fixture format and usage.
- `configs/` — Inline YAML comments for every parameter.

### 11.2 Code Comments
- Comment the **why**, not the **what**. The code shows what; the comment explains why.
- Every alert type file should have a header comment with: trigger conditions, severity logic, cooldown, and a plain-English description of what market condition it detects.
- No commented-out code in committed files. Delete it; git has history.
- `TODO` comments must include a task ID: `# TODO(AL-3): Handle edge case when RS z-score is exactly at threshold`

### 11.3 ADRs (Architecture Decision Records)
- For non-obvious technical decisions (e.g., "why NATS over Kafka", "why asyncpg over psycopg", "why TradingView lightweight-charts"), create a short ADR in `docs/adr/`.
- Format: Title, Context, Decision, Consequences.
- This is optional for solo speed but recommended for decisions you might question in 3 months.

---

## 12. Do Not

- ❌ Merge PRs without explicit user confirmation
- ❌ Push code without explicit user permission
- ❌ Commit or push directly to `main`
- ❌ Mention Claude or AI in commits, PRs, code, or comments
- ❌ Hardcode threshold values in application code
- ❌ Let LLM code import from or call alert engine code
- ❌ Accept non-entity-tagged on-chain data
- ❌ Swallow exceptions silently (every error must produce a log)
- ❌ Use `SELECT *` in production queries
- ❌ Store secrets in code, logs, Docker images, or git history
- ❌ Create tables without hypertable conversion for time-series data
- ❌ Use string interpolation in SQL queries
- ❌ Skip contract schema validation when changing message formats
- ❌ Let one service's failure crash another service
- ❌ Ship alert code without deterministic test vectors
- ❌ Use `any` in TypeScript without a justifying comment
- ❌ Use `.unwrap()` in production Rust code
- ❌ Leave `TODO` comments without a task ID
- ❌ Create Redis keys without a TTL

---

## 13. MVP Execution Discipline

### 13.1 Task Completion Definition
A task is "done" when:
1. All acceptance criteria are checked off
2. All listed tests pass
3. PR is created with full description
4. Contract schemas still pass (if applicable)
5. Smoke test still passes (once QA-1 exists)
6. User has reviewed and approved the PR

### 13.2 Scope Control
- If a task feels like it's growing beyond its definition, stop. Check `SCOPE.md`. Check the task's acceptance criteria. If the extra work isn't in the criteria, it's a new task.
- "While I'm here" changes go in separate commits at minimum, separate branches ideally.
- Resist the urge to refactor unrelated code while implementing a task. File a tech debt issue instead.

### 13.3 When Stuck
- If blocked on a provider decision (F-2), skip ahead to non-dependent tasks.
- If blocked on an external API, use mock data and recorded fixtures (DI-0).
- If a task is taking 3x longer than expected, reassess: is the task too big? Split it. Is the approach wrong? Step back and re-evaluate.
- Document blockers in the Linear task comments so there's a trail.

---

## 14. Task Execution Order

Sequenced for fastest time-to-value. Working crypto alerts by end of Week 2. Full system by Week 12.

### Phase 0 — Foundation (Days 1–3)

```
F-1:  MVP Scope Lock & Non-Goals Checklist
F-3:  Docker Compose Infrastructure Setup
F-4:  Database Schema & Migrations
F-5a: Configuration Files — MVP (Phases 1–2 thresholds only)
F-6:  Service Skeletons & Project Structure
F-7:  Schema Contracts (JSON Schemas + Validators)

In parallel:
  DI-0: Capture Real Fixture Data (record 1h Binance + sample API responses)
  F-2:  On-Chain Provider Decision Gate (research, doesn't block Phases 1–3)
```

**Gate:** `docker-compose up` starts all infra. Migrations applied. Configs load. Skeletons build.

---

### Phase 1–2 — Crypto Alerts End-to-End (Weeks 1–2)

```
DI-1:  Binance WebSocket Collector (Rust)
DI-2:  NATS-to-TimescaleDB Normalizer
FE-1:  Feature Engine — Core Indicators (5-min cycle)
FE-2:  Feature Engine — Cross-Asset Features
AL-1:  Alert Engine Core (cooldowns, dedup, persistence)
AL-2:  Alert — VOL_EXPANSION
AL-3:  Alert — LEADERSHIP_ROTATION
AL-4:  Alert — Breakout Detection
AL-11: Alert Routing Rules
DEL-1: Discord Bot — Core Setup
DEL-2: Alert Embed Formatter
OPS-1: Health Model & /api/health Contract
QA-1:  End-to-End Smoke Test
```

**Gate:** `make smoke` passes. Crypto alerts fire and arrive in Discord with correct formatting and routing.

---

### Phase 2 continued — Macro + Regime + LLM (Weeks 3–4)

```
DI-3:  FRED API Collector
DI-4:  Yahoo Finance Collector
FE-3:  Macro Stress Composite (0–100)
FE-6:  Regime Classifier (5 regimes, deterministic)
AL-5:  Alert — REGIME_SHIFT
AL-6:  Alert — CORRELATION_BREAK
LLM-1: Context Builder
LLM-2: Claude Client + Prompt Library
LLM-3: Daily Brief (9 AM + 7 PM Dubai)
DEL-3: Discord — Daily Brief Delivery
OPS-2: Macro Degrade Path
```

**Gate:** Regime classification running. Daily briefs posting to Discord. Macro degradation tested.

---

### Phase 3 — Derivatives (Weeks 5–6)

```
DI-5:  Coinglass API Collector
FE-4:  Derivatives Feature Computation
AL-7:  Alert — CROWDED_LEVERAGE
AL-8:  Alert — DELEVERAGING_EVENT
LLM-4: Event-Triggered Analysis
OPS-3: Derivatives Degrade Path
```

**Gate:** 6 market alerts active. Event-triggered LLM analysis working. Derivatives degradation tested.

---

### Phase 4 — On-Chain Intelligence (Weeks 7–8)

```
F-2:   Finalize provider decision (if not done)
DI-6:  On-Chain Exchange Flow Collector (BTC/ETH only)
FE-5:  On-Chain Feature Computation
AL-9:  Alert — EXCHANGE_INFLOW_RISK
AL-10: Alert — NETFLOW_SHIFT
F-5b:  Configuration Completeness Pass (all Section 17 thresholds)
OPS-4: On-Chain Degrade Path
```

**Gate:** Full 8-alert suite active. On-chain alerts fire for BTC/ETH. All thresholds in YAML.

---

### Phase 5 — Evaluation + Hardening (Weeks 9–10)

```
EV-1:  Post-Alert Move Tracking (4h/12h windows)
EV-2:  Alert Quality Metrics & API
EV-3:  Threshold Tuning Framework
EV-4:  Backtesting Framework
OPS-5: LLM Degrade Path
OPS-6: Message Bus Degrade Path
OPS-7: Monitoring & Observability
OPS-8: Security Hardening
LLM-5: Weekly Deep Report (Sunday, Claude Opus)
```

**Gate:** Alert quality quantified. All degradation paths tested. Security audit passes.

---

### Phase 6 — Web Dashboard (Weeks 11–12)

```
DEL-4:  FastAPI Backend — REST Endpoints (MVP)
DEL-5:  FastAPI Backend — WebSocket Real-Time
DEL-6:  Dashboard — Shell & Navigation
DEL-7:  Dashboard — View 1: Command Center (MVP view)
DEL-13: Dashboard — WebSocket Integration

Fast-follows (build in order, ship as ready):
  DEL-8:  Dashboard — View 2: Asset Detail
  DEL-9:  Dashboard — View 3: Macro Dashboard
  DEL-10: Dashboard — View 4: On-Chain Intelligence
  DEL-11: Dashboard — View 5: Intelligence Center
  DEL-12: Dashboard — View 6: Evaluation & Performance

ST-1:   TimescaleDB Performance & Query Utilities
```

**Gate:** Dashboard View 1 live with real-time data. Full system operational.

---

### Dependency Graph (Critical Path)

```
F-3 → F-4 → DI-1 → DI-2 → FE-1 → AL-1 → AL-2 → QA-1
                                  ↘ FE-2 → AL-3
                                         → AL-5 (needs FE-6)
                                         → AL-6 (needs FE-3)

DI-3 → FE-3 → FE-6 → AL-5
DI-4 ↗

DI-5 → FE-4 → AL-7
            → AL-8 → LLM-4

F-2 → DI-6 → FE-5 → AL-9
                   → AL-10

AL-1 → EV-1 → EV-2 → EV-3
FE-6 → EV-4

DEL-4 → DEL-5 → DEL-6 → DEL-7 → DEL-8..12
```

The critical path is: **infrastructure → Binance collector → normalizer → features → alert core → first alert → smoke test**. Everything else branches off from there.

---

*End of Rules — CryptoMacro Analyst Bot MVP v2.1*
