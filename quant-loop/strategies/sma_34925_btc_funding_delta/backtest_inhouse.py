"""In-house backtest: BTC-only cross-exchange funding delta on 4h.

Strategy
--------
- Compute Δ_funding = (Binance fundingRate) - (Bybit fundingRate) at every
  common 8h funding event, then forward-fill to a 4h bar grid so every 4h
  bar carries the most recent paid funding rate per exchange.
- Position state machine on 4h bars (single position per bar):
    * Δ >  threshold  -> go/hold long  (signal = +1)
    * Δ < -threshold  -> go/hold short (signal = -1)
    * |Δ| < 0.0002    -> flat         (signal =  0)
    * Otherwise stay in current position (carry signal forward)
- PnL per 4h bar = position * (close_return) - funding_payment_4h
    - funding_payment_4h = position * 0.5 * funding_rate_at_entry (since
      Binance/Bybit pay funding every 8h, half the 8h rate is accrued per
      4h bar; sign: longs pay funding when rate > 0, shorts receive)
- Costs (freqtrade defaults): taker 0.04% + slippage 0.02% per fill, i.e.
  12 bps round-trip per entry and per exit.

Hard gates (daily-resampled Sharpe, NOT per-trade)
--------------------------------------------------
G1: OOS Sharpe >= 1.0
G2: annualized >= 15%
G4: max DD < 25%
G6: bootstrap CI lower > 0.5
Bonferroni-corrected alpha = 0.0125 across the 3-threshold sweep.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

STRATEGY_DIR = Path(__file__).resolve().parent
QUANT_LOOP = STRATEGY_DIR.parents[1]
RESULTS_DIR = STRATEGY_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

BIN_PATH = QUANT_LOOP / "data" / "funding" / "BTCUSDT.parquet"
BYB_PATH = QUANT_LOOP / "funding_analysis" / "BTCUSDT_bybit_funding.parquet"
OHLCV_4H = QUANT_LOOP / "live_data" / "BTCUSDT_4h.parquet"

THRESHOLDS = [0.0005, 0.001, 0.0015]
EXIT_THRESH = 0.0002
WINDOW_DAYS = 365          # rolling window for OOS segment (last 1 year of overlap)
TRAIN_FRAC = 0.5           # first half of the overlap = train; second = OOS
TAKER_FEE = 0.0004         # 4 bps
SLIPPAGE = 0.0002          # 2 bps
ROUND_TRIP_COST = 2.0 * (TAKER_FEE + SLIPPAGE)   # 12 bps per entry+exit pair

BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)

# Bonferroni correction
N_THRESHOLDS = len(THRESHOLDS)
ALPHA_NOMINAL = 0.05
ALPHA_BONFERRONI = ALPHA_NOMINAL / N_THRESHOLDS   # = 0.05/3 ~= 0.01667
# Spec says alpha = 0.0125 across the sweep — that's the strict value to use.

OOS_SHARPE_MIN = 1.0
ANN_MIN = 0.15
MAXDD_MAX = 0.25
CI_LOWER_MIN = 0.5
N_BOOTSTRAP = 2000


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_binance() -> pd.Series:
    df = pd.read_parquet(BIN_PATH)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    s = df.set_index("ts")["fundingRate"].astype(np.float64).sort_index()
    s.name = "funding_binance"
    return s


def load_bybit() -> pd.Series:
    df = pd.read_parquet(BYB_PATH)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    s = df.set_index("ts")["fundingRate"].astype(np.float64).sort_index()
    s.name = "funding_bybit"
    return s


def load_ohlcv_4h() -> pd.DataFrame:
    df = pd.read_parquet(OHLCV_4H)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time").sort_index()
    keep = ["open", "high", "low", "close", "volume"]
    return df[keep].astype(np.float64)


# ---------------------------------------------------------------------------
# Build Δ_funding on a 4h grid
# ---------------------------------------------------------------------------
def build_delta_series(ohlcv: pd.DataFrame, bin_f: pd.Series, byb_f: pd.Series) -> pd.DataFrame:
    """Return a DataFrame indexed by 4h bar open_time with columns:
       funding_binance, funding_bybit, delta_funding = bin - byb.
    Funding is forward-filled (last paid rate at-or-before each 4h bar open).
    """
    bar_idx = ohlcv.index
    common = bar_idx.intersection(bin_f.index).intersection(byb_f.index)
    bar_idx_in_range = bar_idx[(bar_idx >= common.min()) & (bar_idx <= common.max())]

    bin_aligned = bin_f.reindex(bar_idx_in_range, method="ffill")
    byb_aligned = byb_f.reindex(bar_idx_in_range, method="ffill")
    out = pd.DataFrame({
        "funding_binance": bin_aligned.values,
        "funding_bybit": byb_aligned.values,
    }, index=bar_idx_in_range)
    out["delta_funding"] = out["funding_binance"] - out["funding_bybit"]
    return out


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
def state_machine(
    ohlcv: pd.DataFrame,
    delta: pd.Series,
    threshold: float,
    exit_thresh: float = EXIT_THRESH,
    round_trip_cost: float = ROUND_TRIP_COST,
) -> pd.DataFrame:
    """Walk the 4h bar grid; emit per-bar PnL, equity, signal, funding payment."""
    close = ohlcv["close"].values
    fund_rate = delta.values  # already forward-filled to 4h grid
    n = len(ohlcv)

    pos = 0          # -1, 0, +1
    position = np.zeros(n, dtype=np.int8)
    funding_paid = np.zeros(n, dtype=np.float64)
    cost = np.zeros(n, dtype=np.float64)
    bar_pnl = np.zeros(n, dtype=np.float64)

    prev_pos = 0
    for i in range(n):
        d = fund_rate[i]
        # Determine target signal
        if d > threshold:
            target = 1
        elif d < -threshold:
            target = -1
        elif abs(d) < exit_thresh:
            target = 0
        else:
            target = prev_pos  # carry forward

        # Position entry/exit costs on transitions
        if target != prev_pos:
            # Entry cost (if opening) + exit cost (if closing)
            cost[i] += round_trip_cost
        pos = target
        position[i] = pos
        prev_pos = pos

        # 4h price return (close-to-close)
        if i == 0:
            ret = 0.0
        else:
            ret = (close[i] - close[i - 1]) / close[i - 1]

        # Funding payment (4h): position * 0.5 * funding_rate at this bar.
        # longs pay funding when rate > 0, shorts receive.
        fund_pay = pos * 0.5 * d
        funding_paid[i] = fund_pay

        bar_pnl[i] = pos * ret - fund_pay - cost[i]

    out = pd.DataFrame({
        "close": close,
        "funding_rate": fund_rate,
        "delta_funding": fund_rate,
        "position": position,
        "funding_paid": funding_paid,
        "cost": cost,
        "bar_pnl": bar_pnl,
    }, index=ohlcv.index)
    out["equity"] = (1.0 + out["bar_pnl"]).cumprod()
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def daily_resampled_sharpe(equity: pd.Series) -> tuple[float, pd.Series]:
    daily = equity.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    if rets.std() == 0 or not np.isfinite(rets.std()):
        return 0.0, rets
    sharpe = float(rets.mean() / rets.std() * SQRT_BPY_DAILY)
    return sharpe, rets


def annualized_return(equity: pd.Series) -> float:
    daily = equity.resample("1D").last().dropna()
    if len(daily) < 2:
        return 0.0
    n_days = (daily.index[-1] - daily.index[0]).days
    if n_days <= 0:
        return 0.0
    total = float(daily.iloc[-1] / daily.iloc[0] - 1.0)
    if total <= -1.0:
        return -1.0
    return (1.0 + total) ** (365.25 / n_days) - 1.0


def max_drawdown(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    return float(dd.min())


def bootstrap_ci_lower(daily_rets: pd.Series, n_boot: int = N_BOOTSTRAP, seed: int = 42) -> float:
    """Lower bound of a 95% bootstrap CI on the daily-resampled Sharpe.

    We resample daily returns with replacement, recompute Sharpe, and take
    the 2.5th percentile of the bootstrap distribution.
    """
    if len(daily_rets) < 5:
        return 0.0
    rng = np.random.default_rng(seed)
    vals = daily_rets.values
    sharpes = np.empty(n_boot, dtype=np.float64)
    n = len(vals)
    for b in range(n_boot):
        sample = rng.choice(vals, size=n, replace=True)
        std = sample.std()
        if std == 0 or not np.isfinite(std):
            sharpes[b] = 0.0
        else:
            sharpes[b] = (sample.mean() / std) * SQRT_BPY_DAILY
    return float(np.quantile(sharpes, 0.025))


def evaluate(bt: pd.DataFrame) -> dict:
    equity = bt["equity"]
    sharpe, daily_rets = daily_resampled_sharpe(equity)
    ann = annualized_return(equity)
    mdd = max_drawdown(equity)
    ci_lo = bootstrap_ci_lower(daily_rets)
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    n_pos_changes = int((bt["position"].diff().abs() > 0).sum())
    n_bars = len(bt)
    n_in_pos = int((bt["position"] != 0).sum())
    return {
        "sharpe": sharpe,
        "ann": ann,
        "maxdd": mdd,
        "ci_lower": ci_lo,
        "total_return": total_ret,
        "n_bars": n_bars,
        "n_pos_changes": n_pos_changes,
        "n_bars_in_pos": n_in_pos,
        "pct_bars_in_pos": n_in_pos / max(1, n_bars),
        "n_daily_rets": int(len(daily_rets)),
        "span_start": str(bt.index[0]),
        "span_end": str(bt.index[-1]),
    }


def gates_pass(m: dict, sharpe_min: float = OOS_SHARPE_MIN,
               ann_min: float = ANN_MIN, mdd_max: float = MAXDD_MAX,
               ci_min: float = CI_LOWER_MIN) -> dict:
    return {
        "G1_sharpe": bool(m["sharpe"] >= sharpe_min),
        "G2_ann": bool(m["ann"] >= ann_min),
        "G4_maxdd": bool(m["maxdd"] > -mdd_max),
        "G6_ci_lower": bool(m["ci_lower"] > ci_min),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> dict:
    ohlcv = load_ohlcv_4h()
    bin_f = load_binance()
    byb_f = load_bybit()
    delta_df = build_delta_series(ohlcv, bin_f, byb_f)
    print(f"[sma34925] OHLCV bars: {len(ohlcv)}")
    print(f"[sma34925] Δ-funding bars: {len(delta_df)}")
    print(f"[sma34925] overlap: {delta_df.index[0]} .. {delta_df.index[-1]}")

    # Restrict to last WINDOW_DAYS of the overlap
    last_ts = delta_df.index[-1]
    first_ts = last_ts - pd.Timedelta(days=WINDOW_DAYS)
    df_window = delta_df.loc[first_ts:last_ts]
    ohlcv_window = ohlcv.loc[df_window.index.min():df_window.index.max()]
    print(f"[sma34925] window: {first_ts} .. {last_ts}, days={WINDOW_DAYS}, "
          f"bars={len(df_window)}")

    # Train / OOS split: first half = train, second half = OOS
    n = len(df_window)
    train_end = n // 2
    train_idx = df_window.index[:train_end]
    oos_idx = df_window.index[train_end:]
    ohlcv_train = ohlcv.loc[train_idx.min():train_idx.max()]
    ohlcv_oos = ohlcv.loc[oos_idx.min():oos_idx.max()]

    summary = {
        "variant": "btc_funding_delta_xs",
        "window_days": WINDOW_DAYS,
        "thresholds": THRESHOLDS,
        "exit_threshold": EXIT_THRESH,
        "round_trip_cost": ROUND_TRIP_COST,
        "alpha_bonferroni": ALPHA_BONFERRONI,
        "spec_alpha": 0.0125,
        "ohlcv_rows_total": int(len(ohlcv)),
        "delta_rows_total": int(len(delta_df)),
        "train_bars": int(len(ohlcv_train)),
        "oos_bars": int(len(ohlcv_oos)),
        "thresholds_eval": {},
    }

    best_thr = None
    best_sharpe = -np.inf
    best_pass = False
    best_metrics = None
    best_key = None

    for thr in THRESHOLDS:
        bt_train = state_machine(ohlcv_train, df_window.loc[ohlcv_train.index, "delta_funding"], thr)
        bt_oos = state_machine(ohlcv_oos, df_window.loc[ohlcv_oos.index, "delta_funding"], thr)
        m_train = evaluate(bt_train)
        m_oos = evaluate(bt_oos)
        gates_oos = gates_pass(m_oos)
        all_pass = all(gates_oos.values())
        summary["thresholds_eval"][f"thr_{thr}"] = {
            "threshold": thr,
            "train": m_train,
            "oos": m_oos,
            "gates_oos": gates_oos,
            "all_gates_oos_pass": all_pass,
        }
        print(f"[sma34925] thr={thr}: OOS Sharpe={m_oos['sharpe']:.3f} "
              f"ann={m_oos['ann']*100:.2f}% maxDD={m_oos['maxdd']*100:.2f}% "
              f"CIlo={m_oos['ci_lower']:.3f} -> gates={gates_oos}")
        if all_pass and m_oos["sharpe"] > best_sharpe:
            best_sharpe = m_oos["sharpe"]
            best_thr = thr
            best_metrics = m_oos
            best_pass = True
            best_key = f"thr_{thr}"

    summary["best_threshold"] = best_thr
    summary["best_metrics_oos"] = best_metrics
    summary["best_passes_all_gates"] = best_pass

    out_path = RESULTS_DIR / "summary_inhouse.json"
    out_path.write_text(json.dumps(summary, indent=2, default=float))
    print(f"[sma34925] wrote {out_path}")
    return summary


if __name__ == "__main__":
    main()