# SPEC — vpvr_multi_tf_funding (1m / 15m / 4h)

**Strategy key**: `vpvr_multi_tf_funding`
**Cycle**: 47 (post-cycle-46 multi-TF formalization)
**Primary direction**: VPVR-funding-carry-asym
**Author**: strategy-worker-1 (SMA-34911, dispatched 2026-07-18)

## Goal

Wire three single-TF edges into one multi-TF strategy that produces
a single per-bar entry decision with explicit confirmation/conflict
rules. Each edge already exists as a standalone prototype; this SPEC
defines the **combination** layer:

- **1m microstructure** — LOID-confirmed iceberg bars × tick-level VPVR
  HVN/LVN proximity (per `loid_detector` + `vpvr_levels`)
- **15m short-term** — funding-carry-asym × HVN support (per
  `funding_carry_asym` + `vpvr_funding_hvn_lvn_confluence_20260718`)
- **4h structural** — VPVR HVN/LVN zones × funding regime
  (TREND_UP / TREND_DOWN / MEAN_REVERT / BLOCKED, per
  `vpvr_funding_regime_15m_20260711` adapted to 4h cadence)

The 1d timeframe is **deliberately excluded** — per issue body.

This is a SPEC-only deliverable. No code is opened until the SPEC
passes hard-gate B7 (review). Implementation issues will be filed as
children of SMA-34911 after the SPEC clears review.

## Universe & data

- **Universe**: BTCUSDT (single symbol for the v1 cut; multi-symbol
  expansion is deferred to a follow-up). ETH/SOL are out of scope
  for v1 because their funding distributions differ (cycle-46 stats:
  ETH p99 |funding| ≈ 4.3 bps vs BTC 3.9 bps), which would require
  per-symbol threshold tuning this SPEC does not yet specify.
- **Timeframes**: 1m, 15m, 4h — three independent per-TF bar series
  on the same BTCUSDT perp book.
- **Funding**: 8h events from
  `/home/smark/multica/quant-loop/funding_analysis/BTCUSDT_bybit_funding.parquet`
  (Bybit linear, Binance fallback). Reindexed to each TF with
  `ffill` and `shift(1)` (per the cycle-46 no-look-ahead
  convention).
- **OHLCV**:
  - 1m: `/home/smark/multica/quant-loop/data/perp_1m/BTCUSDT_1m.parquet`
  - 15m: `/home/smark/multica/quant-loop/live_data/BTCUSDT_15m.parquet`
  - 4h: `/home/smark/multica/quant-loop/live_data/BTCUSDT_4h.parquet`
- **Indicators** (reused, not reimplemented):
  - VPVR: `_indicators/vpvr_levels.detect_vpvr_levels` /
    `compute_vpvr_levels` (SMA-34790)
  - LOID: `loid_detector.detect_symbol` /
    `run_detection` (SMA-34910)
  - Iceberg (fallback): `iceberg_detector.detect_iceberg_bars`
    (SMA-34779) — used only when LOID cluster data is missing
    for a window

All no-look-ahead rules follow the cycle-46 convention: rolling
baselines shifted by one bar, snapshot grids `shift(1)`'d, funding
ffill + shift(1).

## Per-TF edge definitions

### 1m — microstructure (LOID × tick-level VPVR)

Per bar `t` at the 1m cadence:

1. **LOID cluster flag** — `loid_detector.detect_symbol("BTCUSDT",
   bars_1m, LoidConfig(...))` produces `events` (one row per
   cluster). Forward-fill the cluster flag so every bar inside the
   cluster window inherits the cluster's `side_bias` and `max_z`.
   `cluster_active[t] = 1` iff bar `t` lies inside an event
   `[timestamp_start, timestamp_end]`.
2. **Tick-level VPVR** — rolling 1m VPVR with `vpvr_window_bars =
   240` (4 hours of 1m bars), snapshot every 30 bars (30 min),
   24 bins, `num_hvn = 3`, `num_lvn = 3`. Snapshot is `shift(1)`'d
   so the level used at bar `t` is computed on the trailing 240
   1m bars strictly before `t`.
3. **Edge conditions**:
   - `near_hvn[t] = |close[t] - hvn_mid[t]| ≤ 0.5 * ATR(14, 1m)[t]`
   - `near_lvn[t] = |close[t] - lvn_mid[t]| ≤ 0.5 * ATR(14, 1m)[t]`
