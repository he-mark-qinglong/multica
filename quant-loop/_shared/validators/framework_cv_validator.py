"""Framework cross-validation validator.

Stricter companion to metrics_validator. Compares the in-house backtest
numbers against an independent framework (freqtrade / backtrader) and raises
AssertionError when the two disagree so badly that the in-house result is not
trustworthy — even if the in-house metrics pass their own range checks.

Rules (first match wins; all returns in FRACTIONAL units unless noted):
  1. SIGN FLIP — sharpe OR ann_return flips sign between in-house and framework,
     AND the absolute gap exceeds 0.05 sharpe units / 0.05 fractional return
     (= 5 percentage points). The abs-gap floor excludes near-zero noise.
  2. WIPEOUT — framework total return < -50% (-0.50 fractional). The framework
     believes the strategy loses more than half its capital; always reject.
  3. 4x DIVERGENCE — in-house return > 200% (> 2.0 fractional) but framework
     return < 50% (< 0.5 fractional). A 4x+ gap even when signs agree.

Missing fields → return without raising (caller maps that to UNVALIDATED —
conservative; never silently OK).

Usage:
    from framework_cv_validator import validate_framework_cv
    validate_framework_cv(inhouse_metrics, framework_cv, strategy_name)
"""
from typing import Any, Optional


# Absolute-gap floors for the sign-flip rule.
SHARPE_GAP_FLOOR = 0.05      # sharpe units
RETURN_GAP_FLOOR = 0.05      # fractional return (= 5 percentage points)

# Wipeout + 4x thresholds (fractional return units).
WIPEOUT_THRESHOLD = -0.50        # framework return below this = wipeout
DIVERGENCE_4X_INHOUSE = 2.00     # in-house return above this = "huge" (+200%)
DIVERGENCE_4X_FRAMEWORK = 0.50   # framework return below this = "tiny" (+50%)


def _as_float(x: Any) -> Optional[float]:
    """Coerce to a finite float, else None. Rejects bool/NaN/inf/non-numeric."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None
        if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
            return None
        return f
    return None


def _ih_value(d: dict, *keys: str) -> Optional[float]:
    """First numeric value found under any of keys in d (top-level only)."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = _as_float(d.get(k))
        if v is not None:
            return v
    return None


def _fw_value(fw: dict, *paths: tuple) -> Optional[float]:
    """First numeric value found by walking nested key paths in framework_cv.

    Each path is a tuple of keys, e.g. ("framework", "sharpe"). Walks paths in
    order; returns the first numeric hit.
    """
    if not isinstance(fw, dict):
        return None
    for path in paths:
        node: Any = fw
        ok = True
        for k in path:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                ok = False
                break
        if ok:
            v = _as_float(node)
            if v is not None:
                return v
    return None


def _sign(x: float) -> int:
    return 1 if x >= 0 else -1


def validate_framework_cv(inhouse_metrics: dict, framework_cv: dict,
                          strategy_name: str = "<unknown>") -> None:
    """Raise AssertionError if in-house and framework results diverge.

    Args:
        inhouse_metrics: flat in-house metrics dict; needs 'sharpe' and
            'ann_return' (aliases accepted). Return values fractional.
        framework_cv: loaded framework_cv_freqtrade.json; needs framework
            sharpe + total_return under 'framework' / 'framework_oos' /
            top-level legacy keys. Return values fractional.
        strategy_name: error-message context.

    Raises:
        AssertionError on divergence. Returns None when required values are
        missing (caller's responsibility to mark UNVALIDATED).
    """
    if not isinstance(inhouse_metrics, dict) or not isinstance(framework_cv, dict):
        return  # nothing to validate

    ih_sharpe = _ih_value(inhouse_metrics, "sharpe", "agg_sharpe_mean", "sharpe_ratio")
    ih_return = _ih_value(inhouse_metrics, "ann_return", "annualized_return",
                          "agg_annualised_return_pct", "total_return")

    fw_sharpe = _fw_value(framework_cv,
                          ("framework", "sharpe"),
                          ("framework_oos", "sharpe"),
                          ("oos_sharpe_mean",))
    fw_return = _fw_value(framework_cv,
                          ("framework", "total_return"),
                          ("framework_oos", "total_return"),
                          ("oos_total_return_mean",))

    # Missing → cannot evaluate; let caller mark UNVALIDATED.
    if ih_sharpe is None or ih_return is None or fw_sharpe is None or fw_return is None:
        return

    # Rule 1: sign flip + abs-gap floor on sharpe OR ann_return.
    if _sign(ih_sharpe) != _sign(fw_sharpe) and abs(ih_sharpe - fw_sharpe) > SHARPE_GAP_FLOOR:
        raise AssertionError(
            f"[{strategy_name}] sign flip on sharpe: in-house={ih_sharpe:.4f} "
            f"vs framework={fw_sharpe:.4f} (gap {abs(ih_sharpe - fw_sharpe):.4f} "
            f"> {SHARPE_GAP_FLOOR})"
        )
    if _sign(ih_return) != _sign(fw_return) and abs(ih_return - fw_return) > RETURN_GAP_FLOOR:
        raise AssertionError(
            f"[{strategy_name}] sign flip on ann_return: in-house={ih_return:.4f} "
            f"vs framework={fw_return:.4f} (gap {abs(ih_return - fw_return):.4f} "
            f"> {RETURN_GAP_FLOOR})"
        )

    # Rule 2: wipeout — framework return < -50%.
    if fw_return < WIPEOUT_THRESHOLD:
        raise AssertionError(
            f"[{strategy_name}] framework wipeout: total_return={fw_return:.4f} "
            f"(< {WIPEOUT_THRESHOLD} = -50%)"
        )

    # Rule 3: 4x divergence — in-house huge, framework tiny.
    if ih_return > DIVERGENCE_4X_INHOUSE and fw_return < DIVERGENCE_4X_FRAMEWORK:
        raise AssertionError(
            f"[{strategy_name}] 4x return divergence: in-house={ih_return:.4f} "
            f"(> {DIVERGENCE_4X_INHOUSE} = +200%) vs framework={fw_return:.4f} "
            f"(< {DIVERGENCE_4X_FRAMEWORK} = +50%)"
        )
