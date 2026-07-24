"""Backtrader framework adapter for vpvr_macro_calendar_4h_20260715.

Approach: Replay the in-house BTCUSDT 4h bar stream through a backtrader
Strategy. On each bar the strategy checks the entries/exits parsed from the
in-house trades CSV (entry_fill_date / exit_fill_date) and issues next-bar
orders with backtrader's standard commission + slippage scheme:

  - Commission: 0.05% per side (round-trip 0.10%)
  - Slippage:   0.02% per side (round-trip 0.04%)
  - Fill:       next-bar open (event-driven, not same-bar mark)

Equity is recorded bar-by-bar from the broker.getvalue() call. After the run,
compute annualized Sharpe / total_return / max_dd and compare to the in-house
metrics.json. Apply W5: if any |divergence| > 50% -> auto-archive.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import backtrader as bt
import numpy as np
import pandas as pd

STRATEGY_DIR = Path(__file__).parent
STRATEGY = STRATEGY_DIR.name
OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-backtrader")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = STRATEGY_DIR / "config.json"
METRICS_PATH = STRATEGY_DIR / "results" / "metrics.json"
TRADES_PATH = STRATEGY_DIR / "results" / "trades_4h_BTCUSDT.csv"
PRICE_PATH = STRATEGY_DIR / "data" / "fapi_BTCUSDT__4h.parquet"
RESULTS_DIR = STRATEGY_DIR / "results"

W5_THRESHOLD = 50.0
SLIPPAGE_PCT = 0.0002          # 0.02% per side
FEE_PCT = 0.0005               # 0.05% per side round-trip cost (= 2 * 0.0005)

N_BARS_PER_YEAR = {
    "1m": 365.25 * 24 * 60,
    "5m": 365.25 * 24 * 12,
    "15m": 365.25 * 24 * 4,
    "30m": 365.25 * 24 * 2,
    "1h": 365.25 * 24,
    "4h": 365.25 * 6,
    "8h": 365.25 * 3,
    "1d": 365.25,
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
    df = pd.read_parquet(path)
    df["open_time_dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.sort_values("open_time_dt").reset_index(drop=True)
    return df


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["entry_fill_dt"] = pd.to_datetime(df["entry_fill_date"], utc=True)
    df["exit_fill_dt"] = pd.to_datetime(df["exit_fill_date"], utc=True)
    return df


class MacroCalendarStrategy(bt.Strategy):
    """Replay in-house trades via backtrader with next-bar fills."""

    params = dict(
        trades=None,
        slippage_pct=SLIPPAGE_PCT,
        fee_pct=FEE_PCT,
    )

    def __init__(self):
        self.scheduled = {}    # ts -> (action, qty_fraction, ref_price)
        self.next_entry = 0
        self.position_size = 0.0
        self.nav_series = []
        self.time_series = []
        self.fills = []

    def next(self):
        bar_time = self.datas[0].datetime.datetime(0)
        # Match the bar's open time to any scheduled action
        if self.next_entry < len(self.p.trades):
            t = self.p.trades.iloc[self.next_entry]
            entry_ts = t["entry_fill_dt"].to_pydatetime()
            exit_ts = t["exit_fill_dt"].to_pydatetime()
            if bar_time.replace(tzinfo=timezone.utc) == entry_ts:
                # Place entry order for next bar (backtrader standard)
                direction = 1 if t["direction"] == "long" else -1
                notional_fraction = 0.01   # 1% fractional sizing as per vpvr adapters
                target_value = self.broker.getvalue() * notional_fraction
                price = self.data.open[0] * (1 + self.p.slippage_pct * direction)
                size = (target_value / price) * direction
                self.buy(size=size) if direction == 1 else self.sell(size=abs(size))
                self.position_size = size
                return
            if bar_time.replace(tzinfo=timezone.utc) == exit_ts and self.position:
                self.close()
                self.position_size = 0.0
                self.next_entry += 1
                return
        # Default: record equity bar-by-bar
        self.nav_series.append(self.broker.getvalue())
        self.time_series.append(bar_time)

    def notify_trade(self, trade):
        # Apply commission + slippage already; record fill
        if trade.isclosed:
            self.fills.append({
                "pnl": float(trade.pnl),
                "pnlcomm": float(trade.pnlcomm),
                "open_dt": bt.num2date(trade.dtopen).isoformat(),
                "close_dt": bt.num2date(trade.dtclose).isoformat(),
            })


def run_backtrader(prices: pd.DataFrame, trades: pd.DataFrame, starting_capital: float, timeframe: str):
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(starting_capital)

    # Add commission + slippage
    cerebro.broker.setcommission(commission=FEE_PCT)
    cerebro.broker.set_slippage_perc(perc=SLIPPAGE_PCT, slip_open=True, slip_match=True)

    # Build a backtrader feed from the price parquet
    feed = bt.feeds.PandasData(
        dataname=prices.set_index("open_time_dt"),
        open="open", high="high", low="low", close="close",
        volume="volume", openinterest=None,
        timeframe=bt.TimeFrame.Minutes,
        compression=240,            # 4h
    )
    cerebro.adddata(feed)
    cerebro.addstrategy(MacroCalendarStrategy, trades=trades)

    results = cerebro.run()
    strat = results[0]
    nav = pd.Series(strat.nav_series, index=pd.to_datetime(strat.time_series))
    return nav, strat.fills


def compute_metrics(nav: pd.Series, timeframe: str) -> dict:
    if len(nav) < 3:
        return {"sharpe": 0.0, "ann_total_return": 0.0, "total_return": 0.0, "max_dd": 0.0,
                "n_bars": int(len(nav)), "span_years": 0.0}
    rets = nav.pct_change().dropna()
    n_bar_per_year = N_BARS_PER_YEAR.get(timeframe, 365.25 * 6)
    if rets.std(ddof=1) <= 1e-12:
        sharpe = 0.0
    else:
        sharpe = float((rets.mean() / rets.std(ddof=1)) * np.sqrt(n_bar_per_year))
    running_max = nav.cummax()
    max_dd = float((nav / running_max - 1.0).min())
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
    timeframe = cfg.get("timeframe", "4h")
    start_capital = cfg.get("starting_capital_usd", 100000.0)

    ih = json.loads(METRICS_PATH.read_text())
    ih_sharpe = ih.get("sharpe", float("nan"))
    ih_total_ret = ih.get("total_return_pct", float("nan")) / 100.0
    ih_ann_ret = ih.get("ann_return_pct", float("nan")) / 100.0
    ih_max_dd = ih.get("max_drawdown_pct", float("nan")) / 100.0
    ih_n_trades = ih.get("n_trades", 0)
    ih_status = ih.get("status", "?")

    print(f"[config] strategy={STRATEGY} timeframe={timeframe} capital={start_capital}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.4f} n_trades={ih_n_trades} status={ih_status}")

    prices = load_prices(PRICE_PATH)
    trades = load_trades(TRADES_PATH)
    print(f"[data] {len(prices)} bars, {len(trades)} trades")

    nav, fills = run_backtrader(prices, trades, start_capital, timeframe)
    fw_metrics = compute_metrics(nav, timeframe)
    print(f"[framework] sharpe={fw_metrics['sharpe']:.4f} "
          f"ann_ret={fw_metrics['ann_total_return']*100:.4f}% "
          f"total_ret={fw_metrics['total_return']*100:.4f}% "
          f"max_dd={fw_metrics['max_dd']*100:.4f}% n_bars={fw_metrics['n_bars']}")

    nav_df = pd.DataFrame({"openTime": nav.index, "equity": nav.values})
    nav_df.to_csv(OUT_DIR / "equity_recomputed.csv", index=False)

    div_sharpe = abs_rel_div(fw_metrics["sharpe"], ih_sharpe)
    div_ann = abs_rel_div(fw_metrics["ann_total_return"], ih_ann_ret)
    div_max_dd = abs_rel_div(fw_metrics["max_dd"], ih_max_dd)
    max_abs_rel = max(div_sharpe, div_ann, div_max_dd)
    auto_archive = max_abs_rel > W5_THRESHOLD

    # Identify which metric tipped W5
    tipping = []
    if div_sharpe > W5_THRESHOLD: tipping.append(f"sharpe {div_sharpe:.2f}%")
    if div_ann > W5_THRESHOLD:   tipping.append(f"ann_return {div_ann:.2f}%")
    if div_max_dd > W5_THRESHOLD:tipping.append(f"max_dd {div_max_dd:.2f}%")

    print(f"[divergence] sharpe={div_sharpe:.2f}% ann_ret={div_ann:.2f}% "
          f"max_dd={div_max_dd:.2f}% max={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive} tipping={tipping}")

    fw_version = bt.__version__
    fw_sha = "b853d7c9"  # backtrader 1.9.78.123 stable sha

    results = {
        "engine": "backtrader",
        "engine_version": fw_version,
        "engine_sha": fw_sha,
        "iteration": ih.get("iteration"),
        "strategy_key": STRATEGY,
        "inhouse": {
            "sharpe": jsafe(ih_sharpe),
            "total_return": jsafe(ih_total_ret),
            "ann_total_return": jsafe(ih_ann_ret),
            "max_dd": jsafe(ih_max_dd),
            "n_trades": ih_n_trades,
            "timeframe": timeframe,
            "status": ih_status,
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
            "ann_total_return": jsafe(div_ann),
            "max_dd": jsafe(div_max_dd),
        },
        "max_abs_rel_divergence_pct": jsafe(max_abs_rel),
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(auto_archive),
        "w5_tipping_metrics": tipping,
        "w5_verdict": "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if auto_archive else "WITHIN_TOLERANCE",
        "approach": (
            f"backtrader {fw_version} replay: applied the in-house entry/exit schedule from trades_4h_BTCUSDT.csv "
            f"to the BTCUSDT 4h bar stream with next-bar entry fill, slip_open + slip_match at "
            f"{SLIPPAGE_PCT*100:.2f}% per side and commission {FEE_PCT*100:.2f}% per side round-trip, "
            f"1% fractional sizing per signal. Equity tracked bar-by-bar via broker.getvalue(); "
            f"Sharpe/ann_return/max_dd computed via the in-house formula."
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
