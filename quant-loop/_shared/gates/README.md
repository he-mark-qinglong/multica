# gates / enforcement

Automated certification for the G1–G7 gate stack. `enforce.py` reads a
strategy's `metrics.json` and refuses to certify SHIP-eligibility when any
gate fails. Wave 2 correction: **Deflated Sharpe Ratio** (Bailey & López de
Prado 2014) replaces the bogus "Bonferroni α=0.0125" family-size claim as G7.

## The gate stack

| Gate | Criterion |
|------|-----------|
| G1 | `sharpe_daily >= 1.0` |
| G2 | `annualized_return >= 0.15` (15%) |
| G3 | `max_drawdown_pct > -0.25` (> -25%) |
| G4 | `profit_factor > 1.5` |
| G5 | `cpcv_mean_oos_sharpe >= 1.0` (skipped if CPCV not yet run) |
| G6 | `bootstrap_ci95_lower >= 0.5` |
| G7 | `deflated_sharpe > 0.0` (DSR; replaces Bonferroni) |
| T1 | `n_trades >= 30` |

## Usage

```python
from _shared.gates.enforce import certify_metrics
result = certify_metrics(metrics_dict, strict=True)
if not result.passed:
    print(result.reasons)          # why it failed
```

`certify_strategy(metrics_path, n_trials=100)` reads a `metrics.json` and
auto-computes DSR from `cpcv_mean_oos_sharpe` when `deflated_sharpe` is absent.

## CI hook

`.github/workflows/strategy-gate.yml` runs `enforce.py` on every PR that
touches `strategies/**/metrics.json`. A failing gate fails the check.

## Status

**Opt-in.** Not wired into any existing strategy, autopilot, cron, or daemon.
