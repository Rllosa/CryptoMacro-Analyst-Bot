# CryptoMacro Analyst Bot — Project Rules (MVP v3)

These rules govern all development on the CryptoMacro Analyst Bot.
They are binding for every task, branch, commit, PR, and architectural decision.
No code may be written, committed, pushed, or merged without following this document.

---

## 0. Operating Mode (Plan-First Discipline)

### 0.1 Plan Before Code

No code changes are allowed until:

- The problem is clearly defined
- Tradeoffs are explained
- A recommended option is proposed
- Explicit user approval is given

### 0.2 Review Mode Selection (Must Ask First)

Before reviewing code or a plan, always ask whether to use:

**1) BIG CHANGE**
Review interactively section-by-section: Architecture → Code Quality → Tests → Performance.
Max 4 top issues per section.

**2) SMALL CHANGE**
Ask one key question per section.

No implementation begins until review decisions are approved.

### 0.3 Engineering Preferences (Guide All Recommendations)

- DRY is important — flag repetition aggressively.
- Tests are non-negotiable — prefer too many tests over too few.
- Code must be "engineered enough" — not fragile, not prematurely abstracted.
- Err on the side of handling more edge cases.
- Prefer explicit over clever.

---

## 1. Architecture Invariants (Blocking If Violated)

These are hard constraints.

### 1.1 Deterministic Core

- Regime classifier and all 8 alert triggers are pure rules + math.
- No randomness. No ML inference. No LLM calls in the trigger path.
- Same inputs → byte-identical outputs.
- All thresholds, weights, cooldowns, and conditions come from `configs/thresholds.yaml`.
- Zero hardcoded threshold-like numeric values in application code.

### 1.2 LLM Never Triggers

- `analyzer/` must have no import path to `processor/alerts/`.
- LLM writes only to `analysis_reports` and Discord.
- LLM must never write to: `alerts`, `regime_state`, or any table that feeds the alert engine.
- Claude outage must not affect alerts — only briefs disappear.

### 1.3 Graceful Degradation

- External dependency failure disables only dependent features.
- The system must never crash due to external failure.
- All degradation states reflected in `/api/health` and Discord #system-health.
- Every failure must log.

### 1.4 Entity-Tagged On-Chain Hard Gate

- On-chain exchange flows must come from entity-tagged provider.
- No wallet clustering. No address heuristics.
- If entity tagging cannot be confirmed → on-chain pipeline does not activate.

### 1.5 Asset Scope

- Market data/features/alerts: BTC, ETH, SOL, HYPE
- On-chain: BTC, ETH only
- SOL/HYPE on-chain UI must show "N/A".

---

## 2. Git Workflow

### 2.1 Branches

- One branch per Linear task. Never commit directly to `main`.
- Naming: `romain/{epic-prefix}-{task-id}-{short-description}`

### 2.2 Commits

Format: `{TASK-ID}: Imperative description`

Rules:
- Atomic commits only.
- No AI mentions anywhere. No "Generated with X". No co-author lines.
- No secrets. Imperative mood.

### 2.3 Commit / Push / PR / Merge Permissions

**Commit Permission (Per-Task Model)**

- Ask once per task branch for permission to commit locally.
- After approval, multiple local commits are allowed without re-asking.
- Permission applies only to the current branch. A new branch requires new permission.

**Push / PR / Merge**

- Explicitly ask before: `git push`, creating a PR, merging a PR.
- Never merge without explicit confirmation.
- Always merge with `--squash --delete-branch`.
- **Wait for CI to go green before proposing a merge.** After pushing/creating a PR, check `gh run list` and confirm the run passes. Never say "all tests pass" based on local results alone — local Python version may differ from CI.

**PR Description Template**

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

## Test Checklist
- [ ] [Copy test items from task]

## Files Changed
- `path/to/file` — [brief description of change]

## Dependencies
- [Other tasks/PRs this depends on]

