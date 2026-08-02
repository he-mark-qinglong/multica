# `_shared.strategy_kit.registry`

Source: `_shared/strategy_kit/registry.py`

Indicator registry — single source of truth for named, schema-validated indicators used across strategy research.

## class `IndicatorNotFoundError`

Raised when ``get_indicator`` is asked for an unknown name.

## class `IndicatorSpec(name: 'str', func: 'Callable[..., pd.Series]', params: 'Mapping[str, ParamSpec]' = <factory>, description: 'str' = '', version: 'str' = '1.0.0', source: 'str' = '') -> None`

A registered indicator: callable + parameter schema + metadata.

## class `ParamSpec(type: 'str', required: 'bool' = False, default: 'Any' = None, min: 'Optional[float]' = None, max: 'Optional[float]' = None, choices: 'Optional[Tuple[Any, ...]]' = None) -> None`

Schema for a single indicator parameter.

## class `ParamValidationError`

Raised when caller params fail schema validation.

## `clear_registry() -> 'None'`

Drop all registrations. Test isolation only — never call in prod.

## `get_indicator(name: 'str', **params: 'Any') -> 'Callable[[Any], pd.Series]'`

Fetch indicator ``name`` with params validated against its schema.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `params` | 'Any' | — |

## `get_spec(name: 'str') -> 'IndicatorSpec'`

Return the full IndicatorSpec (schema + metadata) for ``name``.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |

## `list_indicators() -> 'Dict[str, str]'`

name -> description for every registered indicator.

## `register_builtins() -> 'None'`

Idempotently register the built-in example indicators.

## `register_indicator(name: 'str', params: 'Optional[Mapping[str, ParamSpec]]' = None, description: 'str' = '', version: 'str' = '1.0.0', source: 'str' = '', replace: 'bool' = False) -> 'Callable[[Callable[..., pd.Series]], Callable[..., pd.Series]]'`

Decorator: register ``func(data, **params) -> pd.Series`` under ``name``.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `params` | 'Optional[Mapping[str, ParamSpec]]' | None |
| `description` | 'str' | '' |
| `version` | 'str' | '1.0.0' |
| `source` | 'str' | '' |
| `replace` | 'bool' | False |
