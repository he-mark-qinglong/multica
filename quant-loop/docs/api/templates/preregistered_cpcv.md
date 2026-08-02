# `_shared.templates.preregistered_cpcv`

Source: `_shared/templates/preregistered_cpcv.py`

Pre-registered CPCV evaluation template (Phase D — HF pipeline).

## `decide_chosen(results: 'Sequence[dict]', gates: 'dict | None' = None) -> 'tuple[dict | None, str]'`

Pick the chosen candidate WITHOUT re-ranking on OOS metrics.

| Parameter | Type | Default |
|---|---|---|
| `results` | 'Sequence[dict]' | — |
| `gates` | 'dict | None' | None |

## `evaluate_candidate(candidate: 'dict', data: 'pd.DataFrame', signal_fn: 'SignalFn', cpcv_config: 'dict | None' = None, n_trials: 'int | None' = None) -> 'dict'`

Evaluate one pre-registered candidate through the shared CPCV harness.

| Parameter | Type | Default |
|---|---|---|
| `candidate` | 'dict' | — |
| `data` | 'pd.DataFrame' | — |
| `signal_fn` | 'SignalFn' | — |
| `cpcv_config` | 'dict | None' | None |
| `n_trials` | 'int | None' | None |

## `run_preregistered_cpcv(candidates: 'Sequence[dict]', data: 'pd.DataFrame', signal_fn: 'SignalFn', cpcv_config: 'dict | None' = None, gates: 'dict | None' = None) -> 'dict'`

Evaluate the full pre-registered candidate set and decide the verdict.

| Parameter | Type | Default |
|---|---|---|
| `candidates` | 'Sequence[dict]' | — |
| `data` | 'pd.DataFrame' | — |
| `signal_fn` | 'SignalFn' | — |
| `cpcv_config` | 'dict | None' | None |
| `gates` | 'dict | None' | None |

## `write_results(envelope: 'dict', out_dir: 'str | Path') -> 'list[Path]'`

Write ``cpcv_metrics.json`` + ``cpcv_summary.txt``; returns paths.

| Parameter | Type | Default |
|---|---|---|
| `envelope` | 'dict' | — |
| `out_dir` | 'str | Path' | — |
