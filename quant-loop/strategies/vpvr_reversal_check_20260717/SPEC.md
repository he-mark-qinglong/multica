# VPVR Level × Price-Reversal Cross-Check (SMA-34791)

## Purpose
Provide an *evidence check* on the question raised in work-pool.md
(strategy-worker-2 analysis 2026-07-16):

> Cross-check: do VPVR levels actually correlate with price reversals?
> Sample 20 levels vs outcomes

This is **not** a strategy implementation. It is a small, auditable
review of whether the VPVR levels the SMA-34656 / SMA-34790 detector
produces actually behave like support/resistance on the 4h BTC bars.

## Predeclared rules (written BEFORE running the analysis)

These are written here, in advance, so the sampling is reproducible
and not cherry-picked.

### Universe
- **Symbol**: BTCUSDT
- **Timeframe**: 4h bars (per spec, 1d is banned for new strategy work)
- **Window**: last 30 days of the live_data feed (`BTCUSDT_4h.parquet`),
  ending at the bar index of 2026-07-10 20:00:00. The cutoff is
  2026-06-10 20:00:00 (exclusive). 181 bars available.

### VPVR detector
- Reuse `~/multica/quant-loop/strategies/_indicators/vpvr_levels.py`
  (the SMA-34790 module).
- Rolling VPVR computed on a **30-bar (5-day) trailing window** at every
  bar in the 30-day window. Each call uses `num_bins=200`,
  `value_area_fraction=0.70`, `hvn_quantile=0.85`, `lvn_quantile=0.15`,
  `num_hvn=5`, `num_lvn=5`.

### Level sampling rule
For each bar in the 30-day window (chronological order, oldest first),
compute the rolling VPVR levels and append the new HVN/LVN bands (not
POC). After deduplication (a band that has the same `[price_low,
price_high]` as a previously-seen level is skipped), walk forward bar
by bar until 20 unique HVN/LVN levels have been observed **and**
touched by price at least once in the forward 12 bars (48 hours).

**First touch** for level `i`: the earliest bar `t` such that
`low[t] <= level.price_high AND high[t] >= level.price_low` and
`t > detection_ts` (so no look-ahead).

Take the **chronological first 20 such (level, first-touch-bar) pairs**.

### Reversal definition (predeclared)
For a (level, first-touch) pair:

- Record `touch_close` = close at first touch bar.
- Record forward `max_upside` and `max_drawdown` over the next
  `horizons = [4, 12, 24]` bars (16h / 48h / 96h). Compute as
  pct return from `touch_close`.
- **Reversal yes/no** is set on the **12-bar horizon** (48h):
  - For HVN levels: `reversal = (max_drawdown <= -0.5%) AND
    (max_upside >= +1.0%)`  → price tested the HVN and bounced
    (a draw-down then an upside exceed).
  - For LVN levels: `reversal = (abs(move) >= 1.0%)` where
    `move = close[+12] - touch_close` and sign is whatever direction
    the post-touch drift actually took; LVN is "price slipped
    through" so any decisive follow-through counts.

### Baseline (for context, not proof)
- Same universe and same first-touch timestamps.
- Pick a **random price** in `[bar_low.min(), bar_high.max()]` for each
  of the 20 first-touch bars (single seed = 20260717 for
  reproducibility) and apply the same reversal rule.
- This is *not* a statistical hypothesis test — it is a sanity check
  that any reversal rate observed at VPVR levels is materially
  different from a coin-flip.

### Limitations (stated up front)
- n = 20 is far too small to draw conclusions about predictive power;
  a 95% CI on a binomial proportion at p ≈ 0.5 is roughly ±0.22.
- The rolling 30-bar window mixes historical context with live data —
  recent levels are more relevant than older ones but the detector
  reports them in a single stream.
- Only HVN/LVN bands are sampled (POC is excluded) because HVN/LVN are
  the "support / rejection" axes the funding-carry prototype plans to
  consume.
- Only BTCUSDT. ETH/SOL are out of scope here.

### Verdict rubric
- Supports: HVN reversal rate ≥ baseline + 1 sample (informal).
- Weakens: HVN reversal rate ≤ baseline − 1 sample.
- Inconclusive: |delta| ≤ 1 sample. (Most likely outcome at n=20.)