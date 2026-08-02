# `_shared.ops.risk_dashboard`

Source: `_shared/ops/risk_dashboard.py`

Real-time risk monitoring dashboard (D19).

## class `DashboardState(ts: 'float', equity: 'float', positions: 'Tuple[Position, ...]' = (), pnl_history_bp: 'Tuple[float, ...]' = (), alerts: 'Tuple[Alert, ...]' = (), heartbeat: 'Optional[HeartbeatStatus]' = None, var_limit_bp: 'float' = 200.0, var_warn_fraction: 'float' = 0.7, refresh_sec: 'int' = 5) -> None`

One immutable snapshot of everything the dashboard renders.

## class `TrafficLight(level: 'str', reasons: 'Tuple[str, ...]' = ()) -> None`

Aggregated risk signal: GREEN / YELLOW / RED with reasons.

## `evaluate_traffic_light(state: 'DashboardState', tail: 'TailRiskResult') -> 'TrafficLight'`

Pure aggregation rule for the headline risk light.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'DashboardState' | — |
| `tail` | 'TailRiskResult' | — |

## `load_state_from_dir(state_dir, beat_timeout_sec: 'float' = 30.0, now: 'Optional[float]' = None) -> 'DashboardState'`

Build a DashboardState from ``state.json`` + ``beat.json`` in a dir.

| Parameter | Type | Default |
|---|---|---|
| `state_dir` | — | — |
| `beat_timeout_sec` | 'float' | 30.0 |
| `now` | 'Optional[float]' | None |

## `render_dashboard(state: 'DashboardState') -> 'str'`

Render the dashboard snapshot to a self-contained HTML string. Pure.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'DashboardState' | — |

## `watch_loop(state_dir, out_html, interval_sec: 'float' = 5.0, *, beat_timeout_sec: 'float' = 30.0, stop: 'Optional[Callable[[], bool]]' = None, max_iterations: 'Optional[int]' = None) -> 'int'`

Periodically reload state and rewrite ``out_html``. Returns iterations.

| Parameter | Type | Default |
|---|---|---|
| `state_dir` | — | — |
| `out_html` | — | — |
| `interval_sec` | 'float' | 5.0 |
| `beat_timeout_sec` | 'float' | 30.0 |
| `stop` | 'Optional[Callable[[], bool]]' | None |
| `max_iterations` | 'Optional[int]' | None |

## `write_dashboard(state: 'DashboardState', out_html) -> 'str'`

Render and atomically write the HTML file; returns the HTML string.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'DashboardState' | — |
| `out_html` | — | — |
