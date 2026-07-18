# SPEC — momentum_intraday_fast_15m_btc_20260712

> Strategy id: `momentum_intraday_fast_15m_btc_20260712`
> Iteration: 89 (V13 — orchestrator direction: last attempt at momentum axis, intraday 15m entry + 1h trend)
> Author: trend-strategist
> Date: 2026-07-13
> Universe: **BTCUSDT only** (V12 lesson: ETH degrades under multi-TF regime filter; momentum family closes if V13 also fails)
> Direction: long + short (bidirectional)
> Status: bootstrap, paper-trade only

## 0. V13 deltas vs V10/V11/V12 (the 1h + 4h lineage)

V10/V11/V12 all used **1h entry + 4h trend filter** and failed the
issue-scoped evidence gate (V12 in-sample sharpe 0.26, OOS sharpe −4.79).
Orchestrator diagnosis (kimi 2dc6dfc5 / 6717f6a1 cycle-15): at 1h the
4h EMA(50) reacts too slowly — by the time it tells us trend is down we
are already -2.5 ATR underwater, and the stop is too far away to be
reached before the catastrophic leg.

V13 hypothesis: **intraday 15m + 1h trend**. At 15m:

* 1h EMA(20) gives a faster trend signal than 4h EMA(50) at 1h.
* 15m entries inside 1h trend = much faster reaction.
* Stops at -1.5 ATR (smaller move needed) are reachable before catastrophic loss.
* Sizing at 0.5% per ATR (half of V12's 1.0%) absorbs higher trade frequency.

| delta          | from V12                      | to V13                          | why |
|----------------|-------------------------------|---------------------------------|-----|
| entry TF       | 1h                            | **15m**                         | smaller stop distance reachable |
| trend TF       | 4h                            | **1h**                          | faster regime signal |
| trend indicator | EMA(50) + slope              | **EMA(20) + slope**             | faster reaction at 1h |
| entry RSI cross | 50 (symmetric)               | **55 long / 45 short**          | slightly off 50 to require momentum confirmation (no false crosses in flat markets) |
| ADX confirm    | > 20                          | **> 18** (lower at 15m)         | 15m ADX tends to be lower; tighter threshold would never fire |
| ATR stop K     | 3.5 ATR                       | **1.5 ATR** (hard)              | smaller stop reachable before catastrophic leg |
| position sizing | 1% equity per 1-ATR move     | **0.5% equity per 1-ATR move**  | absorb higher trade frequency |
| instruments    | BTCUSDT (V12 dropped ETH)     | **BTCUSDT only**                | V12 lesson; momentum is a single-symbol alpha carrier |
| fees / slip    | 1bps + 1bps                   | 1bps + 1bps                     | unchanged |
| walk-forward schedule | 1y train / 6m test / 6m step on 1h grid | 1y train / 6m test / 6m step on **15m grid** | scale to 15m (×4) |

The 1h trend filter is loaded from the native `BTCUSDT_1h.parquet`
(same source as V12's 1h entry TF, just used differently). The 1h
EMA(20) is computed on that frame, shifted by 1 bar (strict trailing),
and forward-filled onto the 15m grid.

## 1. Goal

Final attempt at the momentum axis on this BTCUSDT regime. V13 is the
**fourth** momentum-axis iteration (after V10/V11/V12); under
cycle-46 family-exhaustion rules, if V13 also fails the gate, the
momentum family closes and the orchestrator pivots to either
cross-exchange funding arbitrage (delta-neutral) or a portfolio of
weak-edge strategies.

Single primary metric: a deterministic multi-TF backtest + walk-forward
that passes the **issue-scoped evidence gate**:

- in-sample sharpe >= 0.5
- wf_ratio >= 0.5
- min_oos_sharpe >= 0 (no negative OOS windows)

Hard user gates G1–G7 (Sharpe ≥ 1.0, MDD < 25%, framework CV, bootstrap
CI, FWER Bonferroni) are deferred — V13 is a single-strategy family
test, not a ship candidate yet.

## 2. Universe & data

| item        | value                                                       |
|-------------|-------------------------------------------------------------|
| symbols     | BTCUSDT only                                                |
| source      | `/home/smark/multica/quant-loop/live_data/BTCUSDT_<TF>.parquet` |
| TFs         | 15m (entry) + 1h (trend filter)                            |
| 15m span    | 2022-01-01 → 2026-07-10 (≈158k bars)                        |
| 1h span     | 2022-01-01 → 2026-07-10 (≈39k bars)                         |
| manifest    | `data/manifest.parquet.sha256` (per-file SHA256)            |

The 1h frame is used only for the EMA(20) trend filter; signals fire
on 15m bars. The 1h EMA(20) value is forward-filled onto the 15m
grid so the filter is constant between 1h closes (no leakage), and is
shifted by 1 bar to enforce strict trailing.

## 3. Indicators

All indicators are pure functions of an OHLCV frame.

| name             | TF  | params       | formula                                                                |
|------------------|-----|--------------|------------------------------------------------------------------------|
| EMA(20)          | 1h  | period=20    | standard EMA, seed from SMA(20)                                         |
| EMA slope        | 1h  | period=20    | `(EMA[t] - EMA[t-1]) / EMA[t-1]`                                       |
| RSI(14)          | 15m | period=14    | Wilder smoothed RS, output in [0,100]                                   |
| ADX(14)          | 15m | period=14    | Wilder ADX with DI+/DI-                                                 |
| ATR(14)          | 15m | period=14    | Wilder ATR, output in **price units**                                  |

Look-ahead discipline: every indicator on bar `t` is computed from
`[t-W, t-1]` data only. The 1h EMA is `shift(1)` to make the filter
strictly trailing.

## 4. Entry (long)

All three conditions must be true on the same 15m bar:

1. `ema20_1h_slope[t] > 0` — 1h regime is up (V12 used 4h; here at 1h the slope alone is the trigger)
2. `rsi14_15m[t-1] < 55 AND rsi14_15m[t] >= 55` — RSI crosses 55 upward (off-50 to require momentum, not noise)
3. `adx14_15m[t] > 18` — directional trend present (lower threshold vs V12's 20 because 15m ADX is intrinsically lower)

## 5. Entry (short)

Mirror of §4: `ema20_1h_slope < 0` AND `rsi14[t-1] > 45 AND rsi14[t] <= 45` AND `adx14 > 18`.

## 6. Position sizing

Vol-scaled target (half of V12's risk per ATR because 15m trade frequency is ~4× higher):

    size_quote = (0.005 * equity) / (atr14 / price)

This targets 0.5% equity-at-risk per 1-ATR move. Concretely, with
`atr14 = $30` and `price = $60,000`, the notional is `0.005 * equity / (30/60000)`
= `10 * equity`, capped at the per-signal max notional.

| rule                  | value                                                      |
|-----------------------|------------------------------------------------------------|
| risk per signal       | 0.5% of equity per 1-ATR move                              |
| max notional          | 5% of equity per signal                                    |
| max gross exposure    | 5% of equity across open positions                         |
| per-symbol cap        | 1 position (long OR short, never both)                     |

## 7. Exits

First triggered wins:

1. **1h trend reversal**: `ema20_1h_slope[t]` flips sign against the current direction (long closed if slope<0; short closed if slope>0).
2. **15m RSI cross back to 50**: `rsi14[t-1] > 50 AND rsi14[t] <= 50` for a long (mirror for short). Note: the *entry* level is 55/45, but the *exit* level is **50** — this gives the trade room to breathe past the entry threshold.
3. **Hard ATR stop**: `close[t] < entry - 1.5 * ATR(14)[t]` (long) or `close[t] > entry + 1.5 * ATR(14)[t]` (short). The anchor is the **entry price** (no ratcheting); the smaller K reflects the 15m bar-count vs 1h bar-count difference in underlying volatility.

A bar that satisfies an exit is the **exit bar**; the next bar is the first that may re-enter.

## 8. Costs

| item                   | value                                                       |
|------------------------|-------------------------------------------------------------|
| fees per side          | 1.0 bps                                                     |
| slippage per side      | 1.0 bps                                                     |
| total round-trip cost  | 4.0 bps                                                     |

Applied at entry (`close * (1+cost_per_side)`) and at exit
(`close * (1-cost_per_side)`). Same convention as V10/V11/V12.

## 9. Walk-forward / acceptance

Walk-forward (per spec, scaled to 15m grid):

- 4 sequential non-overlapping windows
- train 1y (35,040 15m bars = 4 × 24 × 365) + test 6m (17,520 15m bars) + step 6m
- schedule: train `[k*17520, k*17520 + 35040]`, test `[k*17520 + 35040, k*17520 + 52560]`
- bars_per_year for Sharpe = 35,040 (15m, 4 × 24 × 365)

In-sample (full-period) and OOS metrics are computed. `wf_ratio` is
defined as:

    wf_ratio = mean(OOS sharpe) / in-sample sharpe

Issue-scoped evidence gate (must all be true to mark this issue done):

- [ ] in-sample sharpe >= 0.5
- [ ] wf_ratio >= 0.5
- [ ] min_oos_sharpe >= 0 (no negative OOS windows)

Below any gate → archive path, status `done` with `[NOT-PROFITABLE]` verdict + cycle-46 family-exhaustion note (momentum family closes after V13).

## 10. Out of scope (deferred)

- G1–G7 hard user gates (Sharpe ≥ 1.0, MDD < 25%, framework CV, bootstrap CI, FWER Bonferroni)
- ETHUSDT or other symbols (V12 lesson; family closes if V13 fails)
- Trailing-stop ratchet
- Portfolio combo with vpvr_reversion / xs_pairs
- Funding-rate filter (V13 does not consume funding)

## 11. Risk & human-in-the-loop

- Paper-trade only — no live orders are placed by this strategy.
- Irreversible operations: none.
- `data_loader.py` and `run_backtest.py` both refuse to run if `LIVE_TRADING=1` is set.

---

## Fee model

This strategy follows multica canonical fee model (see `../FEE_MODEL.md`):

- taker_fee: 0.0010 (10 bps) per side
- slippage: 0.0005 (5 bps) per side
- effective round-trip cost: 0.0030 (30 bps)
- fill price: `bar.close` of the signal/exit bar (NOT next-bar open)
- compounding: yes, equity updated after each closed trade
- funding_rate / borrow_fee: not modeled

If this strategy deviates from the spec, document the deviation here as
`[DEVIATION] <field>: <value> (vs spec <baseline>)`.

[DEVIATION] fees: spec uses 1bps/side (matching V10/V11/V12) but canonical is 10bps taker.
This iteration aligns with V12 fee convention so the comparison is
apples-to-apples. Revisit if/when this strategy is upgraded to a ship candidate.