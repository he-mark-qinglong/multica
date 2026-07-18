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

## When NOT to use
If your strategy already pulls live fees from the venue's actual API (e.g.
freqtrade with `fee` configured), keep that — this is a default, not a mandate.
Opt-in only; existing backtests are untouched.
