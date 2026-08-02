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

## `certify_strategy(metrics_path: str | pathlib.Path, n_trials: int | None = None, ledger_path: str | pathlib.Path | None = None) -> _shared.gates.enforce.GateResult`

Read metrics.json + compute DSR if not present, then certify.

When DSR must be computed, the real trial count is required — resolution
order: explicit `n_trials` argument → `n_trials` field in metrics.json →
results-ledger family size. If none resolve, certification FAILS explicitly
with reason `MISSING_N_TRIALS` (the old hard-coded default of 100 is gone).

| Parameter | Type | Default |
|---|---|---|
| `metrics_path` | str | pathlib.Path | — |
| `n_trials` | int | None | — |
| `ledger_path` | str | pathlib.Path | None | — |

## `main()`

CLI: python -m _shared.gates.enforce <metrics.json> [n_trials]