4. **Per-bar edge output**:
   - `micro_long[t] = 1` iff `cluster_active & near_hvn &
     side_bias ∈ {buy_absorption, mixed}` (mixed is accepted because
     HVN confirms the side)
   - `micro_short[t] = 1` iff `cluster_active & near_lvn &
     side_bias ∈ {sell_absorption, mixed}` (LVN above entry →
     short through slip-zone)
   - Otherwise 0

Direction comes from the **joint (side_bias, level_kind)** reading,
not from price action alone. This is the no-look-ahead-safe rule
because `side_bias` is a function of past cluster fills.

### 15m — funding-carry-asym × HVN support

Per bar `t` at the 15m cadence (per `funding_carry_asym`
`build_signals` + `vpvr_funding_hvn_lvn_confluence_20260718`):

1. `funding[t]` — 8h funding rate paid strictly before bar `t`,
   ffill + shift(1).
2. `funding_threshold = 0.0003` (3 bps / 8h event).
3. `hvn_levels[t]` — VPVR levels from a rolling 180-bar 15m window
   (≈ 30 days), snapshot every 16 bars (≈ 4h), `shift(1)`'d.
4. `support_zone[t]` = 1 iff `|close[t] - hvn_center| ≤
   proximity_atr * ATR(14, 15m)[t]` with `proximity_atr = 1.0`.
5. `lvn_zones[t]` — same snapshot, filtered to LVNs whose
   `price_low > entry_hvn_price` (above the support HVN).

**Per-bar edge output**:
- `carry_long[t] = 1` iff `funding[t] > funding_threshold &
  support_zone[t]`
- `carry_short[t]` — explicitly **0** in v1 (short side is out of
  scope per cycle-46 family exhaustion rule; reopening it would
  require a new SPEC)

### 4h — structural regime (HVN/LVN × funding regime)

Per bar `t` at the 4h cadence (per `vpvr_funding_regime_15m_20260711`
adapted):

1. **Funding regime classifier**:
   - `funding_div[t] = funding[t] - funding[t - 24h]` (24h = 6 ×
     4h bars)
   - `z_funding[t] = (funding_div[t] - rolling_mean) / rolling_std`
     over a 180-bar (30-day) 4h lookback
   - `vol_regime_ok[t] = rolling_std(funding_div, 30d) ≤ 15 bps`
2. **Regime labels**:
   | regime | condition |
   |---|---|
   | TREND_UP | `z_funding > +1.5 & vol_regime_ok` |
   | TREND_DOWN | `z_funding < -1.5 & vol_regime_ok` |
   | MEAN_REVERT | `|z_funding| ≤ 1.5 & vol_regime_ok` |
   | BLOCKED | `vol_regime_ok = False` |
3. **VPVR structural zones**:
   - Rolling 4h VPVR with `vpvr_window_bars = 180` (≈ 30 days),
     24 bins, snapshot every 6 bars (≈ 1 day), `shift(1)`'d
   - `hvn_bands[t]`, `lvn_bands[t]` — top-3 each by volume rank
4. **Per-bar edge output**:
   - `struct_long[t] = 1` iff regime ∈ {TREND_UP, MEAN_REVERT} AND
     `|close[t] - nearest_hvn_band| ≤ 1.0 * ATR(14, 4h)[t]`
   - `struct_short[t] = 1` iff regime = TREND_DOWN AND
     `|close[t] - nearest_lvn_band| ≤ 1.0 * ATR(14, 4h)[t]`
   - regime = BLOCKED → both `struct_*` are forced to 0

## Combination logic

Three rules govern how the per-TF edges aggregate into one
decision.

### Rule 1 — Confirmation matrix (how many TFs must agree?)

```
                4h TREND_UP   4h MEAN_REVERT   4h TREND_DOWN   4h BLOCKED
1m long            ALLOW            ALLOW            DENY           DENY
1m short           DENY             DENY             ALLOW          DENY
15m long           ALLOW            ALLOW            DENY           DENY
15m short          DENY             DENY             DENY           DENY
```

The 4h regime is the **gate**, not the vote. TREND_UP gates longs
only (regardless of lower-TF direction); TREND_DOWN gates shorts
only; MEAN_REVERT is symmetric; BLOCKED is a hard DENY.

