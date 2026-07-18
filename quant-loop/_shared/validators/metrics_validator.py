"""Range and sentinel validator for strategy metrics.json.

Catches the SMA-34922 class of bug: notional-accounting error -> max_dd
degenerates to -4e-6 sentinel -> silent misclassification.

Usage:
    from _shared.validators.metrics_validator import validate_metrics
    validate_metrics(metrics_dict)  # raises AssertionError with context if bad
"""
import math
from typing import Any


# (metric_name, min, max, sentinel_patterns)
RANGE_RULES = [
    # name,                min,   max,  sentinels_to_reject
    ("sharpe_daily",      -20.0, 20.0,  [0.0]),   # exact-zero sharpe is suspicious
    ("annualized_return", -1.0,  10.0,  []),
    ("max_drawdown_pct",  -1.0,  0.0,   [-4e-6, -1e-8, -1e-9]),  # known sentinels
    ("profit_factor",      0.0,  1000.0, [0.0]),  # zero PF means no trades
    ("n_trades",            0,   1_000_000, [-1]),
    ("n_bars",              0,   100_000_000, [-1]),
    ("win_rate",           0.0,  1.0,   []),
    ("calmar",            -100.0, 100.0, [0.0]),
    ("sortino",           -100.0, 100.0, [0.0]),
]


def _is_close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) < tol


def validate_metrics(metrics: dict[str, Any], strategy_name: str = "<unknown>") -> None:
    """Raise AssertionError if any metric is NaN, inf, sentinel, or out of expected range.

    Args:
        metrics: dict like {"sharpe_daily": 1.2, "max_drawdown_pct": -0.18, ...}
        strategy_name: for error message context

    Raises:
        AssertionError: with metric name + offending value + expected range
    """
    for name, lo, hi, sentinels in RANGE_RULES:
        if name not in metrics:
            continue  # absence is fine; only validate what's present
        v = metrics[name]
        if not isinstance(v, (int, float)):
            raise AssertionError(f"[{strategy_name}] {name}={v!r} not numeric")
        if math.isnan(v):
            raise AssertionError(f"[{strategy_name}] {name}=NaN -- degenerate (zero-division?)")
        if math.isinf(v):
            raise AssertionError(f"[{strategy_name}] {name}=inf -- degenerate (overflow?)")
        for s in sentinels:
            if _is_close(float(v), s):
                raise AssertionError(
                    f"[{strategy_name}] {name}={v} matches sentinel {s} -- "
                    f"likely accounting bug (see SMA-34922)"
                )
        if not (lo <= float(v) <= hi):
            raise AssertionError(
                f"[{strategy_name}] {name}={v} outside expected range [{lo}, {hi}]"
            )


def safe_validate(metrics: dict, strategy_name: str = "<unknown>") -> tuple[bool, str]:
    """Non-raising variant. Returns (ok, message)."""
    try:
        validate_metrics(metrics, strategy_name)
        return True, "ok"
    except AssertionError as e:
        return False, str(e)
