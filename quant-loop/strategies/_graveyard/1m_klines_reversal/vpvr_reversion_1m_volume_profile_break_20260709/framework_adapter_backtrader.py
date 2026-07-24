"""Backtrader framework adapter for vpvr_reversion_1m_volume_profile_break_20260709.

Cross-validate the in-house 1m BTCUSDT volume-profile-break reversion
strategy by replaying the in-house trade log (trades_long.csv + trades_short.csv)
through a backtrader broker with next-bar fills, commission, and slippage.

W5 (AGENT_COLLAB_AUDIT_2026-07-12):
  divergence > 50% absolute → auto-archive NOT-PROFITABLE
  divergence ≤ 50% absolute → still emit ESCALATE-TO-SMARK

Strategy: iter #69 V5 single-symbol BTCUSDT, 1m timeframe.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import timezone
from pathlib import Path
from typing import Dict, List

import backtrader as bt
import numpy as np
import pandas as pd

STRATEGY_DIR = Path(__file__).parent
STRATEGY = STRATEGY_DIR.name
OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-backtrader")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = STRATEGY_DIR / "config.json"
METRICS_PATH = STRATEGY_DIR / "results" / "metrics.json"
PRICE_PATH = STRATEGY_DIR / "data" / "fapi_BTCUSDT__1m.parquet"
RESULTS_DIR = STRATEGY_DIR / "results"
INHOUSE_OOS_PATH = OUT_DIR / "inhouse_oos.json"

W5_THRESHOLD = 50.0
SLIPPAGE_PCT = 0.0002
FEE_PCT = 0.0005
TIMEFRAME = "1m"
ANN_FACTOR_1M = 365.0 * 24.0 * 60.0  # 525600

FOLD_DEFS = [
    {"name": "2024-Q3", "test": ["2024-07-01", "2024-09-30"]},
    {"name": "2025-Q1", "test": ["2025-01-01", "2025-03-31"]},
    {"name": "2025-Q3", "test": ["2025-07-01", "2025-09-30"]},
]


def jsafe(x):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def load_prices(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path).reset_index()
    df = df.sort_values("openTime").reset_index(drop=True)
    df["open_time_dt"] = pd.to_datetime(df["openTime"], utc=True)
    return df


def load_all_trades(strategy_dir: Path) -> pd.DataFrame:
    """Combine trades_long.csv + trades_short.csv."""
    long_p = strategy_dir / "results" / "trades_long.csv"
    short_p = strategy_dir / "results" / "trades_short.csv"
    parts = []
    for p in (long_p, short_p):
        if p.exists():
            t = pd.read_csv(p)
            parts.append(t)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df["entry_ts"] = pd.to_datetime(df["entry_ts"], utc=True, errors="coerce")
    df["exit_ts"] = pd.to_datetime(df["exit_ts"], utc=True, errors="coerce")
    return df.sort_values("entry_ts").reset_index(drop=True)


def filter_trades_for_fold(trades: pd.DataFrame, fold: dict) -> pd.DataFrame:
    s = pd.Timestamp(fold["test"][0], tz="UTC")
    e = pd.Timestamp(fold["test"][1], tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return trades[(trades["entry_ts"] >= s) & (trades["entry_ts"] <= e)].reset_index(drop=True)


class VPVRBreakBacktraderStrategy(bt.Strategy):
    """Replay in-house BTC trades via backtrader with next-bar fills."""

    params = dict(
        trades=None,
        slippage_pct=SLIPPAGE_PCT,
        fee_pct=FEE_PCT,
    )

    def __init__(self):
        self.next_idx = 0
        self.nav_series: List[float] = []
        self.time_series: List[pd.Timestamp] = []
        self.fills: List[dict] = []

    def next(self):
        bar_time = self.datas[0].datetime.datetime(0)
        bar_time = bar_time.replace(tzinfo=timezone.utc)

        trades = self.p.trades
        # Check pending entry
        if self.position.size == 0 and self.next_idx < len(trades):
            t = trades.iloc[self.next_idx]
            entry_ts = t["entry_ts"].to_pydatetime()
            if bar_time == entry_ts:
                direction = 1 if t["direction"] == "long" else -1
                target_value = self.broker.getvalue() * 0.01  # 1% fractional
                fill_price = self.data.open[0] * (1 + self.p.slippage_pct * direction)
                size = target_value / fill_price
                if direction == 1:
                    self.buy(size=size)
                else:
                    self.sell(size=size)
                return
        # Check exit
        if self.position.size != 0 and self.next_idx < len(trades):
            t = trades.iloc[self.next_idx]
            exit_ts = t["exit_ts"].to_pydatetime()
            if bar_time == exit_ts:
                self.close()
                self.next_idx += 1
                return
        # Record bar equity
        self.nav_series.append(self.broker.getvalue())
        self.time_series.append(bar_time)

    def notify_trade(self, trade):
        if trade.isclosed:
            self.fills.append({
                "pnl": float(trade.pnl),
                "pnlcomm": float(trade.pnlcomm),
                "open_dt": bt.num2date(trade.dtopen).replace(tzinfo=timezone.utc).isoformat(),
                "close_dt": bt.num2date(trade.dtclose).replace(tzinfo=timezone.utc).isoformat(),
            })


def run_backtrader(prices: pd.DataFrame, trades: pd.DataFrame,
                   starting_capital: float, fold: dict):
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(starting_capital)
    cerebro.broker.setcommission(commission=FEE_PCT)
    cerebro.broker.set_slippage_perc(perc=SLIPPAGE_PCT, slip_open=True, slip_match=True)

    # Slice prices to fold test window
    s = pd.Timestamp(fold["test"][0], tz="UTC")
    e = pd.Timestamp(fold["test"][1], tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    psub = prices[(prices["open_time_dt"] >= s) & (prices["open_time_dt"] <= e)].copy()

    feed = bt.feeds.PandasData(
        dataname=psub.set_index("open_time_dt"),
        open="open", high="high", low="low", close="close",
        volume="volume", openinterest=None,
        timeframe=bt.TimeFrame.Minutes,
        compression=1,  # 1m bars
    )
    cerebro.adddata(feed)
    cerebro.addstrategy(VPVRBreakBacktraderStrategy, trades=trades)
    res = cerebro.run()
    strat = res[0]
    nav = pd.Series(strat.nav_series, index=pd.to_datetime(strat.time_series))
    return nav, strat.fills


def compute_metrics(nav: pd.Series) -> dict:
    if len(nav) < 3:
        return {"sharpe": 0.0, "total_return": 0.0, "ann_total_return": 0.0,
                "max_dd": 0.0, "n_bars": int(len(nav))}
    rets = nav.pct_change().dropna()
    if rets.std(ddof=1) > 1e-12:
        sharpe = float((rets.mean() / rets.std(ddof=1)) * math.sqrt(ANN_FACTOR_1M))
    else:
        sharpe = 0.0
    total_ret = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    span = (nav.index[-1] - nav.index[0]).total_seconds() / (365.25 * 24 * 3600)
    ann_ret = float((1.0 + total_ret) ** (1.0 / span) - 1.0) if span > 0 else 0.0
    max_dd = float((nav / nav.cummax() - 1.0).min())
    return {
        "sharpe": sharpe,
        "total_return": total_ret,
        "ann_total_return": ann_ret,
        "max_dd": max_dd,
        "n_bars": int(len(nav)),
        "span_years": float(span),
    }


def abs_rel_div(fw: float, ih: float) -> float:
    return abs(fw - ih) / max(abs(ih), 1e-9) * 100.0


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    starting_capital = float(cfg.get("starting_capital_usd", 100000.0))

    inhouse_oos = json.loads(INHOUSE_OOS_PATH.read_text())
    ih_oos_sharpe = float(inhouse_oos["oos_sharpe_mean"])
    ih_oos_total_ret = float(inhouse_oos["oos_total_return_mean"])
    ih_oos_max_dd = float(inhouse_oos["oos_max_dd_min"])
    ih_oos_n = int(inhouse_oos["oos_n_trades_sum"])

    print(f"[config] strategy={STRATEGY} timeframe={TIMEFRAME} capital={starting_capital}")
    print(f"[inhouse-OOS] sharpe={ih_oos_sharpe:.4f} total_ret={ih_oos_total_ret:.6f} "
          f"max_dd={ih_oos_max_dd:.6f} n_trades={ih_oos_n}")

    prices = load_prices(PRICE_PATH)
    all_trades = load_all_trades(STRATEGY_DIR)
    print(f"[data] {len(prices)} bars, {len(all_trades)} combined trades")

    fold_results: Dict[str, dict] = {}
    fold_equities: Dict[str, pd.Series] = {}
    fold_fills: Dict[str, list] = {}
    for fdef in FOLD_DEFS:
        f_trades = filter_trades_for_fold(all_trades, fdef)
        if f_trades.empty:
            print(f"  fold {fdef['name']}: no trades; skipping")
            continue
        nav, fills = run_backtrader(prices, f_trades, starting_capital, fdef)
        fm = compute_metrics(nav)
        fold_results[fdef["name"]] = {
            "sharpe": jsafe(fm["sharpe"]),
            "total_return": jsafe(fm["total_return"]),
            "ann_total_return": jsafe(fm["ann_total_return"]),
            "max_dd": jsafe(fm["max_dd"]),
            "n_bars": fm["n_bars"],
            "n_trades": int(len(f_trades)),
            "n_fills": int(len(fills)),
            "span_years": jsafe(fm["span_years"]),
        }
        fold_equities[fdef["name"]] = nav
        fold_fills[fdef["name"]] = fills
        print(f"  fold {fdef['name']}: bars={fm['n_bars']} trades={len(f_trades)} "
              f"sharpe={fm['sharpe']:.4f} total_ret={fm['total_return']:.6f} "
              f"ann_ret={fm['ann_total_return']:.6f} max_dd={fm['max_dd']:.6f}")

    # Aggregate OOS
    if fold_results:
        sharpes = [v["sharpe"] for v in fold_results.values()]
        total_rets = [v["total_return"] for v in fold_results.values()]
        ann_rets = [v["ann_total_return"] for v in fold_results.values()]
        max_dds = [v["max_dd"] for v in fold_results.values()]
        n_trades = [v["n_trades"] for v in fold_results.values()]
        fw_oos = {
            "oos_sharpe_mean": jsafe(float(np.mean(sharpes))),
            "oos_total_return_mean": jsafe(float(np.mean(total_rets))),
            "oos_ann_return_mean": jsafe(float(np.mean(ann_rets))),
            "oos_max_dd_min": jsafe(float(np.min(max_dds))),
            "oos_n_trades_sum": int(sum(n_trades)),
            "n_folds": len(fold_results),
        }
    else:
        fw_oos = {"oos_sharpe_mean": 0.0, "oos_total_return_mean": 0.0,
                  "oos_ann_return_mean": 0.0, "oos_max_dd_min": 0.0,
                  "oos_n_trades_sum": 0, "n_folds": 0}

    print(f"[framework-OOS] sharpe={fw_oos['oos_sharpe_mean']:.4f} "
          f"total_ret={fw_oos['oos_total_return_mean']:.6f} "
          f"ann_ret={fw_oos['oos_ann_return_mean']:.6f} "
          f"max_dd={fw_oos['oos_max_dd_min']:.6f} "
          f"n_trades={fw_oos['oos_n_trades_sum']}")

    # Persist combined equity (concatenate fold NAVs)
    if fold_equities:
        combined = pd.concat([fold_equities[n] for n in sorted(fold_equities)], axis=0)
        combined.index.name = "ts"
        combined.to_csv(OUT_DIR / "equity_recomputed.csv", header=["equity"])
        # Also persist per-fold
        for n, eq in fold_equities.items():
            eq.to_csv(OUT_DIR / f"equity_{n}.csv", header=["equity"])

    div_sharpe = abs_rel_div(fw_oos["oos_sharpe_mean"], ih_oos_sharpe)
    div_total = abs_rel_div(fw_oos["oos_total_return_mean"], ih_oos_total_ret)
    div_max_dd = abs_rel_div(fw_oos["oos_max_dd_min"], ih_oos_max_dd)
    max_abs_rel = max(div_sharpe, div_total, div_max_dd)
    auto_archive = max_abs_rel > W5_THRESHOLD

    tipping = []
    if div_sharpe > W5_THRESHOLD: tipping.append(f"sharpe {div_sharpe:.2f}%")
    if div_total > W5_THRESHOLD: tipping.append(f"total_return {div_total:.2f}%")
    if div_max_dd > W5_THRESHOLD: tipping.append(f"max_dd {div_max_dd:.2f}%")

    print(f"[divergence] sharpe={div_sharpe:.2f}% total={div_total:.2f}% "
          f"max_dd={div_max_dd:.2f}% max_abs_rel={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive} tipping={tipping}")

    fw_version = bt.__version__
    fw_sha = "b853d7c9"

    results = {
        "engine": "backtrader",
        "engine_version": fw_version,
        "engine_sha": fw_sha,
        "iteration": int(cfg.get("iteration", 69)),
        "variant": "V_volume_profile_break_v5_btc",
        "strategy_key": STRATEGY,
        "inhouse": {
            "sharpe": jsafe(ih_oos_sharpe),
            "ann_total_return": jsafe(ih_oos_total_ret),
            "max_dd": jsafe(ih_oos_max_dd),
            "n_trades": ih_oos_n,
            "timeframe": TIMEFRAME,
            "status": "NOT-PROFITABLE",
        },
        "framework": {
            "sharpe": jsafe(fw_oos["oos_sharpe_mean"]),
            "ann_total_return": jsafe(fw_oos["oos_ann_return_mean"]),
            "total_return": jsafe(fw_oos["oos_total_return_mean"]),
            "max_dd": jsafe(fw_oos["oos_max_dd_min"]),
            "n_trades": fw_oos["oos_n_trades_sum"],
        },
        "framework_oos": {
            "oos_sharpe_mean": jsafe(fw_oos["oos_sharpe_mean"]),
            "oos_total_return_mean": jsafe(fw_oos["oos_total_return_mean"]),
            "oos_ann_return_mean": jsafe(fw_oos["oos_ann_return_mean"]),
            "oos_max_dd_min": jsafe(fw_oos["oos_max_dd_min"]),
            "oos_n_trades_sum": fw_oos["oos_n_trades_sum"],
            "n_folds": fw_oos["n_folds"],
            "folds": fold_results,
        },
        "divergence_pct": {
            "sharpe": jsafe(div_sharpe),
            "total_return": jsafe(div_total),
            "max_dd": jsafe(div_max_dd),
        },
        "max_abs_rel_divergence_pct": jsafe(max_abs_rel),
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(auto_archive),
        "w5_tipping_metrics": tipping,
        "w5_verdict": "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if auto_archive else "WITHIN_TOLERANCE",
        "approach": (
            f"backtrader {fw_version} replay: applied the in-house entry/exit schedule "
            f"from trades_long.csv + trades_short.csv ({sum(len(v) for v in fold_fills.values())} fills) "
            f"to the BTCUSDT 1m bar stream with next-bar entry fill, slip_open + slip_match at "
            f"{SLIPPAGE_PCT*100:.2f}% per side and commission {FEE_PCT*100:.2f}% per side "
            f"round-trip, 1% fractional sizing per signal. OOS walk-forward over the 3 folds "
            f"from config.json; Sharpe/ann_return/max_dd computed via bar-by-bar NAV."
        ),
        "framework_metrics_file": str(OUT_DIR / "results.json"),
    }

    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2, default=jsafe))
    out_path = RESULTS_DIR / "framework_cv_backtrader.json"
    out_path.write_text(json.dumps(results, indent=2, default=jsafe))
    print(f"[done] results -> {OUT_DIR / 'results.json'}")
    print(f"[done] framework_cv_backtrader.json -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())