With the 4h direction fixed, lower-TF edges vote:

- **2-of-3 (4h + 1m + 15m)**: standard entry, full size
- **3-of-3**: rare, high-conviction — same size but flagged in
  results as `conviction=high`
- **1-of-3 (only 4h agrees)**: counter-trend lean — half size, tighter
  stop (per the cross-TF override below)
- **0-of-3 (4h BLOCKED)**: no entry, no exception

### Rule 2 — Conflict resolution (which TF wins on disagreement?)

When 1m and 15m disagree on direction **but the 4h gate allows the
non-zero side**, the 15m edge wins because it has lower noise. The
1m signal is then logged as a counter-signal but **does not block
the 15m entry**.

When 1m and 15m agree on direction but **disagree with the 4h
gate**, the 4h gate wins — no entry.

The "1m wins" branch is reserved for one case only: when 1m fires
inside an active LOID cluster **and** the 15m edge has not fired in
the prior 4 bars. This is a "1m clock starts the bar, 15m catches
up" pattern; it allows microstructure to lead the carry signal by
one 15m bar. In this branch:

- 1m's size is half the standard size
- Entry must be confirmed within 1 bar (next 1m candle) or it is
  cancelled
- Max hold on this branch is the 1m max_hold_bars (not 15m's)

### Rule 3 — Weighting scheme

Default weighting is **equal** for the three TFs. An optional
**vol-adjusted** override is specified but disabled by default in
v1 (see `weighting_mode` in the config sketch below):

| mode | formula |
|---|---|
| `equal` | each TF contributes 1/3 |
| `vol_adjusted` | `w_tf = (1 / ATR_pct_tf) / Σ_tf (1 / ATR_pct_tf)` |
| `recency_weighted` | `w_tf(t) = exp(-Δt_tf(t) / half_life_tf)` where `half_life_tf = max_hold_bars_tf` |

`recency_weighted` is the recommended v2 path because it lets the
1m clock start an entry while the 15m signal is still building up
evidence — i.e. it encodes Rule 2's "1m leads by one bar" pattern
naturally rather than as a special case.

For v1 the strategy uses **`equal`**. Vol-adjusted and recency-weighted
are flagged as future variants in `## Out of scope`.

## Entry / exit per TF

Each TF has its own ATR-scaled stop/target because the noise scales
with bar width. Stops and targets are evaluated on the **decision
TF's** close price (i.e. 1m exits check 1m closes).

| knob | 1m | 15m | 4h |
|---|---|---|---|
| `take_profit_atr_k` | 1.5 | 2.5 | 4.0 |
| `hard_stop_atr_k`   | 1.0 | 1.5 | 2.0 |
| `max_hold_bars`     | 30  | 8   | 6  |
| `cooldown_bars`     | 5   | 5   | 5  |
| `risk_target_pct`   | 0.005 | 0.005 | 0.005 |
| `tp_sl_ratio`       | 1.5 | 1.67 | 2.0 |
| `fee_bps_per_fill`  | 4.0 | 4.0 | 4.0 |
| `slippage_bps_per_fill` | 1.0 | 1.0 | 1.0 |

Round-trip cost = 2 × (fee + slippage) = 10 bps per completed
position. Funding carry is **charged against PnL** at every 8h
funding event that overlaps an open position (per
`vpvr_funding_asym_4h_20260713` convention).

Exit precedence (first triggered wins), in order:

1. Hard stop (intra-bar)
2. Take profit (intra-bar)
3. Trailing stop at 1.0× ATR from the highest-since-entry
   (activated only after price has moved ≥ 1.0× ATR in favor)
4. **Cross-TF override**: if 4h regime flips to BLOCKED, all open
   positions exit at the next 15m bar's open
5. **Conviction override**: if entry was tagged `conviction=high`
   (3-of-3), the trailing stop is widened to 1.5× ATR
6. Time stop = max_hold_bars

## Cross-TF confirmation requirements

The 4h → 15m → 1m cascade has two distinct responsibilities:

### Higher-TF bias filter (4h → 15m → 1m)

The 4h regime gates **direction** at the strategy level. The 15m
funding-carry trigger is then required to confirm in the **same
direction** as the 4h gate. The 1m LOID trigger is required to
confirm in the **same direction** as the 15m trigger.

This is a strict cascade — no edge downstream of a `DENY` is allowed
to overrule it (Rule 2 conflict resolution).

### Lower-TF entry timing (1m within 15m within 4h)

When 4h has just flipped regime (transition bar), the strategy
**waits one 15m bar** before allowing 15m entries in the new
direction. This delay prevents the "first bar of a new regime"
fake signal that recurs in funding-regime classifiers.

When 15m has just confirmed in-zone, the strategy **waits for the
next 1m LOID cluster to start** before allowing a 1m entry in the
same direction. This prevents entries at the bare 15m signal with
no microstructure support.

The cascade also has an explicit **anti-cascade rule**: if 1m fires
first (Rule 2's special branch), the strategy must see the 15m
confirm within **1 bar of 15m** (i.e. 15 minutes) or the position
is exited at the next 1m bar's open. This caps the "1m leads"
risk at 15 minutes of carry drag.

## Validation plan

### Out-of-sample windows (≥ 3, walk-forward)

| fold | train | test |
|---|---|---|
| 2024-Q1 | 2023-01 → 2023-12 | 2024-Q1 |
| 2024-Q3 | 2023-Q3 → 2024-Q2 | 2024-Q3 |
| 2025-Q2 | 2024-Q2 → 2025-Q1 | 2025-Q2 |

Train windows are 12 months; test windows are 3 months. The strategy
is **trained on the regime classifier's z_funding thresholds only**
(no parameters are fit per fold — the rolling windows in the
classifiers are fixed at 30 days, which is the cycle-46 default).
The OOS Sharpe is therefore a true out-of-sample measurement, not
an in-sample fit.

### G1–G7 hard gates

| gate | criterion | source |
|---|---|---|
| G1 | `sharpe_daily ≥ 1.0` (daily resample, sqrt(365.25)) | quant-analyst audit (SMA-34787) |
| G2 | `annualized_return ≥ 15%` | issue body |
| G3 | `max_drawdown_pct > -25%` (i.e. MDD < 25%) | issue body |
| G4 | `profit_factor > 1.5` | issue body |
| G5 | `framework_cv_oos_sharpe ≥ 1.0` (mean of 3 OOS Sharpes) | cycle-46 CV standard |
| G6 | `bootstrap_ci_lower_sharpe ≥ 0.5` (95% CI, 1000 resamples) | cycle-46 bootstrap standard |
| G7 | Bonferroni-corrected α = 0.05 / 4 = **0.0125** | cycle-46 4-family correction |

### Cycle-46 mandatory add-ons

- **Minimum 30 trades per OOS window** — folds producing fewer than
  30 trades are flagged as **insufficient sample** and not counted
  toward G5. At least 2 of 3 folds must clear this floor.
- **Hot-funding-window sanity** — at least one fold must overlap a
  documented hot-funding regime (2023-11 → 2024-12 per
  `vpvr_funding_hvn_lvn_confluence_20260718` SPEC), otherwise the
  validation is flagged **regime-blind** and not counted toward G5.
- **Sharpe convention** — `sharpe_daily` only, computed as
  `mean(daily_returns) / std(daily_returns) * sqrt(365.25)` on the
  **combined** equity curve (all TFs, all folds, single instrument).
  Per-trade Sharpe is deliberately not emitted.

### Pass / fail logic

- All 7 gates pass on all 3 folds → `[PROFITABLE]` flag, strategy
  eligible for cycle-48 production hand-off.
- Any single gate fails on any fold → `[FAIL_GATE_X]` flag,
  documented but not archived (cycle-47 multi-TF direction is still
  alive).
- Two or more gates fail on two or more folds → `[NOT-PROFITABLE]`,
  archived.

## Honest caveats

1. **Cycle-46 family exhaustion rule applies**: this is a
   *combination* of three prior single-TF families (1m LOID+VPVR
   from `loid_vpvr_confluence_20260717`; 15m funding-carry from
   `funding_carry_asym` / `vpvr_funding_hvn_lvn_confluence_20260718`;
   4h regime from `vpvr_funding_regime_15m_20260711` adapted and
   `vpvr_funding_asym_4h_20260713`). The combination is a new
   family per cycle-47 rules; if it fails G1–G7 the cycle-47
   multi-TF direction is exhausted and the strategy is archived.
2. **Funding carry drag**: a 4h-driven position held for 6 bars
   (24h) pays 6 funding events × ~3 bps = ~18 bps of carry. This
   eats the trailing-stop allowance on the smallest winners.
3. **LOID data requirement**: 1m LOID clusters need
   `perp_1m/BTCUSDT_1m.parquet` with `taker_buy_base` populated.
   If that column is missing, the detector falls back to
   `iceberg_detector.detect_iceberg_bars` which has `side_proxy =
   "unknown"` for every bar — the 1m edge then degrades to
   "long at HVN or short at LVN, side-blind" with reduced hit-rate.
4. **Regime transition risk**: the 1-bar 15m delay on 4h regime
   flips may miss the first bar of a new move. Cycle-46 lesson
   applied; the alternative (immediate entry on regime flip) was
   tested in `vpvr_funding_regime_15m_20260711` and lost to the
   first-bar fake.
5. **No short side in 15m**: per the funding-carry family's
   cycle-46 exhaustion. The 4h TREND_DOWN leg fires shorts through
   the 4h edge alone, not through 15m.
6. **Multi-symbol deferred**: ETH/SOL out of scope in v1 because
   their funding distributions differ enough to need per-symbol
   `funding_threshold` and proximity tuning. A v2 SPEC would
   reopen this.

## Out of scope (deliberate, for v1)

- `vol_adjusted` and `recency_weighted` weighting modes (flagged in
  Rule 3 as future variants, not implemented in v1).
- Multi-symbol expansion (ETH/SOL/AVAX etc.).
- Trade-tape-level iceberg clusterer (SMA-34796's
  `trade_size_clusterer.py`). Only bar-level LOID is used.
- Onchain / liquidation cascade overlays (separate family,
  not in scope for the VPVR-funding-carry-asym direction).
- Asymmetric RR exits per TF other than the `tp_sl_ratio` defined
  in the table above (no `tp_atr_k = 4.0, sl_atr_k = 1.0` cycle-46
  asymmetric exits in v1; the asymmetric pattern is a v2 candidate).
- Reg-test parameter optimization (z_funding thresholds,
  proximity_atr, vpvr_window_bars) — these are fixed in v1; the
  optimizer pass is a separate workstream.

## Done criteria (this SPEC)

- [ ] SPEC.md lives at
      `~/multica/quant-loop/strategies/SPEC_vpvr_multi_tf_funding.md`.
- [ ] Combines three prior single-TF prototypes without reimplementing
      any of them (re-uses `_indicators/vpvr_levels`, `loid_detector`,
      `funding_carry_asym.build_signals`).
- [ ] Confirmation matrix and conflict resolution are explicit and
      unambiguous.
- [ ] Cross-TF cascade (4h → 15m → 1m) is documented as gate + timing.
- [ ] Validation plan names ≥ 3 OOS windows and all 7 hard gates.
- [ ] SPEC-DRAFT comment posted to SMA-34911 with the required
      `[strategy-worker-1 <HH:MM>] SPEC-DRAFT:` marker.
- [ ] Issue status moved to `in_review` (B7 review gate).

## Done criteria (follow-up implementation, post-B7)

Hard-gate **B7**: no implementation issue is opened until this SPEC
passes review. If B7 clears, the following implementation issues are
filed as children of SMA-34911:

1. **SMA-34911.1** — `vpvr_multi_tf_funding` strategy directory
   skeleton (`SPEC.md` copy, `build_signals.py`, `run_backtest.py`,
   `config.json`, `tests/`, `data/`).
2. **SMA-34911.2** — per-TF edge adapters (1m, 15m, 4h) that call
   the upstream indicators without modification.
3. **SMA-34911.3** — combination layer (`combine_signals.py`)
   implementing Rules 1–3.
4. **SMA-34911.4** — cross-TF cascade + exit logic.
5. **SMA-34911.5** — unit tests for the combination layer (≥ 80%
   coverage per cycle-46 standard).
6. **SMA-34911.6** — walk-forward backtest on the 3 OOS folds,
   emitting `metrics.json` + `trades.csv` + `equity.csv` per fold.
7. **SMA-34911.7** — G1–G7 gate report (`results/gates_report.json`)
   and a final comment back to SMA-34911 with the verdict.