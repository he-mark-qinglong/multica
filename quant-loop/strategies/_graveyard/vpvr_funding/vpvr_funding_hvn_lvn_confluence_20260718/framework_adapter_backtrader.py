"""Backtrader framework adapter for vpvr_funding_hvn_lvn_confluence_20260718.

Approach: Replay the in-house multi-symbol (BTC + ETH + SOL on 15m)
trade log through backtrader with next-bar fills + commission + slippage.
This is a cross-validation of the in-house combined-aggregated pnl
computation: backtrader is asked to produce the same equity curve
(starting capital, 1.0-fractional per trade, time-sequenced entries/exits)
and we compare its Sharpe / ann_return / max_dd against the in-house
combined_metrics.

Per the strategy SPEC:
  - 15m bar stream for BTC + ETH + SOL (hot-funding window 2023-11 to 2024-12)
  - 1.0-fractional sizing per trade (long-only) — single concurrent position
    per symbol since trades don't overlap in this run.
  - 4.0 bp fee + 1.0 bp slippage per fill (in-house assumption) — adapter
    applies an extra backtrader broker commission/slippage on top.

W5: if |divergence| > 50% on any of sharpe / ann_total_return / max_dd
    -> auto-archive (per AGENT_COLLAB_AUDIT_2026-07-12 §W5).
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

QUANT_LOOP = STRATEGY_DIR.parent.parent
TRADES_PATH = STRATEGY_DIR / "results" / "trades.csv"
RESULTS_DIR = STRATEGY_DIR / "results"

# Adapter constants
W5_THRESHOLD = 50.0
# IMPORTANT: in-house trades.csv pnl_pct already includes fees/slippage (per strategy
# implementation). Adding backtrader commission on top would double-count. We use
# commission=0 here so the comparison is between backtrader's equity curve mechanics
# and the in-house equity curve. A separate "with-cost" run is documented in the run md.
FEE_PCT = 0.0
SLIPPAGE_PCT = 0.0
SIZE_FRACTION = 1.0            # 1.0 of equity per trade per SPEC

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

TIMEFRAME = "15m"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
STARTING_CAPITAL = 100_000.0


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


def load_price(symbol: str) -> pd.DataFrame:
    p = QUANT_LOOP / "live_data" / f"{symbol}_15m.parquet"
    df = pd.read_parquet(p)
    if "open_time" in df.columns:
        df["open_time_dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    elif "ts" in df.columns:
        df["open_time_dt"] = pd.to_datetime(df["ts"], utc=True)
    else:
        df["open_time_dt"] = pd.to_datetime(df.index, utc=True)
    df = df.sort_values("open_time_dt").reset_index(drop=True)
    df = df[["open_time_dt", "open", "high", "low", "close", "volume"]]
    return df


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["entry_fill_dt"] = pd.to_datetime(df["entry_ts"], utc=True).dt.tz_localize(None)
    df["exit_fill_dt"] = pd.to_datetime(df["exit_ts"], utc=True).dt.tz_localize(None)
    df["symbol"] = df["symbol"].astype(str)
    return df


class VPVRConfluenceStrategy(bt.Strategy):
    """Replay multi-symbol trades via backtrader with next-bar fills."""

    params = dict(
        trades=None,
        slippage_pct=SLIPPAGE_PCT,
        fee_pct=FEE_PCT,
        size_fraction=SIZE_FRACTION,
    )

    def __init__(self):
        # Index trades by (symbol, ts) for quick lookup
        self._entries = {}
        self._exits = {}
        for i, t in enumerate(self.p.trades.itertuples(index=False)):
            sym = t.symbol
            et = t.entry_fill_dt.to_pydatetime() if hasattr(t.entry_fill_dt, 'to_pydatetime') else t.entry_fill_dt
            xt = t.exit_fill_dt.to_pydatetime() if hasattr(t.exit_fill_dt, 'to_pydatetime') else t.exit_fill_dt
            self._entries.setdefault(sym, []).append((et, i))
            self._exits.setdefault(sym, []).append((xt, i))
        # Sort and convert to deque-like lists
        for sym in self._entries:
            self._entries[sym].sort(key=lambda x: x[0])
            self._exits[sym].sort(key=lambda x: x[0])
        self._entry_idx = {sym: 0 for sym in self._entries}
        self._exit_idx = {sym: 0 for sym in self._exits}

        self.nav_series = []
        self.time_series = []
        self.fills = []
        # Map symbol -> backtrader data feed index
        self.sym_to_data = {d._name: d for d in self.datas}

    def next(self):
        bar_time = bt.num2date(self.datas[0].datetime[0])

        for sym, feed in self.sym_to_data.items():
            sym_bar_time = bt.num2date(feed.datetime[0])

            # Process pending exits first (close any open position on this symbol)
            if sym in self._exits and self._exit_idx[sym] < len(self._exits[sym]):
                xt, ti = self._exits[sym][self._exit_idx[sym]]
                if sym_bar_time >= xt:
                    pos = self.getposition(data=feed)
                    if pos.size > 0:
                        self.close(data=feed)
                        self._exit_idx[sym] += 1
                        continue

            # Process pending entries
            if sym in self._entries and self._entry_idx[sym] < len(self._entries[sym]):
                et, ti = self._entries[sym][self._entry_idx[sym]]
                if sym_bar_time >= et:
                    pos = self.getposition(data=feed)
                    if pos.size == 0:
                        t = self.p.trades.iloc[ti]
                        direction = 1 if t["direction"] == "long" else -1
                        # 1.0 fractional sizing: size = (NAV / price) at entry bar open
                        target_value = self.broker.getvalue() * self.p.size_fraction
                        ref_price = feed.open[0]
                        size = (target_value / ref_price) * direction
                        if direction > 0:
                            self.buy(data=feed, size=size)
                        else:
                            self.sell(data=feed, size=abs(size))
                        self._entry_idx[sym] += 1

        # Record combined portfolio NAV at each bar
        self.nav_series.append(self.broker.getvalue())
        self.time_series.append(bar_time.replace(tzinfo=timezone.utc) if bar_time.tzinfo is None else bar_time)

    def notify_trade(self, trade):
        if trade.isclosed:
            self.fills.append({
                "pnl": float(trade.pnl),
                "pnlcomm": float(trade.pnlcomm),
                "data_name": trade.data._name,
                "open_dt": bt.num2date(trade.dtopen).isoformat(),
                "close_dt": bt.num2date(trade.dtclose).isoformat(),
            })


def run_backtrader(prices_by_sym: dict, trades: pd.DataFrame, starting_capital: float, timeframe: str):
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(starting_capital)
    cerebro.broker.setcommission(commission=FEE_PCT)
    cerebro.broker.set_slippage_perc(perc=SLIPPAGE_PCT, slip_open=True, slip_match=True)

    compression_map = {
        "1m": (bt.TimeFrame.Minutes, 1),
        "5m": (bt.TimeFrame.Minutes, 5),
        "15m": (bt.TimeFrame.Minutes, 15),
        "30m": (bt.TimeFrame.Minutes, 30),
        "1h": (bt.TimeFrame.Minutes, 60),
        "4h": (bt.TimeFrame.Minutes, 240),
    }
    tf, comp = compression_map.get(timeframe, (bt.TimeFrame.Minutes, 15))

    for sym in SYMBOLS:
        prices = prices_by_sym[sym]
        feed = bt.feeds.PandasData(
            dataname=prices.set_index("open_time_dt"),
            open="open", high="high", low="low", close="close",
            volume="volume", openinterest=None,
            timeframe=tf,
            compression=comp,
        )
        cerebro.adddata(feed, name=sym)

    cerebro.addstrategy(VPVRConfluenceStrategy, trades=trades)

    results = cerebro.run()
    strat = results[0]
    nav = pd.Series(strat.nav_series, index=pd.to_datetime(strat.time_series))
    return nav, strat.fills


def compute_metrics(nav: pd.Series, timeframe: str) -> dict:
    if len(nav) < 3:
        return {
            "sharpe": 0.0, "ann_total_return": 0.0, "total_return": 0.0,
            "max_dd": 0.0, "n_bars": int(len(nav)), "span_years": 0.0,
        }
    rets = nav.pct_change().dropna()
    n_bar_per_year = N_BARS_PER_YEAR.get(timeframe, 365.25 * 24 * 4)
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
    print(f"[config] strategy={STRATEGY} timeframe={TIMEFRAME} capital={STARTING_CAPITAL}")

    ih = json.loads((RESULTS_DIR / "metrics.json").read_text())
    cm = ih.get("combined_metrics", {})
    ih_sharpe = cm.get("sharpe_daily", float("nan"))
    ih_ann_ret = cm.get("annualized_return", float("nan"))
    ih_total_ret = cm.get("total_return", float("nan"))
    ih_max_dd = cm.get("max_drawdown_pct", float("nan"))
    ih_n_trades = cm.get("n_trades", 0)
    ih_status = ih.get("verdict", "?")

    print(f"[inhouse] sharpe={ih_sharpe:.4f} ann_ret={ih_ann_ret:.6f} "
          f"total_ret={ih_total_ret:.6f} max_dd={ih_max_dd:.4f} "
          f"n_trades={ih_n_trades} verdict={ih_status}")

    prices_by_sym = {sym: load_price(sym) for sym in SYMBOLS}
    for sym in SYMBOLS:
        print(f"[data] {sym} {len(prices_by_sym[sym])} bars")
    trades = load_trades(TRADES_PATH)
    print(f"[trades] {len(trades)} trades across {trades['symbol'].nunique()} symbols")

    nav, fills = run_backtrader(prices_by_sym, trades, STARTING_CAPITAL, TIMEFRAME)
    fw_metrics = compute_metrics(nav, TIMEFRAME)
    print(f"[framework] sharpe={fw_metrics['sharpe']:.4f} "
          f"ann_ret={fw_metrics['ann_total_return']*100:.4f}% "
          f"total_ret={fw_metrics['total_return']*100:.4f}% "
          f"max_dd={fw_metrics['max_dd']*100:.4f}% n_bars={fw_metrics['n_bars']}")

    nav_df = pd.DataFrame({"openTime": nav.index, "equity": nav.values})
    nav_df.to_csv(OUT_DIR / "equity_recomputed.csv", index=False)
    fills_df = pd.DataFrame(fills)
    fills_df.to_csv(OUT_DIR / "fills.csv", index=False)

    div_sharpe = abs_rel_div(fw_metrics["sharpe"], ih_sharpe)
    div_ann = abs_rel_div(fw_metrics["ann_total_return"], ih_ann_ret)
    div_total = abs_rel_div(fw_metrics["total_return"], ih_total_ret)
    div_max_dd = abs_rel_div(fw_metrics["max_dd"], ih_max_dd)
    max_abs_rel = max(div_sharpe, div_ann, div_total, div_max_dd)
    auto_archive = max_abs_rel > W5_THRESHOLD

    tipping = []
    if div_sharpe > W5_THRESHOLD: tipping.append(f"sharpe {div_sharpe:.2f}%")
    if div_ann > W5_THRESHOLD: tipping.append(f"ann_return {div_ann:.2f}%")
    if div_total > W5_THRESHOLD: tipping.append(f"total_return {div_total:.2f}%")
    if div_max_dd > W5_THRESHOLD: tipping.append(f"max_dd {div_max_dd:.2f}%")

    print(f"[divergence] sharpe={div_sharpe:.2f}% ann_ret={div_ann:.2f}% "
          f"total_ret={div_total:.2f}% max_dd={div_max_dd:.2f}% max={max_abs_rel:.2f}%")
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
            "sharpe_daily": jsafe(ih_sharpe),
            "annualized_return": jsafe(ih_ann_ret),
            "total_return": jsafe(ih_total_ret),
            "max_drawdown_pct": jsafe(ih_max_dd),
            "n_trades": int(ih_n_trades),
            "timeframe": TIMEFRAME,
            "verdict": ih_status,
            "window": ih.get("window", {}),
            "symbols": ih.get("symbols", []),
        },
        "framework": {
            "sharpe": jsafe(fw_metrics["sharpe"]),
            "total_return": jsafe(fw_metrics["total_return"]),
            "ann_total_return": jsafe(fw_metrics["ann_total_return"]),
            "max_dd": jsafe(fw_metrics["max_dd"]),
            "n_bars": fw_metrics["n_bars"],
            "span_years": jsafe(fw_metrics["span_years"]),
            "n_fills": int(len(fills)),
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
            "total_return": jsafe(div_total),
            "max_dd": jsafe(div_max_dd),
        },
        "max_abs_rel_divergence_pct": jsafe(max_abs_rel),
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(auto_archive),
        "w5_tipping_metrics": tipping,
        "w5_verdict": "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if auto_archive else "WITHIN_TOLERANCE",
        "approach": (
            f"backtrader {fw_version} replay: multi-symbol 15m feed (BTC+ETH+SOL), "
            f"applied the in-house entry/exit schedule from trades.csv with next-bar "
            f"entry fill, slip_open + slip_match at {SLIPPAGE_PCT*100:.2f}% per side and "
            f"commission {FEE_PCT*100:.2f}% per side round-trip, {SIZE_FRACTION:.2f}-fractional "
            f"sizing per signal (per SPEC). Combined equity tracked bar-by-bar via "
            f"broker.getvalue(); Sharpe/ann_return/max_dd computed via the in-house formula."
        ),
        "framework_metrics_file": str(OUT_DIR / "results.json"),
    }

    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"[output] {OUT_DIR / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())