## Next Steps
[What comes after this PR in the execution order]
```

**IMPORTANT:** Never include Claude Code, AI, or automated tool attribution (e.g., "🤖 Generated with Claude Code") in PR descriptions.

### 2.4 Linear Workflow

Task progression:
- Branch created → **In Progress**
- PR created → **In Review**
- PR merged → **Done**
- Abandoned → **Canceled** (with explanation)

Never mark Done before merge.

### 2.5 Documentation Before Done

If implementation expands scope, reveals architectural constraints, or deviates from original task wording, then update:
- Linear task description (Implementation Notes)
- `documentation/CryptoMacro_Linear_Tasks_v3.md`

Before marking Done.

---

## 3. Code Standards

### 3.1 Rust (Collector)

- Edition 2021+
- tokio multi-thread runtime
- No `.unwrap()` in production; `anyhow` for app errors, `thiserror` for libraries
- Structured JSON logs via `tracing`
- `cargo fmt` + `cargo clippy -- -D warnings`
- Exact dependency pinning
- Unit tests for all public functions
- No unbounded channels in hot path
- Profile before optimizing; avoid heap allocations in hot path

### 3.2 Python (Processor / Analyzer / API / Bot / Eval)

- Python 3.11+
- Type hints required; `from __future__ import annotations` for forward references
- No bare `except`; structured logging with `structlog`
- No ORM for TimescaleDB; parameterized SQL only
- No blocking calls inside async; pin dependencies
- All public functions must have unit tests
- Batch DB writes only; no unbounded in-memory buffers

### 3.3 React / TypeScript

- Strict TypeScript; no `any` unless justified with comment
- Zustand for global state; Tailwind only
- Single WebSocket connection
- Prettier + ESLint required

### 3.4 SQL Rules

- UPPERCASE SQL keywords
- Idempotent migrations (`IF NOT EXISTS`)
- No `SELECT *`; always parameterized queries
- Hypertables only for time-series
- Every time-range query must include `WHERE time` bounds
- Verify performance with `EXPLAIN ANALYZE`

### 3.5 YAML Rules

- Every parameter commented
- No anchors (`&`/`*`)
- Grouped by domain
- Threshold additions require schema update

---

## 4. Performance & Hot Path Mandate

These are defaults, not suggestions.

- One pass over collections only.
- No append-in-loop if extend/generator works.
- Use single multi-row INSERT, never `executemany`.
- No loop-await — use `gather`/`join`.
- Hoist all loop invariants.
- No rebuilding constants inside functions.
- Batch I/O.
- Profile before exotic optimization.

Before submitting PR touching data processing, verify:
- No double iteration.
- No loop-await.
- No per-row DB writes.
- No per-call constant rebuild.

---

## 5. Testing Standards

### 5.1 Philosophy

- Alerts and regime are highest criticality.
- Golden fixtures are the primary testing strategy for deterministic components.
- Don't chase 100% coverage — test the things that would hurt most if wrong.

### 5.2 Required Test Types

- Golden fixtures
- Deterministic alert vectors
- Contract tests
- Fault injection tests
- Replay tests
- Smoke test

Alert code cannot ship without deterministic vectors.

### 5.3 Critical Code Enforcement

Any change to `processor/regime/` or `processor/alerts/` requires:
- Updated golden fixtures or vectors
- At least one new edge-case test

---

## 6. Error Handling & Logging

- Never swallow errors. Every caught exception logs.
- External API failures degrade gracefully.
- DB failures retry before marking DOWN.
- Correlation IDs across services.
- Never log secrets.
- Alert fires must log trigger conditions + cooldown state.

---

## 7. Database Discipline

- Schema migrations only — no ad-hoc `ALTER TABLE`.
- Hypertables only for time-series.
- Redis TTL mandatory on all keys.
- Read-heavy → Redis first; writes → DB then cache.
- Continuous aggregates for aggregation queries.

---

## 8. Service Boundaries

No cross-service imports except:
- `schema/contracts/`
- `configs/`
- `tests/fixtures/`

If two services need the same logic, it belongs in one service; the other consumes its output.

---

## 9. File Size Guidelines

- Max 500 lines per file.
- Max 80 lines per function.
- If exceeded, justify.

---

## 10. Deployment

- Docker for every service. No secrets in images. Pin base images.
- Same images across environments.
- Health checks required for all services.

---

## 11. Documentation

Required files:
- `README.md`, `SCOPE.md`, `docs/STORAGE.md`, `docs/ONCHAIN_PROVIDER.md`
- `schema/contracts/README.md`, `tests/fixtures/README.md`

Rules:
- Comment the why, not the what.
- No commented-out code in committed files.
- `TODO` must include task ID: `# TODO(AL-3): description`

---

## 12. Hard Prohibitions

- ❌ Merge without approval
- ❌ Push without approval
- ❌ Commit to main
- ❌ Mention AI in code/commits/PRs
- ❌ Hardcode thresholds
- ❌ Accept non-entity-tagged on-chain data
- ❌ Swallow exceptions
- ❌ `SELECT *`
- ❌ Store secrets in code
- ❌ Skip contract validation
- ❌ Let one service crash another
- ❌ Ship alerts without deterministic tests
- ❌ Use `executemany` in hot path
- ❌ Rebuild constants inside functions
- ❌ Leave `TODO` without task ID
- ❌ Create Redis keys without TTL

---

## 13. Task Completion Definition

A task is Done when:
1. Acceptance criteria checked
2. Tests pass
3. PR created with full description
4. Schemas validated
5. Smoke test passes (once available)
6. User approves PR
7. Documentation updated if needed

---

