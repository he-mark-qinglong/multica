# vpvr_funding_hvn_lvn — SPEC

SMA-34890 prototype. Wires a **funding asymmetry trigger** (per-8h
funding > 0.03% on the long side) with a **4h VPVR structural
level** so the long entry fires only when both are present:

- **support trigger** — price sits inside an HVN absorption zone
  (high-volume node, computed on a rolling 4h window);
- **carry trigger** — most recent funding event strictly before the
  15m bar's open paid > 0.03% to shorts (i.e. longs are paying
  carry).

Exit is **structural**: price reaches the next LVN zone *above*
the entry price. No hard stop, no time stop. (Per the issue body:
"exit when price reaches next LVN zone.") A safety-net max-hold
of 96 15m bars (≈ 24 h) is included to prevent indefinite
hangovers if no LVN is hit.

## Why this should work

When the long side is paying >3 bps / 8h, the spot-vs-perp basis
is in contango and perp leverage is heavily skewed long. An HVN
absorption zone is exactly where the heavy longs are willing to
absorb supply (defensive bids cluster). A bounce off that zone
into an LVN pocket above is the textbook "squeeze + thin air
above" pattern — price can travel quickly through an LVN zone
because there are few resting orders to slow it.

This is a **prototype**. We are not declaring profitability here;
the question is whether the wire-up produces a believable
equity curve at all. Honest verdict in `results/summary.txt`.

## Signal (long-only)

For each 15m bar `t`:

1. `funding[t]` — last funding rate paid strictly before bar `t`,
   reindexed onto the 15m bar index with `ffill`.
2. `carry_gate[t]` = `funding[t] > funding_threshold`.
3. `hvn_levels[t]` — VPVR level list computed on the **trailing
   4h bars strictly before bar `t`** (rolling 180 × 4h bars ≈ 30
   days), via `vpvr_levels.detect_vpvr_levels`. Levels are
   `shift(1)`-equivalent: the snapshot used to evaluate bar `t`
   uses 4h bars in window `[-181 .. -1]` from bar `t`'s time.
4. `support_zone[t]` — exists iff `close[t]` is inside the band
   `[price_low, price_high]` of any HVN level (we use a 1-ATR
   proximity test against `price_center` for robustness; bands
   that are tighter than 1 ATR get the band, otherwise the ATR
   proxy applies).
5. `lvn_zones[t]` — same rolling snapshot, filtered to LVN
   nodes whose `price_low > entry_price` (i.e. above the entry
   HVN).

`entry_signal[t] = 1` iff `carry_gate[t]` and `support_zone[t]`
both true.

## Trade management

| knob | value |
|---|---|
| funding_threshold | 0.0003 (3 bps / 8h) |
| vpvr_window_bars (4h) | 180 ≈ 30 days |
| vpvr_num_bins | 24 |
| vpvr_num_hvn | 3 |
| vpvr_num_lvn | 5 |
| vpvr_hvn_quantile | 0.85 |
| vpvr_lvn_quantile | 0.15 |
| proximity_atr | 1.0 (against HVN center) |
| atr_period | 14 |
| cooldown_bars (15m) | 16 (≈ 4 h) |
| max_hold_bars (15m) | 96 (≈ 24 h) |
| fee_bps_per_fill | 4.0 |
| slippage_bps_per_fill | 1.0 |
| round_trip_cost | 10 bps |
| starting_capital_usd | 100000 |

Position sizing: **fixed-fraction 1.0** of equity per trade (no
scaling) so the equity curve is the simplest possible read on the
signal's hit-rate. We do not pretend a Kelly/ATR-size result on a
prototype.

## Exit logic

1. **Primary exit** — `high[t] >= nearest_lvn_price_high[t]` where
   `nearest_lvn_price_high` is the LVN whose `price_low` is the
   smallest value `> entry_price`. (Closer LVN first; if a higher
   LVN is hit first it still counts because we take whichever
   the bar's high touches.)
2. **Time stop** — `max_hold_bars` reached.
3. **Funding flips** — funding becomes strongly negative
   (`funding[t] < -0.0003`) → emergency exit (this is the only
   short-side condition; we still only run long).

## No-look-ahead

- 4h VPVR is computed on bars in `[t-180*4h, t)` strictly before
  bar `t`. The rolling snapshot uses `4h_close.shift(1)` etc.
- ATR(14) at bar `t` uses `high[t-1] .. low[t-14]` (shift(1)
  convention from cycle-46).
- Funding at bar `t` is the rate paid at the most recent funding
  event strictly before bar `t`. Funding events are
  `ffill`-shifted.

## Window choice (honest)

Two windows will be reported:

| window | rationale |
|---|---|
| `last_30d` | the obvious user-facing window; in the current regime carries are muted so very few signals fire |
| `q1_2024_hot_funding` (2024-02-01 → 2024-04-30) | the regime the signal is designed for; ~57 funding events cross 0.0003 in this window per SMA-34858 iter 2 |

Reporting both is intentional: the prototype's job is to wire
the signal end-to-end. The "is this profitable" question is
answered in the q1_2024 window where the carry trigger can
actually fire.

## Metrics envelope

`metrics.json` includes:

- `sharpe_daily` (daily-resampled, sqrt(365.25) per SMA-34787
  audit).
- `annualized_return`.
- `max_drawdown_pct`.
- `n_trades`, `win_rate`, `profit_factor`.
- Per-trade mean / median pnl.
- Per-window diagnostics: `n_long_signals`,
  `signal_bars_funding_above_threshold`,
  `signal_bars_near_hvn`.

A combined `summary.txt` table summarises both windows side by
side. Equity curves are written as `equity_<window>.csv` and
plotted as `equity_<window>.png`.

## Out of scope (deliberate)

- Walk-forward / bootstrap CI / Bonferroni — this is a
  prototype, not a shippable strategy (per cycle-46 discipline).
- Multi-symbol expansion (ETH/SOL differ in funding regime;
  out of scope for the prototype).
- Short side / asymmetric exit / vol-target sizing — out of
  scope per the issue body.
- Asymmetric TP / SL beyond the structural LVN exit + time
  stop + funding-flip emergency exit.

## Done criteria

- [ ] Python script compiles and runs end-to-end on BTCUSDT.
- [ ] Both `last_30d` and `q1_2024_hot_funding` windows produce
      a `metrics.json` / `equity.csv` / `equity.png` set.
- [ ] Honest verdict in `summary.txt`: are gates G1-G5 even
      met? (G6/G7 are statistically meaningless on a prototype
      window and will be marked N/A.)