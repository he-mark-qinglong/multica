"""Shared schedule-replay engine for framework-validate cross-validation.

Root cause fixed here (SMA-34922):
  The original per-strategy freqtrade adapters debited ONLY the entry fee from
  cash at entry and credited `notional * (1 + pnl_pct)` at exit — the position
  notional was never subtracted from cash. NAV therefore ratcheted upward at
  every fill regardless of win/loss, so max_dd degenerated to the per-entry
  fee dip (~ -4.0e-06 = 1% sizing x 0.04% fee; or ~ -3.1e-04 when a 0.01
  weight diluted pnl). Those near-zero values are artifacts, not drawdowns.

Correct approach (this module):
  Replay the in-house entry/exit schedule over real bar close prices and
  mark the position to market EVERY held bar, mirroring the in-house equity
  construction (same sizing convention, same carry model), changing ONLY the
  cost model to the framework's (freqtrade: 4bp fee + 2bp slippage per side
  = 12bp round trip). Each replay is also run in `validation` mode with the
  in-house cost, where it must reproduce the in-house equity CSV — this
  verifies the engine before the framework-cost run is trusted.

Conventions mirrored per strategy:
  - vpvr_funding_aware_v1_20260711 (4h, long-only, BTC+ETH):
      full-notional sizing; held bars (entry_bar, exit_bar] earn close-to-close
      returns; funding carry per trade distributed evenly over held bars
      (in-house applies -funding_rate per held bar; per-trade total is in the
      trades CSV as pnl_carry_pct); round-trip cost amortised over held bars.
  - vpvr_funding_asym_4h_20260713 (4h, long+short, BTC+ETH):
      risk_target-scaled bar pnl; entry bar..exit-1 earn scale*price_ret*dir;
      exit bar earns scale*net where net = gross - cost_rt + synthetic carry
      (-funding_carry_bps_per_bar * bars_held * dir), mirroring the in-house
      update structure exactly.
  - vpvr_funding_reset_window_1h_20260715 (1h, long+short, BTC):
      risk_per_trade-scaled bar pnl; held bars (entry_bar, exit_bar] earn
      price_ret*dir with round-trip cost amortised over held bars.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

FREQTRADE_FEE_BPS_PER_SIDE = 4.0       # 0.04% taker fee (freqtrade crypto perp default)
FREQTRADE_SLIP_BPS_PER_SIDE = 2.0      # 0.02% slippage model
FREQTRADE_COST_RT = 2.0 * (FREQTRADE_FEE_BPS_PER_SIDE + FREQTRADE_SLIP_BPS_PER_SIDE) / 1e4  # 0.0012

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


@dataclass
class ReplayResult:
    equity: pd.Series          # per-symbol equity, indexed by bar timestamp (UTC)
    n_fills: int


def load_prices(path: str, span_start: str | None = None,
                span_end: str | None = None) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    if span_start is not None:
        df = df[df["ts"] >= pd.Timestamp(span_start, tz="UTC")]
    if span_end is not None:
        df = df[df["ts"] <= pd.Timestamp(span_end, tz="UTC")]
    return df.reset_index(drop=True)


def load_trades(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["entry_ts"] = pd.to_datetime(df["entry_ts"], utc=True, errors="coerce")
    df["exit_ts"] = pd.to_datetime(df["exit_ts"], utc=True, errors="coerce")
    return df.sort_values("entry_ts").reset_index(drop=True)


def _bar_index(ts_index: pd.DatetimeIndex, ts: pd.Timestamp) -> int | None:
    """Position of ts in the bar index, or None if not on a bar."""
    loc = ts_index.searchsorted(ts)
    if loc < len(ts_index) and ts_index[loc] == ts:
        return int(loc)
    return None


def replay_full_notional(prices: pd.DataFrame, trades: pd.DataFrame,
                         start_equity: float, cost_rt: float,
                         carry_pcts: pd.Series | None = None) -> ReplayResult:
    """aware_v1 convention: full-notional MTM, long-only.

    Held bars (entry_bar, exit_bar] earn close-to-close returns; per-trade
    carry is spread evenly over held bars; round-trip cost amortised over
    held bars.
    """
    ts_index = pd.DatetimeIndex(prices["ts"])
    close = prices["close"].to_numpy(dtype=float)
    n = len(prices)
    bar_ret = np.zeros(n)
    n_fills = 0
    for k, t in trades.iterrows():
        ei = _bar_index(ts_index, t["entry_ts"])
        xi = _bar_index(ts_index, t["exit_ts"])
        if ei is None or xi is None or xi <= ei:
            continue
        n_fills += 1
        bh = xi - ei
        carry = float(carry_pcts.iloc[k]) if carry_pcts is not None else 0.0
        for j in range(ei + 1, xi + 1):
            bar_ret[j] += (close[j] / close[j - 1] - 1.0)
            bar_ret[j] += carry / bh
            bar_ret[j] -= cost_rt / bh
    equity = np.empty(n)
    equity[0] = start_equity
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + bar_ret[i])
    return ReplayResult(pd.Series(equity, index=ts_index), n_fills)


def replay_risk_scaled(prices: pd.DataFrame, trades: pd.DataFrame,
                       start_equity: float, cost_rt: float,
                       size_scale: float) -> ReplayResult:
    """reset_window convention: equity *= (1 + size_scale * bar_ret).

    Held bars (entry_bar, exit_bar] earn price_ret * direction with the
    round-trip cost amortised over held bars.
    """
    ts_index = pd.DatetimeIndex(prices["ts"])
    close = prices["close"].to_numpy(dtype=float)
    n = len(prices)
    bar_ret = np.zeros(n)
    n_fills = 0
    for _, t in trades.iterrows():
        ei = _bar_index(ts_index, t["entry_ts"])
        xi = _bar_index(ts_index, t["exit_ts"])
        if ei is None or xi is None or xi <= ei:
            continue
        n_fills += 1
        d = 1.0 if t["direction"] == "long" else -1.0
        bh = xi - ei
        for j in range(ei + 1, xi + 1):
            bar_ret[j] += (close[j] / close[j - 1] - 1.0) * d
            bar_ret[j] -= cost_rt / bh
    equity = np.empty(n)
    equity[0] = start_equity
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + size_scale * bar_ret[i])
    return ReplayResult(pd.Series(equity, index=ts_index), n_fills)


def replay_asym(prices: pd.DataFrame, trades: pd.DataFrame,
                start_equity: float, cost_rt: float, size_scale: float,
                funding_carry_bps_per_bar: float) -> ReplayResult:
    """asym_4h convention: mirrors in-house strategy.py equity updates.

    Bars [entry_bar, exit_bar) earn size_scale * price_ret * direction;
    the exit bar earns size_scale * net, where
      net = gross - cost_rt + (-funding_carry_bps_per_bar/1e4 * bars_held * dir)
    and gross = (exit_price/entry_price - 1) * dir from the trades CSV.
    """
    ts_index = pd.DatetimeIndex(prices["ts"])
    close = prices["close"].to_numpy(dtype=float)
    n = len(prices)
    equity = np.empty(n)
    equity[0] = start_equity
    # exit-bar net multiplier events: bar_idx -> size_scale * net
    exit_net: dict[int, float] = {}
    n_fills = 0
    for _, t in trades.iterrows():
        ei = _bar_index(ts_index, t["entry_ts"])
        xi = _bar_index(ts_index, t["exit_ts"])
        if ei is None or xi is None or xi <= ei:
            continue
        n_fills += 1
        d = 1.0 if t["direction"] == "long" else -1.0
        bh = xi - ei
        gross = (float(t["exit_price"]) / float(t["entry_price"]) - 1.0) * d
        carry = -funding_carry_bps_per_bar / 1e4 * bh * d
        net = gross - cost_rt + carry
        exit_net[xi] = exit_net.get(xi, 0.0) + size_scale * net
        # mark entry/exit bars for price-return skipping
    # per-bar walk; bars [entry, exit) of any open trade earn price ret
    # Build held-mask from trade schedule
    held = np.zeros(n, dtype=float)  # direction while held (for price bars)
    for _, t in trades.iterrows():
        ei = _bar_index(ts_index, t["entry_ts"])
        xi = _bar_index(ts_index, t["exit_ts"])
        if ei is None or xi is None or xi <= ei:
            continue
        d = 1.0 if t["direction"] == "long" else -1.0
        for j in range(ei, xi):
            held[j] = d
    for i in range(1, n):
        r = 0.0
        if held[i] != 0.0:
            r += size_scale * (close[i] / close[i - 1] - 1.0) * held[i]
        if i in exit_net:
            # exit bar: in-house applies scale*net INSTEAD of price bar ret
            r = exit_net[i]
        equity[i] = equity[i - 1] * (1.0 + r)
    return ReplayResult(pd.Series(equity, index=ts_index), n_fills)


# ---------------------------------------------------------------- metrics

def max_dd(nav: pd.Series) -> float:
    peak = nav.cummax()
    return float((nav / peak - 1.0).min())


def total_return(nav: pd.Series) -> float:
    return float(nav.iloc[-1] / nav.iloc[0] - 1.0)


def span_years(nav: pd.Series) -> float:
    return (nav.index[-1] - nav.index[0]).total_seconds() / (365.25 * 24 * 3600)


def ann_return(nav: pd.Series) -> float:
    tr = total_return(nav)
    sp = span_years(nav)
    return float((1.0 + tr) ** (1.0 / sp) - 1.0) if sp > 0 and tr > -1 else -1.0


def trade_sharpe_bars_annualized(pnls: np.ndarray, bars_per_year: float) -> float:
    """aware_v1 in-house formula: mean/std of per-trade pnl x sqrt(bars/year)."""
    mu = float(np.mean(pnls))
    sd = float(np.std(pnls, ddof=0))
    return (mu / sd) * math.sqrt(bars_per_year) if sd > 0 else 0.0


def trade_sharpe_tpy_annualized(pnls: np.ndarray, n_trades: float, years: float) -> float:
    """asym/reset in-house formula: mean/std of per-trade pnl x sqrt(trades/year)."""
    mu = float(np.mean(pnls))
    sd = float(np.std(pnls, ddof=0))
    tpy = n_trades / max(years, 1e-9)
    return (mu / sd) * math.sqrt(tpy) if sd > 0 else 0.0


def nav_bar_sharpe(nav: pd.Series, timeframe: str) -> float:
    """Framework-native bar-return Sharpe (supplementary reference)."""
    rets = nav.pct_change().dropna()
    sd = float(rets.std(ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(rets.mean() / sd * math.sqrt(N_BARS_PER_YEAR.get(timeframe, 365.25 * 6)))


def abs_rel_div(fw: float, ih: float) -> float:
    return abs(fw - ih) / max(abs(ih), 1e-9) * 100.0


def equity_validation(replayed: pd.Series, inhouse_csv: str) -> dict:
    """Compare a validation-mode replay against the in-house equity CSV."""
    ih = pd.read_csv(inhouse_csv)["equity"].to_numpy(dtype=float)
    rp = replayed.to_numpy(dtype=float)
    m = min(len(ih), len(rp))
    ih, rp = ih[:m], rp[:m]
    denom = np.maximum(np.abs(ih), 1e-9)
    rel_err = np.abs(rp - ih) / denom
    dd_rp = float((rp / np.maximum.accumulate(rp) - 1.0).min())
    dd_ih = float((ih / np.maximum.accumulate(ih) - 1.0).min())
    return {
        "n_bars_compared": int(m),
        "max_abs_rel_err": float(rel_err.max()),
        "final_rel_err": float(abs(rp[-1] - ih[-1]) / max(abs(ih[-1]), 1e-9)),
        "replayed_max_dd": dd_rp,
        "inhouse_max_dd": dd_ih,
        "max_dd_abs_diff": float(abs(dd_rp - dd_ih)),
    }