## 14. Scope Discipline

- If task expands: Stop. Check `SCOPE.md`. If not in criteria → new task.
- No "while I'm here" refactors. File a tech debt issue instead.

---

## 15. Review Protocol (Enforced Format)

For every issue found during review:

```
Issue N: Title
Location: file + line (or plan-level)
Why it matters

Options:
  A (Recommended): effort / risk / impact / maintenance
  B: …
  C (Do nothing): …
```

Then explicitly ask for selection using `AskUserQuestion` with issue NUMBER + option LETTER clearly labeled.

After each review section: pause and wait for feedback before moving on.

---

## 16. Performance Reviewer Checklist (Mandatory for Data Path Changes)

Complete before approving any PR touching: collector, normalizer, feature engine, regime classifier, alert engine, DB write paths, WebSocket broadcast, Redis hot paths.

If any item fails, the PR must not merge until resolved or explicitly justified.

### 16.1 Iteration Discipline

- [ ] No collection iterated more than once when a single pass would suffice
- [ ] No `append` inside Python loops when `extend(generator)` is possible
- [ ] No per-element push in Rust when `.map().collect()` or `.fold()` would work
- [ ] No nested loops creating accidental O(n²) behavior
- [ ] Loop-invariant values hoisted outside the loop

Remediation: refactor to single-pass extraction, combine loops, precompute invariants.

### 16.2 Async & Concurrency

- [ ] No `await` inside loops when `asyncio.gather()` can be used
- [ ] No sequential fan-out for independent I/O calls
- [ ] No blocking calls inside async functions
- [ ] Concurrency limits enforced for rate-limited APIs
- [ ] Rust async tasks use bounded channels

Remediation: replace with `gather()` / `tokio::join!`, add semaphore, add explicit buffer size.

### 16.3 Database Write Path

- [ ] No `executemany`; no per-row `execute` calls
- [ ] Multi-row `INSERT VALUES (...), (...), ...` used
- [ ] Placeholder strings pre-built at module load
- [ ] Flat parameter list built in one comprehension
- [ ] Writes batched with both size cap and timeout flush

Remediation: refactor to single multi-row INSERT, hoist placeholder constants.

### 16.4 Memory Discipline

- [ ] No unbounded in-memory accumulation
- [ ] Buffers have both max size and timeout
- [ ] Large temporary lists avoided when generator suffices
- [ ] No repeated JSON serialization/deserialization in hot loops

Remediation: switch to generators, reuse objects, introduce bounded buffers.

### 16.5 Allocation & Constant Hoisting

- [ ] No SQL placeholder string rebuilt per call
- [ ] No regex compiled inside function
- [ ] No static tuple/list rebuilt inside function
- [ ] No repeated format string construction inside loop

Remediation: hoist constants to module scope, precompute static structures.

### 16.6 Logging Performance

- [ ] No heavy debug logging inside hot loops
- [ ] Log fields are structured, not string-concatenated
- [ ] No large payload dumps at INFO level
- [ ] Debug logs disabled by default in production

Remediation: downgrade to DEBUG, remove heavy payload logging, log summary metrics.

### 16.7 Redis & Cache Discipline

- [ ] No cache write without TTL
- [ ] No repeated writes of identical values inside same cycle
- [ ] No N+1 Redis reads inside loops
- [ ] No blocking Redis calls in async hot path

Remediation: add TTL, deduplicate writes, batch reads where possible.

### 16.8 Query Performance

- [ ] All time-range queries include explicit time bounds
- [ ] Supporting index exists for query pattern
- [ ] `EXPLAIN ANALYZE` verified for new queries
- [ ] No full table scans on hypertables
- [ ] Continuous aggregates used instead of raw aggregation

Remediation: add index, refactor query, use continuous aggregate.

### 16.9 Determinism & Reproducibility

- [ ] No randomness introduced
- [ ] No reliance on dict iteration order where order matters
- [ ] No floating-point instability without rounding policy
- [ ] Thresholds read from config, not embedded

Remediation: make order explicit, add rounding policy, move thresholds to YAML.

### 16.10 Pre-Merge Performance Questions (Must Be Answered)

Before merging, explicitly answer:
1. Does any loop iterate the same collection twice?
2. Does any I/O fan-out run sequentially when it could be concurrent?
3. Are all DB writes batched into single multi-row inserts?
4. Are all loop invariants hoisted?
5. Could this code run 10x more frequently without collapsing?
6. Did we profile if this is in the hot path?

If the answer to any is "no", justify in PR description.

### 16.11 When Profiling Is Required

Mandatory when:
- Adding new feature computation
- Modifying alert evaluation logic
- Changing DB write structure
- Increasing batch sizes
- Adding cross-asset computation
- Introducing new fan-out calls

