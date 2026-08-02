"""Portfolio re-optimization scheduler (I18).

Wraps :class:`_shared.market_making.dynamic_erc.DynamicERC` with a
scheduling and debounce layer:

  * **Schedule** — recompute weights only on schedule, not every bar:
    ``every_n_bars`` (bar count), ``daily`` (first bar of each new UTC
    date), and/or cron-style ``cron_times`` (first bar at/after each
    ``"HH:MM"`` UTC slot). :meth:`Reoptimizer.trigger_manual` bypasses
    the schedule for operator intervention.
  * **Debounce** — new weights are applied only when the max absolute
    weight change vs the currently applied weights exceeds
    ``weight_change_threshold``; below that the churn is not worth the
    transaction costs and the recompute is logged as skipped.
  * **Audit** — every fired trigger appends one JSON line with a
    covariance summary of the input window (mean pairwise correlation,
    mean asset vol, n observations), the per-asset weight diff, and
    whether it was applied.

The ERC params passed in should use ``rebalance_freq=1`` (the default
constructed here) — cadence belongs to this scheduler, not to the
inner DynamicERC, whose own frequency gate would double-count.

References:
  - Roncalli (2013), "Introduction to Risk Parity and Budgeting", Ch. 2
    (ERC weights from rolling covariance).
  - DeMiguel, Garlappi & Uppal (2009), RFS 22(5) (estimation error in
    covariance inputs — small weight deltas are noise, hence debounce).
"""
from __future__ import annotations

import json
import time as _time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from _shared.market_making.dynamic_erc import DynamicERC, DynamicERCParams


