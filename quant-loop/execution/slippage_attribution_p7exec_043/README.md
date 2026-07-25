# slippage_attribution — Per-fill slippage decomposition

> P7-EXEC-043. Decomposes every exchange fill's total slippage into
> the spread cost (structural, venue-driven) vs market impact
> (execution-policy-driven) legs.

A live-observer + cold-path-aggregator hybrid for the live trading
execution layer. Consumes the runner's
:class:`execution.runner.OrderJournal`, decomposes each fill into
its two-leg breakdown, and persists one row per fill to the
additive `slippage_attribution_fills` table.

## Folder layout

```
slippage_attribution_p7exec_043/
├── README.md                          # this file
├── SPEC.md                            # full specification
├── INTERFACE.md                       # stable type / function contract
├── slippage_attribution.py            # implementation
├── __init__.py                        # package surface
├── test_slippage_attribution.py       # unit tests
├── test_smoke.py                      # E2E smoke vs ExecutionRunner
├── bench_slippage_attribution.py      # hot-path bench
└── evidence/
    ├── bench_slippage_attribution.json
    └── smoke.json
```

## Run the tests

```bash
cd ~/multica/quant-loop/execution/slippage_attribution_p7exec_043
python3 test_slippage_attribution.py     # unit tests (pure helpers + observer)
python3 test_smoke.py                    # E2E vs ExecutionRunner
python3 bench_slippage_attribution.py    # hot-path bench
```

## Wire it into the runner

```python
from execution.runner import ExecutionRunner, OrderJournal, OutboundTransport
from execution.slippage_attribution_p7exec_043 import (
    SlippageAttributionClassifier,
    SlippageAttributionReport,
)

journal = OrderJournal("/var/lib/multica/orders.db")
runner = ExecutionRunner(journal=journal, transport=OutboundTransport(callable_send=lambda req: {"ok": True, "price": req["price"]}))
runner.register_on_fill(SlippageAttributionClassifier(journal=journal))

# Cold-path daily aggregator (cron-driven):
report = SlippageAttributionReport(journal=journal, min_sample=5)
daily = report.compute_day("2026-07-25")
report.record(daily)
```

## What you get back per fill

For each `on_fill` invocation, the runner's returned ack carries:

```python
{
    "client_order_id": "...",
    "ack": {"price": ..., "qty": ...},
    "observations": {
        "slippage_attribution_row_id": 42,
        "classification": "SPREAD" | "IMPACT" | "MIXED" | "NO_BOOK" | "NO_ARRIVAL",
        "total_slippage_bps": -7.0,
        "spread_cost_bps": -5.0,
        "impact_bps": -2.0,
        "residual_bps": 0.0,
    },
}
```

The classification label is the dominant leg (>=50% of `|total|`).
`SPREAD` = venue is the problem; `IMPACT` = execution policy is the
problem; `MIXED` = both legs matter; `NO_BOOK` = arrival book
snapshot missing (caller should enrich the request); `NO_ARRIVAL` =
strategy sent no expected-price reference (configuration bug).

## Conventions honored

- **Hot-path overhead < 250us / fill** (verified by the bench;
  default-policy median is sub-100us end-to-end including the
  `INSERT`).
- **Pure-function decomposition** — `attribute_fill(FillRecord)`
  has zero I/O and is the unit-tested surface. The observer is a
  thin wrapper around the pure helper plus a single `INSERT`.
- **NEVER silently drop fills** — every malformed input raises
  `ValueError`; missing book snapshots are recorded as `NO_BOOK`,
  not coerced to a synthetic value.
- **Folder name suffix `_p7exec_NNN`** (this: `043`); never
  `_v1` / `_v2`.
- **Persistence is the journal's job** — this module never
  bypasses `OrderJournal`.

See [SPEC.md](./SPEC.md) §6 for the canonical usage block and
[INTERFACE.md](./INTERFACE.md) for the type contract.