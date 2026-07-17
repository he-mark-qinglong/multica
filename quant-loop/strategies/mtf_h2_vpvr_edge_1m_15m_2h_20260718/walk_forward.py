"""Walk-forward OOS for mtf_h2_vpvr_edge_1m_15m_2h_20260718."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "_indicators"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data_loader import load_all  # noqa: E402
from strategy import (  # noqa: E402
    profit_factor_and_mdd,
    run_backtest,
    sharpe_daily_resampled,
)

CONFIG_PATH = _HERE / "config.json"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _bootstrap_ci(values, n_resamples: int, seed: int) -> tuple:
    arr = np.array(values, dtype=float)
    if len(arr) < 2:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples)
    n = len(arr)
    for i in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        means[i] = arr[idx].mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _summarise_for_window(per_symbol: list, idx_win: pd.DatetimeIndex,
                          starting_capital: float) -> dict:
    if not per_symbol:
        return {
            "sharpe_daily_resampled": 0.0,
            "annualized_return_daily": 0.0,
            "max_drawdown_pct": 0.0,
            "n_days": 0,
            "n_trades_total": 0,
            "per_symbol": {},
        }
    # build portfolio bar return by averaging per-symbol bar returns
    n_bars = min(p["n_bars"] for p in per_symbol)
    port_returns = np.mean([p["bar_return"][:n_bars] for p in per_symbol], axis=0)
    sr = sharpe_daily_resampled(port_returns, idx_win[:n_bars])
    pfdd = profit_factor_and_mdd(port_returns, starting_capital)
    per_sym = {}
    n_total = 0
    for p in per_symbol:
        idx_p = idx_win[: p["n_bars"]]
        sr_p = sharpe_daily_resampled(p["bar_return"], idx_p)
        per_sym[p["symbol"]] = {
            "sharpe_daily_resampled": sr_p["sharpe_daily_resampled"],
            "annualized_return_daily": sr_p["annualized_return_daily"],
            "n_trades": len(p["trades"]),
            "max_drawdown_pct": float(pfdd["max_drawdown_pct"]),
        }
        n_total += len(p["trades"])
    return {
        "sharpe_daily_resampled": sr["sharpe_daily_resampled"],
        "annualized_return_daily": sr["annualized_return_daily"],
        "max_drawdown_pct": pfdd["max_drawdown_pct"],
        "n_days": sr["n_days"],
        "n_trades_total": n_total,
        "per_symbol": per_sym,
    }


def _window_split_bounds(n_bars: int, train: int, test: int, step: int):
    out = []
    test_start = train
    while test_start + test <= n_bars:
        out.append((0, test_start, test_start, test_start + test))
        test_start += step
    return out


def _slice_for_window(d1m: dict, start: int, end: int) -> dict:
    return {sym: df.iloc[start:end] for sym, df in d1m.items()}


def main() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text())
    syms = list(cfg["instruments"])
    print("Loading 1m data for", syms)
    data = load_all(syms)
    for s, df in data.items():
        print(" ", s, len(df), "span", df.index[0], "->", df.index[-1])

    wf_cfg = cfg["walk_forward"]
    train = int(wf_cfg["train_bars_1m"])
    test = int(wf_cfg["test_bars_1m"])
    step = int(wf_cfg["step_bars_1m"])
    min_windows = int(wf_cfg.get("min_windows", 3))
    gates = cfg.get("hard_gates", {})
    sharpe_min = float(gates.get("oos_sharpe_min", 1.0))
    ann_min = float(gates.get("oos_annualized_min", 0.15))
    boot_min = float(gates.get("bootstrap_ci_lower_min", 0.5))

    n_bars = min(len(df) for df in data.values())
    windows = _window_split_bounds(n_bars, train, test, step)
    if len(windows) < min_windows:
        raise SystemExit("insufficient windows: " + str(windows))

    first_index = next(iter(data.values())).index
    starting = float(cfg.get("starting_capital_usd", 100000.0))

    per_window = []
    for i, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
        d_win = _slice_for_window(data, te_s, te_e)
        res = run_backtest(d_win, cfg)
        idx_win = first_index[te_s:te_e]
        s = _summarise_for_window(res["per_symbol"], idx_win, starting)
        per_window.append({
            "window_id": i,
            "train_bars": [int(tr_s), int(tr_e)],
            "test_bars": [int(te_s), int(te_e)],
            "test_start_iso": str(first_index[te_s]),
            "test_end_iso": str(first_index[te_e - 1]),
            "n_test_bars": int(te_e - te_s),
            "portfolio": {
                "sharpe_daily_resampled": s["sharpe_daily_resampled"],
                "annualized_return_daily": s["annualized_return_daily"],
                "max_drawdown_pct": s["max_drawdown_pct"],
                "n_days": s["n_days"],
                "n_trades_total": s["n_trades_total"],
            },
            "per_symbol": s["per_symbol"],
        })

    sharpes = np.array([w["portfolio"]["sharpe_daily_resampled"] for w in per_window])
    rets = np.array([w["portfolio"]["annualized_return_daily"] for w in per_window])
    mean_sharpe = float(np.mean(sharpes))
    mean_ret = float(np.mean(rets))
    boot_lo, boot_hi = _bootstrap_ci(sharpes.tolist(),
                                     int(gates.get("bootstrap_resamples", 10000)),
                                     int(gates.get("bootstrap_seed", 42)))

    passed = (mean_sharpe >= sharpe_min) and (mean_ret >= ann_min) and (boot_lo >= boot_min)
    out = {
        "strategy": cfg["strategy"],
        "iteration": cfg.get("iteration"),
        "hypothesis": cfg.get("hypothesis"),
        "campaign": cfg.get("campaign"),
        "n_windows": len(per_window),
        "train_bars_1m": train,
        "test_bars_1m": test,
        "step_bars_1m": step,
        "oos_sharpe_mean_daily_resampled": mean_sharpe,
        "oos_annualized_mean_daily": mean_ret,
        "oos_max_drawdown_worst": float(np.min([w["portfolio"]["max_drawdown_pct"]
                                                  for w in per_window])) if per_window else 0.0,
        "bootstrap_ci_lower": boot_lo,
        "bootstrap_ci_upper": boot_hi,
        "bootstrap_resamples": int(gates.get("bootstrap_resamples", 10000)),
        "bootstrap_seed": int(gates.get("bootstrap_seed", 42)),
        "sharpe_method": "daily_resampled",
        "per_window": per_window,
        "gates": {
            "oos_sharpe_min_required": sharpe_min,
            "oos_annualized_min_required": ann_min,
            "bootstrap_ci_lower_min_required": boot_min,
            "passed": passed,
        },
        "tag": "PROFITABLE" if passed else "NOT-PROFITABLE",
        "verdict": (
            "PROFITABLE" if passed else
            "NOT-PROFITABLE — OOS Sharpe "
            f"{mean_sharpe:.2f} < {sharpe_min:.2f} OR "
            f"OOS annualized {mean_ret:.2%} < {ann_min:.2%} OR "
            f"bootstrap CI lower {boot_lo:.2f} < {boot_min:.2f}"
        ),
    }
    (RESULTS_DIR / "walk_forward.json").write_text(json.dumps(out, indent=2, default=float))

    print("=== walk_forward (" + cfg["strategy"] + ") ===")
    print("n_windows              :", out["n_windows"])
    print("oos_sharpe_mean        :", f"{mean_sharpe:.3f}")
    print("oos_annualized_mean    :", f"{mean_ret:.4f}")
    print("oos_max_drawdown_worst :", f"{out['oos_max_drawdown_worst']:.4f}")
    print("bootstrap_ci_lower     :", f"{boot_lo:.3f}")
    print("bootstrap_ci_upper     :", f"{boot_hi:.3f}")
    print("gates.passed           :", passed)
    print("tag                    :", "[" + out["tag"] + "]")
    print("verdict                :", out["verdict"])
    print("sharpe_method          :", out["sharpe_method"])
    print("walk_forward.json      :", str(RESULTS_DIR / "walk_forward.json"))
    return out


if __name__ == "__main__":
    main()