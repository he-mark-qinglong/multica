"""Ops-layer config hot-reload with audit log and rollback (H8).

Wraps ``_shared/strategy_kit/hot_reload.ConfigReloader`` for ops configs
(alert thresholds, kill-switch params, exposure limits) and adds the two
things the raw reloader deliberately leaves out:

  * an append-only JSONL audit log — every reload attempt records
    ``(ts, source, applied, diff of changed fields, error)``;
  * version history with rollback — any previously applied config can be
    restored by index or timestamp (implemented by writing the historical
    config back to the watched file and letting the reloader re-apply it,
    so the file and the active config never diverge).

References:
- Google SRE Book, ch. 14 "Managing Incidents" — config changes are a
  leading cause of outages; every change must be attributable and
  reversible (audit log + rollback here).
- Nygard, "Release It!", ch. 4 "Stability Antipatterns" — fail-fast
  validation: a bad config is rejected before it can take effect.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from _shared.strategy_kit.hot_reload import (
    ConfigDict,
    ConfigReloader,
    OnReload,
    ReloadEvent,
    Validator,
)

OPS_NUMERIC_SECTIONS: Tuple[str, ...] = (
    "alert_thresholds",
    "kill_switch",
    "exposure_limits",
)


def validate_ops_config(cfg: ConfigDict) -> None:
    """Default validator for ops configs. Raises ValueError on bad input.

    The three known sections (alert_thresholds / kill_switch /
    exposure_limits), when present, must be flat mappings of
    non-negative numbers (or null = disabled). Unknown top-level keys are
    left to the caller's own validator.
    """
    for section in OPS_NUMERIC_SECTIONS:
        if section not in cfg:
            continue
        val = cfg[section]
        if not isinstance(val, dict):
            raise ValueError(f"{section} must be an object, got {type(val).__name__}")
        for key, v in val.items():
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(
                    f"{section}.{key} must be a non-negative number or null, "
                    f"got {v!r}"
                )
            if v < 0:
                raise ValueError(f"{section}.{key} must be >= 0, got {v}")


def diff_configs(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    prefix: str = "",
) -> Dict[str, Dict[str, Any]]:
    """Flat dotted-path diff of two configs: path -> {"old", "new"}. Pure."""
    out: Dict[str, Dict[str, Any]] = {}
    for key in sorted(set(old) | set(new)):
        path = f"{prefix}{key}"
        in_old, in_new = key in old, key in new
        if in_old and in_new:
            ov, nv = old[key], new[key]
            if isinstance(ov, dict) and isinstance(nv, dict):
                out.update(diff_configs(ov, nv, path + "."))
            elif ov != nv:
                out[path] = {"old": ov, "new": nv}
        elif in_new:
            if isinstance(new[key], dict):
                out.update(diff_configs({}, new[key], path + "."))
            else:
                out[path] = {"old": None, "new": new[key]}
        else:
            if isinstance(old[key], dict):
                out.update(diff_configs(old[key], {}, path + "."))
            else:
                out[path] = {"old": old[key], "new": None}
    return out


@dataclass(frozen=True)
class ConfigVersion:
    """One applied config version (the rollback target unit)."""

    ts: float
    source: str
    config: ConfigDict = field(default_factory=dict)


@dataclass(frozen=True)
class AuditEntry:
    """One audit-log record: every reload attempt, applied or not."""

    ts: float
    source: str            # "initial" | "file" | "rollback"
    applied: bool
    diff: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    error: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": self.ts,
                "source": self.source,
                "applied": self.applied,
                "diff": dict(self.diff),
                "error": self.error,
            },
            sort_keys=True,
            default=str,
        )


class OpsConfigReloader:
    """ConfigReloader + audit log + versioned rollback.

    Usage::

        hot = OpsConfigReloader(
            path="ops_config.json",
            audit_path="ops_config_audit.jsonl",
            on_reload=lambda cfg: runner.apply_ops_params(cfg),
        )
        hot.load_initial()
        ...
        hot.check_once()          # from the ops loop; audited
        hot.rollback_to(index=0)  # restore the first applied version
    """

    def __init__(
        self,
        path,
        audit_path,
        on_reload: OnReload,
        validator: Optional[Validator] = validate_ops_config,
    ) -> None:
        self._audit_path = Path(audit_path)
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._reloader = ConfigReloader(
            path, on_reload=on_reload, validator=validator
        )
        self._history: List[ConfigVersion] = []

    # ----- introspection ---------------------------------------------------
    @property
    def config(self) -> Optional[ConfigDict]:
        """Currently active config (None until first successful load)."""
        return self._reloader.config

    @property
    def history(self) -> Tuple[ConfigVersion, ...]:
        return tuple(self._history)

    # ----- audit -----------------------------------------------------------
    def _record(
        self,
        source: str,
        applied: bool,
        old: Optional[Mapping[str, Any]],
        new: Optional[Mapping[str, Any]],
        error: Optional[str],
        ts: Optional[float] = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            ts=time.time() if ts is None else float(ts),
            source=source,
            applied=applied,
            diff=diff_configs(old or {}, new or {}) if new is not None else {},
            error=error,
        )
        with self._audit_path.open("a", encoding="utf-8") as fh:
            fh.write(entry.to_json() + "\n")
        return entry

    def read_audit_log(self) -> Tuple[AuditEntry, ...]:
        """Parse the JSONL audit log back into AuditEntry records."""
        entries = []
        try:
            lines = self._audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ()
        for line in lines:
            if not line.strip():
                continue
            raw = json.loads(line)
            entries.append(
                AuditEntry(
                    ts=float(raw["ts"]),
                    source=str(raw["source"]),
                    applied=bool(raw["applied"]),
                    diff=dict(raw.get("diff", {})),
                    error=raw.get("error"),
                )
            )
        return tuple(entries)

    # ----- reload paths ------------------------------------------------------
    def load_initial(self, source: str = "initial") -> ConfigDict:
        """Load + validate the initial config; audited; raises when bad."""
        cfg = self._reloader.load_initial()
        ts = time.time()
        self._history.append(ConfigVersion(ts=ts, source=source, config=dict(cfg)))
        self._record(source, True, None, cfg, None, ts)
        return cfg

    def check_once(self, source: str = "file") -> ReloadEvent:
        """Check the file once; audit the attempt, keep history on success."""
        prev = self._reloader.config
        event = self._reloader.check_once()
        if not event.changed:
            return event
        ts = time.time()
        if event.applied:
            self._history.append(
                ConfigVersion(ts=ts, source=source, config=dict(event.config))
            )
            self._record(source, True, prev, event.config, None, ts)
        else:
            attempted: Optional[Mapping[str, Any]] = None
            try:
                raw = json.loads(self._reloader.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    attempted = raw
            except (OSError, json.JSONDecodeError):
                attempted = None
            self._record(source, False, prev, attempted, event.error, ts)
        return event

    # ----- rollback ----------------------------------------------------------
    def rollback_to(
        self,
        index: Optional[int] = None,
        ts: Optional[float] = None,
    ) -> ReloadEvent:
        """Restore a historical version by history index or exact timestamp.

        The historical config is written back to the watched file and
        re-applied through the normal validate-then-swap path, so a
        rollback is itself validated, audited (source="rollback"), and
        becomes the newest history entry.
        """
        if not self._history:
            raise ValueError("no applied config versions to roll back to")
        if ts is not None:
            matches = [v for v in self._history if v.ts == float(ts)]
            if not matches:
                raise ValueError(f"no config version with ts={ts}")
            version = matches[-1]
        else:
            idx = 0 if index is None else int(index)
            try:
                version = self._history[idx]
            except IndexError:
                raise ValueError(
                    f"no config version at index {idx} "
                    f"(history has {len(self._history)} versions)"
                ) from None
        target = json.loads(json.dumps(version.config))  # deep copy
        self._reloader.path.write_text(
            json.dumps(target, indent=2, sort_keys=True), encoding="utf-8"
        )
        return self.check_once(source="rollback")
