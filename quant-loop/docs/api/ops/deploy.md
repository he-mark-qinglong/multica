# `_shared.ops.deploy`

Source: `_shared/ops/deploy.py`

Deployment unit generator (H11).

## class `DeploySpec(strategy: 'str', argv: 'Tuple[str, ...]', working_dir: 'str', log_dir: 'str', env: 'Mapping[str, str]' = <factory>, user: 'str' = '', restart_sec: 'int' = 5) -> None`

One strategy's deployment shape.

## `placeholder_env(*names: 'str') -> 'Mapping[str, str]'`

Map VAR -> "${VAR}" so secrets are injected at load time, not written.

| Parameter | Type | Default |
|---|---|---|
| `names` | 'str' | — |

## `render_launchd_plist(spec: 'DeploySpec') -> 'str'`

Render a launchd .plist (XML). Pure.

| Parameter | Type | Default |
|---|---|---|
| `spec` | 'DeploySpec' | — |

## `render_systemd_unit(spec: 'DeploySpec') -> 'str'`

Render a systemd .service unit. Pure.

| Parameter | Type | Default |
|---|---|---|
| `spec` | 'DeploySpec' | — |

## `write_unit(spec: 'DeploySpec', out_path, platform: 'Optional[str]' = None) -> 'Path'`

Write the unit for ``platform`` ("systemd" | "launchd") to ``out_path``.

| Parameter | Type | Default |
|---|---|---|
| `spec` | 'DeploySpec' | — |
| `out_path` | — | — |
| `platform` | 'Optional[str]' | None |
