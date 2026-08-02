# `_shared.ops_profile`

Source: `_shared/ops_profile.py`

Performance profiling utilities (J18).

## class `SectionStats(name: 'str', calls: 'int', total_seconds: 'float', stats: 'pstats.Stats') -> None`

Aggregated profiling stats for one named section.

## `main() -> 'None'`

Profile ``simulate_market_making`` on a synthetic trade tape.

## `profile_callable(fn: 'Callable', *args: 'Any', _section: 'Optional[str]' = None, **kwargs: 'Any') -> 'tuple[Any, str]'`

Run ``fn`` under cProfile; return ``(result, table)``.

| Parameter | Type | Default |
|---|---|---|
| `fn` | 'Callable' | — |
| `args` | 'Any' | — |
| `_section` | 'Optional[str]' | None |
| `kwargs` | 'Any' | — |

## `profile_section(name: 'str') -> 'Callable'`

Decorator: profile every call under section ``name``.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |

## `report(top_n: 'int' = 30) -> 'str'`

Function-level cumulative-time table for all registered sections.

| Parameter | Type | Default |
|---|---|---|
| `top_n` | 'int' | 30 |

## `reset() -> 'None'`

Clear all accumulated section stats.
