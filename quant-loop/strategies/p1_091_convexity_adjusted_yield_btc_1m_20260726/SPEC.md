# SPEC — p1_091_convexity_adjusted_yield_btc_1m_20260726

> **Status**: SPEC submitted, hypothesis tested OOS, see verdict in `results/cpcv_summary.txt`.
> **Date**: 2026-07-26. **Author**: strategy-worker-1 (c8fa1e20).
> **Parent issue**: SMA-36109 — "[P1-STRAT-091] Convexity Adjusted Yield".
> **Variant suffix**: `_p1_091_convexity_adjusted_yield_btc_1m_20260726` — descriptive
> (parent_idea p1_091, variant_name `convexity_adjusted_yield`, symbol/timeframe, build_date).
> Does NOT contain `_v1`/`_optimize` per SMA-36109 naming constraint.

## Hypothesis (mechanistic)

In fixed-income theory, the *convexity adjustment* to a forward yield
corrects for the non-linear payoff of a forward contract. For a bond
with embedded optionality, the YTM **overstates** the true expected
return because the option is more likely to be exercised when rates move
adversely. The convexity-adjusted yield (CAY) = YTM − convexity_adj,
where `convexity_adj = 0.5 × σ² × T` (the second-order Taylor term of
the price-with-respect-to-rate).

For a perpetuals position the analogue is the **variance drag**: a
delta-hedged leveraged position earns the funding rate LESS the variance
tax (`0.5 × σ²`), because position notional scales with the underlying's
volatility. The funding rate, taken alone, is **procyclical** — high
funding clusters with high realized vol (the same crowded longs that
push funding up also drive the variance). The CAY strips out this
procyclicality and asks: "is the funding rate genuinely attractive on a
risk-adjusted basis?"

For perps:
- **CAY_apr = funding_apr − 0.5 × σ_apr²**
- where `funding_apr` is the 8h funding rate annualised
  (`funding_8h × 3 × 365.25`), and `σ_apr` is the rolling realised vol
  annualised from 1m returns.

For BTC with `σ_apr ≈ 0.5`, the convexity tax is `0.5 × 0.25 = 0.125 =
12.5% APR`. Funding historically oscillates between -10% and +30% APR.
So CAY is *negative* most of the time (the carry doesn't beat the vol
drag) and turns *positive* only when funding surges above ~12.5% APR
(sparse but real).

**The mechanical edge**: CAY is mean-reverting (funding is
well-documented as mean-reverting; vol is a slower Ornstein–Uhlenbeck).
Fade CAY extremes: when CAY is in the upper tail, the carry looks
attractive but the vol drag is being underestimated — funding will
normalise and the trade will revert down. When CAY is in the lower
tail, longs are getting insufficient carry for the risk and shorts will
close. We FADE both tails.

## Pre-registered candidate family (a priori, no OOS-driven re-ranking)

Three parameter candidates — chosen on economic reasoning, fixed before
any OOS reading:

| Label | z_entry | z_exit | hold_bars | rationale |
|-------|---------|--------|-----------|-----------|
| `tight_z2.0` | 2.0 | 0.0 | 240 | strict entry (top 2.3% of bars); deeper reversion expected; small sample |
| `medium_z1.5` | 1.5 | 0.0 | 240 | looser entry (top 6.7%); more trades but smaller alpha per trade |
| `wide_z1.0` | 1.0 | 0.0 | 240 | widest entry (top 15.9%); vol-target cap limits max exposure |

DSR `n_trials = 3` (the family size).

## Data

- 1m Binance USDⓈ-M perp klines for **BTCUSDT** only (cycle-46 single-
  symbol convention).
- 8h funding rate from the canonical funding pool
  (`data/funding/BTCUSDT.parquet`), forward-filled to each 1m bar.
- Span: 2021-11-20 → 2026-07-17 (~4.7y, the full perpetual inventory
  window for which funding coverage exists — funding predates the
  perp_1m start).
- Paths:
  - `data/perp_1m/BTCUSDT_1m.parquet`
  - `data/funding/BTCUSDT.parquet`
- Funding data only refreshes at the 8h event boundary
  (00:00 / 08:00 / 16:00 UTC). Within each 8h window the funding value
  is constant but vol evolves, so CAY is recomputed every bar.

