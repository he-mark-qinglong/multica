# metrics.json schema (9-key + provenance)

Single source of truth for the canonical `metrics.json` schema. Every
strategy's `results/metrics.json` MUST validate against this schema before
it lands in dashboards, gates, or autopilot feeds.

The schema is the return dict literal of
`compute_metrics.compute_metrics` (`_shared/validation/compute_metrics.py`,
lines 108–117). **Do not hand-roll these values** — call
`compute_metrics(...)` and use its return value directly.

## 9 metric keys

| Key | Type | Semantics | Domain |
|---|---|---|---|
| `sharpe_daily` | float | Annualised Sharpe on per-bar returns, ddof=1. | finite |
| `annualized_return` | float | Geometric annualised return from start→end equity. | finite |
| `max_drawdown_pct` | float | Worst peak-to-trough drawdown as **negative fraction** (-0.25 = 25%). | `[-1.0, 0.0]` |
| `profit_factor` | float | `sum(positive bar returns) / |sum(negative bar returns)|`. | `≥ 0` |
| `n_trades` | int | Positions opened + closed (supplied by the backtest). | `≥ 0` (not bool) |
| `n_bars` | int | Number of bars in the equity curve. | `≥ 0` (not bool) |
| `win_rate` | float | Fraction in `[0, 1]`. Per-trade when `trade_pnls` given, else bar-based. | `[0.0, 1.0]` |
| `calmar` | float | `annualized_return / |max_drawdown_pct|`. | finite |
| `sortino` | float | Annualised Sharpe on downside-only returns (ddof=1). | finite |

Sentinels like `max_dd = -4e-6` cannot appear by construction
(see `compute_metrics` docstring).

## Provenance fields (4)

Required on every **newly produced** `metrics.json`. Historical files (any
file written before this schema shipped) are exempt — the validator downgrades
missing provenance to `WARN`, not `FAIL` (use `--strict-provenance` to fail).

| Key | Type | Meaning |
|---|---|---|
| `strategy` | str | The strategy directory name (e.g. `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720`). |
| `cost_bps_rt` | float | Round-trip cost in basis points applied during the run. |
| `data_window` | str | Coverage as `"<first_ts>..<last_ts>"` (ISO timestamps, inclusive). |
| `generated_at` | str | ISO-8601 UTC timestamp at which the metrics file was written. |

## Generation example

```python
from datetime import datetime, timezone
from _shared.validation.compute_metrics import compute_metrics

m = compute_metrics(
    equity=equity_series,           # pd.Series, index = timestamp, start > 0
    n_trades=len(trades),
    freq_per_year=365 * 24 * 60,    # 1-minute bars
    trade_pnls=[t.pnl_fraction for t in trades],
)
m.update({
    "strategy": "my_strategy_v1",
    "cost_bps_rt": 22.0,
    "data_window": f"{ts_first}..{ts_last}",
    "generated_at": datetime.now(timezone.utc).isoformat(),
})
```

## Validation

```bash
python3 -m _shared.validation.validate_metrics path/to/metrics.json --report
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Schema OK (and provenance OK, or warnings allowed). |
| 1 | Schema violation OR `--strict-provenance` + any provenance missing. |
| 2 | File missing or unreadable / JSON parse error. |

The validator (`validate_metrics.py`) is the canonical schema mirror; cross-check
it whenever the upstream `compute_metrics` return dict changes — they MUST
stay in lock-step.
