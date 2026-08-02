# `_shared.market_making.online_adverse`

Source: `_shared/market_making/online_adverse.py`

Online learning of adverse selection cost.

## class `OnlineASParams(alpha: 'float' = 0.05, min_observations: 'int' = 10, prior_bp: 'float' = 1.74, min_cost_bp: 'float' = 0.0, max_cost_bp: 'float' = 20.0) -> None`

Online adverse selection learning parameters.

## class `OnlineASState(learned_cost_bp: 'float', n_observations: 'int', last_update_ts: 'pd.Timestamp | None' = None) -> None`

Mutable (by convention) state for online learning.

## `adaptive_belief_update(prior_fair_value: 'float', fill_side: 'str', state: 'OnlineASState', params: 'OnlineASParams' = OnlineASParams(alpha=0.05, min_observations=10, prior_bp=1.74, min_cost_bp=0.0, max_cost_bp=20.0)) -> 'float'`

Like belief_update but uses the *learned* cost instead of fixed 1.74bp.

| Parameter | Type | Default |
|---|---|---|
| `prior_fair_value` | 'float' | — |
| `fill_side` | 'str' | — |
| `state` | 'OnlineASState' | — |
| `params` | 'OnlineASParams' | OnlineASParams(alpha=0.05, min_observations=10, prior_bp=1.74, min_cost_bp=0.0, max_cost_bp=20.0) |

## `get_effective_cost(state: 'OnlineASState', params: 'OnlineASParams' = OnlineASParams(alpha=0.05, min_observations=10, prior_bp=1.74, min_cost_bp=0.0, max_cost_bp=20.0)) -> 'float'`

Get the effective adverse selection cost for quoting decisions.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'OnlineASState' | — |
| `params` | 'OnlineASParams' | OnlineASParams(alpha=0.05, min_observations=10, prior_bp=1.74, min_cost_bp=0.0, max_cost_bp=20.0) |

## `init_online_as(params: 'OnlineASParams' = OnlineASParams(alpha=0.05, min_observations=10, prior_bp=1.74, min_cost_bp=0.0, max_cost_bp=20.0)) -> 'OnlineASState'`

Initialize with the T10 prior.

| Parameter | Type | Default |
|---|---|---|
| `params` | 'OnlineASParams' | OnlineASParams(alpha=0.05, min_observations=10, prior_bp=1.74, min_cost_bp=0.0, max_cost_bp=20.0) |

## `observe_fill(state: 'OnlineASState', observed_markout_bp: 'float', fill_ts: 'pd.Timestamp', params: 'OnlineASParams' = OnlineASParams(alpha=0.05, min_observations=10, prior_bp=1.74, min_cost_bp=0.0, max_cost_bp=20.0)) -> 'OnlineASState'`

Update the learned adverse selection cost with a new observation.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'OnlineASState' | — |
| `observed_markout_bp` | 'float' | — |
| `fill_ts` | 'pd.Timestamp' | — |
| `params` | 'OnlineASParams' | OnlineASParams(alpha=0.05, min_observations=10, prior_bp=1.74, min_cost_bp=0.0, max_cost_bp=20.0) |
