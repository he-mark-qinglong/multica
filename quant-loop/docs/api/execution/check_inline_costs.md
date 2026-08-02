# `_shared.execution.check_inline_costs`

Source: `_shared/execution/check_inline_costs.py`

Inline-cost scanner — read-only drift detector for quant-loop strategies.

## class `Violation(path: ForwardRef('Path'), lineno: ForwardRef('int'), line: ForwardRef('str'), pattern: ForwardRef('str'), matched: ForwardRef('str'))`

Violation(path, lineno, line, pattern, matched)

### `render(self) -> 'str'`

## `is_exempt(path: 'Path') -> 'bool'`

Return True if `path` is exempt from the inline-cost scan.

| Parameter | Type | Default |
|---|---|---|
| `path` | 'Path' | — |

## `iter_targets(root: 'Path') -> 'Iterator[Path]'`

Yield every ``*.py`` file under ``root`` that passes ``is_exempt``.

| Parameter | Type | Default |
|---|---|---|
| `root` | 'Path' | — |

## `main(argv: 'List[str] | None' = None) -> 'int'`

| Parameter | Type | Default |
|---|---|---|
| `argv` | 'List[str] | None' | None |

## `scan(root: 'Path' = PosixPath('/Users/mark/multica/quant-loop/strategies')) -> 'List[Violation]'`

Scan every non-exempt file under ``root`` and return violations.

| Parameter | Type | Default |
|---|---|---|
| `root` | 'Path' | PosixPath('/Users/mark/multica/quant-loop/strategies') |

## `scan_file(path: 'Path') -> 'List[Violation]'`

Return every cost-literal violation in ``path``.

| Parameter | Type | Default |
|---|---|---|
| `path` | 'Path' | — |
