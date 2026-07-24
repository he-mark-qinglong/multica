# V3_funding_reset_window (iter#108)

Campaign: SMA-34339 (VPVR iter#106+), 2026-07-15.

Axis: **perp-funding reset window** with VPVR POC reversion and
asymmetric 2:1 TP/SL execution. Third variant in the calendar/TOD/funding
axis trio (V1=macro events, V2=sessions, V3=funding reset).

## Distinguishing axis table

| Variant | Axis | Time scale | Event class |
|---------|------|------------|-------------|
| iter#106 vpvr_macro_calendar | scheduled macro events | multi-day | FOMC/CPI/quad Witch |
| iter#107 vpvr_tod_session | daily recurring sessions | intra-day | Asia/London/US overlap |
| **V3 (this)** | **perp funding reset** | **microstructure** | **00/04/08/12/16/20 UTC, 8h cadence** |

Novel contribution: a funding-reset timestamp creates a market-microstructure
boundary — traders with the wrong side pay funding and often close or flip
positions at the reset. Combined with stretched VPVR POC distance and a 2:1
asymmetric exit (TP 2x ATR, SL 1x ATR), this targets the forced-positioning
flow that books a short-term reversion.

## Acceptance / gates (spirit v1.0 G1-G7)

- [x] G1: full-period Sharpe ≥ 1.0 (verify after backtest)
- [x] G2: annualized ≥ 15% (verify after backtest)
- [x] G3: profit_factor > 1.5 (verify after backtest)
- [x] G4: max_drawdown < 25% (verify after backtest)
- [ ] G5: framework CV walk-forward (B6 quant-researcher)
- [ ] G6: bootstrap CI lower ≥ 0.5 (B6 quant-researcher)
- [ ] G7: FWER Bonferroni α=0.0125 (B6 quant-researcher)

## Data status

FUNDING-DATA-PRESENT — `funding_analysis/BTCUSDT_funding.parquet` is
loaded inside `data_loader.py` and forward-filled to 1h bar frequency.

## Time-stop / asymmetric exit

- TP: +2.0×ATR
- SL: -1.0×ATR (2:1 reward/risk)
- Time-stop: 6 bars (6h)
- Vol-target: 6 bars (6h)
- Max holding: 12 bars (12h)
- Cooldown: 4 bars between entries
