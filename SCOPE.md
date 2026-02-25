# CryptoMacro Analyst Bot — MVP Scope Definition

> **Purpose:** This document defines the hard boundaries of the MVP. When tempted to add features during implementation, refer to this document first. Scope creep is the enemy of shipping.

---

## ✅ What IS In Scope (MVP v2.1)

### Core Functionality
- **Real-time data ingestion:**
  - Crypto price/volume: Binance (WebSocket, 1m candles)
  - Derivatives: Coinglass — **Phase 1.5 priority** (funding rates, OI, liquidations — unlocks DELEVERAGING regime and CROWDED_LEVERAGE alert)
  - Macro indicators: VIX + DXY via Yahoo Finance (daily); full FRED series deferred to Phase 3+
  - News headlines: Cryptopanic / The Block (Phase 2, async LLM classification → NEWS_EVENT alert)
  - Implied volatility: Deribit DVOL for BTC + ETH (Phase 2)
  - BTC Dominance: CoinGecko (Phase 2)
  - On-chain flows: provider-based, BTC/ETH only
- **Feature computation:** Technical indicators, volatility metrics, relative strength, derivatives features, on-chain flow features — computed every 5 minutes. Per-asset threshold multipliers for HYPE/alt calibration.
- **Regime classification:** Rules-based classifier with 5 named states plus INDETERMINATE:
  - Named: RISK_ON_TREND, RISK_OFF_STRESS, CHOP_RANGE, VOL_EXPANSION, DELEVERAGING
  - INDETERMINATE: fires in REGIME_SHIFT alert after ≥25 consecutive minutes of sub-threshold confidence, signaling transitional/ambiguous market structure
- **9 alert types:**
  - 7 market alerts: VOL_EXPANSION, LEADERSHIP_ROTATION, Breakout Detection, REGIME_SHIFT (incl. INDETERMINATE), CORRELATION_BREAK, CROWDED_LEVERAGE, NEWS_EVENT
  - 2 on-chain alerts: EXCHANGE_INFLOW_RISK, NETFLOW_SHIFT (BTC/ETH only)
- **LLM synthesis:** Daily briefs (2x/day via Claude API) with **POSITIONING BIAS section** (BULLISH/BEARISH/NEUTRAL/VOLATILE direction + leverage risk + alt exposure + actionable conditions); event-triggered analysis for high-severity alerts; async news classification (LLM-2b) feeding NEWS_EVENT alerts
- **Discord bot:** Alert delivery, slash commands for system queries
- **Web dashboard:** 3 core MVP views (Command Center, Asset Detail, Intelligence Center); remaining views are fast-follows
- **Evaluation framework:** Post-alert move tracking, hit rate metrics, threshold tuning tools, backtesting on 90–180 days of historical data (**required before live deployment**)

### Asset Coverage
- **Market data (price, derivatives, features, alerts):** BTC, ETH, SOL, HYPE
- **On-chain data (exchange flows, on-chain alerts):** **BTC and ETH ONLY**
  - SOL and HYPE on-chain flows: **explicitly out of scope** for MVP
  - Dashboard on-chain panels for SOL/HYPE display "N/A" or "Not available for this asset"

### Infrastructure
- TimescaleDB for time-series storage with hypertables and continuous aggregates
- Redis for caching latest state
- NATS JetStream for message bus
- Docker Compose for local dev environment
- Graceful degradation for all external dependencies

---

## ❌ What IS NOT In Scope (Hard Non-Goals)

These are **explicitly excluded** from the MVP. Do not implement, research, or prepare for these features unless they become a funded Phase 2.

### Trading & Execution
- ❌ **No automatic trading or execution**
  - No order placement, no position management, no risk management automation
  - This is a monitoring and analysis tool only

### Blockchain Infrastructure
- ❌ **No raw blockchain parsing**
  - No direct RPC node queries
  - No transaction mempool monitoring
  - No block-level event parsing
- ❌ **No address clustering or wallet graph analysis**
  - We only accept **entity-tagged exchange flow data** from providers (Glassnode, CryptoQuant)
  - No pattern matching, no heuristics, no "likely exchange" inference
  - This is a **hard gate** — on-chain integration does not proceed without confirmed entity tagging

### Machine Learning & Prediction
- ❌ **No complex predictive ML models**
  - No LSTMs, no transformers, no neural networks for price prediction
  - Regime classifier is **rules-based and deterministic** only
  - LLM is used for synthesis and explanation, never for triggering alerts

### Sentiment & Alternative Data
- ❌ **No news scraping or parsing**
- ❌ **No social sentiment analysis** (Twitter, Reddit, Telegram)
  - Fear & Greed Index is acceptable as a single API endpoint (optional)
  - No text mining, no sentiment models, no influencer tracking

### Market Microstructure
- ❌ **No order book analysis**
  - No bid-ask spread monitoring
  - No order flow imbalance detection
  - No market maker behavior analysis
