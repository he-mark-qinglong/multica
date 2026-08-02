# `_shared.strategy_kit.doc_links`

Source: `_shared/strategy_kit/doc_links.py`

Research-document linkage for strategy directories (metric A20).

## class `StrategyManifest(strategy: 'str', status: 'str' = 'research', parent_research: 'Optional[str]' = None, research_docs: 'Tuple[str, ...]' = (), notes: 'str' = '') -> None`

Research linkage of one strategy directory.

## `discover_docs(strategy_dir: 'Path | str') -> 'Tuple[str, ...]'`

Auto-discover research docs inside a strategy directory: every top-level ``*.md`` file plus anything under a local ``docs/`` dir, as paths relative to the strategy directory (sorted).

| Parameter | Type | Default |
|---|---|---|
| `strategy_dir` | 'Path | str' | — |

## `ensure_manifest(strategy_dir: 'Path | str', status: 'str' = 'research', parent_research: 'Optional[str]' = None, notes: 'str' = '') -> 'StrategyManifest'`

Return the existing manifest, or create one with auto-discovered ``research_docs`` and write it. The only write function a scan-adjacent workflow should need.

| Parameter | Type | Default |
|---|---|---|
| `strategy_dir` | 'Path | str' | — |
| `status` | 'str' | 'research' |
| `parent_research` | 'Optional[str]' | None |
| `notes` | 'str' | '' |

## `read_manifest(strategy_dir: 'Path | str') -> 'Optional[StrategyManifest]'`

Read ``strategy.manifest.json``; None when absent. Raises ValueError on a malformed manifest.

| Parameter | Type | Default |
|---|---|---|
| `strategy_dir` | 'Path | str' | — |

## `render_markdown(rows: 'List[StrategyManifest]') -> 'str'`

Render the strategy ↔ research-document association table.

| Parameter | Type | Default |
|---|---|---|
| `rows` | 'List[StrategyManifest]' | — |

## `scan_markdown(root: 'Path | str') -> 'str'`

Scan a strategies root and return the markdown association table.

| Parameter | Type | Default |
|---|---|---|
| `root` | 'Path | str' | — |

## `scan_strategies(root: 'Path | str') -> 'List[StrategyManifest]'`

One :class:`StrategyManifest` row per strategy directory under ``root`` (non-recursive beyond one level). Directories without a manifest get an in-memory default with auto-discovered docs — nothing is written. Skips private dirs (``_graveyard``, ``.x``) and non-strategy dirs (``reports/``).

| Parameter | Type | Default |
|---|---|---|
| `root` | 'Path | str' | — |

## `write_manifest(strategy_dir: 'Path | str', manifest: 'StrategyManifest') -> 'Path'`

Write ``strategy.manifest.json`` (overwrites). Returns the path.

| Parameter | Type | Default |
|---|---|---|
| `strategy_dir` | 'Path | str' | — |
| `manifest` | 'StrategyManifest' | — |
