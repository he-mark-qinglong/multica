# attribution — strategy-level performance attribution

> Research #87 (SMA-35757) / T14. SPEC: `research/specs/performance_attribution_v1_20260728/SPEC.md`.

Decomposes a closed-trade ledger into **gross signal PnL − execution cost =
net PnL**, swept over cost scenarios, and classifies the ledger:

| verdict | meaning |
|---|---|
| `VIABLE_AT_COST` | gross > 0 and net > 0 at the scenario |
| `COST_CAP_KILL` | gross > 0 but net ≤ 0 — cost ate the edge (T01/T04 pattern) |
| `MECHANISM_KILL` | gross ≤ 0 — cost is irrelevant (T11 pattern) |

## Why

Cycle-46 family-seals kept re-deriving "was it cost or mechanism?" by hand
(mtf_xs_pairs fee-shock, T01/T04 cost-cap, T11 mechanism). This makes the
verdict mechanical and reproducible across any strategy ledger.

## Usage

```python
import pandas as pd
from _shared.attribution.decompose import CostSpec, attribute, normalize_trades, write_report

trades = normalize_trades(pd.read_csv("results/trades_all.csv"))
report = attribute(trades, [
    ("config_2bp", CostSpec(1, 1, fills_per_round_trip=4)),      # pair: 4 fills
    ("ratified_11bp", CostSpec(4, 7, fills_per_round_trip=4)),   # 22bp RT/instrument
])
write_report(report, "attribution.json")
```

Two schemas auto-detected: single (`symbol, direction, entry_ts, exit_ts,
entry_price, exit_price`) and pair (`pair, direction, entry_price_a/b,
exit_price_a/b`). Gross return is always recomputed from prices — the
ledger's own `pnl_pct` is never trusted (cost conventions differ per
strategy). Sentinels reject exit<entry, price≤0, unknown direction.

Break-even cost is solved analytically per ledger
(`break_even_bps_per_side`), so "how much cost headroom does this edge
have?" is one number, not a sweep plot.

## Tests

```bash
python3 _shared/attribution/test_decompose.py   # plain asserts, N/N passed
python3 -m pytest _shared/attribution/test_decompose.py
```
