# Zero-Fee Retest: T01 OFI at 0bp (Lighter Path)

**Date**: 2026-08-02 · **Author**: quant-researcher · **Data**: BTC aggTrades 2026-04-19 → 2026-07-17 (129M trades)

## TL;DR

**The T01 OFI signal cannot be revived at 0bp because the edge was never real.** The original "corr +0.21, quintile spread +3.41bp/trade" finding was an artifact of using vwap-to-vwap returns instead of close-to-close returns. With proper close-to-close returns rebuilt from the raw trade tape, OFI has **corr −0.012** with next-bar returns — essentially zero predictive power. At 0bp fee, OOS Sharpe is **−1.28** (negative even at zero cost). The 300ms Lighter taker latency is irrelevant (costs only 0.17bp/entry) because there is no gross edge to eat.

The break-even fee is **0.00–0.01 bp** — the strategy barely breaks even at literal zero cost.

---

## 1. The vwap Artifact (Root Cause)

### What happened

The prior T01 research (SMA-35037) used pre-built 1m bars from `research/ofi/btc_1m_3mo.parquet`. These bars contain `buy_vol`, `sell_vol`, and `vwap` but **no `close` price**. The signal returns were computed as:

```python
bars['mid_ret'] = bars['vwap'].pct_change()  # ← the bug
```

This creates a **mechanical correlation** between OFI and returns because both are driven by the same within-bar volume imbalance:

| Return basis | OFI corr (next bar) | OFI corr (same bar) |
|---|---|---|
| **close-to-close** (correct) | **−0.0116** | 0.5466 |
| **vwap-to-vwap** (prior used) | **+0.2051** | 0.5166 |

**Explanation**: vwap is the volume-weighted average price. When buy-side taker volume exceeds sell-side (positive OFI), the vwap is biased upward relative to the bar's open — more buy volume occurred at higher prices. This inflates the vwap-to-vwap return in the direction of the OFI signal, creating a spurious +0.21 correlation that vanishes with close-to-close returns.

### Proof

The correlation holds at all tested horizons:

| Horizon | OFI corr (next-bar close return) |
|---|---|
| 1m | −0.0116 |
| 5m | −0.0104 |
| 15m | −0.0115 |

OFI has **zero predictive power** for future close-to-close returns at any tested frequency.

---

## 2. Fee Sweep (OOS, Best IS Parameters)

Best IS parameters (grid search at 0bp, first half of data): **L=30, thr=0.5, hold=5 bars**.

IS Sharpe at 0bp: **3.58** (deceptively positive — does not survive OOS).

### OOS Fee Sweep (second half of data)

| Fee RT (bp) | OOS Sharpe | OOS Ann Return | Net Edge/Trade (bp) |
|---|---|---|---|
| **0.00** | **−1.28** | −0.24 | +4.51 |
| 0.50 | −21.43 | −4.09 | +4.01 |
| 1.00 | −41.30 | −7.94 | +3.51 |
| 2.00 | −79.20 | −15.64 | +2.51 |
| 5.00 | −168.39 | −38.74 | −0.49 |
| 10.83 | −247.88 | −83.62 | −6.32 |

**Break-even fee**: 0.00 bp (OOS) / 0.01 bp (full dataset). The strategy loses money even at literal zero fees on out-of-sample data.

Note: the "gross edge" of 4.51 bp/trade is the mean of **absolute** returns on active bars — it is not a directional edge. The signed mean return per active bar is near zero (consistent with the −0.012 correlation).

### Full OOS Grid at 0bp

Tested 150 cells (6 lookbacks × 5 thresholds × 5 holding periods):

- **19/150** cells have OOS Sharpe ≥ 1.0 at 0bp
- **36/150** cells are positive
- This is **within the expected false-positive rate** for 150 trials — no cell passes Deflated Sharpe Ratio (DSR) multiple-testing correction
- The best OOS cell (L=15, thr=2.0, hold=60) has Sharpe 5.57 with only 905 trades — high variance, not robust

### CPCV Robustness (best params, 0bp)

15 CPCV folds: mean Sharpe **0.98 ± 1.63**, range [−1.91, +3.80], 73% positive.

The mean is marginally positive but dominated by IS-period folds. The OOS is negative. This is not a robust edge.

---

## 3. Lighter Latency Analysis

### Latency Cost Estimation

Using the `LighterAdapter.batch_latency_slippage()` method on 36,421 entry signals against the raw trade tape:

| Execution | Latency | Mean \|slippage\| per entry |
|---|---|---|
| Taker | 300 ms | **0.17 bp** |
| Maker | 200 ms | **0.07 bp** |

The Lighter artificial latency is **negligible** — 0.17bp per entry is far smaller than the 1.8bp edge claimed by the prior research (and even smaller than the ~0 signed edge found here).

### Lighter Scenario (0bp fee + 300ms latency, OOS)

