# T01 — OFI on real aggTrades (Cont-Kukanov-Stoikov replication)

**Status**: killed (2026-07-20)
**Discipline**: paper-replication (falsification-first)
**Skill context**: execution-microstructure + research-journal

## Question
Does Cont-Kukanov-Stoikov (2014) Order Flow Imbalance — generalized to signed taker volume on
real aggTrades — predict next-bar mid-price drift on BTC perp at the 1m horizon, and is that
edge tradable after realistic costs?

## Data (canonical window)
- Symbol: BTCUSDT aggTrades (Binance format, `is_buyer_maker` semantics)
- Range: 2026-04-19 00:00 UTC → 2026-06-30 23:59 UTC (~73 days)
- Rows: 105,119 1-min bars after filter (≥10 trades per bar)
- Raw trades: ~108M (16M Apr + 34M May + 58M Jun)
- Source: `~/multica/quant-loop/data/trades/BTCUSDT_aggtrades.parquet/`

## Signal definition
- Per-bar `ofi_t = buy_vol_t − sell_vol_t`
  - `buy_vol`: Σ qty over trades where `is_buyer_maker = False` (taker buy)
  - `sell_vol`: Σ qty where `is_buyer_maker = True` (taker sell)
- Z-scored ofi over rolling window L: `z_t = (ofi_t − μ_{L}) / σ_{L}`
- Position: `+1` if `z_t > +thr`, `−1` if `z_t < −thr`, else flat
- Entry at bar t+1 close, exit at bar t+1+H close (H = hold bars)

## Pre-registered falsification gates (paper-replication standard)
- G1: OOS Sharpe ≥ 1.0 (CPCV walk-forward, no leakage)
- G2: DSR passing
- G3: Annualized ≥ 15% after realistic costs
- G4: Robustness: ≥5/35 cells pass G1 (no single-cell fitting)
- G5: cost-cap — gross edge ≥ 5× round-trip cost

## Sweep
Lookbacks × thresholds × hold-bars: 3 × 6 × 5 = 90 cells on SPOT venue (BTC).

## Result

**Mechanistic / sensitivity check** (NO cost):
- `corr(z_ofi_t, mid_ret_{t+1})` = +0.206 (L=240)
- Per-quintile forward H=1 return:
  - Q1 (z lowest):  −1.74e-04  (−17.4bp)
  - Q5 (z highest): +1.67e-04  (+16.7bp)
  - **Top-bottom quintile spread: 3.41bp** (per trade gross edge)
- GROSS long-short spread Sharpe (no cost): +498

**With realistic cost** (BINANCE_SPOT round-trip = 17.83bp):
- Net edge per trade (top-bot spread − cost): **−14.42bp**
- 0/90 cells pass G1 (OOS Sharpe ≥ 1.0 after cost)
- Best cell (L=1440, thr=2.0, hold=240) OOS Sharpe = **−33.0** (deeply negative)
- CPCV (4 sub-windows, OOS=last half of each): −38.9 / −37.9 / −28.6 / −31.1
  - Mean = **−34.1** ± 5.1

**With FUTURES venue** (round-trip = 10.83bp):
- Net edge per trade: **−7.42bp** (still fails cost-cap by 2x)

**Cost-cap test**:
- Gross top-bottom spread 3.41bp vs round-trip cost SPOT 17.83bp → ratio 0.19 (need ≥5)
- KILL

## Verdict
**KILL — Cont-Kukanov-Stoikov OFI on BTC 1m aggTrades is statistically real but unviable as a taker strategy.**

## Why recorded
- Signal edge per trade (~3.4bp top-bottom quintile spread at 1-bar horizon) is too small
  relative to Binance taker round-trip cost (10.83bp futures / 17.83bp spot).
- Net of cost, signal goes deeply negative: −7.4bp (futures) to −14.4bp (spot) per trade.
- 0/90 sweep cells survive G1 (post-cost OOS Sharpe ≥ 1.0).
- The signal was independently-confirmed vs prior literature (corr +0.20) — this is real,
  not noise — but the cost threshold is the binding constraint.
- v1 was KILLED on kline proxy (SMA-34997); this v2 (real aggTrades) ALSO KILL, but on a
  different gate (cost-clearance, not signal-noise). The mechanism is real; the cost is
  the wall.

## Revival conditions (any ONE enables a re-attempt)
1. **Sub-taker execution**: maker entry + queue-priority logic → effective cost < 1bp.
   Then 3.4bp edge × multi-bar holding period could clear.
2. **Aggregation to higher horizon**: at H=60 or H=240 currently signal-spread drops to
   ~2bp per entry. Doesn't help — need a fundamentally stronger signal at multi-bar horizon.
3. **Liquidation-cascade regime only**: per execution-microstructure skill, liquidation
   cascades can produce 10x normal OFI amplitude. In that sub-regime only, edge may exceed cost.
4. **Confluence with T04 (iceberg)**: T04/SMA-34992 detects absorption patterns that
   precede larger moves. Combining iceberg-confirmed entries with OFI direction could push
   per-trade edge > 20bp.
5. **In a NEW regime / dataset**: e.g., BTC during forced-leverage-unwrap or pre/post
   halving. Canonical 2026-04–06 window doesn't qualify.

## Files
- `~/multica/quant-loop/research/ofi/btc_1m_3mo.parquet` — 1m buckets
- `~/multica/quant-loop/research/ofi/ofi_sanity.py` — shared helpers
- `~/multica/quant-loop/research/ofi/02_ofi_signal.py` — initial grid (had sign bug → fixed)
- `~/multica/quant-loop/research/ofi/03_signed_check.py` — corr / sign verification
- `~/multica/quant-loop/research/ofi/04_simple_long.py` — gross Sharpe sanity
- `~/multica/quant-loop/research/ofi/05_net_backtest.py` — net Sharpe sweep (90 cells)
- `~/multica/quant-loop/research/ofi/06_summary.py` — final verdict
- `~/multica/quant-loop/research/ofi/verdict.json` — machine-readable verdict
- `~/multica/quant-loop/research/ofi/sweep_hold.csv` — full grid
- `~/multica/quant-loop/research/ofi/sweep_summary.json` — top cell / CPCV

## Linked issues / threads
- Parent: SMA-35021 [ROOT RESEARCH] Persistent research journal
- Sibling: SMA-34992 [STRATEGY-PIVOT] iceberg / large-order detection (T04)
- Sibling: SMA-34997 — prior OFI v1 (KILLED on kline-proxy)
- Trigger: SMA-35037 OFI on real aggTrades (this work)

## Anti-pattern guard (per research-journal skill)
- Did NOT tune thr/lookback to find a passing cell (cost-clearance gate fails uniformly).
- Did NOT skip the cost-cap gate because it was inconvenient.
- Did NOT extrapolate from gross Sharpe (498) — that's a no-cost theoretical ceiling.
- Recorded revival conditions so this isn't silently retried.
