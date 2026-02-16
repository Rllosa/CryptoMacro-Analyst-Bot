# CryptoMacro Analyst Bot

> Real-time market intelligence system monitoring crypto and macro markets with deterministic alerts and LLM-enhanced analysis.

## Overview

The CryptoMacro Analyst Bot is a rules-based monitoring and analysis system that:
- Tracks BTC, ETH, SOL, and HYPE across multiple data sources
- Detects regime shifts and market anomalies using deterministic algorithms
- Delivers high-signal alerts via Discord and web dashboard
- Provides LLM-powered daily briefs and event analysis

**Version:** MVP v2.1
**Status:** In Development
**Architecture:** Rust collectors → NATS JetStream → Python processor → TimescaleDB + Redis → Discord bot + FastAPI + React dashboard

---

## 🎯 MVP Scope

**Before implementing any feature, check:** [**SCOPE.md**](SCOPE.md)

The SCOPE.md document defines hard boundaries for the MVP to prevent scope creep. All non-goals, stretch goals, and asset limitations are explicitly documented.

---

## 📋 Project Documentation

- **[SCOPE.md](SCOPE.md)** — MVP boundaries and explicit non-goals
- **[.claude/rules.md](.claude/rules.md)** — Development rules and standards
- **[documentation/CryptoMacro_MVP_v2.1_Final.docx](documentation/CryptoMacro_MVP_v2.1_Final.docx)** — Complete system specification
- **[documentation/CryptoMacro_Linear_Tasks_v3.md](documentation/CryptoMacro_Linear_Tasks_v3.md)** — Task breakdown (64 tasks across 10 epics)

---

## 🏗️ Architecture

### Core Components
- **Collector (Rust):** Binance WebSocket ingestion with reconnection and heartbeat monitoring
- **Processor (Python):** Normalizer, feature engine, regime classifier, alert engine
- **Analyzer (Python):** LLM context builder and Claude API integration (non-triggering)
- **Bot (Python):** Discord bot for alert delivery and slash commands
- **API (FastAPI):** REST endpoints and WebSocket for real-time dashboard updates
- **Dashboard (React):** 6-view web interface with live data

### Infrastructure
- **TimescaleDB:** Time-series storage with hypertables and continuous aggregates
- **Redis:** Caching layer for latest state
- **NATS JetStream:** Message bus for event streaming
- **Docker Compose:** Local development environment

---

## 🚀 Quick Start

*(Coming soon after F-3: Docker Compose Infrastructure Setup)*

```bash
# Start infrastructure
docker-compose up -d

# Run smoke test (after QA-1)
make smoke
```

---

## 📊 Alert Types

### Market Alerts (6)
1. **VOL_EXPANSION** — Volatility + volume spike with breakout confirmation
2. **LEADERSHIP_ROTATION** — Significant relative strength shifts between assets
3. **Breakout Detection** — Price breakouts beyond 4h/24h ranges with volume
4. **REGIME_SHIFT** — Transition between 5 market regimes with confidence
5. **CORRELATION_BREAK** — BTC-equity or BTC-DXY correlation breakdown
6. **CROWDED_LEVERAGE** — Dangerously crowded positioning (funding + OI)
7. **DELEVERAGING_EVENT** — Cascade liquidation detection (always HIGH severity)

### On-Chain Alerts (2) — BTC/ETH Only
8. **EXCHANGE_INFLOW_RISK** — Large entity-tagged exchange inflows
9. **NETFLOW_SHIFT** — Structural netflow direction changes (accumulation ↔ distribution)

---

## 🧠 Regime States

The system classifies markets into 5 deterministic regimes:
1. **RISK_ON_TREND** — Trending up, positive momentum, low stress
2. **RISK_OFF_STRESS** — Macro stress elevated, correlations breaking
3. **CHOP_RANGE** — Range-bound, no clear trend, low volatility
4. **VOL_EXPANSION** — Volatility spiking, breakouts occurring
5. **DELEVERAGING** — Cascade liquidations, forced selling

---

## 🎯 Asset Coverage

| Asset | Price/Derivatives | On-Chain Flows | Notes |
|-------|-------------------|----------------|-------|
| **BTC** | ✅ Yes | ✅ Yes | Full coverage |
| **ETH** | ✅ Yes | ✅ Yes | Full coverage |
| **SOL** | ✅ Yes | ❌ No | On-chain out of MVP scope |
| **HYPE** | ✅ Yes | ❌ No | On-chain not applicable |

See [SCOPE.md](SCOPE.md) for detailed asset scope rationale.

---

## 🔧 Development

### Git Workflow
- **Never commit directly to `main`**
- Branch naming: `romain/{epic-prefix}-{task-id}-{short-description}`
- Commit format: `{TASK-ID}: Brief description`
- All changes via branches and PRs

### Project Rules
See [.claude/rules.md](.claude/rules.md) for:
- Architecture invariants (deterministic core, LLM never triggers, graceful degradation)
- Code standards (Rust, Python, TypeScript, SQL, YAML)
- Testing requirements (golden fixtures, contract tests, fault injection)
- Configuration management (no hardcoded thresholds)

---

## 📈 Implementation Timeline

**12-week MVP across 6 phases:**
- **Phase 0 (Days 1-3):** Foundation & DevEx
- **Weeks 1-2:** Crypto alerts end-to-end
- **Weeks 3-4:** Macro + regime + LLM
- **Weeks 5-6:** Derivatives alerts
- **Weeks 7-8:** On-chain intelligence
- **Weeks 9-10:** Evaluation + hardening
- **Weeks 11-12:** Web dashboard

See [documentation/CryptoMacro_Linear_Tasks_v3.md](documentation/CryptoMacro_Linear_Tasks_v3.md) for detailed task breakdown.

---

## 📝 License

*(To be determined)*

---

*For questions or scope clarifications, refer to [SCOPE.md](SCOPE.md) first.*
