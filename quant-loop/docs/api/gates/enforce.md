# `_shared.gates.enforce`

Source: `_shared/gates/enforce.py`

Gate enforcement — refuses to certify a strategy as SHIP-eligible if metrics fail G1-G7 + Wave 2 additions (CPCV + DSR).

## class `GateResult(passed: bool, failed_gates: list[str] = <factory>, reasons: list[str] = <factory>, metrics: dict = <factory>) -> None`

GateResult(passed: bool, failed_gates: list[str] = <factory>, reasons: list[str] = <factory>, metrics: dict = <factory>)

## `certify_metrics(metrics: dict, strict: bool = True) -> _shared.gates.enforce.GateResult`

Check a metrics dict against all gates.

| Parameter | Type | Default |
|---|---|---|
| `metrics` | dict | — |
| `strict` | bool | True |

## `certify_strategy(metrics_path: str | pathlib.Path, n_trials: int = 100) -> _shared.gates.enforce.GateResult`

Read metrics.json + compute DSR if not present, then certify.

| Parameter | Type | Default |
|---|---|---|
| `metrics_path` | str | pathlib.Path | — |
| `n_trials` | int | 100 |

## `main()`

CLI: python -m _shared.gates.enforce <metrics.json>
