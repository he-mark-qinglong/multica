# `_shared.swarm.accept`

Source: `_shared/swarm/accept.py`

Mechanical acceptance executor for swarm runs (SMA-36514 / W5-T12).

## `main(argv: 'list[str] | None' = None) -> 'int'`

| Parameter | Type | Default |
|---|---|---|
| `argv` | 'list[str] | None' | None |

## `run_acceptance(run_dir: 'str | Path', timeout_override: 'int | None' = None) -> 'int'`

Run acceptance for the swarm run at ``run_dir``.

| Parameter | Type | Default |
|---|---|---|
| `run_dir` | 'str | Path' | — |
| `timeout_override` | 'int | None' | None |
