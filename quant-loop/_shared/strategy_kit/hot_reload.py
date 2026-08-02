"""Config hot-reload — swap strategy parameters without restarting the loop.

Watches a JSON config file's mtime. On change: parse -> validate -> invoke
the ``on_reload`` callback with the new config. Any failure (bad JSON,
validator rejection, callback exception) leaves the *previous good config*
active — the reload is rolled back, never half-applied. This mirrors how
long-running market-making daemons (e.g. the paper runner's kill-switch
params) must retune without dropping their order-book state.

Design is deliberately polling-based and synchronous (``check_once``) so
callers control the cadence from their own event loop and tests are
deterministic — no background threads, no watchdog dependency.

References:
- Erb & Harvey (2006) "The Strategic and Tactical Value of Commodity
  Futures" — motivation for regime-dependent parameter retuning.
- Project convention: ``_shared/paper/runner.py`` kill-switch params are
  the canonical consumer (retune PF floor / DD multiplier without restart).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ConfigDict = Dict[str, Any]
Validator = Callable[[ConfigDict], None]  # raises on invalid
OnReload = Callable[[ConfigDict], None]   # raises -> rollback


@dataclass(frozen=True)
class ReloadEvent:
    """Record of one reload attempt (returned by ``check_once``)."""
    changed: bool          # mtime changed since last check
    applied: bool          # new config is now active
    config: ConfigDict     # config currently active after the attempt
    error: Optional[str]   # failure reason when applied=False and changed=True


class ConfigReloader:
    """Poll-based JSON config watcher with validate-then-swap semantics.

    Usage::

        reloader = ConfigReloader(
            path="config.json",
            on_reload=lambda cfg: strategy.update_params(cfg),
            validator=lambda cfg: (_ for _ in ()).throw(ValueError("bad"))
                                  if cfg.get("risk", 0) <= 0 else None,
        )
        reloader.load_initial()          # raises if the initial file is bad
        ...
        event = reloader.check_once()    # call from the strategy loop
    """

    def __init__(
        self,
        path: os.PathLike | str,
        on_reload: OnReload,
        validator: Optional[Validator] = None,
    ) -> None:
        self._path = Path(path)
        self._on_reload = on_reload
        self._validator = validator
        self._config: Optional[ConfigDict] = None
        self._mtime_ns: Optional[int] = None

    # ----- properties -----------------------------------------------------

    @property
    def config(self) -> Optional[ConfigDict]:
        """Currently active config (None until first successful load)."""
        return self._config

    @property
    def path(self) -> Path:
        return self._path

    # ----- internals --------------------------------------------------------

    def _read_and_validate(self) -> ConfigDict:
        with self._path.open("r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            raise ValueError(
                f"config root must be a JSON object, got {type(cfg).__name__}"
            )
        if self._validator is not None:
            self._validator(cfg)
        return cfg

    def _stat_mtime_ns(self) -> Optional[int]:
        try:
            return self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    # ----- public API ---------------------------------------------------------

    def load_initial(self) -> ConfigDict:
        """Load + validate the config for the first time.

        Unlike ``check_once``, a bad *initial* config is fatal (there is no
        previous good state to roll back to), so this raises.
        """
        cfg = self._read_and_validate()
        self._on_reload(cfg)
        self._config = cfg
        self._mtime_ns = self._stat_mtime_ns()
        return cfg

    def check_once(self) -> ReloadEvent:
        """Check mtime once; reload if changed. Safe to call in a hot loop.

        Rollback semantics: if parsing, validation, or the ``on_reload``
        callback fails, the previously active config stays active and the
        error is reported in the returned event. The failed file's mtime is
        still recorded so a broken edit is retried only after the file
        changes again (no hot-loop error spam).
        """
        mtime = self._stat_mtime_ns()
        if mtime is None:
            return ReloadEvent(
                changed=False,
                applied=False,
                config=self._config if self._config is not None else {},
                error=f"config file missing: {self._path}",
            )
        if self._config is not None and mtime == self._mtime_ns:
            return ReloadEvent(changed=False, applied=False,
                               config=self._config, error=None)

        first_load = self._config is None
        try:
            cfg = self._read_and_validate()
            self._on_reload(cfg)  # callback runs BEFORE swap; raise = rollback
        except Exception as exc:  # noqa: BLE001 — any failure rolls back
            self._mtime_ns = mtime
            if first_load:
                # No prior good config: stay empty, surface the error.
                return ReloadEvent(changed=True, applied=False,
                                   config={}, error=str(exc))
            return ReloadEvent(changed=True, applied=False,
                               config=self._config, error=str(exc))

        self._config = cfg
        self._mtime_ns = mtime
        return ReloadEvent(changed=True, applied=True, config=cfg, error=None)

    def watch(self, poll_seconds: float = 1.0,
              stop: Optional[Callable[[], bool]] = None) -> None:
        """Blocking poll loop. ``stop()`` returning True ends the loop.

        Thin convenience wrapper for daemons; strategy loops should prefer
        calling ``check_once`` directly from their own cadence.
        """
        import time

        while True:
            self.check_once()
            if stop is not None and stop():
                return
            time.sleep(poll_seconds)
