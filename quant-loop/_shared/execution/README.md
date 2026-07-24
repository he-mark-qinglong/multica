# _shared/execution — Authoritative Cost Model

Single source of truth for execution cost across quant-loop strategies. Replaces
hardcoded 8bp / 24bp assumptions scattered per-strategy.

## Why
W5 archive proved strategies flip from Sharpe +1.0 → -5.85 when cost moves 8bp →
24bp. Each strategy rolling its own number was a systemic fragility. This module
gives a Binance-realistic default (taker fee + BNB discount + sqrt-market-impact
slippage) so backtests stop lying about fill economics.

## Use
```python
from _shared.execution.cost_model import apply_cost, BINANCE_SPOT
cost_usd = apply_cost(notional_usd=1000, adv_usd=1e9, venue=BINANCE_SPOT)
# subtract from gross PnL each round-trip in the bar loop
```

## Futures path (ratified, 2026-07-24)
`BINANCE_FUTURES` is wired to the ratified SMA-34900/SMA-34913 constants from
`backtest/factor_backtester.py`: 4 bps taker fee + 7 bps pure slippage per side
= 22 bps round trip, size-independent. `apply_cost(..., venue=BINANCE_FUTURES)`
is exactly equivalent to `CostModel.sma34900_baseline()`; use it for all USDT-M
perp backtests. The spot venues keep the legacy sqrt-impact path (fee + BNB
discount + size-dependent slippage), whose slippage parameters are not
empirically ratified — spot only, never for perp backtests.

## When NOT to use
If your strategy already pulls live fees from the venue's actual API (e.g.
freqtrade with `fee` configured), keep that — this is a default, not a mandate.
Opt-in only; existing backtests are untouched.
