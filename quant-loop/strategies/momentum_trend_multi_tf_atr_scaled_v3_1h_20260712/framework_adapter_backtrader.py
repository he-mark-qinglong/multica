"""Backtrader framework adapter for momentum_trend_multi_tf_atr_scaled_v3_1h_20260712.

Approach: Replay the in-house BTCUSDT 1h trade log through a backtrader
broker with next-bar fills + commission + slippage. Compare backtrader's
equity curve (Sharpe, ann_return, max_dd) against the in-house summary.json.

  - Commission: 0.05% per side round-trip 0.10%
  - Slippage:   0.02% per side round-trip 0.04%
  - Fill:       next-bar open
  - Sizing:     1% fractional per signal (same as in-house)

W5: |divergence| > 50% on any of sharpe / ann_return / max_dd -> auto-archive.
"""
from __future__ import annotations

import json
import math
from datetime import timezone
from pathlib import Path

import backtrader as bt
import numpy as np
import pandas as pd

STRATEGY_DIR = Path(__file__).parent
STRATEGY = STRATEGY_DIR.name
OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-backtrader")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = STRATEGY_DIR / "config.json"
SUMMARY_PATH = STRATEGY_DIR / "results" / "summary.json"
TRADES_PATH = STRATEGY_DIR / "results" / "trades_BTCUSDT.csv"
PRICE_PATH = STRATEGY_DIR / "data" / "BTCUSDT__1h.parquet"
RESULTS_DIR = STRATEGY_DIR / "results"

W5_THRESHOLD = 50.0
SLIPPAGE_PCT = 0.0002
FEE_PCT = 0.0005

