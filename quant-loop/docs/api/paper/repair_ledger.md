# `_shared.paper.repair_ledger`

Source: `_shared/paper/repair_ledger.py`

Graveyard daily-metrics ledger repair tool.

## `main(argv: 'Sequence[str] | None' = None) -> 'int'`

CLI entry point.  Returns the process exit code (0 on success).

| Parameter | Type | Default |
|---|---|---|
| `argv` | 'Sequence[str] | None' | None |

## `parse_glued_daily_metrics(path: 'Path', fieldnames: 'Sequence[str]' = ['date', 'total_trades', 'winning_trades', 'losing_trades', 'win_rate', 'gross_pnl_usd', 'net_pnl_usd', 'fees_usd', 'slippage_usd', 'equity_usd', 'daily_return_pct', 'rolling_20d_sharpe', 'rolling_20d_pf', 'max_drawdown_pct', 'max_drawdown_pct_vs_backtest', 'profit_factor_lifetime', 'bootstrap_ci_lo', 'action', 'kill_triggered', 'kill_reason', 'notes']) -> 'list[dict[str, str]]'`

Read the original ``daily_metrics.csv``, tolerating glued headers.

| Parameter | Type | Default |
|---|---|---|
| `path` | 'Path' | — |
| `fieldnames` | 'Sequence[str]' | ['date', 'total_trades', 'winning_trades', 'losing_trades', 'win_rate', 'gross_pnl_usd', 'net_pnl_usd', 'fees_usd', 'slippage_usd', 'equity_usd', 'daily_return_pct', 'rolling_20d_sharpe', 'rolling_20d_pf', 'max_drawdown_pct', 'max_drawdown_pct_vs_backtest', 'profit_factor_lifetime', 'bootstrap_ci_lo', 'action', 'kill_triggered', 'kill_reason', 'notes'] |
