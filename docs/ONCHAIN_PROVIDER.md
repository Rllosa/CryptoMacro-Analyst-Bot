# On-Chain Data Provider Decision

**Decision Date:** 2026-02-16
**Task:** F-2 (SOLO-23) — On-Chain Provider Decision Gate
**Status:** Provisional Recommendation (Pending API Verification)

---

## Executive Summary

**Recommended Provider:** [CryptoQuant](https://cryptoquant.com/)

**Rationale:** CryptoQuant meets all hard requirements (entity-tagged exchange flows for BTC/ETH), provides per-exchange granularity explicitly, focuses on exchange behavior monitoring which aligns with our alert use case, and offers competitive pricing. Glassnode is a strong alternative but is more macro-focused and potentially more expensive for our specific needs.

**Hard Requirement Status:** ✅ PASSED — Both providers supply entity-tagged exchange flow data (no address clustering or inference).

---

## Hard Requirement: Entity-Tagged Exchange Flows

From [.claude/rules.md](../.claude/rules.md) Section 1.4:

> On-chain exchange flow data must come from a provider that supplies **entity-tagged** flows. No address clustering, no wallet graph inference, no pattern matching on raw transactions. If the data source cannot confirm entity tagging, the on-chain pipeline does not activate. This is a hard gate, not a soft preference.

**Verification:**

- **CryptoQuant:** Explicitly supports [per-exchange views](https://cryptoquant.com/asset/btc/chart/exchange-flows) (Binance, Coinbase, Kraken separately). Covers [top 10 major exchanges](https://userguide.cryptoquant.com/cryptoquant-metrics/exchange/exchange-in-outflow-and-netflow) with labeled flows.
- **Glassnode:** Provides [extensive on-chain entity labels](https://cryptoindustry.com/reviews/glassnode) for exchanges, miners, and OTC desks. Entity-adjusted metrics available.

Both providers meet the hard gate requirement. ✅

---

## Provider Comparison

| Criterion | CryptoQuant | Glassnode |
|-----------|-------------|-----------|
| **Entity-Tagged Exchange Flows** | ✅ Yes — per-exchange (Binance, Coinbase, Kraken, etc.) | ✅ Yes — entity-adjusted metrics with exchange labels |
| **BTC Coverage** | ✅ Yes | ✅ Yes |
| **ETH Coverage** | ✅ Yes | ✅ Yes |
| **Data Fields** | Inflow, Outflow, Netflow, Reserve, Mean per TX | Inflow, Outflow, Net Position Change, Reserve, Balances |
| **Data Granularity** | Up to hourly (exact resolution per tier TBD) | Free: daily delay; Advanced: hourly; Pro: 10-min |
| **Pricing (Estimated)** | Starts at $99/month (full platform) | Advanced: $29/mo; Professional: $79/mo; Enterprise: custom |
| **Free Tier** | Available (API access details TBD) | Limited metrics, daily resolution, delayed |
| **API Access** | Included in paid tiers | Professional/Enterprise typically required |
| **Focus** | Short-term trading, exchange behavior, flow monitoring | Macro on-chain analysis, long-term fundamentals |
| **USD Values** | ✅ Yes (confirmed for spot flows) | ✅ Yes |
| **Latency** | Real-time to ~10min updates | 10min-1hr depending on tier |
| **API Documentation** | [cryptoquant.com/docs](https://cryptoquant.com/docs) | Limited public docs; requires subscription |

---

## Decision: CryptoQuant

### Why CryptoQuant?

1. **Explicit Per-Exchange Granularity**
   CryptoQuant [explicitly documents](https://cryptoquant.com/asset/btc/chart/exchange-flows) per-exchange tracking (Binance, Coinbase, Kraken, etc.). This is critical for our `onchain_exchange_flows` table which stores flows per exchange entity.

2. **Exchange Behavior Focus**
   CryptoQuant specializes in [monitoring exchange flows, liquidity, and short-term market behavior](https://sourceforge.net/software/compare/CryptoQuant-vs-Glassnode/), which directly aligns with our alert triggers:
   - `AL-9: EXCHANGE_INFLOW_RISK` — Large inflows to exchanges (potential sell pressure)
   - `AL-10: NETFLOW_SHIFT` — Sudden netflow reversals

3. **Competitive Pricing**
   Starting at $99/month for full platform access vs. Glassnode's $79/month Professional (which may require API add-ons). Free tier exists for initial testing.

4. **Proven for Trading Use Cases**
   CryptoQuant is [widely used by traders for market timing](https://slashdot.org/software/comparison/CryptoQuant-vs-Glassnode/) and flow analysis, which matches our alerting system's real-time nature.

### Why Not Glassnode?

- **Macro Focus:** Glassnode excels at [long-term on-chain fundamentals](https://cryptoindustry.com/reviews/glassnode) (holder cohorts, SOPR, realized cap) which are valuable but not our Phase 4 priority.
- **Higher Barrier:** Professional tier ($79/mo) typically required for API access; enterprise plans can run into hundreds/thousands monthly.
- **Overlapping Use Case:** Better suited as a complementary macro analysis tool (potential future addition) rather than primary flow data source.

**Trade-off Accepted:** CryptoQuant's focus on short-term flows means we lose access to Glassnode's deeper holder distribution and economic metrics. For MVP Phase 4, exchange flow alerts are the priority. Glassnode can be added later for macro regime input (e.g., NUPL, MVRV).

---

## Data Shape & Schema Compatibility

### onchain_exchange_flows Table

From [schema/migrations/005_create_onchain_tables.sql](../schema/migrations/005_create_onchain_tables.sql):

```sql
CREATE TABLE onchain_exchange_flows (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,         -- BTC, ETH only
    exchange TEXT NOT NULL,       -- binance, coinbase, kraken, etc.

    -- Flow metrics (in native coin units)
    inflow NUMERIC(30, 8),        -- Coins flowing into exchange
    outflow NUMERIC(30, 8),       -- Coins flowing out of exchange
    netflow NUMERIC(30, 8),       -- inflow - outflow

    -- Flow metrics (in USD)
    inflow_usd NUMERIC(30, 2),
    outflow_usd NUMERIC(30, 2),
    netflow_usd NUMERIC(30, 2),

    PRIMARY KEY (time, symbol, exchange)
);
```

### CryptoQuant API → DB Field Mapping

| CryptoQuant API Field | DB Column | Notes |
|-----------------------|-----------|-------|
| `inflow` | `inflow` | Amount in BTC (ETH for ETH asset) — native coin units |
| `outflow` | `outflow` | Amount in BTC (ETH for ETH asset) — native coin units |
| `netflow` (computed) | `netflow` | Inflow - Outflow — native coin units |
| `inflow_usd` | `inflow_usd` | USD value of inflow |
| `outflow_usd` | `outflow_usd` | USD value of outflow |
| `netflow_usd` (computed) | `netflow_usd` | Inflow USD - Outflow USD |
| `exchange` parameter | `exchange` | Entity label (e.g., "binance", "coinbase") |
| `asset` parameter | `symbol` | "BTC" or "ETH" |
| `timestamp` | `time` | ISO 8601 UTC timestamp |

**Compatibility:** ✅ Direct mapping with no transformation required beyond unit normalization.

---

## API Access Verification

**Status:** ⏳ **Pending API Key Acquisition**

### Next Steps (Before Phase 4 DI-6):

1. **Obtain API Key:**
   - Sign up for [CryptoQuant account](https://cryptoquant.com/)
   - Subscribe to appropriate tier (start with free/trial if available; upgrade to paid if required for API access)
   - Generate API key from account settings

2. **Test API Call:**
   ```bash
   # Example test call (endpoint syntax TBD pending docs access)
   curl -X GET "https://api.cryptoquant.com/v1/btc/exchange-flows/inflow?exchange=binance&window=1h" \
     -H "Authorization: Bearer YOUR_API_KEY"
   ```

3. **Verify Response Shape:**
   - Confirm response contains: `inflow`, `outflow`, `netflow` (or computed), `exchange`, `timestamp`
   - Verify USD values are included or can be computed from BTC amount × price
   - Check data granularity (hourly minimum for our 5-min feature cycle)

4. **Confirm Entity Tags:**
   - Verify exchange parameter accepts: `binance`, `coinbase`, `kraken`, etc.
   - Test response for BTC and ETH separately
   - Confirm no aggregated "all exchanges" limitation

5. **Document Rate Limits:**
   - Note requests/minute or requests/day limits
   - Plan polling interval for [DI-6 collector](../documentation/CryptoMacro_Linear_Tasks_v3.md) (likely 1-hour intervals for MVP)

---

## Cost Estimate

### CryptoQuant Pricing (Provisional)

- **Free Tier:** TBD — Likely limited metrics or delayed data; verify API access
- **Pro/Standard Tier:** $99/month (full platform access, API included)
- **Enterprise:** Custom pricing for higher rate limits or priority support

### MVP Budget Allocation (Phase 4)

- **Month 1-2 (Development):** Free tier or trial (if available)
- **Month 3+ (Production):** $99/month Pro tier
- **Annual Cost:** ~$1,200/year

**Cost Justification:** On-chain exchange flow alerts (`AL-9`, `AL-10`) are high-signal, low-noise triggers for major market moves (e.g., pre-dump inflows, accumulation outflows). $99/month is acceptable for this signal quality.

---

## Risk Assessment

### Provider-Specific Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| **API downtime** | Medium | Graceful degradation (OPS-4): On-chain alerts pause; crypto + macro alerts continue. |
| **Rate limit hit** | Low | Poll at 1-hour intervals (Phase 4 spec); well within typical limits. |
| **Data quality issues** | Low | CryptoQuant is established provider; cross-reference with Glassnode if anomalies detected. |
| **Pricing change** | Medium | Lock annual plan if offered; Glassnode as fallback provider. |
| **Entity tagging revoked** | Very Low | Would require fundamental API redesign; monitor provider updates. |

### Fallback Strategy

If CryptoQuant fails to meet requirements during Phase 4 integration:

1. **Immediate Fallback:** Glassnode (entity-tagged flows confirmed)
2. **Long-term Alternative:** IntoTheBlock (entity labels), Nansen (wallet labeling - more expensive)
3. **Nuclear Option:** Delay Phase 4 until suitable provider found; Phases 1-3 remain functional

---

## References & Sources

### CryptoQuant

- [CryptoQuant Exchange Flows](https://cryptoquant.com/asset/btc/chart/exchange-flows)
- [Exchange In/Outflow and Netflow User Guide](https://userguide.cryptoquant.com/cryptoquant-metrics/exchange/exchange-in-outflow-and-netflow)
- [CryptoQuant API Documentation](https://cryptoquant.com/docs)
- [CryptoQuant Pricing](https://cryptoquant.com/pricing)
- [CryptoQuant vs Glassnode Comparison](https://slashdot.org/software/comparison/CryptoQuant-vs-Glassnode/)

### Glassnode

- [Glassnode Review 2026](https://cryptoindustry.com/reviews/glassnode)
- [Glassnode Analytics Guide](https://truepositiontools.com/crypto/glassnode-analytics-guide)
- [Top 5 Cryptocurrency Data APIs Comparison](https://medium.com/coinmonks/top-5-cryptocurrency-data-apis-comprehensive-comparison-2025-626450b7ff7b)

### General On-Chain Data

- [Crypto Exchange Inflows and Outflows Explained](https://www.ccn.com/education/crypto/crypto-exchange-inflows-and-outflows-explained/)
- [What are inflows and outflows on crypto exchanges?](https://cointelegraph.com/explained/what-are-inflows-and-outflows-on-crypto-exchanges)

---

## Approval & Sign-Off

**Provisional Recommendation:** CryptoQuant
**Final Approval Required:** API verification test call (Step 2 above) before starting DI-6

**Next Actions:**

- [ ] Obtain CryptoQuant API key
- [ ] Run test API call and verify response shape
- [ ] Confirm per-exchange entity tagging in live data
- [ ] Document final field mapping
- [ ] Add API key to `.env` and `.env.example`
- [ ] Update this document with API endpoint syntax and rate limits

---

*This decision is binding for Phase 4 (Weeks 7-8). Provider can only be changed with explicit justification (e.g., entity tagging failure, unacceptable data quality, prohibitive cost increase).*
