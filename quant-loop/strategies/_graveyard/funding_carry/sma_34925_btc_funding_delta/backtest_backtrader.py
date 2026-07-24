"""Backtrader cross-check for the BTC funding-delta strategy on 4h.

Third framework for the W5 reproducibility gate. Builds an explicit
backtrader strategy that consumes the same Δ_funding series and
applies the same threshold + exit logic as the in-house engine.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import backtrader as bt
import numpy as np
import pandas as pd

STRATEGY_DIR = Path(__file__).resolve().parent
QUANT_LOOP = STRATEGY_DIR.parents[1]
RESULTS_DIR = STRATEGY_DIR / "results"

BIN_PATH = QUANT_LOOP / "data" / "funding" / "BTCUSDT.parquet"
BYB_PATH = QUANT_LOOP / "funding_analysis" / "BTCUSDT_bybit_funding.parquet"
OHLCV_4H = QUANT_LOOP / "live_data" / "BTCUSDT_4h.parquet"

THRESHOLDS = [0.0005, 0.001, 0.0015]
EXIT_THRESH = 0.0002
WINDOW_DAYS = 365
TAKER_FEE = 0.0004
SLIPPAGE = 0.0002
BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)
N_BOOTSTRAP = 2000

OOS_SHARPE_MIN = 1.0
ANN_MIN = 0.15
MAXDD_MAX = 0.25
CI_LOWER_MIN = 0.5


def load_binance():
    df = pd.read_parquet(BIN_PATH)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")["fundingRate"].astype(float).sort_index()


def load_bybit():
    df = pd.read_parquet(BYB_PATH)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    return df.set_index("ts")["fundingRate"].astype(float).sort_index()


def load_ohlcv_4h():
    df = pd.read_parquet(OHLCV_4H)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time").sort_index()
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def build_delta_series(ohlcv, bin_f, byb_f):
    common_ts = ohlcv.index.intersection(bin_f.index).intersection(byb_f.index)
    bars = ohlcv.index[(ohlcv.index >= common_ts.min()) & (ohlcv.index <= common_ts.max())]
    bin_aligned = bin_f.reindex(bars, method="ffill")
    byb_aligned = byb_f.reindex(bars, method="ffill")
    return pd.DataFrame({
        "funding_binance": bin_aligned.values,
        "funding_bybit": byb_aligned.values,
        "delta_funding": (bin_aligned - byb_aligned).values,
    }, index=bars)


class FundingDeltaFeed(bt.feeds.PandasData):
    """Pandas feed that adds the funding-delta series as an extra line."""
    lines = ("delta_funding",)
    params = (("delta_funding", -1),)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Carry the column onto a line so strategy code can index it
        self.lines.delta_funding.csv = self.p.delta_funding


class FundingDeltaStrategy(bt.Strategy):
    params = dict(threshold=0.0005, exit_thresh=EXIT_THRESH)

    def __init__(self):
        self.delta = self.datas[0].delta_funding
        self.target_pos = 0
        self.entries = 0

    def next(self):
        d = float(self.delta[0])
        prev = self.target_pos
        if d > self.p.threshold:
            self.target_pos = 1
        elif d < -self.p.threshold:
            self.target_pos = -1
        elif abs(d) < self.p.exit_thresh:
            self.target_pos = 0
        else:
            self.target_pos = prev

        # Trade only when target flips
        current = self.position.size
        if current == 0 and self.target_pos != 0:
            if self.target_pos > 0:
                self.buy()
            else:
                self.sell()
            self.entries += 1
        elif current > 0 and self.target_pos <= 0:
            self.close()
            if self.target_pos < 0:
                self.sell()
                self.entries += 1
        elif current < 0 and self.target_pos >= 0:
            self.close()
            if self.target_pos > 0:
                self.buy()
                self.entries += 1


def run_backtrader(ohlcv_df, delta_s, threshold):
    df = ohlcv_df.copy()
    df["delta_funding"] = delta_s.reindex(df.index)
    # backtrader wants ascending ts index
    df = df.reset_index()
    df.columns = ["datetime"] + list(df.columns[1:])
    df = df[["datetime", "open", "high", "low", "close", "volume", "delta_funding"]]

    data = FundingDeltaFeed(
        dataname=df,
        datetime="datetime",
        open="open", high="high", low="low", close="close", volume="volume",
        openinterest=-1,
        delta_funding="delta_funding",
        plot=False,
    )

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.adddata(data)
    cerebro.broker.setcash(100000.0)
    cerebro.broker.setcommission(commission=TAKER_FEE, leverage=1.0)
    cerebro.broker.set_slippage_perc(perc=SLIPPAGE)
    cerebro.addstrategy(FundingDeltaStrategy, threshold=threshold)

    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="timereturn",
                        timeframe=bt.TimeFrame.Days)
    results = cerebro.run()
    strat = results[0]
    rets = pd.Series(strat.analyzers.timereturn.get_analysis())
    rets = rets.astype(float).sort_index()

    if len(rets) < 2:
        return {"sharpe": 0.0, "ann": 0.0, "maxdd": 0.0, "ci_lower": 0.0,
                "n_entries": strat.entries, "n_bars": len(ohlcv_df),
                "pct_bars_in_pos": 0.0}

    if rets.std() == 0 or not np.isfinite(rets.std()):
        sharpe = 0.0
    else:
        sharpe = float(rets.mean() / rets.std() * SQRT_BPY_DAILY)

    n_days = (rets.index[-1] - rets.index[0]).days if len(rets) > 1 else 0
    if n_days > 0:
        equity_curve = (1.0 + rets).cumprod()
        total = float(equity_curve.iloc[-1] / equity_curve.iloc[0] - 1.0)
        ann = (1.0 + total) ** (365.25 / n_days) - 1.0 if total > -1.0 else -1.0
        roll_max = equity_curve.cummax()
        mdd = float((equity_curve / roll_max - 1.0).min())
    else:
        ann = 0.0
        mdd = 0.0

    if len(rets) >= 5:
        rng = np.random.default_rng(42)
        vals = rets.values
        sharpes = np.empty(N_BOOTSTRAP, dtype=np.float64)
        n = len(vals)
        for b in range(N_BOOTSTRAP):
            sample = rng.choice(vals, size=n, replace=True)
            std = sample.std()
            if std == 0 or not np.isfinite(std):
                sharpes[b] = 0.0
            else:
                sharpes[b] = (sample.mean() / std) * SQRT_BPY_DAILY
        ci_lo = float(np.quantile(sharpes, 0.025))
    else:
        ci_lo = 0.0

    return {
        "sharpe": sharpe,
        "ann": ann,
        "maxdd": mdd,
        "ci_lower": ci_lo,
        "n_entries": strat.entries,
        "n_bars": len(ohlcv_df),
        "pct_bars_in_pos": 0.0,
    }


def gates_pass(m: dict) -> dict:
    return {
        "G1_sharpe": bool(m["sharpe"] >= OOS_SHARPE_MIN),
        "G2_ann": bool(m["ann"] >= ANN_MIN),
        "G4_maxdd": bool(m["maxdd"] > -MAXDD_MAX),
        "G6_ci_lower": bool(m["ci_lower"] > CI_LOWER_MIN),
    }


def main() -> dict:
    ohlcv = load_ohlcv_4h()
    bin_f = load_binance()
    byb_f = load_bybit()
    delta_df = build_delta_series(ohlcv, bin_f, byb_f)

    last_ts = delta_df.index[-1]
    first_ts = last_ts - pd.Timedelta(days=WINDOW_DAYS)
    df_window = delta_df.loc[first_ts:last_ts]
    ohlcv_window = ohlcv.loc[df_window.index.min():df_window.index.max()]

    n = len(df_window)
    train_end = n // 2
    oos_idx = df_window.index[train_end:]
    ohlcv_oos = ohlcv.loc[oos_idx.min():oos_idx.max()]
    delta_oos = df_window.loc[oos_idx, "delta_funding"]

    summary = {
        "variant": "btc_funding_delta_xs_bt",
        "framework": "backtrader",
        "framework_version": bt.__version__,
        "oos_bars": int(len(ohlcv_oos)),
        "thresholds": {},
    }

    for thr in THRESHOLDS:
        m = run_backtrader(ohlcv_oos, delta_oos, thr)
        gates = gates_pass(m)
        summary["thresholds"][f"thr_{thr}"] = {
            "threshold": thr,
            "metrics": m,
            "gates": gates,
            "all_gates_pass": all(gates.values()),
        }
        print(f"[bt] thr={thr}: Sharpe={m['sharpe']:.3f} ann={m['ann']*100:.2f}% "
              f"maxDD={m['maxdd']*100:.2f}% CIlo={m['ci_lower']:.3f} "
              f"entries={m['n_entries']} -> gates={gates}")

    out = RESULTS_DIR / "summary_backtrader.json"
    out.write_text(json.dumps(summary, indent=2, default=float))
    print(f"[bt] wrote {out}")
    return summary


if __name__ == "__main__":
    main()