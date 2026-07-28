"""Run backtest for mtf_xs_pairs_1m_15m_2h_h1_20260718 (H1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # strategies/
sys.path.insert(0, str(_HERE.parent / "_indicators"))

from data_loader import load_all, load_funding  # noqa: E402
from _indicators.mtf_xs_runner_20260718 import run_backtest, write_metrics  # noqa: E402


CONFIG_PATH = _HERE / "config.json"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _write_trades_csv(per_pair: list, run_dir: Path, tag: str) -> int:
    """Write trades_<tag>.csv combining every pair's trades.

    Returns the number of trade rows written.
    """
    rows = []
    for pp in per_pair:
        for t in pp["trades"]:
            row = dict(t)
            row["pair"] = pp["pair"]
            rows.append(row)
    if not rows:
        # Always write an empty CSV with the expected columns so downstream
        # tooling has a stable schema.
        pd.DataFrame(columns=[
            "pair", "direction", "entry_ts", "entry_price_a", "entry_price_b",
            "exit_ts", "exit_price_a", "exit_price_b", "pnl_pct", "bars_held",
            "z_at_entry", "z_at_exit", "slope15m_at_entry", "trend2h_at_entry",
            "exit_reason",
        ]).to_csv(run_dir / f"trades_{tag}.csv", index=False)
        return 0
    df = pd.DataFrame(rows)
    df.sort_values(["entry_ts", "pair"], inplace=True)
    df.to_csv(run_dir / f"trades_{tag}.csv", index=False)
    return len(df)


def _write_equity_csv(res: dict, data: dict, run_dir: Path, tag: str,
                      starting_capital: float) -> int:
    """Write two equity CSVs at the strategy directory:

      * equity_<tag>_1m.csv   - full 1m bar equity (can be very large)
      * equity_<tag>_1d.csv   - daily-resampled equity (compact, committable)

    The full 1m file is useful for offline analysis but exceeds GitHub's
    per-file size cap when n_bars > a few million, so the daily-resampled
    file is the canonical committable artefact.
    """
    port = res["portfolio"]
    n_bars = int(port.get("n_bars", 0))
    if n_bars == 0:
        empty = pd.DataFrame(columns=["bar_index", "timestamp", "equity", "bar_return"])
        empty.to_csv(run_dir / f"equity_{tag}_1d.csv", index=False)
        empty.to_csv(run_dir / f"equity_{tag}_1m.csv", index=False)
        return 0
    # Recover a real timestamp index from the per-symbol common-1m index passed
    # into run_backtest (it is the intersection of every symbol's 1m index).
    first_sym = next(iter(data))
    src_idx = data[first_sym].index
    if len(src_idx) >= n_bars:
        idx = src_idx[:n_bars]
    else:
        try:
            start = pd.Timestamp(res["per_pair"][0]["span_start"])
        except Exception:
            start = pd.Timestamp("2022-01-01")
        idx = pd.date_range(start, periods=n_bars, freq="1min")
    eq = np.asarray(port["equity"], dtype=float)[:n_bars]
    br = np.asarray(port["bar_return"], dtype=float)[:n_bars]
    df_1m = pd.DataFrame({
        "bar_index": np.arange(n_bars),
        "timestamp": idx,
        "equity": eq,
        "bar_return": br,
    })
    # Canonical, committable daily equity — resample to last-bar-of-day.
    df_1d = df_1m.set_index("timestamp").resample("1D").last().dropna().reset_index()
    df_1d["daily_return"] = df_1d["equity"].pct_change().fillna(0.0)
    df_1d["bar_index"] = df_1d.index  # treat day index
    df_1d = df_1d[["bar_index", "timestamp", "equity", "daily_return"]]
    df_1d.to_csv(run_dir / f"equity_{tag}_1d.csv", index=False)
    df_1m.to_csv(run_dir / f"equity_{tag}_1m.csv", index=False)
    return n_bars


def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    syms = list(cfg["instruments"])
    print("Loading 1m data for", syms)
    data = load_all(syms)
    funding = load_funding(syms) if cfg.get("hypothesis") == "H3" else None
    for s, df in data.items():
        print(" ", s, len(df), "span", df.index[0], "->", df.index[-1])

    print("Running backtest …")
    res = run_backtest(data, cfg, funding=funding)
    port = res["portfolio"]
    print(f"portfolio n_bars={port['n_bars']}, n_pairs={len(res['per_pair'])}")
    for pp in res["per_pair"]:
        print(f"  {pp['pair']}: n_trades={len(pp['trades'])}")
    payload = write_metrics(res, cfg, RESULTS_DIR)
    tag = cfg["strategy"]
    n_trade_rows = _write_trades_csv(res["per_pair"], RESULTS_DIR, tag)
    n_eq_rows = _write_equity_csv(
        res, data, RESULTS_DIR, tag,
        starting_capital=float(cfg.get("starting_capital_usd", 100000.0)),
    )
    print("=== " + cfg["strategy"] + " (" + cfg.get("hypothesis", "?") + ") ===")
    print("tag                  :", "[" + payload["tag"] + "]")
    print("avg pair sharpe(d/r) :", f"{payload['avg_pair_sharpe_daily_resampled']:.3f}")
    print("avg pair ann.ret(d) :", f"{payload['avg_pair_annualized_return_daily']:.4f}")
    print("avg pair max DD      :", f"{payload['avg_pair_max_drawdown_pct']:.4f}")
    print("avg pair profit_f    :", f"{payload['profit_factor_avg']:.3f}")
    print("n_trades_total       :", payload["n_trades_total"])
    print("sharpe_method        :", payload["sharpe_method"])
    print("metrics.json         :", str(RESULTS_DIR / "metrics.json"))
    print("trades_csv           :", str(RESULTS_DIR / ("trades_" + tag + ".csv")),
          "rows=", n_trade_rows)
    print("equity_csv (1d, commit):", str(RESULTS_DIR / ("equity_" + tag + "_1d.csv")))
    print("equity_csv (1m, large) :", str(RESULTS_DIR / ("equity_" + tag + "_1m.csv")),
          "rows=", n_eq_rows)


if __name__ == "__main__":
    main()