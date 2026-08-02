# `_shared.market_making.adverse_selection`

Source: `_shared/market_making/adverse_selection.py`

Adverse-selection guard for market making.

## class `AdverseSelectionParams(fill_penalty_bp: 'float' = 1.0, penalty_decay_per_second: 'float' = 0.5, sweep_threshold: 'int' = 3, sweep_cooldown_seconds: 'float' = 5.0, max_penalty_bp: 'float' = 10.0, expected_sweep_cost_bp: 'float' = 1.74) -> None`

Tunable thresholds.

## class `AdverseSelectionState(last_fill_side: 'str | None' = None, last_fill_ts: 'pd.Timestamp | None' = None, consecutive_same_side: 'int' = 0, penalty_bp: 'float' = 0.0, cooldown_until: 'pd.Timestamp | None' = None) -> None`

Mutable-by-copy tracking of recent fill pressure.

## `belief_update(prior_fair_value: 'float', fill_side: 'str', expected_sweep_cost_bp: 'float' = 1.74) -> 'float'`

Bayesian belief update — shift fair value against the fill direction.

| Parameter | Type | Default |
|---|---|---|
| `prior_fair_value` | 'float' | — |
| `fill_side` | 'str' | — |
| `expected_sweep_cost_bp` | 'float' | 1.74 |

## `decay_penalty(state: 'AdverseSelectionState', current_ts: 'pd.Timestamp', params: 'AdverseSelectionParams') -> 'AdverseSelectionState'`

Apply time-based decay to the spread penalty.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'AdverseSelectionState' | — |
| `current_ts` | 'pd.Timestamp' | — |
| `params` | 'AdverseSelectionParams' | — |

## `empty_state() -> 'AdverseSelectionState'`

Factory for a fresh guard.

## `is_quoting_allowed(state: 'AdverseSelectionState', current_ts: 'pd.Timestamp') -> 'bool'`

``False`` during the cooldown window.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'AdverseSelectionState' | — |
| `current_ts` | 'pd.Timestamp' | — |

## `on_fill(state: 'AdverseSelectionState', fill_side: 'str', fill_ts: 'pd.Timestamp', params: 'AdverseSelectionParams') -> 'AdverseSelectionState'`

Process a fill event.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'AdverseSelectionState' | — |
| `fill_side` | 'str' | — |
| `fill_ts` | 'pd.Timestamp' | — |
| `params` | 'AdverseSelectionParams' | — |
