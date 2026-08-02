# `_shared.visualization.core`

> ⚠ Generated via static AST parsing — the module could not be imported (`No module named 'visualization'`).

Source: `_shared/visualization/core.py`

Standard strategy visualization bundle.

## class `StrategyVisualizer`

Generate human-reviewable strategy artifacts.

### `generate_all(max_trade_samples: int = 150) -> dict[str, Path]`

Generate the full mandatory bundle.

| Parameter | Type | Default |
|---|---|---|
| `max_trade_samples` | int | 150 |

### `plot_equity_curve(figsize: tuple[int, int] = (14, 10)) -> Path`

Plot combined + per-side equity curves with drawdown bands.

| Parameter | Type | Default |
|---|---|---|
| `figsize` | tuple[int, int] | (14, 10) |

### `plot_trade_history(side: str = 'long', n_windows: int = 6, trades_per_window: int = 5, window_bars: int = 120, figsize: tuple[int, int] = (20, 12)) -> Path`

Plot representative trades in local K-line windows.

| Parameter | Type | Default |
|---|---|---|
| `side` | str | 'long' |
| `n_windows` | int | 6 |
| `trades_per_window` | int | 5 |
| `window_bars` | int | 120 |
| `figsize` | tuple[int, int] | (20, 12) |

### `plot_trade_diagnostic(figsize: tuple[int, int] = (16, 12)) -> Path`

Scatter / histogram diagnostics of trade population.

| Parameter | Type | Default |
|---|---|---|
| `figsize` | tuple[int, int] | (16, 12) |

### `plot_returns_heatmap(figsize: tuple[int, int] = (14, 6)) -> Path`

Monthly return heatmap by symbol.

| Parameter | Type | Default |
|---|---|---|
| `figsize` | tuple[int, int] | (14, 6) |

### `plot_vpvr_overlay(figsize: tuple[int, int] = (12, 8)) -> Path`

VPVR volume profile with long/short entry clusters.

| Parameter | Type | Default |
|---|---|---|
| `figsize` | tuple[int, int] | (12, 8) |

### `plot_indicator_overlay(figsize: tuple[int, int] = (16, 10)) -> Path`

Price + indicator + trades.

| Parameter | Type | Default |
|---|---|---|
| `figsize` | tuple[int, int] | (16, 10) |
