# `_shared.strategy_kit.hot_reload`

Source: `_shared/strategy_kit/hot_reload.py`

Config hot-reload — swap strategy parameters without restarting the loop.

## class `ConfigReloader(path: 'os.PathLike | str', on_reload: 'OnReload', validator: 'Optional[Validator]' = None) -> 'None'`

Poll-based JSON config watcher with validate-then-swap semantics.

### `check_once(self) -> 'ReloadEvent'`

Check mtime once; reload if changed. Safe to call in a hot loop.

### `load_initial(self) -> 'ConfigDict'`

Load + validate the config for the first time.

### `watch(self, poll_seconds: 'float' = 1.0, stop: 'Optional[Callable[[], bool]]' = None) -> 'None'`

Blocking poll loop. ``stop()`` returning True ends the loop.

| Parameter | Type | Default |
|---|---|---|
| `poll_seconds` | 'float' | 1.0 |
| `stop` | 'Optional[Callable[[], bool]]' | None |

## class `ReloadEvent(changed: 'bool', applied: 'bool', config: 'ConfigDict', error: 'Optional[str]') -> None`

Record of one reload attempt (returned by ``check_once``).
