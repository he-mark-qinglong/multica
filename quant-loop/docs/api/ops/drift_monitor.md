# `_shared.ops.drift_monitor`

Source: `_shared/ops/drift_monitor.py`

Live-vs-backtest drift monitor (H19).

## class `DriftReport(n_live: 'int', n_expected: 'int', price_dev_bp: 'float', fill_rate_dev: 'float', pnl_dev_bp: 'float', breaches: 'Tuple[str, ...]' = <factory>) -> None`

Immutable drift measurement between matched fill streams.

## class `DriftThresholds(max_price_dev_bp: 'float' = 5.0, max_fill_rate_dev: 'float' = 0.2, max_pnl_dev_bp: 'float' = 50.0, min_fills: 'int' = 1) -> None`

Breach levels; any one breach makes the report not ok.

## class `Fill(price: 'float', qty: 'float', pnl: 'float' = 0.0) -> None`

One fill observation (live or backtest-expected).

## `compute_drift(live: 'Sequence[Fill]', expected: 'Sequence[Fill]', thresholds: 'DriftThresholds' = DriftThresholds(max_price_dev_bp=5.0, max_fill_rate_dev=0.2, max_pnl_dev_bp=50.0, min_fills=1)) -> 'DriftReport'`

Compute the full drift report and evaluate all thresholds. Pure.

| Parameter | Type | Default |
|---|---|---|
| `live` | 'Sequence[Fill]' | — |
| `expected` | 'Sequence[Fill]' | — |
| `thresholds` | 'DriftThresholds' | DriftThresholds(max_price_dev_bp=5.0, max_fill_rate_dev=0.2, max_pnl_dev_bp=50.0, min_fills=1) |

## `cumulative_pnl_deviation_bp(live: 'Sequence[Fill]', expected: 'Sequence[Fill]') -> 'float'`

(sum live pnl - sum expected pnl) as bp of expected traded notional.

| Parameter | Type | Default |
|---|---|---|
| `live` | 'Sequence[Fill]' | — |
| `expected` | 'Sequence[Fill]' | — |

## `drift_alert(report: 'DriftReport', strategy: 'str' = 'strategy', now: 'Optional[float]' = None) -> 'Optional[Alert]'`

CRITICAL alert when the report breaches any threshold; else None.

| Parameter | Type | Default |
|---|---|---|
| `report` | 'DriftReport' | — |
| `strategy` | 'str' | 'strategy' |
| `now` | 'Optional[float]' | None |

## `fill_price_deviation_bp(live: 'Sequence[Fill]', expected: 'Sequence[Fill]') -> 'float'`

Qty-weighted mean signed deviation of live vs expected fill prices (bp).

| Parameter | Type | Default |
|---|---|---|
| `live` | 'Sequence[Fill]' | — |
| `expected` | 'Sequence[Fill]' | — |

## `fill_rate_deviation(n_live: 'int', n_expected: 'int') -> 'float'`

Relative shortfall/surplus of live fills vs expected. 0 = on par.

| Parameter | Type | Default |
|---|---|---|
| `n_live` | 'int' | — |
| `n_expected` | 'int' | — |
