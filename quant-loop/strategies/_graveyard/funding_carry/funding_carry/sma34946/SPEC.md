# Strategy: funding_carry (U5 — SMA-34946)

## Hypothesis
Binance USDT-M perpetuals pay funding every 8h at 00:00 / 08:00 / 16:00 UTC. When the funding rate `r` is **strongly negative**, shorts pay longs — so a long perp earns carry. On the 8h cadence, the rate is bounded near ±1 bp on liquid pairs (SOL/ETH) with rare excursions toward ±2-3 bp on dislocations. A long-only carry harvest — enter at the funding event where `r < -threshold`, exit at the next funding event — should produce a positive expected return proportional to the size of the negative funding tail, *if* the price move between events is mean-zero and the realised carry is collected.

## Signal
- **Event-driven carry gate** (Binance USDT-M 8h cadence):
  - Long entry at event `E_i` iff `r_i < -funding_threshold` (absolute gate).
  - Exit at event `E_{i+1}` (always; one trade per event pair).
- **Direction:** long-only. (No short side — the issue scope is single-pair funding carry.)
- **No-look-ahead:** the signal at event `E_i` uses only `r_i` and prior events for any percentile reference.

## Entry
- Absolute threshold gate: `r_E < -funding_threshold`.
- Sweep grid (per issue spec): thresholds ∈ {0.0001, 0.0003, 0.0005, 0.001} (= ±1 bp, ±3 bp, ±5 bp, ±10 bp per 8h).
- Mirror short-only entry (positive-funding regime) is logged for diagnostic completeness but not part of the primary single-pair test.

## Exit
- Always exit at the next funding event `E_{i+1}` (one-bar-per-event cadence).
- No stop; no take-profit — the carry itself is the thesis.

## Stop
- **None** — the event-driven structure already bounds exposure to one funding interval (~8h). Adding a stop would corrupt the carry measurement.

## Position sizing
- Vol-target 0.5% per trade (`risk_target_pct = 0.005`) per the cycle-46 convention.
- Each trade's P&L contribution = `risk_target * (price_pnl + funding_pnl − round_trip_cost)`.

## Timeframes
- Primary: 1m (used for entry/exit bar prices).
- Carry cadence: 8h funding events (00:00 / 08:00 / 16:00 UTC).
- Sharpe: daily-resampled per SMA-34787 convention (`ann_factor = sqrt(365.25)`).

## Symbols
- **Primary**: SOLUSDT (never tested solo on 1m real data; this is the U5 gap).
- **Cross-check**: ETHUSDT (cross-check on a pair that was previously only tested as part of BTC/ETH bundle SMA-34733-V1, OOS Sharpe −147.15 → FAIL).

## Fee model
- Taker 0.04% + slippage 0.01% per fill × 2 (round-trip) = **10 bps** per trade (cycle-46 convention).
- Funding carry is realised at `E_{i+1}` as `-r_{i+1}` (longs receive when `r<0`).

## Window
- **90 days** rolling, anchored to data end (`funding/ETHUSDT.parquet` last event 2026-07-17 08:00 UTC; `perp_1m/ETHUSDT_1m.parquet` last bar 2026-07-18 05:19 UTC; SOL same).
- 30-day minimum enforced by data depth (~5100 funding events over 1700 days for both symbols — 90d comfortably fits).

## Data provenance (real Binance, no synthetic)
- ETH OHLCV 1m: `/home/smark/multica/quant-loop/data/perp_1m/ETHUSDT_1m.parquet` (3,491,275 rows, shared pool).
- SOL OHLCV 1m: `/home/smark/multica/quant-loop/data/perp_1m/SOLUSDT_1m.parquet` (3,071,420 rows, shared pool, refetched 2026-07-18 per `fetch_report_usdm_1m.json`).
- ETH funding: `/home/smark/multica/quant-loop/data/funding/ETHUSDT.parquet` (5,100 events, 8h cadence, Binance USDT-M via fapi.binance.com).
- SOL funding: `/home/smark/multica/quant-loop/data/funding/SOLUSDT.parquet` (5,175 events; coverage 1.0147 — 75 boundary-misaligned duplicates dropped by `drop_duplicates`).

## Expected (per G1-G7 hard gates)
- Sharpe ≥ 1.0 (G1), ann ≥ 15% (G2), maxDD ≥ −25% (G3), PF ≥ 1.5 (G4), bootstrap CI lower ≥ 0.5 (G5), Bonferroni α=0.0125 (G6), trades ≥ 30 (G7).
- Trade-floor: a 90d × 8h window gives ~270 funding events; threshold −1bp captures tail only.

## Cancel rule (smark directive)
- Negative annualized return across the BEST variant → mark cycle-46 family exhaustion; archive `funding_carry` single-pair as `NOT-PROFITABLE`.
- Sharpe < 0.5 after 500+ trades → archive.
- No improvement vs SMA-34930 baseline → confirm family exhausted.

## Reference prior work
- SMA-34733-V1: BTC+ETH 1m pair → OOS Sharpe −147.15 (FAIL).
- SMA-34734-V1: 1m synthetic data → FAIL on data constraint.
- SMA-34930: SOL+ETH 1m with `abs_{1bp, 0.5bp, 0.25bp} + pct_{q20,q10,q05}` grid → ALL 12 variants FAIL G1-G7; best SOL `pct_q05` Sharpe +0.862 (fails G1, G2, G7).