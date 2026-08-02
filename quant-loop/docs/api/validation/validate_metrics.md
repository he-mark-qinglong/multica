# `_shared.validation.validate_metrics`

Source: `_shared/validation/validate_metrics.py`

Validate metrics.json against the 9-key schema + provenance fields.

## `check_provenance(payload: 'Any', keys: 'Iterable[str]' = ('strategy', 'cost_bps_rt', 'data_window', 'generated_at')) -> 'list[str]'`

Return WARN-grade strings for missing provenance fields.

| Parameter | Type | Default |
|---|---|---|
| `payload` | 'Any' | — |
| `keys` | 'Iterable[str]' | ('strategy', 'cost_bps_rt', 'data_window', 'generated_at') |

## `main(argv: 'list[str] | None' = None) -> 'int'`

CLI entry point. See module docstring + ``--help`` for usage.

| Parameter | Type | Default |
|---|---|---|
| `argv` | 'list[str] | None' | None |

## `validate_metrics(payload: 'Any') -> 'list[str]'`

Return schema violation strings; empty list means PASS.

| Parameter | Type | Default |
|---|---|---|
| `payload` | 'Any' | — |
