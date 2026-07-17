"""H2 strategy — single-pair VPVR edge touch (15m + 2h profiles) on 1m reversion.

Implements SMA-34875 H2 directive:
  - 1m primary entry; 15m filter + 2h regime.
  - HVN/LVN zones from 15m and 2h volume profiles (POC, VAH, VAL).
  - Entry on 1m close touches VAH (short toward POC) or VAL (long toward POC).
  - Exit on 1m reversal (close crosses back toward POC) OR 15m close
    crosses back through POC against our position, OR max-hold time-out.
  - Per-symbol profile (BTCUSDT, ETHUSDT, SOLUSDT). Cross-pair alignment is
    NOT required for H2 — each symbol runs its own single-pair book.

Returns:
    build_signals(df_1m_by_symbol, cfg) -> dict[sym] -> {poc, vah, val, atr}
    run_backtest(df_1m_by_symbol, cfg) -> result dict (per-symbol +
    portfolio equity + trades).
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Reuse the campaign-wide canonical 15m / 2h aggregation + rolling VPVR
# helpers shipped for VPVR iter#70+ (SMA-34736). They live one level up
# under the shared _indicators/ folder.
_ROOT = Path(__file__).resolve().parents[2]
_INDICATORS = _ROOT / "_indicators"
for _p in (str(_ROOT), str(_INDICATORS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    aggregate_ohlcv,
    align_lower_to_upper,
    rolling_vpvr_levels,
    wilder_atr,
)


# ---------------------------------------------------------------------------
# Config defaults — overridable via cfg["indicators"]
# ---------------------------------------------------------------------------
DEFAULT_INDICATORS = {
    "vpvr_window_bars_15m": 96,    # ~1 day of 15m bars (24 * 4)
    "vpvr_window_bars_2h": 60,     # ~5 days of 2h bars
    "vpvr_n_bins": 24,
    "touch_atr_k": 0.6,            # close must be within k*ATR of edge
    "near_edge_atr_k": 1.5,        # "near edge" band for entry confirmation
    "min_holding_bars": 8,         # bars before any exit is allowed
    "max_holding_bars": 240,       # ~4 hours
    "target_reached_poc_frac": 0.5,# require close to reach 50% of VAL->POC path
    "stop_atr_k": 2.0,             # adverse excursion stop
    "cooldown_bars": 30,           # min bars between consecutive entries per symbol
    "require_2h_confirm": True,    # 2h regime must align
    "trend_2h_fast": 30,
    "trend_2h_slow": 90,
    "require_15m_close_poc_align": True,  # require 15m close on the right side of POC
}


# ---------------------------------------------------------------------------
# Pure helpers (testable)
# ---------------------------------------------------------------------------
def trend_direction(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """+1 if fast EMA > slow EMA, -1 if below, 0 otherwise. Pure."""
    f = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    s = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    diff = f - s
    out = np.where(diff > 0, 1, np.where(diff < 0, -1, 0))
    return pd.Series(out, index=close.index, name="trend").astype(int)


@dataclass
class Trade:
    symbol: str
    direction: str           # "long" / "short"
    entry_ts: object
    entry_price: float
    exit_ts: Optional[object]
    exit_price: Optional[float]
    pnl_pct: float
    bars_held: int
    vah_at_entry: float
    val_at_entry: float
    poc_at_entry: float
    atr_at_entry: float
    exit_reason: str
    trend_2h_at_entry: int


# ---------------------------------------------------------------------------
# Signal build — per-symbol 1m table with all features aligned
# ---------------------------------------------------------------------------
def build_signals(d1m: dict, cfg: dict) -> dict:
    """For each symbol, build a 1m DataFrame with these columns:

        open, high, low, close, volume,
        poc_15m, vah_15m, val_15m,
        poc_2h,  vah_2h,  val_2h,
        atr,             (1m ATR proxy from 15m wilder ATR)
        trend_2h,        # +1 / 0 / -1
        touch_vah_short, # bool — close within touch_atr_k * ATR of VAH
        touch_val_long,  # bool — close within touch_atr_k * ATR of VAL
        side_hint        # -1 near VAH, +1 near VAL, 0 neutral
    """
    ind = dict(DEFAULT_INDICATORS)
    ind.update(cfg.get("indicators", {}))

    out = {}
    for sym, df_1m in d1m.items():
        df = df_1m.copy()
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        df = df.sort_index()

        # Aggregate to 15m and 2h
        df_15m = aggregate_ohlcv(df, "15min")
        df_2h = aggregate_ohlcv(df, "2h")

        # Rolling VPVR profiles (strict-past: bar i uses bars [i-window, i))
        prof_15m = rolling_vpvr_levels(
            df_15m["close"], df_15m["volume"],
            int(ind["vpvr_window_bars_15m"]), int(ind["vpvr_n_bins"]),
        )
        prof_2h = rolling_vpvr_levels(
            df_2h["close"], df_2h["volume"],
            int(ind["vpvr_window_bars_2h"]), int(ind["vpvr_n_bins"]),
        )
        prof_15m_1m = prof_15m.reindex(df.index, method="ffill")
        prof_2h_1m = prof_2h.reindex(df.index, method="ffill")

        # 1m ATR proxy from 15m ATR
        atr_15m = wilder_atr(df_15m, 14)
        atr_1m = align_lower_to_upper(df, atr_15m).ffill()

        # 2h trend on close
        trend_2h = trend_direction(
            df_2h["close"],
            int(ind["trend_2h_fast"]),
            int(ind["trend_2h_slow"]),
        )
        trend_1m = align_lower_to_upper(df, trend_2h).fillna(0).astype(int)

        # 15m close vs POC alignment — used for the 15m regime change exit
        poc_15m_15m = prof_15m["poc"]
        close_15m = df_15m["close"]
        above = close_15m > poc_15m_15m
        # Stickiness: hold the most-recent non-NaN boolean, NaN → False.
        last_known = False
        flags = []
        for v in above.values:
            if bool(v) == v and not (isinstance(v, float) and math.isnan(v)):
                last_known = bool(v)
                flags.append(last_known)
            else:
                flags.append(last_known)
        side_15m = pd.Series(flags, index=df_15m.index, name="above_poc").astype(bool)
        side_15m_1m = side_15m.reindex(df.index, method="ffill").ffill().fillna(False).astype(bool)

        # Touch detection: combine 15m + 2h VAH/VAL; touch if EITHER is reached
        tol = float(ind["touch_atr_k"]) * atr_1m
        near_tol = float(ind["near_edge_atr_k"]) * atr_1m

        vah_avg = pd.concat([prof_15m_1m["vah"], prof_2h_1m["vah"]], axis=1).mean(axis=1)
        val_avg = pd.concat([prof_15m_1m["val"], prof_2h_1m["val"]], axis=1).mean(axis=1)
        poc_avg = pd.concat([prof_15m_1m["poc"], prof_2h_1m["poc"]], axis=1).mean(axis=1)

        close = df["close"]
        touch_vah_short = (close >= vah_avg - tol) & (close <= vah_avg + tol)
        touch_val_long = (close >= val_avg - tol) & (close <= val_avg + tol)
        near_vah = (close >= vah_avg - near_tol) & (close <= vah_avg + near_tol)
        near_val = (close >= val_avg - near_tol) & (close <= val_avg + near_tol)

        # side_hint: prefer explicit touches, fall back to "near edge + on correct side"
        side_hint = pd.Series(0, index=df.index, dtype=int)
        side_hint = side_hint.mask(near_val & (close >= val_avg), 1)
        side_hint = side_hint.mask(near_vah & (close <= vah_avg), -1)

        out[sym] = {
            "df": df,
            "poc_15m": prof_15m_1m["poc"],
            "vah_15m": prof_15m_1m["vah"],
            "val_15m": prof_15m_1m["val"],
            "poc_2h": prof_2h_1m["poc"],
            "vah_2h": prof_2h_1m["vah"],
            "val_2h": prof_2h_1m["val"],
            "poc_avg": poc_avg,
            "vah_avg": vah_avg,
            "val_avg": val_avg,
            "atr": atr_1m,
            "trend_2h": trend_1m,
            "side_15m": side_15m_1m,
            "touch_vah": touch_vah_short,
            "touch_val": touch_val_long,
            "side_hint": side_hint,
        }
    return out


# ---------------------------------------------------------------------------
# Backtest — single-symbol book, mean-reversion to POC
# ---------------------------------------------------------------------------
def run_symbol(signals: dict, sym: str, cfg: dict,
               fee_bps: float = 1.0, slip_bps: float = 1.0) -> dict:
    ind = dict(DEFAULT_INDICATORS)
    ind.update(cfg.get("indicators", {}))
    max_hold = int(ind["max_holding_bars"])
    min_hold = int(ind["min_holding_bars"])
    cooldown = int(ind["cooldown_bars"])
    need_2h = bool(ind["require_2h_confirm"])
    need_15m_poc = bool(ind["require_15m_close_poc_align"])
    target_poc_frac = float(ind["target_reached_poc_frac"])
    stop_atr_k = float(ind["stop_atr_k"])

    df = signals["df"]
    close = df["close"]
    n = len(df)
    pnl_per_bar = np.zeros(n)
    trades: list = []

    pos = 0
    bars_held = 0
    entry_idx = None
    entry_price = None
    entry_poc = entry_vah = entry_val = entry_atr = None
    entry_trend = 0
    best_close = None  # most-favorable close since entry (for long=highest, short=lowest)
    last_exit_idx = -cooldown - 1

    for i in range(1, n):
        cl = float(close.iat[i])
        if not np.isfinite(cl):
            continue

        atr_v = float(signals["atr"].iat[i]) if np.isfinite(signals["atr"].iat[i]) else 0.0
        poc_v = float(signals["poc_avg"].iat[i]) if np.isfinite(signals["poc_avg"].iat[i]) else np.nan
        vah_v = float(signals["vah_avg"].iat[i]) if np.isfinite(signals["vah_avg"].iat[i]) else np.nan
        val_v = float(signals["val_avg"].iat[i]) if np.isfinite(signals["val_avg"].iat[i]) else np.nan
        tr = int(signals["trend_2h"].iat[i]) if np.isfinite(signals["trend_2h"].iat[i]) else 0
        s15 = bool(signals["side_15m"].iat[i])
        hint = int(signals["side_hint"].iat[i])

        if pos == 0:
            if (i - last_exit_idx) < cooldown:
                continue
            touched_val = bool(signals["touch_val"].iat[i])
            touched_vah = bool(signals["touch_vah"].iat[i])
            near_val = (hint == 1)
            near_vah = (hint == -1)

            direction = 0
            # LONG candidates: touch VAL or "near VAL from below" + regime
            if touched_val or near_val:
                if (not need_2h) or (tr >= 0):  # long allowed in up or flat regime
                    if (not need_15m_poc) or s15:  # 15m close already below POC -> mean-reversion up
                        direction = +1
            # SHORT candidates: touch VAH or "near VAH from above" + regime
            if direction == 0 and (touched_vah or near_vah):
                if (not need_2h) or (tr <= 0):
                    if (not need_15m_poc) or (not s15):  # 15m close above POC
                        direction = -1

            if direction != 0 and atr_v > 0:
                pos = direction
                bars_held = 1
                entry_idx = i
                entry_price = cl
                entry_poc = poc_v
                entry_vah = vah_v
                entry_val = val_v
                entry_atr = atr_v
                entry_trend = tr
                best_close = cl

        if pos != 0:
            bars_held += 1
            prev_cl = float(close.iat[i - 1])
            bar_ret = (cl / prev_cl) - 1.0
            pnl_per_bar[i] = pos * bar_ret

            if pos == +1:
                if cl > best_close:
                    best_close = cl
            else:
                if cl < best_close:
                    best_close = cl

            exit_reason: Optional[str] = None
            # After min_hold, allow the following exits:
            if bars_held >= min_hold:
                # 1m reached the target fraction of VAL->POC (or VAH->POC) path.
                # Long entered at VAL: target = VAL + target_poc_frac * (POC - VAL)
                if pos == +1 and entry_val is not None and entry_poc is not None \
                        and not math.isnan(entry_val) and not math.isnan(entry_poc):
                    target = entry_val + target_poc_frac * (entry_poc - entry_val)
                    if cl >= target:
                        exit_reason = "target_reached"
                elif pos == -1 and entry_vah is not None and entry_poc is not None \
                        and not math.isnan(entry_vah) and not math.isnan(entry_poc):
                    target = entry_vah + target_poc_frac * (entry_poc - entry_vah)
                    if cl <= target:
                        exit_reason = "target_reached"
                # 15m regime change — current 15m close crossed back through POC against us
                if exit_reason is None:
                    if pos == +1 and s15:
                        exit_reason = "15m_close_above_poc"
                    elif pos == -1 and not s15:
                        exit_reason = "15m_close_below_poc"
            # protective stop: trade goes the wrong way by stop_atr_k * ATR (always allowed)
            if exit_reason is None and entry_price is not None and entry_atr is not None:
                if pos == +1 and cl <= entry_price - stop_atr_k * entry_atr:
                    exit_reason = "stop_loss"
                elif pos == -1 and cl >= entry_price + stop_atr_k * entry_atr:
                    exit_reason = "stop_loss"
            # max-hold time-out
            if exit_reason is None and bars_held >= max_hold:
                exit_reason = "max_holding"

            if exit_reason:
                cost = 2.0 * (fee_bps + slip_bps) / 10_000.0
                gross = pos * (cl / entry_price - 1.0)
                net = gross - cost
                trades.append(Trade(
                    symbol=sym,
                    direction="long" if pos == +1 else "short",
                    entry_ts=df.index[entry_idx],
                    entry_price=entry_price,
                    exit_ts=df.index[i],
                    exit_price=cl,
                    pnl_pct=net,
                    bars_held=bars_held,
                    vah_at_entry=entry_vah,
                    val_at_entry=entry_val,
                    poc_at_entry=entry_poc,
                    atr_at_entry=entry_atr,
                    exit_reason=exit_reason,
                    trend_2h_at_entry=entry_trend,
                ))
                pos = 0
                bars_held = 0
                entry_idx = entry_price = entry_poc = entry_vah = entry_val = entry_atr = None
                entry_trend = 0
                best_close = None
                last_exit_idx = i

    return {
        "symbol": sym,
        "trades": [asdict(t) for t in trades],
        "bar_return": pnl_per_bar,
        "n_bars": n,
        "span_start": str(df.index[0].date()),
        "span_end": str(df.index[-1].date()),
    }


def _trade_dict(t: Trade) -> dict:
    d = asdict(t)
    for k, v in d.items():
        if isinstance(v, pd.Timestamp):
            d[k] = v.isoformat()
    return d


# ---------------------------------------------------------------------------
# Portfolio — equal-weight across symbols
# ---------------------------------------------------------------------------
def build_portfolio(per_symbol: list, starting_capital: float = 100000.0) -> dict:
    n_bars = min(p["n_bars"] for p in per_symbol) if per_symbol else 0
    if n_bars == 0:
        return {"equity": np.zeros(0), "bar_return": np.zeros(0), "n_bars": 0}
    returns = np.mean([p["bar_return"][:n_bars] for p in per_symbol], axis=0)
    equity = np.empty(n_bars)
    equity[0] = starting_capital
    for i in range(1, n_bars):
        equity[i] = equity[i - 1] * (1.0 + returns[i])
    return {"equity": equity, "bar_return": returns, "n_bars": n_bars}


# ---------------------------------------------------------------------------
# Metrics — daily-resampled Sharpe ONLY (smark directive 2026-07-18)
# ---------------------------------------------------------------------------
def daily_returns(bar_return: np.ndarray, index: pd.DatetimeIndex) -> pd.Series:
    if len(bar_return) == 0 or len(index) == 0:
        return pd.Series(dtype=float)
    eq = np.empty(len(bar_return))
    eq[0] = 1.0
    for i in range(1, len(bar_return)):
        eq[i] = eq[i - 1] * (1.0 + bar_return[i])
    eq_s = pd.Series(eq, index=index)
    daily_eq = eq_s.resample("1D").last().dropna()
    return daily_eq.pct_change().dropna()


def sharpe_daily_resampled(bar_return: np.ndarray, index: pd.DatetimeIndex) -> dict:
    dr = daily_returns(bar_return, index)
    if len(dr) < 5:
        return {"sharpe_daily_resampled": 0.0, "annualized_return_daily": 0.0,
                "n_days": int(len(dr)), "span": [None, None]}
    mu = float(dr.mean())
    sd = float(dr.std(ddof=1))
    sharpe = (mu / sd) * math.sqrt(365.0) if sd > 0 else 0.0
    total = float((1.0 + dr).prod() - 1.0)
    n_days = len(dr)
    cagr = (1.0 + total) ** (365.0 / n_days) - 1.0 if n_days > 0 and (1.0 + total) > 0 else -1.0
    return {
        "sharpe_daily_resampled": float(sharpe),
        "annualized_return_daily": float(cagr),
        "n_days": int(n_days),
        "span": [str(dr.index[0].date()), str(dr.index[-1].date())],
    }


def profit_factor_and_mdd(bar_return: np.ndarray, starting_capital: float) -> dict:
    if len(bar_return) == 0:
        return {"profit_factor": 0.0, "max_drawdown_pct": 0.0}
    pos = float(bar_return[bar_return > 0].sum())
    neg = float(-bar_return[bar_return < 0].sum())
    pf = pos / neg if neg > 0 else float("inf")
    eq = np.empty(len(bar_return))
    eq[0] = starting_capital
    for i in range(1, len(bar_return)):
        eq[i] = eq[i - 1] * (1.0 + bar_return[i])
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    return {"profit_factor": float(pf), "max_drawdown_pct": float(dd.min())}


# ---------------------------------------------------------------------------
# Public entrypoint: run_backtest(data_1m, cfg)
# ---------------------------------------------------------------------------
def run_backtest(d1m: dict, cfg: dict) -> dict:
    fee_bps = float(cfg.get("fees_bps_per_side", 1.0))
    slip_bps = float(cfg.get("slippage_bps_per_side", 1.0))

    # Normalise tz-naive indexes
    d1m_norm = {}
    for sym, df in d1m.items():
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df = df.copy()
            df.index = df.index.tz_convert(None)
        d1m_norm[sym] = df.sort_index()

    signals = build_signals(d1m_norm, cfg)
    per_symbol = []
    for sym, sig in signals.items():
        per_symbol.append(run_symbol(sig, sym, cfg, fee_bps=fee_bps, slip_bps=slip_bps))
    portfolio = build_portfolio(per_symbol,
                                starting_capital=float(cfg.get("starting_capital_usd", 100000.0)))
    return {"per_symbol": per_symbol, "portfolio": portfolio, "signals": signals}
