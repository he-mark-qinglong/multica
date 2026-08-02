# `_shared.swarm.upload_artifacts`

Source: `_shared/swarm/upload_artifacts.py`

Swarm-run artifact uploader: push collected files to multica + post an EVIDENCE receipt.

## `main(argv: 'list[str] | None' = None) -> 'int'`

| Parameter | Type | Default |
|---|---|---|
| `argv` | 'list[str] | None' | None |

## `valid_comment_first_line(line: 'str') -> 'bool'`

Return True iff ``line`` matches the AGENTS.md comment-schema first line.

| Parameter | Type | Default |
|---|---|---|
| `line` | 'str' | — |
