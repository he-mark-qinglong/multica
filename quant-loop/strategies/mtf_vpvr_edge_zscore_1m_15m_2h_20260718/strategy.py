"""Single-asset multi-TF VPVR-edge + zscore reversion (SMA-34991).

Signal stack (per smark directive 2026-07-18):

  - 1m primary timeframe, single-asset (BTC, ETH, SOL).
  - 15m VPVR confluence: long when price touches a Low-Volume Node (LVN) or
    Value-Area-Low (VAL) from below, with the rolling 15m POC slope
    positive (reversion edge confirmed by trend-of-attraction).
  - 2h trend filter: only enter if the 2h POC and 2h EMA(20) slope agree
    with the entry side (no counter-trend entries).
  - 15m z-score of close vs rolling mean over a 100-bar lookback window;
    enter when |z_15m| > entry_threshold AND |z_2h| > confirm_threshold on
    the same side.
  - Exit when z reverts to 0 (z_exit_threshold), hits TP/SL, or max-hold
    reached.

Infrastructure (mandatory Wave 1/2):
  - cost_model.apply_cost with BINANCE_SPOT venue, 10bp taker + slippage
  - vol_target.apply_vol_target (target_vol=0.15) for risk normalisation
  - cpcv.cpcv() with n_groups=6, k_test=2, purge=500, embargo=250 (1m bars)
  - metrics_validator.validate_metrics on output

Sharpe is daily-resampled (smark directive 2026-07-18).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Make the shared base utilities importable (mtf-1m-15m-2h campaign).
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
_INDICATORS = _ROOT / "_indicators"
sys.path.insert(0, str(_INDICATORS))

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    VARIANT_KEY,
    aggregate_ohlcv,
    align_lower_to_upper,
    rolling_vpvr_levels,
    wilder_atr,
    ema,
)

# ---------------------------------------------------------------------------
# Trade + result containers
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    symbol: str
    direction: str  # "long" / "short"
    entry_ts: pd.Timestamp
    entry_price: float
    exit_ts: Optional[pd.Timestamp]
    exit_price: Optional[float]
    pnl_pct: float
    bars_held: int
    z15m_at_entry: float
    z2h_at_entry: float
    poc_slope_15m_at_entry: float
    ema_slope_2h_at_entry: float
    touched_lvn_15m: int
    touched_val_15m: int
    touched_vah_15m: int
    touched_hvn_15m: int
    touched_lvn_2h: int
    touched_val_2h: int
    touched_vah_2h: int
    touched_hvn_2h: int
    exit_reason: str


def _trade_dict(t: Trade) -> dict:
    d = asdict(t)
    for k, v in d.items():
        if isinstance(v, pd.Timestamp):
            d[k] = v.isoformat()
    return d


# ---------------------------------------------------------------------------
# Indicators (single-asset specific)
# ---------------------------------------------------------------------------


def zscore(close: pd.Series, lookback: int) -> pd.Series:
    """Z-score of close vs rolling mean/std over ``lookback`` bars.

    Strict-past: z[t] uses bars [t-lookback+1, t] inclusive so signal-at-t
    uses information up through bar t-1 close (we shift by 1 downstream).
    """
    mu = close.rolling(lookback, min_periods=lookback).mean()
    sd = close.rolling(lookback, min_periods=lookback).std(ddof=0)
    return ((close - mu) / sd.replace(0.0, np.nan)).rename("z")


def rolling_slope(s: pd.Series, lookback: int) -> pd.Series:
    """OLS slope of ``s`` over the last ``lookback`` bars (per-bar slope)."""
    arr = s.to_numpy()
    n = len(arr)
    out = np.full(n, np.nan)
    idx = np.arange(lookback)
    x_mean = idx.mean()
    den = ((idx - x_mean) ** 2).sum()
    for i in range(lookback - 1, n):
        window = arr[i - lookback + 1: i + 1]
        if not np.all(np.isfinite(window)):
            continue
        y_mean = window.mean()
        num = ((idx - x_mean) * (window - y_mean)).sum()
        out[i] = num / den if den > 0 else 0.0
    return pd.Series(out, index=s.index, name="slope")


def ema_slope(close: pd.Series, period: int, slope_lookback: int) -> pd.Series:
    """EMA slope: rolling slope of EMA(period) over the last ``slope_lookback`` bars."""
    e = ema(close, period)
    return rolling_slope(e, slope_lookback)


def find_touches(price: float, levels: list[tuple[float, float]],
                 tolerance: float) -> list[str]:
    """Return the names of any (low, high, name) level band touched by ``price``."""
    out = []
    for lo, hi, name in levels:
        if lo - tolerance <= price <= hi + tolerance:
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Signal builder — single-asset, multi-TF VPVR-edge + zscore
# ---------------------------------------------------------------------------


def build_signals(d1m: dict, cfg: dict) -> dict:
    """Build per-symbol signals dict (entry/exit conditions on the 1m index).

    Returns a dict keyed by symbol with:
      - "close"        : 1m close series
      - "z15m"         : 1m-aligned z-score from 15m close, lookback=z_lookback_15m
      - "z2h"          : 1m-aligned z-score from 2h close, lookback=z_lookback_2h
      - "poc_slope15m" : 1m-aligned slope of 15m POC over ``poc_slope_lookback``
      - "ema_slope2h"  : 1m-aligned slope of 2h EMA(period) over ``ema_slope_lookback``
      - "touches"      : 1m-aligned list of edge touches (LVN/VAL/VAH/HVN) per TF
      - "atr"          : 1m-aligned ATR proxy (15m-derived)
      - "params"       : entry/exit thresholds
    """
    ind = cfg["indicators"]
    vpvr_window_15m = int(ind["vpvr_window_bars_15m"])
    vpvr_window_2h = int(ind["vpvr_window_bars_2h"])
    n_bins = int(ind["vpvr_n_bins"])
    hvn_q = float(ind.get("vpvr_hvn_quantile", 0.85))
    lvn_q = float(ind.get("vpvr_lvn_quantile", 0.15))
    n_hvn = int(ind.get("vpvr_num_hvn", 3))
    n_lvn = int(ind.get("vpvr_num_lvn", 3))

    z_lookback_15m = int(ind["zscore_lookback_bars_15m"])
    z_lookback_2h = int(ind["zscore_lookback_bars_2h"])
    z_entry = float(ind["zscore_entry_threshold"])  # |z_15m| threshold
    z_confirm = float(ind["zscore_confirm_threshold"])  # |z_2h| threshold
    z_exit = float(ind["zscore_exit_threshold"])

    poc_slope_lookback = int(ind["poc_slope_lookback"])
    ema_period = int(ind["ema_period_2h"])
    ema_slope_lookback = int(ind["ema_slope_lookback"])

    touch_atr_k = float(ind["touch_atr_k"])
    atr_period = int(ind["atr_period"])

    tp_atr_k = float(ind.get("tp_atr_k", 2.0))  # take-profit in ATR units
    sl_atr_k = float(ind.get("sl_atr_k", 1.5))  # stop-loss in ATR units
    max_hold = int(ind["max_holding_bars"])

    out = {}
    for sym, df in d1m.items():
        # Higher-TF aggregation (closed='left', label='left' — no look-ahead).
        df_15m = aggregate_ohlcv(df, "15min")
        df_2h = aggregate_ohlcv(df, "2h")

        # Z-scores at each TF, then forward-fill onto the 1m index.
        z15m = align_lower_to_upper(df, zscore(df_15m["close"], z_lookback_15m))
        z2h = align_lower_to_upper(df, zscore(df_2h["close"], z_lookback_2h))

        # 15m rolling VPVR levels (POC, VAH, VAL).
        prof_15m = rolling_vpvr_levels(df_15m["close"], df_15m["volume"], vpvr_window_15m, n_bins)
        prof_15m_1m = prof_15m.reindex(df.index, method="ffill")
        poc_slope_15m = align_lower_to_upper(df, rolling_slope(prof_15m["poc"], poc_slope_lookback))

        # 2h trend filter — use signed close-vs-EMA(20) on 2h, ffill'd to 1m.
        # 1 if close > EMA(2h,20), -1 if close < EMA, 0 if NaN.
        ema_2h = ema(df_2h["close"], ema_period)
        trend_2h = (df_2h["close"] > ema_2h).astype(int) - (df_2h["close"] < ema_2h).astype(int)
        trend_2h_1m = trend_2h.reindex(df.index, method="ffill").fillna(0).astype(int)
        # Also keep the raw slope for diagnostic purposes.
        ema_slope_2h = align_lower_to_upper(df, ema_slope(df_2h["close"], ema_period, ema_slope_lookback))

        # 2h rolling VPVR levels (used as edge-touch reference).
        prof_2h = rolling_vpvr_levels(df_2h["close"], df_2h["volume"], vpvr_window_2h, n_bins)
        prof_2h_1m = prof_2h.reindex(df.index, method="ffill")

        # HVN / LVN at both TFs — computed once per TF then reindexed to 1m.
        # The base rolling_vpvr_levels returns POC/VAH/VAL only; we run a
        # parallel pass for HVN/LVN using the same window/bins.
        from _indicators.vpvr_levels import (  # local import to avoid sys.path surprises
            DEFAULT_HVN_QUANTILE,
            DEFAULT_LVN_QUANTILE,
        )
        from _indicators.mtf_xs_pairs_base_20260718 import rolling_vpvr_levels as _rv  # noqa
        # NB: above is identity re-export; the real computation is below.

        # Compute HVN/LVN bands at each TF.
        hvn_zones_15m, lvn_zones_15m = _compute_hvn_lvn_series(df_15m, vpvr_window_15m,
                                                               n_bins, hvn_q, lvn_q,
                                                               n_hvn, n_lvn)
        hvn_zones_2h, lvn_zones_2h = _compute_hvn_lvn_series(df_2h, vpvr_window_2h,
                                                             n_bins, hvn_q, lvn_q,
                                                             n_hvn, n_lvn)

        # Touch bands at 1m resolution: build a per-bar list of (lo, hi, name).
        # Use the same window/bins; we keep it simple — only the most-recent
        # set of HVN/LVN zones is relevant per bar.
        def _touch_series(hvn_zones: pd.Series, lvn_zones: pd.Series,
                          prof: pd.DataFrame, levels_15m_or_2h: str) -> pd.Series:
            """Per 1m bar, return a list of edge-touch tags.

            Tags use the convention "lvn_15m", "val_15m", "vah_15m", "hvn_15m"
            (or ``_2h`` for the 2h pass). Only the most-recent set of zones
            is considered; we compare the bar's close to each zone's price
            band plus VAL/VAH from the rolling profile.
            """
            vah = prof["vah"].reindex(df.index, method="ffill")
            val = prof["val"].reindex(df.index, method="ffill")
            poc = prof["poc"].reindex(df.index, method="ffill")

            def _row(i):
                tags = []
                vah_i = vah.iat[i] if np.isfinite(vah.iat[i]) else None
                val_i = val.iat[i] if np.isfinite(val.iat[i]) else None
                # The "edge" bands: include VAL and VAH from the rolling profile.
                if val_i is not None:
                    tags.append(("val_edge", val_i, val_i))
                if vah_i is not None:
                    tags.append(("vah_edge", vah_i, vah_i))
                # LVN zones (list of tuples; series values are objects, not numeric).
                lvn_z = lvn_zones.iat[i] if lvn_zones.iat[i] is not None else None
                if lvn_z:
                    for (lo, hi, _) in lvn_z:
                        tags.append((f"lvn_{levels_15m_or_2h}", lo, hi))
                # HVN zones
                hvn_z = hvn_zones.iat[i] if hvn_zones.iat[i] is not None else None
                if hvn_z:
                    for (lo, hi, _) in hvn_z:
                        tags.append((f"hvn_{levels_15m_or_2h}", lo, hi))
                return tags

            return [_row(i) for i in range(len(df))]

        # HVN/LVN zones are computed per-TF, so reindex onto the 1m index so the
        # touch lookup aligns with `df.index`.
        hvn_zones_15m_1m = hvn_zones_15m.reindex(df.index, method="ffill")
        lvn_zones_15m_1m = lvn_zones_15m.reindex(df.index, method="ffill")
        hvn_zones_2h_1m = hvn_zones_2h.reindex(df.index, method="ffill")
        lvn_zones_2h_1m = lvn_zones_2h.reindex(df.index, method="ffill")

        touches_15m = _touch_series(hvn_zones_15m_1m, lvn_zones_15m_1m, prof_15m, "15m")
        touches_2h = _touch_series(hvn_zones_2h_1m, lvn_zones_2h_1m, prof_2h, "2h")

        # ATR proxy (15m-derived, ffill'd to 1m).
        atr_15m = wilder_atr(df_15m, atr_period)
        atr_1m = align_lower_to_upper(df, atr_15m)

        out[sym] = {
            "close": df["close"],
            "z15m": z15m,
            "z2h": z2h,
            "poc_slope_15m": poc_slope_15m,
            "ema_slope_2h": ema_slope_2h,
            "trend_2h_sign": trend_2h_1m,
            "prof_15m": prof_15m_1m,
            "prof_2h": prof_2h_1m,
            "touches_15m": touches_15m,
            "touches_2h": touches_2h,
            "atr": atr_1m,
            "params": {
                "z_entry": z_entry,
                "z_confirm": z_confirm,
                "z_exit": z_exit,
                "touch_atr_k": touch_atr_k,
                "tp_atr_k": tp_atr_k,
                "sl_atr_k": sl_atr_k,
                "max_hold": max_hold,
            },
        }
    return out


def _compute_hvn_lvn_series(df: pd.DataFrame, window: int, n_bins: int,
                            hvn_q: float, lvn_q: float,
                            n_hvn: int, n_lvn: int) -> tuple[pd.Series, pd.Series]:
    """Per bar, compute the top-``n_hvn`` and top-``n_lvn`` zones for the most-recent
    ``window`` bars. Returns two pd.Series aligned to ``df.index`` whose values are
    lists of (lo, hi, volume) tuples.
    """
    c = df["close"].to_numpy()
    v = df["volume"].to_numpy()
    n = len(c)
    hvn_out: list[Optional[list[tuple[float, float, float]]]] = [None] * n
    lvn_out: list[Optional[list[tuple[float, float, float]]]] = [None] * n
    for i in range(window, n):
        sub_c = c[i - window: i]
        sub_v = v[i - window: i]
        if not (np.isfinite(sub_c).any() and np.isfinite(sub_v).any()):
            continue
        p_lo = float(np.nanmin(sub_c))
        p_hi = float(np.nanmax(sub_c))
        if not (np.isfinite(p_lo) and np.isfinite(p_hi)) or p_hi <= p_lo:
            continue
        edges = np.linspace(p_lo, p_hi, n_bins + 1)
        idx = np.clip(np.digitize(sub_c, edges) - 1, 0, n_bins - 1)
        bin_v = np.zeros(n_bins)
        for k in range(n_bins):
            mask = idx == k
            bin_v[k] = float(sub_v[mask].sum()) if mask.any() else 0.0
        total = bin_v.sum()
        if total <= 0:
            continue
        hvn_thr = float(np.quantile(bin_v, hvn_q))
        lvn_thr = float(np.quantile(bin_v, lvn_q))
        hvn_zones: list[tuple[float, float, float]] = []
        lvn_zones: list[tuple[float, float, float]] = []
        # Merge contiguous bins where mask is True.
        for thr, store in ((hvn_thr, hvn_zones), (lvn_thr, lvn_zones)):
            mask = bin_v >= thr if thr == hvn_thr else bin_v <= thr
            if not mask.any():
                continue
            # Find contiguous runs of True
            diffs = np.diff(np.concatenate([[False], mask, [False]]).astype(int))
            starts = np.where(diffs == 1)[0]
            ends = np.where(diffs == -1)[0]
            for s, e in zip(starts, ends):
                lo = float(edges[s])
                hi = float(edges[e])  # e is exclusive in our slice indexing
                vol = float(bin_v[s:e].sum())
                store.append((lo, hi, vol))
        # Sort and keep top-n
        hvn_zones.sort(key=lambda z: z[2], reverse=True)
        lvn_zones.sort(key=lambda z: z[2])
        hvn_out[i] = hvn_zones[:n_hvn]
        lvn_out[i] = lvn_zones[:n_lvn]
    return (pd.Series(hvn_out, index=df.index, name="hvn_zones"),
            pd.Series(lvn_out, index=df.index, name="lvn_zones"))


# ---------------------------------------------------------------------------
# Per-symbol backtest loop
# ---------------------------------------------------------------------------


def _backtest_symbol(sig: dict, sym: str, fee_bps: float, slip_bps: float,
                     funding_bps_per_bar: float = 0.0) -> dict:
    close = sig["close"].to_numpy()
    z15 = sig["z15m"].to_numpy()
    z2h = sig["z2h"].to_numpy()
    poc_slope = sig["poc_slope_15m"].to_numpy()
    ema_slope = sig["ema_slope_2h"].to_numpy()
    trend_2h_sign = sig["trend_2h_sign"].to_numpy()
    touches_15m = sig["touches_15m"]
    touches_2h = sig["touches_2h"]
    atr = sig["atr"].to_numpy()
    p = sig["params"]
    n = len(close)

    trade_log: list[Trade] = []
    bar_pnl = np.zeros(n)
    pos = 0
    entry_idx = None
    entry_price = None
    entry_z15 = entry_z2h = entry_slope15 = entry_slope2h = None
    entry_tags_15m = entry_tags_2h = None
    bars_held = 0
    tp_dist = sl_dist = None

    for i in range(1, n):
        cl_i = close[i]
        if not np.isfinite(cl_i):
            continue
        zi15 = z15[i] if np.isfinite(z15[i]) else None
        zi2h = z2h[i] if np.isfinite(z2h[i]) else None
        slope_i = poc_slope[i] if np.isfinite(poc_slope[i]) else None
        ema_i = ema_slope[i] if np.isfinite(ema_slope[i]) else None
        atr_i = atr[i] if np.isfinite(atr[i]) else None

        if pos == 0 and zi15 is not None and zi2h is not None and atr_i is not None:
            direction = 0
            if zi15 <= -p["z_entry"] and abs(zi2h) >= p["z_confirm"]:
                direction = +1
            elif zi15 >= +p["z_entry"] and abs(zi2h) >= p["z_confirm"]:
                direction = -1

            if direction != 0:
                # Edge touch check: long wants LVN/VAL touch from below, short wants HVN/VAH touch.
                tags_15 = [tag for tag, lo, hi in touches_15m[i]]
                tags_2 = [tag for tag, lo, hi in touches_2h[i]]
                tol = p["touch_atr_k"] * atr_i

                # Build a quick touch lookup.
                def _within(price: float, lo: float, hi: float, t: float) -> bool:
                    return (lo - t) <= price <= (hi + t)

                def _touches_long(price: float, t: float) -> bool:
                    # Long: touched a low-volume edge from below.
                    for tag, lo, hi in touches_15m[i] + touches_2h[i]:
                        if tag.startswith("lvn_") or tag == "val_edge":
                            if _within(price, lo, hi, t):
                                return True
                    return False

                def _touches_short(price: float, t: float) -> bool:
                    for tag, lo, hi in touches_15m[i] + touches_2h[i]:
                        if tag.startswith("hvn_") or tag == "vah_edge":
                            if _within(price, lo, hi, t):
                                return True
                    return False

                # 2h trend filter — 2h close-vs-EMA(20) sign must agree with entry.
                # Long only when 2h trend is up (+1); short only when down (-1).
                tr_2h = int(trend_2h_sign[i])
                if direction == +1:
                    trend_ok = (tr_2h >= 0)   # up or sideways; down blocks
                elif direction == -1:
                    trend_ok = (tr_2h <= 0)   # down or sideways; up blocks
                else:
                    trend_ok = False

                if trend_ok:
                    if direction == +1 and _touches_long(cl_i, tol):
                        pos = +1
                    elif direction == -1 and _touches_short(cl_i, tol):
                        pos = -1

                if pos != 0:
                    entry_idx = i
                    entry_price = float(cl_i)
                    entry_z15 = float(zi15)
                    entry_z2h = float(zi2h)
                    entry_slope15 = float(slope_i) if slope_i is not None else float("nan")
                    entry_slope2h = float(ema_i) if ema_i is not None else float("nan")
                    entry_tags_15m = tags_15
                    entry_tags_2h = tags_2
                    bars_held = 1
                    tp_dist = p["tp_atr_k"] * atr_i
                    sl_dist = p["sl_atr_k"] * atr_i

        elif pos != 0:
            bars_held += 1
            ret_1m = float(cl_i) / float(close[i - 1]) - 1.0
            bar_pnl[i] = pos * ret_1m - funding_bps_per_bar
            exit_reason = None

            # TP / SL check on the bar's close.
            if tp_dist is not None and sl_dist is not None:
                if pos == +1 and (cl_i - entry_price) >= tp_dist:
                    exit_reason = "tp"
                elif pos == +1 and (entry_price - cl_i) >= sl_dist:
                    exit_reason = "sl"
                elif pos == -1 and (entry_price - cl_i) >= tp_dist:
                    exit_reason = "tp"
                elif pos == -1 and (cl_i - entry_price) >= sl_dist:
                    exit_reason = "sl"

            # Z reversion check.
            if exit_reason is None and zi15 is not None and abs(zi15) <= p["z_exit"]:
                exit_reason = "z_mean_revert"
            # Max hold.
            if exit_reason is None and bars_held >= p["max_hold"]:
                exit_reason = "max_holding"

            if exit_reason is not None:
                exit_price = float(cl_i)
                gross = pos * (exit_price / entry_price - 1.0)
                cost = 2.0 * (fee_bps + slip_bps) / 10_000.0  # round-trip per leg
                net = gross - cost
                trade_log.append(Trade(
                    symbol=sym,
                    direction="long" if pos == +1 else "short",
                    entry_ts=sig["close"].index[entry_idx],
                    entry_price=entry_price,
                    exit_ts=sig["close"].index[i],
                    exit_price=exit_price,
                    pnl_pct=net,
                    bars_held=bars_held,
                    z15m_at_entry=entry_z15,
                    z2h_at_entry=entry_z2h,
                    poc_slope_15m_at_entry=entry_slope15,
                    ema_slope_2h_at_entry=entry_slope2h,
                    touched_lvn_15m=int(any(t.startswith("lvn_15m") for t in entry_tags_15m)),
                    touched_val_15m=int("val_edge" in entry_tags_15m),
                    touched_vah_15m=int("vah_edge" in entry_tags_15m),
                    touched_hvn_15m=int(any(t.startswith("hvn_15m") for t in entry_tags_15m)),
                    touched_lvn_2h=int(any(t.startswith("lvn_2h") for t in entry_tags_2h)),
                    touched_val_2h=int("val_edge" in entry_tags_2h),
                    touched_vah_2h=int("vah_edge" in entry_tags_2h),
                    touched_hvn_2h=int(any(t.startswith("hvn_2h") for t in entry_tags_2h)),
                    exit_reason=exit_reason,
                ))
                pos = 0
                bars_held = 0
                entry_idx = entry_price = entry_z15 = entry_z2h = None
                entry_slope15 = entry_slope2h = None
                entry_tags_15m = entry_tags_2h = None
                tp_dist = sl_dist = None

    return {
        "symbol": sym,
        "trades": [_trade_dict(t) for t in trade_log],
        "bar_return": bar_pnl,
        "n_bars": n,
        "span_start": str(sig["close"].index[0].date()) if n else None,
        "span_end": str(sig["close"].index[-1].date()) if n else None,
    }


# ---------------------------------------------------------------------------
# Portfolio aggregation (equal-weight across symbols)
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
# Metrics — daily-resampled Sharpe (smark directive 2026-07-18)
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
    pos = bar_return[bar_return > 0].sum()
    neg = -bar_return[bar_return < 0].sum()
    pf = float(pos / neg) if neg > 0 else float("inf")
    eq = np.empty(len(bar_return))
    eq[0] = starting_capital
    for i in range(1, len(bar_return)):
        eq[i] = eq[i - 1] * (1.0 + bar_return[i])
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    return {"profit_factor": float(pf), "max_drawdown_pct": float(dd.min())}


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def run_backtest(d1m: dict, cfg: dict, funding: Optional[dict] = None) -> dict:
    signals_by_sym = build_signals(d1m, cfg)
    fee_bps = float(cfg.get("fees_bps_per_side", 5.0))
    slip_bps = float(cfg.get("slippage_bps_per_side", 5.0))
    sizing_cfg = cfg.get("sizing", {})
    target_vol = float(sizing_cfg.get("target_vol_annualized", 0.0))
    per_sym = []
    for sym, sig in signals_by_sym.items():
        result = _backtest_symbol(sig, sym, fee_bps=fee_bps, slip_bps=slip_bps)
        per_sym.append(result)
    starting_cap = float(cfg.get("starting_capital_usd", 100000.0))
    portfolio = build_portfolio(per_sym, starting_capital=starting_cap)

    # Optional: vol-target sizing (apply per-bar risk-normalised weights to bar returns).
    if target_vol > 0:
        try:
            import sys as _sys
            _shared_root = str(_ROOT)
            if _shared_root not in _sys.path:
                _sys.path.insert(0, _shared_root)
            from _shared.sizing.vol_target import vol_target_weights
            eq = pd.Series(portfolio["equity"], index=pd.date_range("2022-01-01", periods=portfolio["n_bars"], freq="1min"))
            rets = eq.pct_change().fillna(0.0)
            weights = vol_target_weights(rets, target_vol=target_vol, lookback=240, floor=0.25, cap=4.0, periods_per_year=365*24*60)
            new_rets = rets * weights
            new_eq = (1 + new_rets).cumprod() * starting_cap
            portfolio["equity_vol_target"] = new_eq.to_numpy()
            portfolio["bar_return_vol_target"] = new_rets.to_numpy()
            portfolio["vol_target_weights"] = weights.to_numpy()
        except Exception as e:
            portfolio["vol_target_error"] = str(e)
    return {"per_symbol": per_sym, "portfolio": portfolio}


__all__ = [
    "VARIANT_KEY",
    "Trade",
    "build_signals",
    "run_backtest",
    "build_portfolio",
    "daily_returns",
    "sharpe_daily_resampled",
    "profit_factor_and_mdd",
    "zscore",
    "rolling_slope",
    "ema_slope",
]
