# `_shared.portfolio.lifecycle`

Source: `_shared/portfolio/lifecycle.py`

Strategy lifecycle state machine (I16).

## class `LifecycleManager(audit_path: 'str | Path | None' = None, rules: 'Mapping[Tuple[LifecycleState, LifecycleState], TransitionRule] | None' = None)`

Tracks strategy states and enforces the transition table.

### `register(self, strategy_id: 'str') -> 'None'`

Enter a strategy into the funnel at REGISTERED.

| Parameter | Type | Default |
|---|---|---|
| `strategy_id` | 'str' | — |

### `state(self, strategy_id: 'str') -> 'LifecycleState'`

| Parameter | Type | Default |
|---|---|---|
| `strategy_id` | 'str' | — |

### `transition(self, strategy_id: 'str', to_state: 'LifecycleState', metrics: 'StrategyMetrics' = StrategyMetrics(sharpe=None, max_drawdown=None), ts: 'float | None' = None) -> 'TransitionRecord'`

Attempt ``strategy_id`` -> ``to_state``.

| Parameter | Type | Default |
|---|---|---|
| `strategy_id` | 'str' | — |
| `to_state` | 'LifecycleState' | — |
| `metrics` | 'StrategyMetrics' | StrategyMetrics(sharpe=None, max_drawdown=None) |
| `ts` | 'float | None' | None |

## class `LifecycleState(*values)`

## class `StrategyMetrics(sharpe: 'float | None' = None, max_drawdown: 'float | None' = None) -> None`

Evidence bundle a transition rule evaluates.

## class `TransitionRecord(ts: 'float', strategy_id: 'str', from_state: 'str', to_state: 'str', accepted: 'bool', reason: 'str') -> None`

Audit record of one attempted transition.

## class `TransitionRule(to_state: 'LifecycleState', condition: 'Optional[Callable[[StrategyMetrics], bool]]' = None, description: 'str' = '') -> None`

Gate on one transition. ``condition=None`` = unconditional.