@dataclass(frozen=True)
class ReoptimizeConfig:
    """Schedule + debounce configuration."""

    every_n_bars: int | None = None          # fire when bar_index % N == 0
    daily: bool = False                      # fire on first bar of each UTC date
    cron_times: Tuple[str, ...] = ()         # "HH:MM" UTC slots
    weight_change_threshold: float = 0.01    # max |Δw| below this → skip apply
    erc_params: DynamicERCParams = field(
        default_factory=lambda: DynamicERCParams(rebalance_freq=1)
    )

    def __post_init__(self) -> None:
        if self.every_n_bars is not None and self.every_n_bars <= 0:
            raise ValueError("every_n_bars must be positive")
        if self.weight_change_threshold < 0:
            raise ValueError("weight_change_threshold must be >= 0")
        for t in self.cron_times:
            hh, _, mm = t.partition(":")
            if not (hh.isdigit() and mm.isdigit()
                    and 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                raise ValueError(f"cron_times entries must be 'HH:MM', got {t!r}")


@dataclass(frozen=True)
class ReoptRecord:
    """Audit record of one fired re-optimization trigger."""

    ts: str                            # ISO timestamp of the firing bar
    trigger: str                       # every_n_bars | daily | cron | manual
    cov_summary: Mapping[str, float]   # input covariance digest
    weight_diff: Mapping[str, float]   # new - current per asset
    applied: bool
    skip_reason: str                   # "" when applied
    weights: Mapping[str, float]       # current (applied or retained) weights


def _cov_summary(window: pd.DataFrame) -> Dict[str, float]:
    """Digest of the input window's covariance structure. Pure."""
    n = window.shape[1]
    if len(window) < 2 or n == 0:
        return {
            "mean_pairwise_corr": 0.0,
            "mean_asset_vol": 0.0,
            "n_assets": float(n),
            "n_observations": float(len(window)),
        }
    corr = window.corr().to_numpy()
    off_diag = corr[~np.eye(n, dtype=bool)] if n > 1 else np.array([0.0])
    off_diag = off_diag[~np.isnan(off_diag)]
    return {
        "mean_pairwise_corr": float(np.mean(off_diag)) if off_diag.size else 0.0,
        "mean_asset_vol": float(window.std().mean()),
        "n_assets": float(n),
        "n_observations": float(len(window)),
    }


class Reoptimizer:
    """Scheduled, debounced dynamic-ERC weight updater.

    Usage::

        ro = Reoptimizer(ReoptimizeConfig(every_n_bars=24),
                         audit_path="reopt.jsonl")
        for i, (ts, _) in enumerate(bars):
            rec = ro.on_bar(i, ts, returns_df.loc[:ts])
        weights = ro.weights
    """

    def __init__(
        self,
        config: ReoptimizeConfig,
        audit_path: str | Path | None = None,
        initial_weights: Optional[Mapping[str, float]] = None,
    ):
        self.config = config
        self._erc = DynamicERC(config.erc_params)
        self._weights: Dict[str, float] = dict(initial_weights or {})
        self._records: List[ReoptRecord] = []
        self._last_daily_date = None
        self._fired_slots: set = set()          # (date, "HH:MM") already fired
        self._audit_path = Path(audit_path) if audit_path is not None else None
        if self._audit_path is not None:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def weights(self) -> Dict[str, float]:
        return dict(self._weights)

    @property
    def records(self) -> List[ReoptRecord]:
        return list(self._records)

    def on_bar(
        self,
        bar_index: int,
        timestamp: pd.Timestamp,
        returns: pd.DataFrame,
    ) -> ReoptRecord | None:
        """Feed one bar; returns a record only when a schedule fired."""
        trigger = self._due(bar_index, timestamp)
        if trigger is None:
            return None
        return self._fire(trigger, timestamp, returns)

    def trigger_manual(
        self,
        timestamp: pd.Timestamp,
        returns: pd.DataFrame,
    ) -> ReoptRecord:
        """Operator-forced recompute, bypassing the schedule."""
        return self._fire("manual", timestamp, returns)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _due(self, bar_index: int, timestamp: pd.Timestamp) -> str | None:
        cfg = self.config
        if cfg.every_n_bars is not None and bar_index % cfg.every_n_bars == 0:
            return "every_n_bars"
        date = timestamp.date()
        if cfg.daily and date != self._last_daily_date:
            self._last_daily_date = date
            return "daily"
        for slot in cfg.cron_times:
            hh, mm = int(slot[:2]), int(slot[3:5])
            if ((timestamp.hour, timestamp.minute) >= (hh, mm)
                    and (date, slot) not in self._fired_slots):
                self._fired_slots.add((date, slot))
                return "cron"
        return None

    def _fire(
        self,
        trigger: str,
        timestamp: pd.Timestamp,
        returns: pd.DataFrame,
    ) -> ReoptRecord:
        window = returns.tail(self.config.erc_params.lookback)
        cov_summary = _cov_summary(window)
        result = self._erc.update(returns)

        if result is None:
            record = ReoptRecord(
                ts=timestamp.isoformat(), trigger=trigger,
                cov_summary=cov_summary, weight_diff={}, applied=False,
                skip_reason="insufficient data for ERC",
                weights=dict(self._weights),
            )
        else:
            new_w = result.weights
            diff = {
                k: new_w.get(k, 0.0) - self._weights.get(k, 0.0)
                for k in set(new_w) | set(self._weights)
            }
            max_delta = max((abs(d) for d in diff.values()), default=0.0)
            if self._weights and max_delta <= self.config.weight_change_threshold:
                record = ReoptRecord(
                    ts=timestamp.isoformat(), trigger=trigger,
                    cov_summary=cov_summary, weight_diff=diff, applied=False,
                    skip_reason=(
                        f"debounced: max |Δw| {max_delta:.4f} <= "
                        f"{self.config.weight_change_threshold:.4f}"
                    ),
                    weights=dict(self._weights),
                )
            else:
                self._weights = dict(new_w)
                record = ReoptRecord(
                    ts=timestamp.isoformat(), trigger=trigger,
                    cov_summary=cov_summary, weight_diff=diff, applied=True,
                    skip_reason="", weights=dict(self._weights),
                )

        self._records.append(record)
        self._write_audit(record)
        return record

    def _write_audit(self, record: ReoptRecord) -> None:
        if self._audit_path is None:
            return
        line = json.dumps(asdict(record), sort_keys=True, default=str)
        with self._audit_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
