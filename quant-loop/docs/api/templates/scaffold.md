# `_shared.templates.scaffold`

Source: `_shared/templates/scaffold.py`

Generate a contract-v2-compliant strategy directory.

## `main(argv: 'Sequence[str] | None' = None) -> 'int'`

| Parameter | Type | Default |
|---|---|---|
| `argv` | 'Sequence[str] | None' | None |

## `scaffold(name: 'str', symbols: 'Sequence[str]', tf: 'str' = '15m', out_root: 'str | Path' = PosixPath('/Users/mark/multica/quant-loop/strategies'), *, today: 'str | None' = None, spec_template_path: 'str | Path | None' = None) -> 'Path'`

Generate the strategy directory at ``<out_root>/<name>``.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `symbols` | 'Sequence[str]' | — |
| `tf` | 'str' | '15m' |
| `out_root` | 'str | Path' | PosixPath('/Users/mark/multica/quant-loop/strategies') |
| `today` | 'str | None' | None |
| `spec_template_path` | 'str | Path | None' | None |