Tools: `py-spy`, `cProfile` (Python); `cargo flamegraph`, `perf` (Rust).

### 16.12 Golden Rule

Performance fixes must:
- Preserve determinism
- Preserve correctness
- Preserve test coverage

Speed is never allowed to compromise alert correctness.

---

## 17. Task Execution Order

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

### Phase 1.5 — Derivatives & Threshold Fixes (Week 2–3)

```
DI-5:  Coinglass API Collector  [HIGHEST PRIORITY — unlocks DELEVERAGING + CROWDED_LEVERAGE]
FE-4:  Derivatives Feature Computation (funding_zscore, oi_drop_1h, liquidations_1h_usd)
FIX-1: Fix volatility_regime threshold (rv_4h_zscore > 0 → > 0.5 in classifier.py)
FIX-2: Per-asset threshold multipliers in symbols.yaml (HYPE calibration)
```

**Gate:** DELEVERAGING regime can fire. CROWDED_LEVERAGE alert activates. HYPE thresholds calibrated.

---

### Phase 2 continued — Macro + Regime + LLM (Weeks 3–4)

```
DI-4:   Yahoo Finance Collector (VIX + DXY — only macro inputs needed for MVP)
DI-6:   Deribit DVOL Collector (BTC + ETH implied vol — leading indicator for VOL_EXPANSION)
DI-7:   CoinGecko BTC Dominance (alt season signal)
DI-8:   News Feed Collector (Cryptopanic / The Block — high-importance headlines only)
DI-10:  Coinglass Liquidation Heatmap Collector (price-level cascade risk — context for LLM-3b)
FE-3:   Macro Stress Composite (VIX + DXY → 0–100; unlocks RISK_OFF_STRESS regime)
FE-6:   [DONE] Regime Classifier (5 regimes + INDETERMINATE, deterministic)
AL-5:   Alert — REGIME_SHIFT (incl. INDETERMINATE after ≥25 min uncertain)
AL-6:   Alert — CORRELATION_BREAK
AL-12:  Alert — NEWS_EVENT (deterministic rules on async LLM output — Rule 1.1 preserved)
LLM-1:  Context Builder
LLM-2:  Claude Client + Prompt Library
LLM-2b: Async News Classifier (batch classifies DI-8 headlines → structured JSON for AL-12)
LLM-3:  Daily Brief (9 AM + 7 PM Dubai) with POSITIONING BIAS section
LLM-3b: Positioning Bias — regime → BULLISH/BEARISH/NEUTRAL/VOLATILE + leverage risk + alt exposure
DEL-3:  Discord — Daily Brief Delivery
OPS-2:  Macro Degrade Path

NOTE: DI-3 (FRED full series) deferred to Phase 5+. VIX + DXY via Yahoo Finance suffices.
NOTE: LLM-2b is async background service — never in 5m hot path. Rule 1.1 preserved.
```

**Gate:** All 5 regimes active (incl. RISK_OFF_STRESS). INDETERMINATE alert fires. Deribit DVOL flowing. Daily briefs posting.

---

### Phase 3 — Derivatives Alerts (Weeks 5–6)

```
AL-7:  Alert — CROWDED_LEVERAGE (uses FE-4 funding_zscore from Phase 1.5)
AL-8:  Alert — DELEVERAGING_EVENT (uses FE-4 liquidations from Phase 1.5)
LLM-4: Event-Triggered Analysis
OPS-3: Derivatives Degrade Path
```

**Gate:** 6 market alerts active. Event-triggered LLM analysis working. Derivatives degradation tested.

---

### Phase 4 — On-Chain Intelligence (Weeks 7–8)

```
F-2:   Finalize provider decision (if not done)
DI-9:  On-Chain Exchange Flow Collector (BTC/ETH only)
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

Phase 1.5:
DI-5 → FE-4 → AL-7
            → AL-8 → LLM-4
FIX-1: classifier.py (rv_4h_zscore threshold)
FIX-2: symbols.yaml (per-asset multipliers)

Phase 2:
DI-4 → FE-3 → FE-6 [DONE] → AL-5
DI-6 (Deribit DVOL) → FE-3/cross_features
DI-7 (CoinGecko BTC.D) → cross_features
DI-10 (Liquidation Heatmap) → LLM-3b

Phase 4:
F-2 → DI-9 → FE-5 → AL-9
                   → AL-10

AL-1 → EV-1 → EV-2 → EV-3
FE-6 → EV-4 (backtesting — required before live deployment)

DEL-4 → DEL-5 → DEL-6 → DEL-7 → DEL-8..12
```

The critical path is: **infrastructure → Binance collector → normalizer → features → alert core → first alert → smoke test**. Everything else branches off from there.

---

*End of Rules — CryptoMacro Analyst Bot MVP v3*
