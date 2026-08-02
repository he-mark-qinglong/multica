# `_shared.paper.ledger_writer`

Source: `_shared/paper/ledger_writer.py`

Atomic, idempotent, per-date ledger writer for paper-trading results.

## `append_daily_row(ledger_dir: 'Path', row: 'Mapping[str, object]', fieldnames: 'Sequence[str]' = ['date', 'total_trades', 'winning_trades', 'losing_trades', 'win_rate', 'gross_pnl_usd', 'net_pnl_usd', 'fees_usd', 'slippage_usd', 'equity_usd', 'daily_return_pct', 'rolling_20d_sharpe', 'rolling_20d_pf', 'max_drawdown_pct', 'max_drawdown_pct_vs_backtest', 'profit_factor_lifetime', 'bootstrap_ci_lo', 'action', 'kill_triggered', 'kill_reason', 'notes']) -> 'Path'`

Upsert a single daily row, keyed on ``row["date"]``.

| Parameter | Type | Default |
|---|---|---|
| `ledger_dir` | 'Path' | — |
| `row` | 'Mapping[str, object]' | — |
| `fieldnames` | 'Sequence[str]' | ['date', 'total_trades', 'winning_trades', 'losing_trades', 'win_rate', 'gross_pnl_usd', 'net_pnl_usd', 'fees_usd', 'slippage_usd', 'equity_usd', 'daily_return_pct', 'rolling_20d_sharpe', 'rolling_20d_pf', 'max_drawdown_pct', 'max_drawdown_pct_vs_backtest', 'profit_factor_lifetime', 'bootstrap_ci_lo', 'action', 'kill_triggered', 'kill_reason', 'notes'] |

## `rebuild_daily_metrics(trades_path: 'Path', starting_capital: 'float') -> 'list[dict[str, object]]'`

Rebuild daily rows from a ``trades.jsonl`` stream.

| Parameter | Type | Default |
|---|---|---|
| `trades_path` | 'Path' | — |
| `starting_capital` | 'float' | — |

## `write_daily_csv(path: 'Path', rows: 'Sequence[Mapping[str, object]]', fieldnames: 'Sequence[str]' = ['date', 'total_trades', 'winning_trades', 'losing_trades', 'win_rate', 'gross_pnl_usd', 'net_pnl_usd', 'fees_usd', 'slippage_usd', 'equity_usd', 'daily_return_pct', 'rolling_20d_sharpe', 'rolling_20d_pf', 'max_drawdown_pct', 'max_drawdown_pct_vs_backtest', 'profit_factor_lifetime', 'bootstrap_ci_lo', 'action', 'kill_triggered', 'kill_reason', 'notes']) -> 'None'`

Full overwrite of a daily-metrics CSV (used by the repair tool).

| Parameter | Type | Default |
|---|---|---|
| `path` | 'Path' | — |
| `rows` | 'Sequence[Mapping[str, object]]' | — |
| `fieldnames` | 'Sequence[str]' | ['date', 'total_trades', 'winning_trades', 'losing_trades', 'win_rate', 'gross_pnl_usd', 'net_pnl_usd', 'fees_usd', 'slippage_usd', 'equity_usd', 'daily_return_pct', 'rolling_20d_sharpe', 'rolling_20d_pf', 'max_drawdown_pct', 'max_drawdown_pct_vs_backtest', 'profit_factor_lifetime', 'bootstrap_ci_lo', 'action', 'kill_triggered', 'kill_reason', 'notes'] |