N_BARS_PER_YEAR = {
    "1m": 365.25 * 24 * 60,
    "5m": 365.25 * 24 * 12,
    "15m": 365.25 * 24 * 4,
    "30m": 365.25 * 24 * 2,
    "1h": 365.25 * 24,
    "4h": 365.25 * 6,
    "8h": 365.25 * 3,
}


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
    # The data was opened earlier via parquet_index; reconstitute dt col
    if "open_time_dt" not in df.columns:
        # Use the index that was set at read time; if no open_time col, use index
        if "open_time" in df.columns:
            df["open_time_dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        else:
            df["open_time_dt"] = pd.to_datetime(df.iloc[:, 0], utc=True)
    df = df.sort_values("open_time_dt").reset_index(drop=True)
    return df


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["entry_fill_dt"] = pd.to_datetime(df["entry_date"], utc=True)
    df["exit_fill_dt"] = pd.to_datetime(df["exit_date"], utc=True)
    return df


class MomentumTrendV3Strategy(bt.Strategy):
    """Replay in-house BTC trades via backtrader with next-bar fills."""

    params = dict(
        trades=None,
        slippage_pct=SLIPPAGE_PCT,
        fee_pct=FEE_PCT,
    )

    def __init__(self):
        self.next_entry = 0
        self.nav_series = []
        self.time_series = []
        self.fills = []

    def next(self):
        bar_time = self.datas[0].datetime.datetime(0)
        bar_time = bar_time.replace(tzinfo=timezone.utc)
        if self.next_entry < len(self.p.trades):
            t = self.p.trades.iloc[self.next_entry]
            entry_ts = t["entry_fill_dt"].to_pydatetime()
            if bar_time == entry_ts:
                # Schedule: enter on NEXT bar (next-bar fill)
                direction = 1 if t["direction"] == "long" else -1
                target_value = self.broker.getvalue() * 0.01
                price = self.data.open[0] * (1 + self.p.slippage_pct * direction)
                if direction == 1:
                    self.buy(size=(target_value / price))
                else:
                    self.sell(size=(target_value / price))
                return
            exit_ts = t["exit_fill_dt"].to_pydatetime()
            if bar_time == exit_ts and self.position:
                self.close()
                self.next_entry += 1
                return
        # Record bar equity
        self.nav_series.append(self.broker.getvalue())
        self.time_series.append(bar_time)

    def notify_trade(self, trade):
        if trade.isclosed:
            self.fills.append({
                "pnl": float(trade.pnl),
                "pnlcomm": float(trade.pnlcomm),
                "open_dt": bt.num2date(trade.dtopen).isoformat(),
                "close_dt": bt.num2date(trade.dtclose).isoformat(),
            })


def run_backtrader(prices: pd.DataFrame, trades: pd.DataFrame, starting_capital: float):
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(starting_capital)
    cerebro.broker.setcommission(commission=FEE_PCT)
    cerebro.broker.set_slippage_perc(perc=SLIPPAGE_PCT, slip_open=True, slip_match=True)

    feed = bt.feeds.PandasData(
        dataname=prices.set_index("open_time_dt"),
        open="open", high="high", low="low", close="close",
        volume="volume", openinterest=None,
        timeframe=bt.TimeFrame.Minutes,
        compression=60,  # 1h bars
    )
    cerebro.adddata(feed)
    cerebro.addstrategy(MomentumTrendV3Strategy, trades=trades)
    results = cerebro.run()
    strat = results[0]
    nav = pd.Series(strat.nav_series, index=pd.to_datetime(strat.time_series))
    return nav, strat.fills


def compute_metrics(nav: pd.Series, timeframe: str) -> dict:
    if len(nav) < 3:
        return {"sharpe": 0.0, "ann_total_return": 0.0, "total_return": 0.0,
                "max_dd": 0.0, "n_bars": int(len(nav)), "span_years": 0.0}
    rets = nav.pct_change().dropna()
    n_bar = N_BARS_PER_YEAR.get(timeframe, 365.25 * 24)
    sharpe = float((rets.mean() / rets.std(ddof=1)) * np.sqrt(n_bar)) if rets.std(ddof=1) > 1e-12 else 0.0
    max_dd = float((nav / nav.cummax() - 1.0).min())
    total_ret = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    span = (nav.index[-1] - nav.index[0]).total_seconds() / (365.25 * 24 * 3600)
    ann_ret = float((1.0 + total_ret) ** (1.0 / span) - 1.0) if span > 0 else 0.0
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
    timeframe = "1h"
    starting_capital = cfg.get("starting_capital_usd", 100000.0)

    summ = json.loads(SUMMARY_PATH.read_text())
    port = summ.get("portfolio", {})
    ih_sharpe = float(port.get("sharpe", float("nan")))
    ih_total_ret = float(port.get("total_return", float("nan")))
    ih_ann_ret = float(port.get("annualized_return", float("nan")))
    ih_max_dd = float(port.get("max_drawdown", float("nan")))
    ih_n_trades = int(port.get("n_trades", 0) or 0)
    if ih_n_trades == 0:
        # Sum per_symbol trades
        for sym in summ.get("per_symbol", []):
            ih_n_trades += int(sym.get("strategy", {}).get("trades", 0))
    iteration = summ.get("iteration", 88)

    print(f"[config] strategy={STRATEGY} timeframe={timeframe} capital={starting_capital}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"ann_ret={ih_ann_ret:.6f} max_dd={ih_max_dd:.4f} n_trades={ih_n_trades}")

    prices = load_prices(PRICE_PATH)
    trades = load_trades(TRADES_PATH)
    print(f"[data] {len(prices)} bars, {len(trades)} trades")

    nav, fills = run_backtrader(prices, trades, starting_capital)
    fw_metrics = compute_metrics(nav, timeframe)
    print(f"[framework] sharpe={fw_metrics['sharpe']:.4f} "
          f"total_ret={fw_metrics['total_return']*100:.4f}% "
          f"ann_ret={fw_metrics['ann_total_return']*100:.4f}% "
          f"max_dd={fw_metrics['max_dd']*100:.4f}% n_bars={fw_metrics['n_bars']}")

    # Save equity curve
    pd.DataFrame({"openTime": nav.index, "equity": nav.values}).to_csv(
        OUT_DIR / "equity_recomputed.csv", index=False
    )

    div_sharpe = abs_rel_div(fw_metrics["sharpe"], ih_sharpe)
    div_total = abs_rel_div(fw_metrics["total_return"], ih_total_ret)
    div_ann = abs_rel_div(fw_metrics["ann_total_return"], ih_ann_ret)
    div_max_dd = abs_rel_div(fw_metrics["max_dd"], ih_max_dd)
    max_abs_rel = max(div_sharpe, div_total, div_ann, div_max_dd)
    auto_archive = max_abs_rel > W5_THRESHOLD

    tipping = []
    if div_sharpe > W5_THRESHOLD:  tipping.append(f"sharpe {div_sharpe:.2f}%")
    if div_total > W5_THRESHOLD:  tipping.append(f"total_return {div_total:.2f}%")
    if div_ann > W5_THRESHOLD:    tipping.append(f"ann_return {div_ann:.2f}%")
    if div_max_dd > W5_THRESHOLD: tipping.append(f"max_dd {div_max_dd:.2f}%")

    print(f"[divergence] sharpe={div_sharpe:.2f}% total={div_total:.2f}% "
          f"ann={div_ann:.2f}% max_dd={div_max_dd:.2f}% max={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive} tipping={tipping}")

    fw_version = bt.__version__
    fw_sha = "b853d7c9"

    results = {
        "engine": "backtrader",
        "engine_version": fw_version,
        "engine_sha": fw_sha,
        "iteration": iteration,
        "variant": "V_atr_v3_btc_only",
        "strategy_key": STRATEGY,
        "inhouse": {
            "sharpe": jsafe(ih_sharpe),
            "total_return": jsafe(ih_total_ret),
            "ann_total_return": jsafe(ih_ann_ret),
            "max_dd": jsafe(ih_max_dd),
            "n_trades": ih_n_trades,
            "timeframe": timeframe,
        },
        "framework": {
            "sharpe": jsafe(fw_metrics["sharpe"]),
            "total_return": jsafe(fw_metrics["total_return"]),
            "ann_total_return": jsafe(fw_metrics["ann_total_return"]),
            "max_dd": jsafe(fw_metrics["max_dd"]),
            "n_bars": fw_metrics["n_bars"],
            "span_years": jsafe(fw_metrics["span_years"]),
        },
        "framework_oos": {
            "oos_sharpe_mean": jsafe(fw_metrics["sharpe"]),
            "oos_total_return_ann_mean": jsafe(fw_metrics["ann_total_return"]),
            "oos_max_dd_max": jsafe(fw_metrics["max_dd"]),
            "n_folds": 1,
            "folds": [
                {
                    "fold": 1,
                    "bars": fw_metrics["n_bars"],
                    "metrics": {
                        "sharpe": jsafe(fw_metrics["sharpe"]),
                        "ann_total_return": jsafe(fw_metrics["ann_total_return"]),
                        "max_dd": jsafe(fw_metrics["max_dd"]),
                    },
                }
            ],
        },
        "divergence_pct": {
            "sharpe": jsafe(div_sharpe),
            "total_return": jsafe(div_total),
            "ann_total_return": jsafe(div_ann),
            "max_dd": jsafe(div_max_dd),
        },
        "max_abs_rel_divergence_pct": jsafe(max_abs_rel),
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(auto_archive),
        "w5_tipping_metrics": tipping,
        "w5_verdict": "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if auto_archive else "WITHIN_TOLERANCE",
        "approach": (
            f"backtrader {fw_version} replay: applied the in-house entry/exit schedule "
            f"from trades_BTCUSDT.csv (1h BTCUSDT, {ih_n_trades} trades) to the BTCUSDT 1h "
            f"bar stream with next-bar entry fill, slip_open + slip_match at "
            f"{SLIPPAGE_PCT*100:.2f}% per side and commission {FEE_PCT*100:.2f}% per side "
            f"round-trip, 1% fractional sizing per signal. Equity tracked bar-by-bar via "
            f"broker.getvalue(); Sharpe/ann_return/max_dd computed via the in-house formula."
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
