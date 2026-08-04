"""Multifactor stacked strategy v1 — KAMA MTF trend gated by flow/session/volume.

Four binary factor signals on 4h bars (BTC/ETH/SOL perps):

  1. ``kama``    — KAMA MTF trend AND-gate: 4h KAMA(er5,f2,s30) slope > 0 AND
                   1d KAMA(er10,f3,s30) slope > 0 (params from the validated
                   strategies/kama_mtf_btc_4h_1d_20260802). The daily leg is
                   shifted one full day before ffilling onto 4h bars, so 4h
                   bars on day D only ever see the *completed* day D-1 —
                   strictly causal.
  2. ``imb``     — order-book imbalance proxy: rolling taker-flow imbalance
                   (taker_buy - taker_sell)/volume > +0.15. Until the OKX
                   books5 collector (scripts/collect_okx_book_ws.py) has
                   enough history, the synthetic proxy from 15m klines
                   (taker_buy_base) stands in for book_imbalance from
                   _shared/factor_analysis/orderbook_factors.py — same sign
                   convention, same threshold semantics.
  3. ``session`` — 20:00–00:00 UTC (US session buy window) → 1 else 0.
  4. ``volume``  — volume > 1.5x trailing mean → conviction → 1 else 0.

Stacking rule (KAMA has veto weight): long iff kama == 1 AND total bullish
votes >= 3 of 4; otherwise flat. Auxiliary factors are blended with the
shared composer (``_shared/strategy_kit/composer.py``, method="vote").

Position is signal.shift(1) — signal at bar close, effective next bar.
Cost: 3.5 bp per side (7 bp round trip), charged on position changes.

Variants (for the comparison study):
  kama_only    — factor 1 alone
  kama_imb     — kama AND imb
  kama_session — kama AND session
  stacked4     — full rule (kama veto + >=3 votes)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

QL_ROOT = Path(__file__).resolve().parents[2]
if str(QL_ROOT) not in sys.path:
    sys.path.insert(0, str(QL_ROOT))

from _shared.strategy_kit.composer import ComposerConfig, compose_signals  # noqa: E402

FEE_PER_SIDE = 0.00035  # 3.5 bp per side = 7 bp round trip
PERIODS_PER_YEAR = 2190  # 4h bars

AUX_FACTORS = ("imb", "session", "volume")


@dataclass
class BacktestResult:
    equity: np.ndarray
    returns: pd.Series
    signal: pd.Series
    n_trades: int
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    calmar: float


# ---------------------------------------------------------------------------
# Factor 1: KAMA MTF trend
# ---------------------------------------------------------------------------

def kama(close: pd.Series, er_window: int, fast: int, slow: int) -> pd.Series:
    """Kaufman Adaptive Moving Average (same implementation as the validated
    kama_mtf_btc_4h_1d strategy)."""
    vals = close.values.astype(float)
    n = len(vals)
    out = np.full(n, np.nan)
    if n <= er_window + 1:
        return pd.Series(out, index=close.index)
    change = np.abs(vals[er_window:] - vals[:-er_window])
    volatility = pd.Series(np.abs(np.diff(vals))).rolling(er_window).sum().values
    er = np.zeros(n)
    er[er_window:] = change / np.maximum(volatility[er_window - 1:], 1e-12)
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
    sc[~np.isfinite(sc)] = slow_sc ** 2
    seed = er_window
    out[seed] = vals[seed]
    for i in range(seed + 1, n):
        out[i] = out[i - 1] + sc[i] * (vals[i] - out[i - 1])
    return pd.Series(out, index=close.index)


def _kama_slope_signal(close: pd.Series, p: dict) -> pd.Series:
    k = kama(close, p["er_window"], p["fast"], p["slow"])
    slope = (k - k.shift(p["slope_lookback"])) / k.shift(p["slope_lookback"])
    return (slope > 0).astype(float)


def kama_mtf_signal(df_4h: pd.DataFrame, df_1d: pd.DataFrame, cfg: dict) -> pd.Series:
    """4h AND 1d KAMA slope gate, daily leg shifted one day (no lookahead).

    The 1d bar labelled D aggregates day D's data; shifting by one day means
    4h bars on day D use the signal computed from the fully-closed day D-1.
    """
    sig_4h = _kama_slope_signal(df_4h["close"], cfg["params"]["tf_4h"])
    sig_1d = _kama_slope_signal(df_1d["close"], cfg["params"]["tf_1d"])
    sig_1d_4h = sig_1d.shift(1).reindex(df_4h.index, method="ffill").fillna(0)
    return (sig_4h * sig_1d_4h).astype(float)


# ---------------------------------------------------------------------------
# Factor 2: order-book imbalance proxy (taker flow)
# ---------------------------------------------------------------------------

def book_imb_signal(df_4h: pd.DataFrame, window: int, threshold: float) -> pd.Series:
    """Rolling taker-flow imbalance > threshold → bullish.

    imb_t = (taker_buy_base - taker_sell_base)/volume per bar, smoothed over
    ``window`` bars. Synthetic stand-in for the book_imbalance order-book
    factor while real books5 history accumulates.
    """
    vol = df_4h["volume"].astype(float)
    buy = df_4h["taker_buy_base"].astype(float)
    sell = vol - buy
    imb = ((buy - sell) / vol.replace(0.0, np.nan)).fillna(0.0)
    imb_roll = imb.rolling(window, min_periods=window).mean().fillna(0.0)
    return (imb_roll > threshold).astype(float)


# ---------------------------------------------------------------------------
# Factor 3: US session window
# ---------------------------------------------------------------------------

def session_signal(index: pd.DatetimeIndex, start_hour: int = 20,
                   end_hour: int = 24) -> pd.Series:
    """1 during [start_hour, end_hour) UTC — the US buy window."""
    hours = index.hour
    on = (hours >= start_hour) & (hours < end_hour)
    return pd.Series(on.astype(float), index=index)


# ---------------------------------------------------------------------------
# Factor 4: volume conviction
# ---------------------------------------------------------------------------

def volume_signal(volume: pd.Series, window: int, mult: float) -> pd.Series:
    """volume > mult x trailing mean (shifted — the mean never includes the
    current bar, so the comparison itself is lookahead-free)."""
    v = volume.astype(float)
    mu = v.rolling(window, min_periods=window).mean().shift(1)
    return (v > mult * mu).fillna(False).astype(float)


# ---------------------------------------------------------------------------
# Stacking
# ---------------------------------------------------------------------------

def stacked_signal(signals: pd.DataFrame, min_votes: int = 3,
                   require_kama: bool = True) -> pd.Series:
    """Long iff (kama veto passes) and total bullish votes >= ``min_votes``.

    The three auxiliary factors are blended through the shared composer in
    vote mode (one vote per factor, no decorrelation shrinkage — the votes
    must stay integer-countable for the >=3 rule).
    """
    kama = signals["kama"].fillna(0.0)
    aux = signals[list(AUX_FACTORS)].fillna(0.0)
    composite = compose_signals(
        aux, ComposerConfig(method="vote", decorrelate=False))
    aux_votes = (composite * len(AUX_FACTORS)).round().astype(int)
    total_votes = kama.astype(int) + aux_votes
    gate = pd.Series(True, index=signals.index) if not require_kama else kama > 0
    return ((total_votes >= min_votes) & gate).astype(float)


def generate_signals(df_4h: pd.DataFrame, df_1d: pd.DataFrame,
                     cfg: dict) -> pd.DataFrame:
    """All four factor signals, aligned to the 4h index, columns
    kama/imb/session/volume (0/1 floats)."""
    p = cfg["params"]
    return pd.DataFrame({
        "kama": kama_mtf_signal(df_4h, df_1d, cfg),
        "imb": book_imb_signal(df_4h, p["imb_window"], p["imb_threshold"]),
        "session": session_signal(df_4h.index, p["session_start_hour"],
                                  p["session_end_hour"]),
        "volume": volume_signal(df_4h["volume"], p["vol_window"],
                                p["vol_mult"]),
    })


def generate_position(signals: pd.DataFrame, variant: str,
                      min_votes: int = 3) -> pd.Series:
    """Variant selector: kama_only | kama_imb | kama_session | stacked4."""
    if variant == "kama_only":
        return signals["kama"].fillna(0.0)
    if variant == "kama_imb":
        return ((signals["kama"] > 0) & (signals["imb"] > 0)).astype(float)
    if variant == "kama_session":
        return ((signals["kama"] > 0) & (signals["session"] > 0)).astype(float)
    if variant == "stacked4":
        return stacked_signal(signals, min_votes=min_votes)
    raise ValueError(f"unknown variant {variant!r}")


VARIANTS = ("kama_only", "kama_imb", "kama_session", "stacked4")


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def backtest_returns(df_4h: pd.DataFrame, df_1d: pd.DataFrame, cfg: dict,
                     variant: str = "stacked4") -> pd.Series:
    """Net per-bar returns for one symbol: shift(1) execution, taker fees."""
    signals = generate_signals(df_4h, df_1d, cfg)
    signal = generate_position(signals, variant, cfg["params"]["min_votes"])
    ret = df_4h["close"].astype(float).pct_change()
    pos = signal.shift(1).fillna(0.0)
    cost = FEE_PER_SIDE * pos.diff().abs().fillna(0.0)
    return (pos * ret - cost).dropna()


def metrics_from_returns(net_rets: pd.Series, signal: pd.Series | None = None,
                         ppy: int = PERIODS_PER_YEAR) -> dict:
    """Standard metric bundle for a per-bar net return series."""
    if len(net_rets) == 0:
        return {"total_return": 0.0, "annualized_return": 0.0, "max_drawdown": 0.0,
                "sharpe": 0.0, "calmar": 0.0, "n_trades": 0}
    equity = np.cumprod(1.0 + net_rets.values)
    n_years = len(net_rets) / ppy
    total = float(equity[-1] - 1.0)
    annualized = float(equity[-1] ** (1.0 / max(n_years, 1e-9)) - 1.0)
    peak = np.maximum.accumulate(equity)
    max_dd = float(np.min((equity - peak) / peak))
    std = float(net_rets.std())
    sharpe = float(net_rets.mean() / std * np.sqrt(ppy)) if std > 1e-12 else 0.0
    calmar = abs(annualized / max_dd) if abs(max_dd) > 1e-9 else 0.0
    return {"total_return": total, "annualized_return": annualized,
            "max_drawdown": max_dd, "sharpe": sharpe, "calmar": calmar,
            "n_trades": int((net_rets != 0).sum())}


def resample_ohlcv(df_raw: pd.DataFrame, rule: str) -> pd.DataFrame:
    """15m klines -> OHLCV at ``rule``; keeps taker flow for the imb proxy."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum", "taker_buy_base": "sum"}
    return df_raw.resample(rule).agg(agg).dropna()


def load_symbol(root: Path, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load perp_15m parquet, return (df_4h, df_1d) UTC-indexed."""
    df_raw = pd.read_parquet(root / "data" / "perp_15m" / f"{symbol}_15m.parquet")
    ts = pd.to_datetime(df_raw["open_time"], unit="ms", utc=True)
    df_raw = df_raw.set_index(ts).sort_index()
    df_raw = df_raw.tz_convert(None)  # naive UTC, matches other strategies
    return resample_ohlcv(df_raw, "4h"), resample_ohlcv(df_raw, "1D")
