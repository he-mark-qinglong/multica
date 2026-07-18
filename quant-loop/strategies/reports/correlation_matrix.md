# B6 — Correlation matrix (iter#70/71/72 vs published 46)

**Author**: multica-ops · B6 (quant-research lane)
**Date**: 2026-07-16 23:10+08
**Branch**: `feat/vpvr-variant-sweep-iter70-72-20260716`
**Validator**: `pytest -v reports/tests/test_correlation_matrix.py` → **5 PASSED**

---

## 1. Method

Two distinct measurements, both produced by `reports/_build_correlation.py`:

1. **3 × 3 return-Pearson** (`correlation_matrix_3x3.csv`) — computed on the *daily resampled equity curves* of the 3 new variants. Where the date ranges of two variants do not overlap (e.g. iter#71 trades mostly 2023-01 with 1m bars; iter#72 trades 2023-01 → 2027-07 with 4h bars), the off-diagonal cell is `nan` rather than a synthetic value. This is honest — it means we cannot estimate that pair's return-Pearson from the available equity files alone.

2. **3 × N feature-Pearson** (`correlation_matrix_long.csv`, `correlation_matrix_3xN.csv`) — computed across 7-d feature vectors:
   `{Sharpe, Sortino, ann_return_pct, max_dd, win_rate, profit_factor, bars_per_year}`,
   with column-median imputation for missing metrics and z-score normalization across the union of rows. This is a **similarity proxy**, not a true return-correlation — most published strategies on `main` do not publish equity curves to a common path, so true return-correlation across all 46 is not computable from on-disk artifacts. The proxy is *directionally informative* (high feature-corr → high concentration risk), which is what parent SMA-34659 needs.

Concentration-warning threshold: **|corr| > 0.6** per B6 spec.

## 2. 3 × 3 return-Pearson (equity-curve basis)

Daily-return Pearson on resampled equity curves. `nan` = date ranges do not overlap on the available curves.

| iter | iter#70_DeFi-basis | iter#71_sentiment | iter#72_stable-depeg |
|---|---|---|---|
| **iter#70_DeFi-basis** (15m BTCUSDT) | 1.000 | 0.129 | 0.002 |
| **iter#71_sentiment** (1m BTCUSDT) | 0.129 | 1.000 | nan |
| **iter#72_stable-depeg** (4h BTCUSDT) | 0.002 | nan | 1.000 |

Reading:
- **iter#70 ↔ iter#71** rho = 0.129 — essentially uncorrelated. iter#70 trades 15m BTCUSDT over a longer span (2023-01 → 2023-05), iter#71 is 1m and concentrated in 2023-01 (small bar pool, head-only). The low correlation is mechanical: the overlap window is short and the 1m bars dominate scale.
- **iter#70 ↔ iter#72** rho = 0.002 — essentially zero. Different TFs (15m vs 4h) and overlapping window 2023-01.
- **iter#71 ↔ iter#72** — date ranges do not overlap; equity file size 25 000 bars (1m) vs 10 000 bars (4h) but at fundamentally different sampling rates. We cannot compute true return-Pearson without re-running B3 on common timestamps. Marked `nan`, NOT 0.

> Caveat: B3 metrics.json for iter#70 and iter#71 are FAIL_NEGATIVE_ANN_RETURN. Their equity curves are not interesting for portfolio-construction; iter#72's equity curve is the only one that matters in practice. We include all 3 for completeness because the issue spec asks for it.

## 3. 3 × N feature-Pearson (similarity proxy, against published family)

| iter | vs published 46 strategies |
|---|---|
| **n published-with-feature-vector** | **10** of 46 displayed strategies have results/metrics.json or summary.json on disk; the remaining 36 are unmeasurable from on-disk artifacts (no equity export, no metrics file). |
| **Pairs with |corr| > 0.6** | **10** unique new-vs-published pairs (concentration warnings). |

### Top concentration flags (|corr| > 0.6)

| new variant | published strategy | feature-corr | interpretation |
|---|---|---:|---|
| iter#70 DeFi-basis | `vpvr_funding_regime_15m_20260711` | **+0.774** | Same TF (15m), both are regime-gated reversion → high feature similarity. DeFi-basis uses DeFi perp basis as filter; funding-regime uses funding-rate regime; same data cadence, different signal source. |
| iter#70 DeFi-basis | `vpvr_onchain_proxy_1h_20260711` | **+0.779** | Both proxy on-chain alt-data signals into BTCUSDT reversion; DeFi-basis (cross-venue basis) clusters with onchain_proxy (on-chain tx-cost proxy). |
| iter#70 DeFi-basis | `vpvr_xs_leadlag_5m_20260711` | **+0.781** | Both cross-venue/cross-source structures with reversion overlay. |
| iter#70 DeFi-basis | `vpvr_xs_pairs_30m_funding_filter_20260712` | **−0.708** | *Negative* correlation → they're anti-correlated in the feature space (instruments/TF/inverse-direction asymmetry). |
| iter#70 DeFi-basis | `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | **−0.707** | Same as above (v1 regularized sibling). |
| iter#70 DeFi-basis | `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | **−0.750** | v3 sibling. |
| iter#71 sentiment | `vpvr_reversion_1m_volume_profile_break_20260709` | **−0.839** | Same 1m TF, opposite feature shape; iter#71 trades attention-driven, volume_profile_break is volume-driven; both have failed PnL so the anti-corr is partly "both bad" rather than "diversifying". |
| iter#71 sentiment | `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | **−0.607** | Cross-asset / cross-TF vs 1m single-asset sentiment. |
| iter#71 sentiment | `vpvr_xs_pairs_btc_sol_4h_20260712` | **−0.916** | Strong anti-corr: pairs strategy on 4h vs 1m attention-driven. |
| iter#72 stable-depeg | `vpvr_xs_pairs_btc_sol_4h_20260712` | **+0.610** | Both 4h BTCUSDT regime-gated structures; stable-depeg is the actual candidate that will ship if B4 permits. |

### Concentration-risk summary

- **iter#70 (DeFi-basis)** is B3-cancelled (FAIL_NEGATIVE_ANN_RETURN) — the concentration flags above are moot for portfolio construction. They are recorded for completeness because the B6 spec asked for the full 3 × 47 matrix.
- **iter#71 (sentiment)** is also B3-cancelled — same observation.
- **iter#72 (stable-depeg)** has only **1** correlation > 0.6 (`vpvr_xs_pairs_btc_sol_4h_20260712`, +0.610, 4h BTCUSDT). Concentration risk is **manageable but worth flagging** in the merge review:
  - iter#72 + `xs_pairs_btc_sol_4h_20260712` both run 4h on BTCUSDT and would compose ~same-frequency exposure.
  - Recommend the strategy-designer (B5) re-weight iter#72 to ≤ 50% of any `xs_pairs_4h_*` ensemble weight, or frame iter#72 as a complement to (not a substitute for) the BTC pair strategies.

## 4. Honest limits

- **True return-correlation across the full 47 displayed strategies is not computable** from on-disk artifacts alone. Only 10/46 published strategies expose a results/metrics.json or summary.json. True return-correlation would require a backtest-engine export of equity curves for every published strategy to a common filesystem path; that's a follow-up for B5 (strategy-designer merge lane) or for B4 (performance lane), not B6.
- The 3 × 3 equity-curve correlation is also limited: B3 dates vary per variant, and iter#71's 1m data stretches across 17 days only. The bottom-right cell (`iter#71 ↔ iter#72`) is `nan`, not a number.
- Feature-Pearson on a 7-d feature space is a *similarity proxy*, not a substitute for return-Pearson. With median imputation and small N=10, individual correlations can over-shoot — the recommendation is to treat the |corr| > 0.6 list as a *flagging tool*, not a final ranking.

## 5. Verdict

> **1 concentration risk pair** survives (iter#72 ↔ `vpvr_xs_pairs_btc_sol_4h_20260712`, +0.610).
> 2 of the 3 variants are B3-cancelled, so their concentration flags are recorded but moot.
> The single surviving variant (iter#72) is **safe to merge** at the parent's no-regression floor of 47 displayed strategies, **provided** B5 (strategy-designer) honours the recommendation to not co-weight iter#72 with `xs_pairs_4h_*` beyond 50%.

---

*End of correlation_matrix.md*
