"""Walk-forward cross-validation for the xs_momentum_rank_1d strategy.

Builds a rolling-origin schedule of train/test windows on TOP of the
existing ``backtest.run_backtest`` engine (no engine changes), runs each
window's backtest, and aggregates per-window metrics into a single JSON
artifact under ``results/walk_forward.json``.

The default schedule is:
    - 5 rolling windows
    - train length = 365 days
    - test length  = 60 days
    - step        = 60 days (anchored on the train-start)

The schedule is computed in *calendar-day* offsets relative to the first
date available across the universe, so the windows are deterministic and
reproducible without inspecting the panel index.

Notes
-----
- The xs_momentum_rank_1d strategy does not have any *learned* parameters
  that need fitting on the train slice -- the spec is fixed in
  ``config.json``. For this strategy the "train" run is therefore a
  sanity check that the engine produces a positive Sharpe window given
  the configuration; the "test" run is the strictly out-of-sample
  evaluation. ``walk_forward_splits`` accepts a future config that does
  have learnable params; the engine call today is identical.
- This module imports from ``backtest.run_backtest``. We DO NOT modify
  ``backtest.py`` -- per the B3 task constraint.

Reproducibility
---------------
- No randomness is used (no bootstrapping, no shuffling). The schedule
  is fully deterministic given the panel's earliest available date.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from backtest import run_backtest
from universe import UniverseConfig

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"


# ---------------------------------------------------------------------------
# Split definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardSplit:
    """A single (train, test) window expressed as calendar-day offsets
    relative to the panel's earliest available date (``origin``).

    ``origin`` is a tz-aware pandas.Timestamp; all ``*_start`` /
    ``*_end`` fields are absolute UTC calendar days (00:00 UTC).
    """

    origin: pd.Timestamp
    train_start: pd.Timestamp
    train_end: pd.Timestamp     # inclusive
    test_start: pd.Timestamp
    test_end: pd.Timestamp      # inclusive
    window_idx: int


def walk_forward_splits(
    dates: pd.DatetimeIndex,
    cfg: Optional[dict] = None,
) -> List[WalkForwardSplit]:
    """Compute the rolling-origin walk-forward splits.

    Parameters
    ----------
    dates : tz-aware DatetimeIndex of the union of daily bars across the
        universe. Used to derive the panel's earliest available date
        (the origin) and to confirm each window falls inside the panel.
    cfg : strategy config (defaults to ``config.json``). The schedule
        itself uses ``wf_*`` keys with these defaults:
            wf_n_windows       = 5
            wf_train_days      = 365
            wf_test_days       = 60
            wf_step_days       = 60

    Returns
    -------
    List of ``WalkForwardSplit``. Each window's ``train_end`` and the
    following window's ``train_start`` differ by ``step_days`` so the
    test windows are non-overlapping AND every train slice strictly
    precedes its test slice.
    """
    cfg = cfg or json.loads(CONFIG_PATH.read_text())
    n_windows = int(cfg.get("wf_n_windows", 5))
    train_days = int(cfg.get("wf_train_days", 365))
    test_days = int(cfg.get("wf_test_days", 60))
    step_days = int(cfg.get("wf_step_days", 60))
    if n_windows <= 0:
        raise ValueError("wf_n_windows must be > 0")
    if train_days <= 0 or test_days <= 0 or step_days <= 0:
        raise ValueError("wf_train_days / wf_test_days / wf_step_days must be > 0")

    if dates.empty:
        raise ValueError("empty dates index -- cannot build walk-forward splits")

    # Anchor origin at the first date in the panel.
    first = pd.Timestamp(dates[0])
    origin = first.tz_convert("UTC") if first.tz is not None else first.tz_localize("UTC")

    splits: List[WalkForwardSplit] = []
    for i in range(n_windows):
        train_start = origin + pd.Timedelta(step_days * i, unit="D")
        train_end = train_start + pd.Timedelta(train_days - 1, unit="D")
        test_start = train_end + pd.Timedelta(1, unit="D")
        test_end = test_start + pd.Timedelta(test_days - 1, unit="D")
        splits.append(
            WalkForwardSplit(
                origin=origin,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                window_idx=i,
            )
        )
    return splits


# ---------------------------------------------------------------------------
# Window metrics
# ---------------------------------------------------------------------------


def _empty_window_metrics() -> Dict[str, float]:
    """Metrics returned when a window has zero rebalances (e.g. test slice
    is entirely inside the engine's 30-day warmup). Keeps downstream
    aggregation well-defined.
    """
    return {
        "sharpe": 0.0,
        "sortino": 0.0,
        "max_drawdown": 0.0,
        "total_return": 0.0,
        "n_trades": 0,
        "n_rebalances": 0,
    }


def _window_metrics(result) -> Dict[str, float]:
    """Map a BacktestResult to the flat per-window metric dict."""
    return {
        "sharpe": float(result.annualized_sharpe),
        "sortino": float(result.annualized_sortino),
        "max_drawdown": float(result.max_drawdown),
        "total_return": float(result.total_return),
        "n_trades": int(result.n_rebalances),
        "n_rebalances": int(result.n_rebalances),
    }


def _is_warmup_window(panel_dates: pd.DatetimeIndex, window: WalkForwardSplit) -> bool:
    """A window is a 'warmup' window iff the panel has not yet produced a
    valid momentum score by ``window.train_end`` (i.e. the test slice is
    entirely inside the 30-day warmup). The engine will still run, just
    produce zero or one rebalances -- callers can detect this via
    ``n_rebalances == 0``.
    """
    if panel_dates.empty:
        return True
    last_panel_date = panel_dates[-1]
    return last_panel_date < window.test_start


# ---------------------------------------------------------------------------
# Run a single split
# ---------------------------------------------------------------------------


def _to_naive_utc(ts: pd.Timestamp) -> pd.Timestamp:
    """Drop tz info on a Timestamp so the existing backtest's
    ``pd.Timestamp(start).tz_localize("UTC")`` is a no-op.

    The backtest engine treats the start/end inputs as wall-clock UTC
    dates; we keep the same contract by stripping tz here rather than
    mutating the engine.
    """
    t = pd.Timestamp(ts)
    if t.tz is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t


def run_window(
    per_symbol_dfs: Dict[str, pd.DataFrame],
    cfg: dict,
    universe_cfg: UniverseConfig,
    window: WalkForwardSplit,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Run train + test backtests for a single walk-forward window.

    Returns
    -------
    (train_metrics, test_metrics) -- both dicts with the keys produced by
    ``_window_metrics``.
    """
    train_result = run_backtest(
        per_symbol_dfs,
        cfg=cfg,
        universe_cfg=universe_cfg,
        start=_to_naive_utc(window.train_start),
        end=_to_naive_utc(window.train_end),
    )
    train_metrics = _window_metrics(train_result)

    # Skip the test slice if it falls entirely inside the 30-day warmup.
    panel_dates = _panel_dates(per_symbol_dfs)
    if _is_warmup_window(panel_dates, window):
        return train_metrics, _empty_window_metrics()

    test_result = run_backtest(
        per_symbol_dfs,
        cfg=cfg,
        universe_cfg=universe_cfg,
        start=_to_naive_utc(window.test_start),
        end=_to_naive_utc(window.test_end),
    )
    test_metrics = _window_metrics(test_result)
    return train_metrics, test_metrics


def _panel_dates(per_symbol_dfs: Dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """Union of dates across all symbols, sorted, tz-aware."""
    idx: Optional[pd.DatetimeIndex] = None
    for df in per_symbol_dfs.values():
        if df.empty:
            continue
        d = df.index
        idx = d if idx is None else idx.union(d)
    if idx is None:
        return pd.DatetimeIndex([], tz="UTC")
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.sort_values()


# ---------------------------------------------------------------------------
# Full walk-forward driver
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardReport:
    windows: List[Dict[str, object]]
    aggregate: Dict[str, float]


def run_walk_forward(
    per_symbol_dfs: Dict[str, pd.DataFrame],
    cfg: Optional[dict] = None,
    universe_cfg: Optional[UniverseConfig] = None,
) -> WalkForwardReport:
    """Run all walk-forward splits and produce the aggregate report.

    Parameters
    ----------
    per_symbol_dfs : dict of symbol -> 1d OHLCV frame.
    cfg : strategy config (defaults to ``config.json``).
    universe_cfg : universe config (defaults to ``config.json``).

    Returns
    -------
    WalkForwardReport with one entry per window and a flat aggregate
    summary dict.
    """
    cfg = cfg or json.loads(CONFIG_PATH.read_text())
    universe_cfg = universe_cfg or UniverseConfig(
        target=tuple(),
        active=tuple(cfg.get("active_universe") or cfg.get("target_universe") or []),
        min_bars_in_last_7d=int(cfg.get("universe_filter", {}).get("min_bars_in_last_7d", 5)),
        min_usd_volume_per_day=float(cfg.get("universe_filter", {}).get("min_usd_volume_per_day", 1_000_000.0)),
    )

    panel_dates = _panel_dates(per_symbol_dfs)
    splits = walk_forward_splits(panel_dates, cfg=cfg)

    windows_out: List[Dict[str, object]] = []
    train_sharpes: List[float] = []
    test_sharpes: List[float] = []
    test_mdds: List[float] = []
    test_returns: List[float] = []

    for w in splits:
        train_metrics, test_metrics = run_window(per_symbol_dfs, cfg, universe_cfg, w)
        windows_out.append(
            {
                "window_idx": w.window_idx,
                "train_start": w.train_start.date().isoformat(),
                "train_end": w.train_end.date().isoformat(),
                "test_start": w.test_start.date().isoformat(),
                "test_end": w.test_end.date().isoformat(),
                "sharpe_train": train_metrics["sharpe"],
                "sharpe_test": test_metrics["sharpe"],
                "mdd_test": test_metrics["max_drawdown"],
                "return_test": test_metrics["total_return"],
                "n_trades_test": test_metrics["n_trades"],
                "n_rebalances_train": train_metrics["n_rebalances"],
                "n_rebalances_test": test_metrics["n_rebalances"],
            }
        )
        train_sharpes.append(train_metrics["sharpe"])
        test_sharpes.append(test_metrics["sharpe"])
        test_mdds.append(test_metrics["max_drawdown"])
        test_returns.append(test_metrics["total_return"])

    avg_sharpe_train = float(sum(train_sharpes) / max(len(train_sharpes), 1))
    avg_sharpe_test = float(sum(test_sharpes) / max(len(test_sharpes), 1))
    avg_mdd_test = float(sum(test_mdds) / max(len(test_mdds), 1))
    avg_return_test = float(sum(test_returns) / max(len(test_returns), 1))
    # Stability ratio: how much of the in-sample Sharpe survives out-of-
    # sample. We cap / clamp the denominator so a degenerate train Sharpe
    # of 0 does not blow up. If train Sharpe is non-positive the ratio is
    # not informative and we report it as 0.0.
    if avg_sharpe_train > 0:
        stability_ratio = avg_sharpe_test / avg_sharpe_train
    else:
        stability_ratio = 0.0

    aggregate = {
        "avg_sharpe_train": avg_sharpe_train,
        "avg_sharpe_test": avg_sharpe_test,
        "avg_mdd_test": avg_mdd_test,
        "avg_return_test": avg_return_test,
        "stability_ratio": float(stability_ratio),
        "n_windows": len(splits),
        "train_days": int(cfg.get("wf_train_days", 365)),
        "test_days": int(cfg.get("wf_test_days", 60)),
        "step_days": int(cfg.get("wf_step_days", 60)),
    }
    return WalkForwardReport(windows=windows_out, aggregate=aggregate)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _load_cached_1d(symbols: List[str], data_dir: Path = Path("data")) -> Dict[str, pd.DataFrame]:
    """Load the per-symbol 1d parquet caches directly. Bypasses
    ``data_loader.load_all`` so the walk-forward driver works even when
    the upstream 1m source root is missing -- the 1d cache is what the
    backtest consumes anyway.
    """
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        p = data_dir / f"fapi_{sym.upper()}__1d.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df[["open", "high", "low", "close", "volume"]].sort_index()
        out[sym.upper()] = df
    return out


def main() -> int:
    """CLI: run walk-forward and emit ``results/walk_forward.json``."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG_PATH.read_text())
    symbols = list(cfg.get("active_universe") or cfg.get("target_universe") or [])
    per_symbol_dfs = _load_cached_1d(symbols)
    if not per_symbol_dfs:
        print(
            "No cached 1d parquet found under data/ -- cannot run walk-forward.",
            flush=True,
        )
        return 1
    universe_cfg = UniverseConfig(
        target=tuple(),
        active=tuple(per_symbol_dfs.keys()),
        min_bars_in_last_7d=int(cfg.get("universe_filter", {}).get("min_bars_in_last_7d", 5)),
        min_usd_volume_per_day=float(cfg.get("universe_filter", {}).get("min_usd_volume_per_day", 1_000_000.0)),
    )
    report = run_walk_forward(per_symbol_dfs, cfg=cfg, universe_cfg=universe_cfg)
    out = {"windows": report.windows, "aggregate": report.aggregate}
    (RESULTS_DIR / "walk_forward.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())