# `_shared.market_making.quote_throttle`

Source: `_shared/market_making/quote_throttle.py`

Quote throttle — exchange rate-limit guard for market-making loops.

## class `ThrottleDecision(allowed: 'bool', reason: 'str', state: 'ThrottleState') -> None`

Result of one ``should_quote`` call.

## class `ThrottleParams(min_interval_seconds: 'float' = 0.5, max_amends_per_minute: 'int' = 40, burst_limit: 'int' = 8, burst_window_seconds: 'float' = 2.0, cooldown_seconds: 'float' = 5.0) -> None`

Tunables for the throttle state machine.

## class `ThrottleState(phase: 'str' = 'READY', quote_times: 'tuple[float, ...]' = (), cooldown_until: 'float' = 0.0) -> None`

Immutable throttle state.

## `initial_state() -> 'ThrottleState'`

Fresh state: READY, no history.

## `should_quote(now: 'float', state: 'ThrottleState', params: 'ThrottleParams') -> 'ThrottleDecision'`

Decide whether a quote update may be sent at ``now`` (epoch seconds).

| Parameter | Type | Default |
|---|---|---|
| `now` | 'float' | — |
| `state` | 'ThrottleState' | — |
| `params` | 'ThrottleParams' | — |