- ❌ **No high-frequency market microstructure**
  - Minimum time resolution: 1-minute candles
  - Feature computation: 5-minute cycles

### Portfolio & Multi-Asset
- ❌ **No portfolio optimization**
  - No asset allocation recommendations
  - No portfolio rebalancing suggestions
  - No position sizing advice
- ❌ **No correlation-based multi-asset strategies**
  - Correlation is tracked for regime context only, not for portfolio construction

### Mobile & Additional Interfaces
- ❌ **No mobile app** (iOS/Android)
  - Web dashboard is responsive but not a native mobile experience
- ❌ **No Telegram bot** (Discord only for MVP)
- ❌ **No email alerts** (Discord only for MVP)
- ❌ **No SMS/push notifications**

### Advanced On-Chain
- ❌ **No SOL on-chain flows** (MVP limitation — providers lack reliable entity-tagged data)
- ❌ **No HYPE on-chain flows** (not applicable — HYPE is a perp DEX token)
- ❌ **No DeFi protocol flows** (Uniswap, Aave, etc.)
- ❌ **No NFT market monitoring**
- ❌ **No smart contract event analysis**

---

## 🎯 Stretch Goals (Deferred, Not in MVP)

These are interesting features that are **explicitly deferred** to post-MVP. They may be considered for Phase 2 if the MVP proves valuable.

### Stretch: Enhanced On-Chain (Post-MVP)
- SOL on-chain flows if reliable entity-tagged provider emerges
- DeFi protocol flow monitoring (Uniswap volumes, Aave liquidations)
- Stablecoin flow analysis

### Stretch: Additional Interfaces (Post-MVP)
- Telegram bot for alerts
- Mobile-responsive dashboard improvements
- Email digest delivery option
- Webhook API for custom integrations

### Stretch: Advanced Analysis (Post-MVP)
- Weekly deep-dive reports with historical regime comparisons
- Cross-regime alert quality analysis
- Multi-timeframe regime detection (detect regime on 1h, 4h, 1d)
- Customizable alert thresholds per user/channel

### Stretch: Additional Alert Types (Post-MVP)
- LIQUIDITY_CRUNCH (based on market depth)
- PROTOCOL_FLOW (DeFi-specific)
- STABLECOIN_STRESS (depeg risk)
- WHALE_ACCUMULATION (long-term flow patterns)

### Stretch: Macro Enhancements (Post-MVP)
- FRED full macro series (M2, Fed Funds, CPI, PCE, Jobless Claims) — deferred; VIX/DXY via Yahoo Finance is sufficient for MVP macro stress
- Fed speech/FOMC minutes parsing
- Macro event calendar integration
- Cross-asset correlation regime detection

---

## 🚧 Asset Scope Clarifications

| Asset | Price/Derivatives | On-Chain Flows | Notes |
|-------|-------------------|----------------|-------|
| **BTC** | ✅ Yes | ✅ Yes | Full coverage |
| **ETH** | ✅ Yes | ✅ Yes | Full coverage |
| **SOL** | ✅ Yes | ❌ No (MVP) | On-chain = "N/A" in dashboard |
| **HYPE** | ✅ Yes | ❌ No (MVP) | On-chain = "Not available" in dashboard |

### Why SOL/HYPE On-Chain Is Out of Scope
- **Provider constraint:** Entity-tagged SOL exchange flow data is not reliably available from Glassnode or CryptoQuant at MVP-acceptable quality
- **Hard gate enforcement:** We do not build our own clustering or inference — we only accept provider-tagged data
- **MVP focus:** BTC/ETH on-chain coverage is sufficient to validate the alert logic and on-chain integration

---

## 📋 Scope Enforcement Checklist

Before implementing any feature, ask:

1. ✅ Is this feature explicitly listed in "What IS In Scope"?
2. ❌ Is this feature listed in "What IS NOT In Scope"?
3. 🎯 Is this a "Stretch Goal" that should be deferred?
4. 📝 Does this feature require changes to `SCOPE.md`? (If yes, it's likely scope creep)

**If in doubt, refer to this document and the Linear task definitions.** When blocked by ambiguity, ask before expanding scope.

---

## 🔗 References

- Full specification: [`documentation/CryptoMacro_MVP_v2.1_Final.docx`](documentation/CryptoMacro_MVP_v2.1_Final.docx)
- Task breakdown: [`documentation/CryptoMacro_Linear_Tasks_v3.md`](documentation/CryptoMacro_Linear_Tasks_v3.md)
- Project rules: [`.claude/rules.md`](.claude/rules.md)

---

*Last updated: February 2026 — MVP v2.1 scope revision (Coinglass Phase 1.5, INDETERMINATE regime, NEWS_EVENT alert, positioning bias brief, backtesting gate, FRED deferred)*
