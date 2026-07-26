# VPVR Edge Reversion — Round-1 vs Round-2 Comparison

> Same data (BTC/ETH/SOL 1m perp klines, 730d), same signal logic, but **different
> VPVR resolution / profile window / confirmation trigger**. Both rounds use
> honest no-look-ahead signal timing.

## Methodology deltas

| axis | round-1 | round-2 |
|------|---------|---------|
| profile window | daily (1440 bars) | 4h (240 bars) |
| bucket count | 200 across daily range (~25-50bp wide) | adaptive, target 5bp wide (~100-200 buckets per 4h) |
| intra-bar distribution | bar volume → close-price bucket only | uniform over [low, high] |
| confirmation trigger | none | z24 ≥ 1.0 in direction of LVN touch |
| cost assumption | VIP0 9bp pair-RT (pre-SMA-36660) | maker 0.8bp + taker 2bp = 2.8bp RT (SMA-36660) |
| horizons tested | 1h / 4h / 1d | 4h / 1d (1h too short for 4h-window setups) |
| shift discipline | +1d shift on daily_metrics (round-1 audit fix) | +4h shift on 4h buckets (round-2 audit fix; same bug class) |

## Combined long+short, scenario_b_defensive, post-cost (net of round-trip fee)

| horizon | symbol | round-1 fill / TP1 / drop / mark | round-2 fill / TP1 / drop / mark | Δ mark |
|---------|--------|----------------------------------|-----------------------------------|--------|
| 4h | BTC | 39.6% / 19.7% / 25.3% / **-37bp** | 59.7% / 49.3% / 27.1% / **-9.8bp** | +27bp |
| 4h | ETH | 38.4% / 20.5% / 27.9% / **-58bp** | 59.1% / 46.8% / 30.9% / **-17.2bp** | +41bp |
| 4h | SOL | 40.5% / 18.1% / 26.4% / **-71bp** | 63.8% / 48.3% / 31.3% / **-18.5bp** | +53bp |
| 1d | BTC | 63.2% / 48.1% / 37.1% / **-28bp** | 83.3% / 62.9% / 34.9% / **-7.0bp** | +21bp |
| 1d | ETH | 63.2% / 45.9% / 39.1% / **-42bp** | 82.1% / 60.4% / 37.6% / **-14.0bp** | +28bp |
| 1d | SOL | 63.7% / 48.0% / 37.0% / **-46bp** | 85.1% / 59.7% / 38.6% / **-15.7bp** | +30bp |

- **Round-2 vs round-1**: every cell is BETTER (less negative mean, higher TP1
  rate, higher fill). Mean improvement ≈ 17-53bp depending on cell. This
  confirms the methodology change (finer VPVR + 4h window + z-confirm) shifts
  the mean upward by ~30bp on average.
- **Round-2 vs smark acceptance gates (TP1-first > 65% AND markout > +30bp)**:
  - TP1-first: 6/6 cells in 46-63% range, NONE clear 65% threshold.
  - Mean markout: 6/6 cells in -19bp to -7bp range, NONE clear +30bp threshold.
  - **Both gates fail → NOT a viable SPEC candidate.**

## Honest interpretation

1. **Methodology change is real, not look-ahead bias**. Round-2 caught and
   fixed the same +4h shift bug that round-1 caught for daily (+1d shift). The
   pre-fix round-2 numbers (TP1 86%, +36bp) were look-ahead-contaminated; the
   post-fix honest numbers are -9 to -19bp. The fact that round-2 *still*
   produces 6/6 negative cells confirms the geometry hypothesis fails even
   with finer VPVR / shorter window / confirmation trigger / lower cost.

2. **Dropout rate is unchanged (27-39% across both rounds, all cells)**. This
   is the load-bearing failure: prices break the entry LVN edge ~⅓ of the time
   regardless of profile resolution. Finer VPVR doesn't change WHERE price
   breaks — it just narrows the definition of what "the edge" is.

3. **TP1-first rate up from 45-48% to 49-63%**, but still below 65% gate.
   Mean reversion IS happening more often with the confirmation trigger, but
   not enough to clear cost.

4. **Fill rate up from 39-63% to 59-85%**. The z-confirm trigger IS predictive
   of fills — when z24 > 1.0 in the direction of LVN touch, limit orders fill
   more often. But high fill rate doesn't help if TP1 rate < 65% and dropout
   rate is still 30%.

## Pre-registered K conditions (re-applied)

| gate | threshold | round-1 status | round-2 status |
|------|-----------|----------------|----------------|
| K1 median OOS Sharpe | < 0.5 | TRIGGERED (-28/-42/-46bp) | TRIGGERED (-7/-14/-16bp) |
| K4 TP1 hit rate | < 50% | TRIGGERED (45-48%) | TRIGGERED (49-63%) |
| K5 cycle-46 negative-fold | 9/9 cells | TRIGGERED (9/9) | TRIGGERED (6/6) |

K1, K4, K5 all TRIGGERED in round-2. **Three different methodology changes
(daily vs 4h profile; 200 vs 5bp buckets; no-trigger vs z24-trigger) all
produce 6/6 negative cells on Binance perp kline proxy.**

## Cycle-46 family-exhaustion update

The `vpvr_edge_reversion` family is now exhausted across **two distinct
methodologies**:

1. Vanilla kline proxy (200 buckets, daily, no trigger) — round-1 KILL.
2. Finer kline proxy + shorter window + confirmation trigger (5bp buckets,
   4h, z24-confirm) — round-2 KILL (this session).

Different mechanisms, same substrate (Binance perp kline proxy). The geometry
hypothesis (LVN-edge entry → HVN-center mean-reversion at multi-hour horizon)
is **NOT supported** by the data even with two distinct methodology passes.

## Revival conditions (unchanged from round-1 thread file)

For any future attempt to differ from this KILL verdict, ALL of these must hold:

1. **Tick-level profile data** (Bookmap / TradingView PaVP / orderbook depth).
   1m kline OHLCV aggregates away the information that distinguishes "real"
   HVN/LVN from "kline artifact".
2. **OFI-augmented signal** to avoid Albers 2025 maker-dilemma — limit orders
   at LVN edges get picked off by sweep events.
3. **Liquidation-cascade sub-regime filter** — only fire in forced-flow windows.
4. **Regime gate** (high vol-of-vol only).
5. **Stronger cost model** verified on account-level VIP data.

Without all 5, the geometry hypothesis on Binance perp kline is a
**structural mechanism kill**, not a parameter-sweep miss.