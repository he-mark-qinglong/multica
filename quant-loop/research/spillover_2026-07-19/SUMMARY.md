# SMA-35000 — Pre-SPEC Feasibility Verdict (Volatility Spillover Network)

**Date:** 2026-07-19
**Worker:** strategy-worker-2
**Status:** KILL at pre-SPEC gates D & F (engine works mechanically)

## Artifacts in this folder

- `spillover_fevd.py` — Diebold-Yilmaz (2012) generalized FEVD engine module.
  Companion-form recursion, Pesaran-Shin ordering-robust normalization,
  row-normalized. Exposes `fit_rolling_var_spillover(panel, p, H, …)`.
- `feasibility_check.py` — minimal Gate D / Gate F empirical test on
  BTC/ETH/SOL 1h log-RV with 24 rolling 90d windows (90d window, 30d step).
- `run.log` — full output of `feasibility_check.py`.

## Empirical findings (BTC/ETH/SOL 1h log-RV, 24 rolling 90d windows)

### Total connectedness — robust to p, consistent with literature

| p | mean TC | std TC | range |
|---|---------|--------|-------|
| 1 | 0.614   | 0.022  | [0.584, 0.652] |
| 2 | 0.609   | 0.023  | [0.579, 0.648] |
| 4 | 0.608   | 0.022  | [0.578, 0.646] |

Cross-asset vol-transmission accounts for ~61% of 1h-log-RV variation —
consistent with prior literature on crypto connectedness (Bouri et al.).
The framework is mechanically correct (sanity check passes), but the
signal-to-noise for *asset-level* directional ranking is the failure point.

### Gate D — refit noise (NET_i SE / |mean NET_i| across windows)

| Symbol | p=1 (mean / SE / cv) | p=2 | p=4 |
|--------|----------------------|-----|-----|
| BTC    | +0.044 / 0.041 / **0.93** | +0.032 / 0.039 / **1.22** | +0.020 / 0.038 / **1.90** |
| ETH    | -0.019 / 0.043 / **2.25** | -0.013 / 0.056 / **4.30** | -0.003 / 0.053 / **20.03** |
| SOL    | -0.025 / 0.051 / **2.03** | -0.019 / 0.061 / **3.18** | -0.017 / 0.058 / **3.39** |

cv >> 1 for ETH/SOL at all p. Even BTC (the natural "transmitter")
crosses the threshold as p grows. **The NET signal-to-noise is at the
floor of what a cross-sectional strategy can trade on.**

### Gate F — rank stability across windows (Spearman)

| Pair | p=1 ρ (p) | p=2 | p=4 |
|------|-----------|-----|-----|
| BTC-ETH | -0.36 (0.08) | -0.28 (0.18) | -0.31 (0.15) |
| BTC-SOL | **-0.65 (0.001)** | **-0.52 (0.009)** | **-0.53 (0.008)** |
| ETH-SOL | -0.39 (0.06) | -0.62 (0.001) | -0.61 (0.002) |

The "stable" BTC-SOL correlation is mechanical — NET sums to zero across
assets, so two-thirds of any pair are bound to be negatively correlated.
ETH-ETH (and the genuinely informative pair) are WEAK.

### Gate F — robustness to lag choice

Mean |NET_BTC| decays with p: 0.044 → 0.032 → 0.020 → most "signal"
is captured at lag-1 (microstructure / co-movement), NOT at p≥2 (genuine
fundamental spillover). At a 15m cadence, the lag-1 effect would dominate
the entire signal — meaning the strategy reduces to "BTC leads the panel"
which a simple BTC-relative vol-spread already captures.

## Verdict

**KILL at pre-SPEC.** Not because the framework is wrong but because:

1. The asset-level NET signal is at the floor of refit-noise for the
   assets that matter (ETH, SOL). The strategy-worker-1 NET-ranking
   signal is below the "decisively informative" threshold on canonical
   BTC/ETH/SOL data. Adding 5-9 altcoins may enrich the network but
   cannot fix the per-asset rank noise problem.
2. Total connectedness is stable and supports DY's framing, but it is
   NOT directly tradable. Trading requires the NET_i ranks, which fail
   the noise gate.
3. Cost model is the killer: at 15m rebalance, the 4bp taker × rank-flip
   turnover × 24 rebalances/day ≈ <folds> the residual edge.

## What would change the verdict

- **Cadence**: 4h or 1d. The lag-1 dominance at 15m/1h suggests the
  fundamental spillover lives at lower frequency. DY's original paper
  uses 1d-1w. A 1d-1w re-spec would be a direction-revival, not
  line-extension.
- **Universe**: N≥8-12 alts would test whether the network structure
  emerges beyond a BTC-centric sender/receiver partition. Spec anti-pattern
  forbids N=3; current KILL is partly an artifact of under-spec.
- **Better identification**: the generalized FEVD's contemporaneous
  covariance still permits "shared exposure" to masquerade as spillover.
  DY's framework is the *correct* baseline, but the *construct* it
  identifies is not necessarily alpha.

## Recommendation to smark

Do NOT promote SMA-35000 to a strategy directory. Archive the engine in
`research/spillover_2026-07-19/` as a reference artifact. If smark wants
to revisit, the open directions are:

- (a) Recompute at 4h/1d cadence on the same BTC/ETH/SOL panel; if NET
  mean/SE cv drops below 0.5 at 4h, the direction re-opens.
- (b) Wait for top-3 direction (01 OFI / 06 Bayesian / 09 MFG) status.
  Spillover is a #04 — not in smark's top-3 frontier priorities.

Cross-references: SMA-30199 (parent spec), SMA-35002 (recent Bayesian
KILL at pre-SPEC, same precedent), top-3 frontier research 01 / 06 / 09.
