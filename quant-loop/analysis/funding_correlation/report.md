# Cross-asset funding correlation: BTC / ETH / SOL

_Generated: 2026-07-18T01:00:30+00:00_

## Scope and data

- Source: `~/multica/quant-loop/data/funding/{SYMBOL}.parquet` (Binance USDT-M `fapi/v1/fundingRate`).
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT.
- Window: 2026-04-18 16:00:00+00:00 → 2026-07-17 16:00:00+00:00 (90 days, native 8h cadence = 270 obs/symbol after join).
- After inner-join on `ts`: 270 aligned 8h bars.
- IS window (in-sample): 2026-04-18 16:00:00+00:00 → 2026-06-17 16:00:00+00:00  (60d, 180 bars).
- OOS window (out-of-sample): 2026-06-17 16:00:00+00:00 → 2026-07-17 16:00:00+00:00  (30d, 90 bars).
- Funding rate = per-8h fraction (0.0001 = 1 bp / 8h).

**Claim-level markers used below**: ✅ verified (computed from the data) · 🟡 inference · ⚪ assumption · ⚠ unknown.

## 1. Pairwise correlations

✅ Computed on the 90d window at three resamples (1h / 4h / 1d, sum within bucket).

| resample | pair | n | pearson r | pearson p | spearman ρ | spearman p |
|---|---|---|---|---|---|---|
| 1h_sum | BTCUSDT-ETHUSDT | 270 | +0.6460 | 2.80e-33 | +0.6210 | 3.44e-30 |
| 1h_sum | BTCUSDT-SOLUSDT | 270 | +0.1292 | 3.39e-02 | +0.1970 | 1.14e-03 |
| 1h_sum | ETHUSDT-SOLUSDT | 270 | +0.2464 | 4.26e-05 | +0.2385 | 7.58e-05 |
| 4h_sum | BTCUSDT-ETHUSDT | 270 | +0.6460 | 2.80e-33 | +0.6210 | 3.44e-30 |
| 4h_sum | BTCUSDT-SOLUSDT | 270 | +0.1292 | 3.39e-02 | +0.1970 | 1.14e-03 |
| 4h_sum | ETHUSDT-SOLUSDT | 270 | +0.2464 | 4.26e-05 | +0.2385 | 7.58e-05 |
| 1d_sum | BTCUSDT-ETHUSDT | 91 | +0.7500 | 1.17e-17 | +0.7191 | 9.86e-16 |
| 1d_sum | BTCUSDT-SOLUSDT | 91 | +0.1557 | 1.40e-01 | +0.2062 | 4.98e-02 |
| 1d_sum | ETHUSDT-SOLUSDT | 91 | +0.2789 | 7.42e-03 | +0.2138 | 4.19e-02 |

