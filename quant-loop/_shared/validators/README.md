# metrics_validator

Range + sentinel validator for strategy `metrics.json`. Catches the
SMA-34922 class of bug where a notional-accounting error made `max_dd`
degenerate to a `-4e-6` sentinel and silently auto-archived 3 legitimate
strategies (SMA-34893/34886/34908).

`validate_metrics(metrics)` raises `AssertionError` (with metric name,
value, expected range) if any metric is NaN, inf, a known sentinel, or
out of range. `safe_validate(...)` returns `(ok, msg)` instead.

## Usage

```python
from _shared.validators.metrics_validator import validate_metrics
validate_metrics(metrics, strategy_name="my_strat")  # raises if bad
```

## Status

**NOT auto-wired.** Opt-in library. Strategies must call it after
computing metrics and before writing `metrics.json`. Does not touch
autopilot, cron, or any daemon. No third-party deps (stdlib only).