| Scenario | OOS Sharpe | OOS Ann Return |
|---|---|---|
| 0bp fee, 0ms latency | −1.28 | −0.24 |
| 0bp fee, 300ms taker latency | **−8.14** | −1.55 |
| 0bp fee, 200ms maker latency | −4.06 | — |

The latency makes things slightly worse, but the dominant problem is the absence of signal edge, not the latency.

---

## 4. Assessment

### Is the edge real at 0bp?

**No.** The edge was never real. The original +3.41bp quintile spread and +0.21 correlation were artifacts of using vwap-to-vwap returns, which are mechanically correlated with the volume-imbalance signal. With proper close-to-close returns, the signal has zero predictive power (corr −0.012). The strategy loses money on OOS even at 0bp.

### Does the 200ms latency eat the edge?

**The question is moot** — there is no edge to eat. The latency cost (0.17bp/entry) is tiny and would be easily survivable if a real edge existed. The finding that 300ms costs only 0.17bp is actually **positive news for other strategies**: the Lighter latency penalty is not a deal-breaker for any strategy with a genuine >1bp edge.

### Why did the prior research confirm the signal?

The prior (SMA-35037) correctly identified that "kline proxy can't capture same-ms trade bursts" and moved to real aggTrades. However, the pre-built 1m bars aggregated from aggTrades used vwap (not close) as the price reference. Since OFI and vwap are both functions of within-bar volume direction, the correlation was guaranteed by construction. The prior's CPCV walk-forward and quintile analysis all inherited this bias.

---

## 5. Which Other Killed Strategies Are Worth Retesting at 0bp?

### T04 — Iceberg absorption: **Worth a quick retest**

T04 was killed for cost-cap with a different mechanism: gross signed returns at 60s/300s horizons were +1.6 to +5.4bp, eaten by the 10.83bp futures RT. Unlike T01, T04's signal was computed from the trade tape directly (not from vwap bars), so it does not inherit the vwap artifact.

**However**, the prior also found:
- Short-horizon (1s/10s) signed returns were **negative** (−0.7 to −1.9bp)
- t-stats were sub-significant (t < 1.2) at all horizons
- Big events were *more* negative post-impact (opposite of the absorption thesis)

At 0bp, the 300s gross edge of +5.4bp would be net positive — but the t-stat of ~1.2 means this is not statistically robust. A 0bp retest with the proper DSR correction and larger sample (or cross-asset validation on ETH/SOL) is the one remaining avenue. **Priority: medium.**

### VPVR reversion variants (T08, T09): **Not worth retesting at 0bp**

- **T08** was killed for funding-gate failure (no funding>0.03% events in 18+ months), not cost.
- **T09** had 0/12 CPCV variants pass with structural negative folds — a robustness failure, not a cost issue.

These strategies failed for structural reasons that 0bp fees do not address.

### Recommendation

The highest-value next step is not retesting killed strategies at 0bp, but rather:

1. **Fix the vwap artifact in any surviving research** that used `btc_1m_3mo.parquet`. The artifact inflates OFI-return correlations and any downstream signal built on vwap returns.
2. **Retest T04 iceberg absorption at 0bp** with DSR correction — it's the only killed strategy where the kill was purely cost-cap and the signal computation doesn't inherit the vwap bias.
3. **Explore the Lighter path for genuinely new signals** — the latency cost (0.17bp) is low enough that any strategy with a real >1bp gross edge would thrive there.

---

## 6. Artifacts

| File | Description |
|---|---|
| `_shared/execution/lighter_adapter.py` | Lighter simulation adapter (fee + latency model) |
| `_shared/execution/test_lighter_adapter.py` | 20 tests (all passing) |
| `research/fee_paths/ofi_zero_fee_backtest.py` | Full backtest with fee sweep + latency analysis |
| `research/fee_paths/ofi_zero_fee_bars.parquet` | 1m bars rebuilt with real close prices (129,599 bars) |
| `research/fee_paths/ofi_zero_fee_results.json` | Full machine-readable results |

---

## Appendix: Methodology

- **Data**: BTCUSDT aggTrades, partitioned by year/month, 2026-04-19 → 2026-07-17 (129M trades)
- **Bar construction**: trades grouped by `ts.floor('1min')`, close = last trade price, vwap = Σ(price×qty)/Σ(qty), buy_vol = Σ(qty where is_buyer_maker=False), sell_vol = Σ(qty where is_buyer_maker=True)
- **OFI signal**: z-scored (buy_vol − sell_vol) over rolling lookback window (Cont-Kukanov-Stoikov 2014)
- **Split**: 50/50 IS/OOS by bar count
- **Fee model**: `LighterAdapter` with configurable fee_bps_rt; cost charged as flat drag per entry
- **Latency model**: per-entry slippage estimated from the raw trade tape via `searchsorted` on signal_ts and signal_ts + delay_ms
- **CPCV**: 6 groups, k=2 test, 60-bar embargo on each fold boundary
- **Annualization**: 525,600 periods/year (1m bars, 24/7)