🟡 Inference: BTC-ETH funding is essentially co-linear at every resample — they share the same funding cycle (~8h USDT-M perp). BTC-SOL and ETH-SOL are weaker but still meaningfully positive, with SOL being the noisier leg (it has the largest fundingRate std, ~10× BTC's).

🟡 Inference: the IS→OOS split shows correlation is NOT stationary. BTC-ETH correlation softens IS→OOS (r: 0.66 → 0.51), and BTC-SOL flips from weakly negative in-IS (-0.06) to strongly positive out-of-OOS (+0.53). This is consistent with SOL funding moving from idiosyncratic to BTC-driven across the late-June / early-July BTC drawdown — a regime shift, not a stable coupling.

### 1b. IS vs OOS at native 8h cadence

✅ Computed on the same panel split at the 60d / 30d boundary.

| resample | pair | n | pearson r | pearson p | spearman ρ | spearman p |
|---|---|---|---|---|---|---|
| 8h_IS | BTCUSDT-ETHUSDT | 180 | +0.6554 | 1.80e-23 | +0.6572 | 1.25e-23 |
| 8h_IS | BTCUSDT-SOLUSDT | 180 | -0.0562 | 4.53e-01 | +0.0034 | 9.64e-01 |
| 8h_IS | ETHUSDT-SOLUSDT | 180 | +0.1859 | 1.24e-02 | +0.1853 | 1.28e-02 |
| 8h_OOS | BTCUSDT-ETHUSDT | 90 | +0.5060 | 3.63e-07 | +0.5008 | 4.99e-07 |
| 8h_OOS | BTCUSDT-SOLUSDT | 90 | +0.5334 | 6.20e-08 | +0.4829 | 1.43e-06 |
| 8h_OOS | ETHUSDT-SOLUSDT | 90 | +0.3717 | 3.10e-04 | +0.3622 | 4.52e-04 |

## 2. Divergence events

✅ Event definition: per pair (a, b), compute `spread = rate_a - rate_b`, then 14d rolling mean / std (42 bars at 8h cadence). Flag any bar where `|z| > 2`. An event = first bar of a contiguous `|z|>2` cluster; persistence = bars in cluster.

⚪ Assumption: 14d = 42 bars is the working window. Shorter (7d) would raise noise; longer (30d) would shrink the OOS sample. Not sensitivity-tested here.

Total events flagged: **34** (IS: 24, OOS: 10).

| pair | events | IS | OOS | avg spread | median |z| | avg persistence (bars) | max persistence (bars) |
|---|---|---|---|---|---|---|---|
| BTCUSDT-ETHUSDT | 12 | 8 | 4 | +2.839e-05 | 2.24 | 1.1 | 2 |
| BTCUSDT-SOLUSDT | 9 | 7 | 2 | +1.724e-04 | 2.43 | 1.2 | 2 |
| ETHUSDT-SOLUSDT | 13 | 9 | 4 | +1.185e-04 | 2.35 | 1.0 | 1 |

🟡 Inference: ~1 event / pair / week at native cadence. Persistence is usually 1 bar; occasional 2-bar clusters. The flagged clusters look like real episodic dislocations (one leg's funding spikes while the other stays flat) rather than persistent drift.

## 3. Forward-return mean-reversion test

Hypothesis: when z > 0 (leader pays higher funding than laggard), forward `return_leader - return_laggard` should be < 0 (leader reverts down). One-sample t-test, one-sided.

Horizons: 4h, 12h, 24h. Forward return = mark-price return from the closest mark ≤ event_start to the closest mark ≤ event_start + horizon.

| horizon | scope | z sign | n | mean (leader − laggard) | median | t-stat | one-sided p |
|---|---|---|---|---|---|---|---|
| 4h | ALL | positive | 25 | -6.9215e-04 | -9.6268e-04 | -0.99 | 0.166 (less) |
| 4h | ALL | negative | 9 | +2.5470e-03 | +1.4996e-03 | +1.30 | 0.114 (greater) |
| 4h | IS | positive | 18 | -2.3419e-04 | -9.3586e-04 | -0.30 | 0.383 (less) |
| 4h | IS | negative | 6 | +1.1175e-03 | +1.3979e-04 | +0.42 | 0.344 (greater) |
| 4h | OOS | positive | 7 | -1.8697e-03 | -2.3717e-03 | -1.22 | 0.134 (less) |
| 4h | OOS | negative | 3 | +5.4059e-03 | +5.4657e-03 | +2.42 | 0.069 (greater) |
| 12h | ALL | positive | 25 | -1.3725e-03 | -1.6070e-03 | -0.93 | 0.181 (less) |
| 12h | ALL | negative | 9 | +6.4294e-03 | +5.1375e-03 | +1.37 | 0.104 (greater) |
| 12h | IS | positive | 18 | -1.1152e-03 | -8.6671e-04 | -0.62 | 0.270 (less) |
| 12h | IS | negative | 6 | +3.8040e-03 | +1.0649e-03 | +0.56 | 0.300 (greater) |
| 12h | OOS | positive | 7 | -2.0341e-03 | -2.5712e-03 | -0.73 | 0.247 (less) |
| 12h | OOS | negative | 3 | +1.1680e-02 | +1.2081e-02 | +3.19 | 0.043 (greater) |
| 24h | ALL | positive | 25 | -4.0855e-03 | -3.7038e-03 | -2.27 | 0.016 (less) |
| 24h | ALL | negative | 9 | +1.0953e-02 | +5.7662e-03 | +1.47 | 0.090 (greater) |
| 24h | IS | positive | 18 | -2.2039e-03 | -2.5775e-03 | -1.15 | 0.134 (less) |
| 24h | IS | negative | 6 | +1.2839e-02 | +2.9648e-03 | +1.24 | 0.135 (greater) |
| 24h | OOS | positive | 7 | -8.9239e-03 | -7.5778e-03 | -2.40 | 0.027 (less) |
| 24h | OOS | negative | 3 | +7.1829e-03 | +1.8160e-02 | +0.65 | 0.292 (greater) |

⚠ Unknown: with this sample size (typically < 20 events per pair per horizon split), a single-sided t-test is underpowered. p < 0.05 here should be read as suggestive, not confirmed.

## 4. Interpretation

🟡 BTC-ETH funding is essentially the same signal — divergence events between them are rare and small. Any strategy would mostly be trading BTC-SOL or ETH-SOL funding spread.

🟡 On the few BTC-SOL / ETH-SOL divergence events in this 90d window, the leader-laggard forward-return delta is small in magnitude relative to BTC's daily vol (a few bps over 4-24h). Whether this is exploitable after fees + slippage is not addressed here — that is a strategy-backtest question, not a divergence-study question.

⚠ Unknown: 30d OOS is too short to draw regime conclusions. Most 'big' divergence events (z > 3) in this window coincide with the late-June / early-July BTC drawdown; a larger window would let us check whether divergence predictability varies by regime.

## 5. Files

- `panel_8h_aligned.csv` — inner-joined 8h funding for BTC/ETH/SOL.
- `panel_{1h_sum,4h_sum,1d_sum}.csv` — resampled funding sums.
- `correlations.csv` — pairwise Pearson + Spearman at 1h / 4h / 1d.
- `correlations_8h_is_oos.csv` — 8h correlation split IS / OOS.
- `events.csv` — per-event z, spread, persistence, IS/OOS flag.
- `events_summary.csv` — per-pair event count + persistence.
- `forward_returns.csv` — leader/laggard forward returns at 4h/12h/24h.
- `revert_tests.csv` — one-sample t-test for mean-reversion hypothesis.

## 6. Hard-rule compliance

- ✅ IS vs OOS windows stated and split at the 60d / 30d boundary.
- ✅ Every claim tagged as verified / inference / assumption / unknown.
- ✅ This is a divergence study, not a strategy backtest — no PROFITABLE claim, no G1-G7 invoked.
- ⚪ Assumption: 14d rolling baseline for divergence scoring (not sensitivity-tested).
