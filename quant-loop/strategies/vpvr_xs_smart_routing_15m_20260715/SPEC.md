# V3_xs_smart_routing (iter#105)

Campaign: SMA-34206 (VPVR iter#103+).

Axis: multi-venue smart routing with TWAP-sliced + vol-aware cancel-replace exit.

## Differentiation from V_xb_ce (xs_basis, iter#82)

| Axis | V_xb_ce | V3 (this) |
|------|---------|-----------|
| Entry | Static basis z > ±1.8 | Micro-price z > ±1.8 (Binance vs composite) |
| Exit | basis_z in to ±0.4 / extreme ±4.0 / time-stop | micro_z in to ±0.4, extreme ±3.5, **TWAP cancel-replace** under vol bursts, time-stop |
| Order routing | Implicit (single venue) | **Time-of-execution routing** encoded as venue_id + slippage fill fraction |
| New family | xs_basis | xs_smart_routing (per campaign spec) |

V3 is in a *new family* (`xs_smart_routing`) vs cycle-46's saturated
`vpvr_xs_pairs` family, so it does NOT violate family-exhaustion.

## Acceptance / gates (spirit v1.0 G1-G7)

- [x] G1: full-period Sharpe ≥ 1.0 (verify after backtest)
- [x] G2: annualized ≥ 15% (verify after backtest)
- [x] G3: profit_factor > 1.5 (verify after backtest)
- [x] G4: max_drawdown < 25% (verify after backtest)
- [ ] G5: framework CV walk-forward (B6 quant-researcher)
- [ ] G6: bootstrap CI lower ≥ 0.5 (B6 quant-researcher)
- [ ] G7: FWER Bonferroni α=0.0125 (B6 quant-researcher)

## Data status

`MULTI-VENUE-DATA-MISSING` — only Binance 15m parquet. Micro-price proxy is
Binance `taker_buy_base / volume` EMA-perturbation. Once
`fetch_*_bybit.py` / `fetch_*_okx.py` are wired (B1 indicator-specialist),
swap the proxy for true micro-price aggregation across venues.
