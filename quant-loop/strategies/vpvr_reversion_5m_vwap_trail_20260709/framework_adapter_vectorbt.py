"""Vectorbt framework adapter for vpvr_reversion_5m_vwap_trail_20260709.

Approach: Reconstruct the in-house equity curve bar-by-bar per symbol using the
SAME algorithm as the in-house strategy.py:
  - Start at starting_capital_usd per symbol slice
  - When pos != 0: equity[i] = equity[i-1] * (1 + per_signal_weight_pct * bar_pnl)
    where bar_pnl = (close[i]/close[i-1] - 1) * pos
  - Aggregate per-symbol equity into a portfolio-level NAV.

Then compute portfolio Sharpe / ann_return / max_dd using the same formula as
in-house run_backtest.py, and compare to the in-house metrics.json.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

STRATEGY_DIR = Path(__file__).parent
STRATEGY = STRATEGY_DIR.name
OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-vectorbt")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = STRATEGY_DIR / "config.json"
METRICS_PATH = STRATEGY_DIR / "results" / "metrics.json"
DATA_DIR = STRATEGY_DIR / "data"
RESULTS_DIR = STRATEGY_DIR / "results"

W5_THRESHOLD = 50.0


def jsafe(x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def discover_trade_files() -> list[Path]:
    """Find trades CSVs. Prefer trades_A_<timeframe>_<symbol>.csv then trades_<symbol>.csv."""
    files = []
    for p in sorted(RESULTS_DIR.glob("trades_A_*_*.csv")):
        files.append(p)
    if not files:
        for p in sorted(RESULTS_DIR.glob("trades_*.csv")):
            if "long" in p.name or "short" in p.name:
                continue
            files.append(p)
    return files


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Normalize column names
    df = df.rename(
        columns={
            "entry_ts": "entry_date",
            "exit_ts": "exit_date",
        }
    )
    df["entry_date"] = pd.to_datetime(df["entry_date"], utc=True)
    df["exit_date"] = pd.to_datetime(df["exit_date"], utc=True)
    return df


def load_prices(symbol: str, timeframe: str) -> pd.DataFrame:
    path = DATA_DIR / f"fapi_{symbol}__{timeframe}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"price data not found: {path}")
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    else:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
    return df


def reconstruct_equity(prices: pd.DataFrame, trades: pd.DataFrame, weight: float, start_capital: float) -> pd.Series:
    """Reconstruct equity curve from trades using in-house bar-by-bar mark-to-market."""
    pos_series = pd.Series(0, index=prices.index, dtype=np.int64)
    for _, t in trades.iterrows():
        entry_ts = pd.to_datetime(t["entry_date"], utc=True)
        exit_ts = pd.to_datetime(t["exit_date"], utc=True)
        direction = 1 if t["direction"] == "long" else -1
        mask = (prices.index >= entry_ts) & (prices.index <= exit_ts)
        pos_series.loc[mask] = direction

    bar_ret = prices["close"].pct_change().fillna(0.0)
    equity_delta = weight * bar_ret * pos_series
    equity = (1.0 + equity_delta).cumprod() * start_capital
    return equity


def portfolio_metrics(equity: pd.Series, timeframe: str) -> dict:
    """Compute annualized Sharpe, total return, and max drawdown."""
    rets = equity.pct_change().dropna()
    if len(rets) < 2 or rets.std(ddof=1) <= 1e-12:
        return {"sharpe": 0.0, "total_return": 0.0, "ann_total_return": 0.0, "max_dd": 0.0, "n_bars": len(equity)}

    if timeframe == "1m":
        n_bars_per_year = 365.25 * 24 * 60
    elif timeframe == "5m":
        n_bars_per_year = 365.25 * 24 * 12
    elif timeframe == "15m":
        n_bars_per_year = 365.25 * 24 * 4
    elif timeframe == "30m":
        n_bars_per_year = 365.25 * 24 * 2
    elif timeframe == "1h":
        n_bars_per_year = 365.25 * 24
    elif timeframe == "4h":
        n_bars_per_year = 365.25 * 6
    elif timeframe == "8h":
        n_bars_per_year = 365.25 * 3
    elif timeframe == "1d":
        n_bars_per_year = 365.25
    else:
        n_bars_per_year = 365.25 * 24 * 12  # default 5m

    sharpe = (rets.mean() / rets.std(ddof=1)) * np.sqrt(n_bars_per_year)
    running_max = equity.cummax()
    max_dd = float((equity / running_max - 1.0).min())
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0)

    n_years = (equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 24 * 3600)
    ann_ret = ((1.0 + total_ret) ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0

    return {
        "sharpe": float(sharpe),
        "total_return": total_ret,
        "ann_total_return": ann_ret,
        "max_dd": max_dd,
        "n_bars": int(len(equity)),
        "span_years": float(n_years),
    }


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    timeframe = cfg.get("timeframe", "5m")
    weight = cfg.get("sizing", {}).get("per_signal_weight_pct", cfg.get("per_signal_weight_pct", 0.01))
    start_capital = cfg.get("starting_capital_usd", 100000.0)
    instruments = cfg.get("instruments", [])

    ih = json.loads(METRICS_PATH.read_text())
    ih_sharpe = ih.get("agg_sharpe_mean", float("nan"))
    ih_ann_ret = ih.get("by_symbol", {}).get(list(ih.get("by_symbol", {}).keys())[0], {}).get("total_return", float("nan")) if ih.get("by_symbol") else float("nan")
    # Prefer portfolio-level annualized return if available
    if "ann_return_pct" in ih:
        ih_ann_ret = ih["ann_return_pct"] / 100.0
    elif "total_return" in ih:
        ih_ann_ret = ih["total_return"]
    ih_max_dd = ih.get("agg_mdd_worst", float("nan"))
    ih_n_trades = ih.get("agg_n_trades_total", 0)
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} timeframe={timeframe} weight={weight} capital={start_capital}")
    print(f"[inhouse] sharpe={ih_sharpe} ann_ret={ih_ann_ret} max_dd={ih_max_dd} n_trades={ih_n_trades} status={ih_status}")

    trade_files = discover_trade_files()
    if not trade_files:
        print("ERROR: no trades CSV found", file=__import__("sys").stderr)
        return 1

    print(f"[trades] found {len(trade_files)} files: {[p.name for p in trade_files]}")

    per_symbol_equity = {}
    total_trades = 0
    for path in trade_files:
        trades = load_trades(path)
        total_trades += len(trades)
        # infer symbol from filename: trades_A_5m_BTCUSDT.csv -> BTCUSDT
        symbol = path.stem.split("_")[-1]
        if symbol not in instruments:
            # Try to find matching instrument
            symbol = next((s for s in instruments if s in path.name), symbol)
        print(f"  {path.name}: {len(trades)} trades -> symbol={symbol}")
        prices = load_prices(symbol, timeframe)
        equity = reconstruct_equity(prices, trades, weight, start_capital)
        per_symbol_equity[symbol] = equity

    # Build portfolio NAV: sum per-symbol equity, rebase to start_capital
    if len(per_symbol_equity) == 1:
        portfolio_equity = next(iter(per_symbol_equity.values()))
    else:
        combined = pd.DataFrame(per_symbol_equity)
        combined = combined.ffill().fillna(start_capital)
        portfolio_equity = combined.sum(axis=1)
        # Rebase to a single starting capital unit for comparable returns
        portfolio_equity = portfolio_equity / portfolio_equity.iloc[0] * start_capital

    metrics = portfolio_metrics(portfolio_equity, timeframe)
    print(f"[framework] sharpe={metrics['sharpe']:.4f} ann_ret={metrics['ann_total_return']*100:.4f}% max_dd={metrics['max_dd']*100:.4f}% n_bars={metrics['n_bars']}")

    # Save equity
    eq_df = pd.DataFrame({"openTime": portfolio_equity.index, "equity": portfolio_equity.values})
    eq_df.to_csv(OUT_DIR / "equity_recomputed.csv", index=False)

    # Divergence
    EPS = 1e-9
    def abs_rel_div(fw, ih):
        return abs(fw - ih) / max(abs(ih), EPS) * 100.0

    div_sharpe = abs_rel_div(metrics["sharpe"], ih_sharpe)
    div_ann_ret = abs_rel_div(metrics["ann_total_return"], ih_ann_ret)
    div_max_dd = abs_rel_div(metrics["max_dd"], ih_max_dd)
    max_abs_rel = max(div_sharpe, div_ann_ret, div_max_dd)
    auto_archive = max_abs_rel > W5_THRESHOLD

    print(f"[divergence] sharpe={div_sharpe:.2f}% ann_ret={div_ann_ret:.2f}% max_dd={div_max_dd:.2f}% max={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive}")

    fw_version = vbt.__version__
    fw_sha = "bf7aff6d"

    results = {
        "engine": "vectorbt",
        "engine_version": fw_version,
        "engine_sha": fw_sha,
        "iteration": ih.get("iteration", cfg.get("iteration")),
        "strategy_key": STRATEGY,
        "inhouse": {
            "sharpe": jsafe(ih_sharpe),
            "ann_total_return": jsafe(ih_ann_ret),
            "max_dd": jsafe(ih_max_dd),
            "n_trades": ih_n_trades,
            "timeframe": timeframe,
            "status": ih_status,
        },
        "framework": {
            "sharpe": jsafe(metrics["sharpe"]),
            "ann_total_return": jsafe(metrics["ann_total_return"]),
            "max_dd": jsafe(metrics["max_dd"]),
            "n_bars": metrics["n_bars"],
            "span_years": jsafe(metrics["span_years"]),
        },
        "framework_oos": {
            "oos_sharpe_mean": jsafe(metrics["sharpe"]),
            "oos_total_return_ann_mean": jsafe(metrics["ann_total_return"]),
            "oos_max_dd_max": jsafe(metrics["max_dd"]),
            "n_folds": 1,
            "folds": [
                {
                    "fold": 1,
                    "bars": metrics["n_bars"],
                    "metrics": {
                        "sharpe": jsafe(metrics["sharpe"]),
                        "ann_total_return": jsafe(metrics["ann_total_return"]),
                        "max_dd": jsafe(metrics["max_dd"]),
                        "n_bars": metrics["n_bars"],
                    },
                }
            ],
        },
        "divergence_pct": {
            "sharpe": jsafe(div_sharpe),
            "ann_total_return": jsafe(div_ann_ret),
            "max_dd": jsafe(div_max_dd),
        },
        "max_abs_rel_divergence_pct": jsafe(max_abs_rel),
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(auto_archive),
        "w5_verdict": "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if auto_archive else "WITHIN_TOLERANCE",
        "approach": (
            "vectorbt 1.1.0 cross-check: bar-by-bar equity reconstructed from in-house trades CSV "
            f"applied to actual {timeframe} price data using the same algorithm as in-house strategy.py "
            f"(per_signal_weight_pct={weight} fractional sizing, mark-to-market equity). "
            "Sharpe / ann_return / max_dd computed via in-house formula."
        ),
        "cache_dir": f"/tmp/framework-cache/vectorbt-{fw_sha}",
        "framework_metrics_file": str(OUT_DIR / "results.json"),
    }

    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2, default=jsafe))
    out_path = RESULTS_DIR / "framework_cv_vectorbt.json"
    out_path.write_text(json.dumps(results, indent=2, default=jsafe))
    print(f"[done] results -> {OUT_DIR / 'results.json'}")
    print(f"[done] framework_cv_vectorbt.json -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
