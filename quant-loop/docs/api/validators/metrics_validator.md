# `_shared.validators.metrics_validator`

Source: `_shared/validators/metrics_validator.py`

Range and sentinel validator for strategy metrics.json.

## `safe_validate(metrics: dict, strategy_name: str = '<unknown>') -> tuple[bool, str]`

Non-raising variant. Returns (ok, message).

| Parameter | Type | Default |
|---|---|---|
| `metrics` | dict | — |
| `strategy_name` | str | '<unknown>' |

## `validate_metrics(metrics: dict[str, typing.Any], strategy_name: str = '<unknown>') -> None`

Raise AssertionError if any metric is NaN, inf, sentinel, or out of expected range.

| Parameter | Type | Default |
|---|---|---|
| `metrics` | dict[str, typing.Any] | — |
| `strategy_name` | str | '<unknown>' |