## Indicators

- **1m returns**: `r_t = close[t]/close[t-1] − 1`.
- **RV short** (`RV_240`): `Σ r_t², t-240..t-1` (4h window, shift-1).
- **σ_apr**: `sqrt(RV_240 / 240 × 525600)` (annualised vol).
- **Funding 8h**: `funding_8h[t]` from the funding file, `ffill` to 1m.
- **Funding APR**: `funding_apr = funding_8h × 3 × 365.25` (3 funding
  events per day × 365.25 days/year).
- **CAY**: `funding_apr − 0.5 × σ_apr²`. This is the convexity-adjusted
  yield in annualised units.
- **CAY z-score**: rolling z of `CAY` over `z_window = 1440` bars
  (shift-1). `z_window=1440` (1d) captures the typical vol cluster
  length without over-weighting the funding mode.
- **ATR(60)**: rolling ATR with `close.shift(1)` for hard-stop.
- **Realised direction** for entry selection: `sign(close[t-1] − close[t-60])`.
  Used as a tie-breaker when CAY_z is in the upper tail but the recent
  trend is sideways — we always FADE the CAY extreme, so direction is
  `−sign(CAY)` (short if CAY is positive, long if negative).

## Entry (CAY-fade)

- At each bar `t` after warm-up:
  - If `CAY_z > z_entry` AND no open position AND cooldown elapsed:
    - direction = `−1` (SHORT — fade the positive CAY extreme).
    - size = vol-target(0.15 / sqrt(RV_240 / 240 × 525600)), capped at 0.95.
  - If `CAY_z < −z_entry` AND no open position AND cooldown elapsed:
    - direction = `+1` (LONG — fade the negative CAY extreme).
    - size = same vol-target formula.
- Entry at bar `t+1`'s open (no look-ahead).

## Exit

- `CAY_z` reverts past `z_exit = 0` (i.e., crosses the mean) → exit at
  next bar's open.
- Time stop: `hold_bars` (default 240 = 4h). The funding cycle is 8h,
  so 4h holds guarantee at most one funding event per position.
- Hard stop: 2× intra-bar ATR (`ATR_60`), one-sided SL.

## Costs

- 24bp round-trip (SMA-36109 spec). Implemented inside the strategy
  state machine as `net = gross − 0.0024`.
- No funding carry debit/credit applied (we're not delta-neutral; we
  trade directional CAY extremes). Funding is observed in the indicator
  but not added to P&L. This is the conservative accounting — the
  signal explicitly uses funding as a feature, not as a P&L leg.

## Vol target

- `vol_target = 0.15` (annualised). `size_fraction = min(0.95, vol_target /
  sqrt(RV_240 / 240 × 525600))`. The cap at 0.95 is from the spec.

## Acceptance gates (pre-registered)

- **G1**: CPCV mean OOS Sharpe ≥ 0.5 (per parent issue SMA-36109).
- **G2**: Worst-fold OOS Sharpe ≥ 0.0 (sub-bar survival).
- **G3**: DSR > 0 (Bailey–López de Prado 2014 correction).
- **G4**: Trades ≥ 30 (T1 floor).
- **G5**: No look-ahead sanity (every indicator shift-1, entry at `t+1`
  open, funding `ffill` is forward-only within the file but the
  `shift(1)` on CAY means the entry decision at bar `t` only sees
  funding ≤ t-1).

## Verification

Run `python run_cpcv.py` from this directory (with
`QUANT_LOOP_ROOT=/home/smark/multica/quant-loop`).
Outputs:
- `results/cpcv_metrics.json` — per-candidate + aggregate + DSR
- `results/cpcv_summary.txt` — human-readable verdict
- `results/metrics.json` — flattened envelope (consumed by
  `publish_metrics.py`).
Then `python publish_metrics.py --check-stale` exits 0 only if the
metrics are fresh.

## Verdict

See `results/cpcv_summary.txt` for the OOS verdict. Per parent issue
spec, the verdict is either:
- `EVIDENCE — Sharpe ≥ 0.5`, with full metrics posted to SMA-36109, OR
- `KILL — Sharpe < 0.5`, with statistical reason recorded.