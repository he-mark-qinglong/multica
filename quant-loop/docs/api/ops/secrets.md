# `_shared.ops.secrets`

Source: `_shared/ops/secrets.py`

API key management (H13).

## class `RedactFilter(secrets: 'Sequence[Secret]')`

logging.Filter that scrubs registered key values from every record.

### `filter(self, record: 'logging.LogRecord') -> 'bool'`

| Parameter | Type | Default |
|---|---|---|
| `record` | 'logging.LogRecord' | — |

## class `Secret(name: 'str', _value: 'str') -> None`

An API key whose textual representation is always redacted.

### `masked(self) -> 'str'`

First 4 chars + mask; fully masked when too short to prefix.

### `reveal(self) -> 'str'`

The raw key value. Use only at the signing/HTTP boundary.

## `load_secret(name: 'str', env: 'Optional[Mapping[str, str]]' = None, file_path=None, prompt_fn: 'Optional[Callable[[str], str]]' = None) -> 'Secret'`

Resolve a key by priority: environment > .env file > prompt.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `env` | 'Optional[Mapping[str, str]]' | None |
| `file_path` | — | None |
| `prompt_fn` | 'Optional[Callable[[str], str]]' | None |

## `parse_env_file(path) -> 'Mapping[str, str]'`

Parse a .env file into a dict. Pure.

| Parameter | Type | Default |
|---|---|---|
| `path` | — | — |

## `redact(text: 'str', secrets: 'Sequence[Secret]') -> 'str'`

Replace every registered key value in ``text`` with its mask. Pure.

| Parameter | Type | Default |
|---|---|---|
| `text` | 'str' | — |
| `secrets` | 'Sequence[Secret]' | — |
