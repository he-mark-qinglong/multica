"""CPCV for P3-OPT-049 with apples-to-apples baseline comparison.

Variant: entry_threshold (bb_k) = 1.8  — strategy loaded from config.json.
Baseline: same as variant with bb_k forced to 2.0 (per P3-OPT-000).

Both runs share the same data, same CPCV fold partition, same purge/embargo,
and the same code path. The single-variable change is isolated to the exit
parameter.

Topology per issue: n_groups=6, k_test=2 -> C(6, 2) = 15 paths.

Implementation note: this dataset uses a tz-aware UTC DatetimeIndex, so the
shared CPCV harness's `df.loc[train_ts]` step raises KeyError (tz-naive vs
tz-aware). We replicate the harness logic locally and index positionally,
which is invariant to tz.

Outputs:
  results/cpcv_metrics.json       — variant vs baseline, per-fold + aggregates + DSR
  results/cpcv_summary.txt        — human-readable verdict
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

STRATEGY_DIR = Path(__file__).resolve().parent
QUANT_LOOP = STRATEGY_DIR.parents[1]
sys.path.insert(0, str(STRATEGY_DIR))
sys.path.insert(0, str(QUANT_LOOP))

from _shared.validation.cpcv import deflated_sharpe, sharpe_from_returns  # noqa: E402
from data_loader import load_all  # noqa: E402
from strategy import run_backtest  # noqa: E402

CONFIG_PATH = STRATEGY_DIR / "config.json"
BASELINE_PATH = STRATEGY_DIR.parent / "bb_reversion_rsi_1m_20260707" / "config.json"
RESULTS_DIR = STRATEGY_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

N_GROUPS = 6
K_TEST = 2
PURGE_BARS = 240
EMBARGO_BARS = 60
PERIODS_PER_YEAR = 60 * 24 * 365


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (float, np.floating)):
        value = float(obj)
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, np.datetime64):
        return pd.Timestamp(obj).isoformat()
    return obj


def _equity_returns(df: pd.DataFrame, cfg: dict) -> pd.Series:
    cfg_local = dict(cfg)
    cfg_local["_symbol"] = cfg["instruments"][0]
    result = run_backtest(df, cfg_local)
    return result.equity_curve.reindex(df.index).ffill().pct_change().fillna(0.0)


def _purge(train_pos: np.ndarray, test_pos: np.ndarray, purge: int) -> np.ndarray:
    if purge <= 0 or len(train_pos) == 0 or len(test_pos) == 0:
        return train_pos
    test_min, test_max = int(test_pos.min()), int(test_pos.max())
    boundary = np.concatenate([test_pos - off for off in range(-purge, purge + 1)])
    boundary = np.unique(boundary[(boundary >= train_pos.min()) & (boundary <= train_pos.max())])
    keep = ~np.isin(train_pos, np.intersect1d(train_pos, boundary))
    return train_pos[keep]


def _embargo(test_pos: np.ndarray, embargo: int) -> np.ndarray:
    if embargo <= 0 or len(test_pos) == 0:
        return test_pos
    s = np.sort(test_pos)
    return s[embargo:]


def _run_cpcv(returns: np.ndarray, df_index: pd.DatetimeIndex, label: str) -> dict:
    n = len(returns)
    paths = list(combinations(range(N_GROUPS), K_TEST))
    group_bounds = np.array_split(np.arange(n), N_GROUPS)

    folds = []
    for test_groups in paths:
        test_pos = np.concatenate([group_bounds[g] for g in test_groups])
        train_pos = np.concatenate([group_bounds[g] for g in range(N_GROUPS) if g not in test_groups])

        train_pos_p = _purge(np.sort(train_pos), np.sort(test_pos), PURGE_BARS)
        test_pos_e = _embargo(np.sort(test_pos), EMBARGO_BARS)

        if len(train_pos_p) < 100 or len(test_pos_e) < 30:
            continue

        test_returns = returns[test_pos_e]
        train_returns = returns[train_pos_p]
        fold = {
            "path": len(folds) + 1,
            "train_start": pd.Timestamp(df_index[train_pos_p[0]]).isoformat(),
            "train_end": pd.Timestamp(df_index[train_pos_p[-1]]).isoformat(),
            "test_start": pd.Timestamp(df_index[test_pos_e[0]]).isoformat(),
            "test_end": pd.Timestamp(df_index[test_pos_e[-1]]).isoformat(),
            "oos_sharpe": sharpe_from_returns(test_returns, PERIODS_PER_YEAR),
            "n_nonzero_return_bars": int((test_returns != 0).sum()),
            "n_test_bars": int(len(test_pos_e)),
            "n_train_bars": int(len(train_pos_p)),
            "in_sample_sharpe": sharpe_from_returns(train_returns, PERIODS_PER_YEAR),
        }
        folds.append(fold)

    sharpes = np.array([f["oos_sharpe"] for f in folds], dtype=float) if folds else np.array([])
    nonzero_full = returns[returns != 0]
    sample_len = max(int(nonzero_full.shape[0]), 2)
    skew = float(nonzero_full.skew()) if len(nonzero_full) > 2 else 0.0
    kurt = float(nonzero_full.kurtosis() + 3.0) if len(nonzero_full) > 3 else 3.0
    mean_sharpe = float(sharpes.mean()) if len(sharpes) else 0.0
    std_sharpe = float(sharpes.std(ddof=1)) if len(sharpes) > 1 else float("nan")
    if len(sharpes) >= 5:
        rng = np.random.default_rng(42)
        boot = rng.choice(sharpes, size=(1000, len(sharpes)), replace=True).mean(axis=1)
        ci = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)))
    else:
        ci = (float("nan"), float("nan"))
    dsr = deflated_sharpe(
        observed_sharpe=mean_sharpe,
        n_trials=100,
        sample_len=sample_len,
        skew=skew,
        kurt=kurt,
    )
    return {
        "label": label,
        "n_paths_completed": len(folds),
        "oos_sharpe_mean": mean_sharpe,
        "oos_sharpe_std": std_sharpe,
        "oos_sharpe_median": float(np.median(sharpes)) if len(sharpes) else None,
        "oos_sharpe_min": float(np.min(sharpes)) if len(sharpes) else None,
        "oos_sharpe_max": float(np.max(sharpes)) if len(sharpes) else None,
        "oos_sharpe_ci95": [ci[0], ci[1]],
        "deflated_sharpe": dsr,
        "folds": folds,
    }


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    base_cfg = json.loads(BASELINE_PATH.read_text())
    symbol = cfg["instruments"][0]
    df = load_all([symbol])[symbol]
    print(
        f"[{datetime.now(timezone.utc).isoformat()}] P3-OPT-049 CPCV start "
        f"rows={len(df)} span={df.index[0]}..{df.index[-1]}"
    )

    variant_returns_series = _equity_returns(df, cfg)
    base_cfg_run = dict(base_cfg)
    base_cfg_run["_symbol"] = symbol
    base_returns_series = _equity_returns(df, base_cfg_run)

    variant_returns = variant_returns_series.to_numpy()
    base_returns = base_returns_series.to_numpy()
    df_index = df.index

    variant_block = _run_cpcv(
        variant_returns,
        df_index,
        label=f"variant bb_k={cfg['indicators']['bb_k']}",
    )
    base_block = _run_cpcv(
        base_returns,
        df_index,
        label="baseline bb_k=2.0",
    )

    delta_mean = variant_block["oos_sharpe_mean"] - base_block["oos_sharpe_mean"]
    delta_dsrs = variant_block["deflated_sharpe"] - base_block["deflated_sharpe"]

    payload = _sanitize(
        {
            "strategy": cfg["strategy"],
            "iteration": cfg["iteration"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_source": cfg["data_source"],
            "symbol": symbol,
            "span": [df.index[0].isoformat(), df.index[-1].isoformat()],
            "n_bars": len(df),
            "parameter": {
                "name": "entry_threshold",
                "config_field": "indicators.bb_k",
                "baseline": base_cfg["indicators"]["bb_k"],
                "variant": cfg["indicators"]["bb_k"],
            },
            "cpcv_topology": {
                "n_groups": N_GROUPS,
                "k_test": K_TEST,
                "n_paths_expected": math.comb(N_GROUPS, K_TEST),
                "purge_bars": PURGE_BARS,
                "embargo_bars": EMBARGO_BARS,
                "periods_per_year": PERIODS_PER_YEAR,
            },
            "variant": variant_block,
            "baseline": base_block,
            "delta": {
                "oos_sharpe_mean_delta": delta_mean,
                "deflated_sharpe_delta": delta_dsrs,
                "promote": delta_mean >= 0.1,
                "kill": delta_mean < 0.1,
                "decision_rule": "promote if Δmean OOS Sharpe >= +0.1, kill otherwise",
            },
        }
    )
    (RESULTS_DIR / "cpcv_metrics.json").write_text(json.dumps(payload, indent=2))

    summary_lines = [
        f"{cfg['strategy']} — P3-OPT-049 single-variable sweep",
        f"entry_threshold variant={cfg['indicators']['bb_k']} baseline=2.0",
        f"CPCV n_groups={N_GROUPS} k_test={K_TEST} paths_expected={math.comb(N_GROUPS, K_TEST)} "
        f"paths_completed(variant/base)={variant_block['n_paths_completed']}/{base_block['n_paths_completed']}",
        f"Baseline OOS Sharpe mean={base_block['oos_sharpe_mean']:.6f} std={base_block['oos_sharpe_std']:.6f} "
        f"CI95=[{base_block['oos_sharpe_ci95'][0]:.6f}, {base_block['oos_sharpe_ci95'][1]:.6f}] "
        f"DSR={base_block['deflated_sharpe']:.6f}",
        f"Variant  OOS Sharpe mean={variant_block['oos_sharpe_mean']:.6f} std={variant_block['oos_sharpe_std']:.6f} "
        f"CI95=[{variant_block['oos_sharpe_ci95'][0]:.6f}, {variant_block['oos_sharpe_ci95'][1]:.6f}] "
        f"DSR={variant_block['deflated_sharpe']:.6f}",
        f"Δmean={delta_mean:+.6f} (promote gate +0.10) -> {'PROMOTE' if delta_mean >= 0.1 else 'KILL'}",
    ]
    (RESULTS_DIR / "cpcv_summary.txt").write_text("\n".join(summary_lines) + "\n")
    print("\n".join(summary_lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())