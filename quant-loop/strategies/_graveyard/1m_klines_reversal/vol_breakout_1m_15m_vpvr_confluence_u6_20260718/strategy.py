"""Single-TF backtest engine for U6 vol_breakout_1m_15m_vpvr_confluence.

Mirrors the iter#84 single-TF 4h design but the bar is whatever native
TF (15m or 1m) we feed in. The state machine is:

  1. Process pending fills scheduled for bar[t].open.
  2. Record NAV after today's fills.
  3. Evaluate signals on close[t]:
       - in_pos=True  -> exit priority ladder (trend_fail, trailing,
                          vol_cool, time_stop)
       - in_pos=False -> long_entry fires -> queue entry for bar[t+1]
  4. End-of-data: force-close any open positions.

Fill convention: signal evaluated on bar[t].close; fill at bar[t+1].open
+/- cost_per_side. No look-ahead.

Public API:
    Trade, BacktestResult, run_backtest(df, cfg, tf)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from indicators import annotate, sqrt_bars_per_year

EXIT_REASON_TREND = "trend_fail"
EXIT_REASON_TRAIL = "trailing_stop"
EXIT_REASON_VOL = "vol_cool"
EXIT_REASON_TIME = "time_stop"
EXIT_REASON_FORCE = "force_close_eod"


@dataclass
class Trade:
    symbol: str
    direction: str  # "long"
    entry_signal_date: pd.Timestamp
    entry_fill_date: pd.Timestamp
    entry_price: float
    exit_signal_date: pd.Timestamp
    exit_fill_date: pd.Timestamp
    exit_price: float
    reason: str
    pnl_usd: float
    pnl_pct: float
    bars_held: int
    atr_at_entry: float
    vpvr_dist_atr_at_entry: float
    realized_vol_at_entry: float
    size_units: float
    nav_at_entry: float


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    equity: List[Tuple[pd.Timestamp, float]] = field(default_factory=list)
    starting_capital: float = 0.0
    final_equity: float = 0.0

    @property
    def n_trades(self) -> int:
        return len(self.trades)


# ---------------------------------------------------------------------------
# Sizing — vol-target on the native TF, in BARS_PER_YEAR for that TF.
# ---------------------------------------------------------------------------

def vol_target_size(
    nav: float,
    close: float,
    realized_vol: float,
    vol_target_pct: float,
    max_position_pct_nav: float,
    sqrt_bpy: float,
) -> float:
    if not np.isfinite(nav) or nav <= 0:
        return 0.0
    if not np.isfinite(close) or close <= 0:
        return 0.0
    if not np.isfinite(realized_vol) or realized_vol <= 0:
        return 0.0
    size_unbounded = (vol_target_pct * nav) / (
        close * realized_vol * sqrt_bpy
    )
    size_capped = (max_position_pct_nav * nav) / close
    return float(max(0.0, min(size_unbounded, size_capped)))


# ---------------------------------------------------------------------------
# Exit evaluation.
# ---------------------------------------------------------------------------

def evaluate_exit(
    *,
    state: Dict,
    cur_close: float,
    cur_low: float,
    cur_atr: float,
    cur_regime: float,
    cur_range_low: float,
    bars_held: int,
    cfg: dict,
    tf: str,
) -> Tuple[bool, str]:
    if not state["in_pos"]:
        return False, ""
    exit_cfg = cfg["exit"]
    atr_k = float(exit_cfg["atr_trailing_k"])
    time_stop_bars = int(cfg["time_stop_bars_per_tf"][tf])
    regime_max = float(exit_cfg["vol_regime_max"])

    if np.isfinite(cur_range_low) and cur_close < cur_range_low:
        return True, EXIT_REASON_TREND

    if np.isfinite(cur_atr) and np.isfinite(cur_low):
        stop_price = state["entry_price"] - atr_k * cur_atr
        if cur_low < stop_price:
            return True, f"{EXIT_REASON_TRAIL}<entry-{atr_k}*ATR"

    if np.isfinite(cur_regime) and cur_regime < regime_max:
        return True, EXIT_REASON_VOL

    if bars_held >= time_stop_bars:
        return True, f"{EXIT_REASON_TIME}>={time_stop_bars}bars"

    return False, ""


# ---------------------------------------------------------------------------
# Backtest.
# ---------------------------------------------------------------------------

@dataclass
class _Pending:
    side: str
    fill_idx: int
    reason: str
    signal_date: pd.Timestamp
    signal_atr: float


def _cost_per_side(cfg: dict) -> float:
    return (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0


def run_backtest(
    df: pd.DataFrame,
    cfg: dict,
    tf: str,
    symbol: str = "TEST",
    starting_capital: Optional[float] = None,
) -> BacktestResult:
    """Run the single-TF Donchian+VPVR backtest on one symbol.

    Parameters
    ----------
    df : pd.DataFrame
        Native-TF OHLCV frame, indexed by openTime (UTC).
    cfg : dict
        Strategy config; ``cfg["indicators_<tf>"]`` and
        ``cfg["time_stop_bars_per_tf"][tf]`` are read.
    tf : str
        "15m" or "1m". Determines indicator block + bars_per_year.
    """
    if starting_capital is None:
        starting_capital = float(cfg["starting_capital_usd"])

    annotated = annotate(df, tf, cfg)
    cost = _cost_per_side(cfg)
    sqrt_bpy = sqrt_bars_per_year(tf)
    n = len(annotated)

    state = {
        "in_pos": False,
        "entry_price": 0.0,
        "entry_signal_date": None,
        "entry_fill_date": None,
        "fill_idx": -1,
        "atr_at_entry": 0.0,
        "vpvr_dist_atr_at_entry": 0.0,
        "realized_vol_at_entry": 0.0,
        "size_units": 0.0,
        "nav_at_entry": 0.0,
    }
    pending: List[_Pending] = []
    nav = starting_capital
    trades: List[Trade] = []
    equity: List[Tuple[pd.Timestamp, float]] = []

    for t in range(n):
        ts_t = annotated.index[t]
        bar = annotated.iloc[t]

        # Step 1: process pending fills at bar[t].open
        if pending:
            still = []
            for order in pending:
                if order.fill_idx != t:
                    still.append(order)
                    continue
                open_price = float(bar["open"])
                if order.side == "entry":
                    if state["in_pos"]:
                        continue
                    rv = float(bar.get("realized_vol", np.nan))
                    if not np.isfinite(rv) or rv <= 0:
                        state["entry_signal_date"] = None
                        continue
                    fill_price = open_price * (1.0 + cost)
                    fresh = vol_target_size(
                        nav=nav,
                        close=open_price,
                        realized_vol=rv,
                        vol_target_pct=cfg["sizing"]["vol_target_annual_pct"],
                        max_position_pct_nav=cfg["sizing"]["max_position_pct_nav"],
                        sqrt_bpy=sqrt_bpy,
                    )
                    if fresh <= 0:
                        state["entry_signal_date"] = None
                        continue
                    state["entry_price"] = fill_price
                    state["entry_fill_date"] = ts_t
                    state["fill_idx"] = t
                    state["atr_at_entry"] = order.signal_atr
                    state["vpvr_dist_atr_at_entry"] = float(
                        bar.get("vpvr_dist_atr", np.nan)
                    )
                    state["realized_vol_at_entry"] = rv
                    state["size_units"] = fresh
                    state["nav_at_entry"] = nav
                    state["in_pos"] = True
                elif order.side == "exit":
                    if not state["in_pos"]:
                        continue
                    fill_price = open_price * (1.0 - cost)
                    pnl_pct = (fill_price / state["entry_price"]) - 1.0
                    pnl_usd = pnl_pct * state["size_units"] * state["entry_price"]
                    trades.append(Trade(
                        symbol=symbol,
                        direction="long",
                        entry_signal_date=state["entry_signal_date"],
                        entry_fill_date=state["entry_fill_date"],
                        entry_price=state["entry_price"],
                        exit_signal_date=order.signal_date,
                        exit_fill_date=ts_t,
                        exit_price=fill_price,
                        reason=order.reason,
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                        bars_held=t - state["fill_idx"],
                        atr_at_entry=state["atr_at_entry"],
                        vpvr_dist_atr_at_entry=state["vpvr_dist_atr_at_entry"],
                        realized_vol_at_entry=state["realized_vol_at_entry"],
                        size_units=state["size_units"],
                        nav_at_entry=state["nav_at_entry"],
                    ))
                    nav += pnl_usd
                    for k in list(state.keys()):
                        if k != "in_pos":
                            state[k] = type(state[k])() if isinstance(state[k], (int, float)) else None
                    state["in_pos"] = False
                    state["fill_idx"] = -1
            pending = still

        equity.append((ts_t, nav))

        if t >= n - 1:
            continue

        cur_close = float(bar["close"])
        cur_low = float(bar.get("low", cur_close))
        cur_atr = float(bar.get("atr", np.nan))
        cur_regime = float(bar.get("vol_regime", np.nan))
        cur_range_low = float(bar.get("range_low", np.nan))
        bars_held = (t - state["fill_idx"]) if state["in_pos"] and state["fill_idx"] >= 0 else 0

        if state["in_pos"]:
            fired, reason = evaluate_exit(
                state=state,
                cur_close=cur_close,
                cur_low=cur_low,
                cur_atr=cur_atr,
                cur_regime=cur_regime,
                cur_range_low=cur_range_low,
                bars_held=bars_held,
                cfg=cfg,
                tf=tf,
            )
            if fired:
                pending.append(_Pending(
                    side="exit",
                    fill_idx=t + 1,
                    reason=reason,
                    signal_date=ts_t,
                    signal_atr=cur_atr,
                ))
        else:
            if bool(bar.get("long_entry", False)):
                rv = float(bar.get("realized_vol", np.nan))
                if np.isfinite(rv) and rv > 0:
                    pending.append(_Pending(
                        side="entry",
                        fill_idx=t + 1,
                        reason="long_entry",
                        signal_date=ts_t,
                        signal_atr=cur_atr,
                    ))
                    state["entry_signal_date"] = ts_t
                    state["atr_at_entry"] = cur_atr
                    state["vpvr_dist_atr_at_entry"] = float(
                        bar.get("vpvr_dist_atr", np.nan)
                    )
                    state["realized_vol_at_entry"] = rv

    # End-of-data force-close
    if state["in_pos"]:
        last_ts = annotated.index[-1]
        last_bar = annotated.iloc[-1]
        last_close = float(last_bar["close"])
        exit_price = last_close * (1.0 - cost)
        pnl_pct = (exit_price / state["entry_price"]) - 1.0
        pnl_usd = pnl_pct * state["size_units"] * state["entry_price"]
        trades.append(Trade(
            symbol=symbol,
            direction="long",
            entry_signal_date=state["entry_signal_date"],
            entry_fill_date=state["entry_fill_date"],
            entry_price=state["entry_price"],
            exit_signal_date=last_ts,
            exit_fill_date=last_ts,
            exit_price=exit_price,
            reason=EXIT_REASON_FORCE,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            bars_held=(n - 1) - state["fill_idx"] if state["fill_idx"] >= 0 else 0,
            atr_at_entry=state["atr_at_entry"],
            vpvr_dist_atr_at_entry=state["vpvr_dist_atr_at_entry"],
            realized_vol_at_entry=state["realized_vol_at_entry"],
            size_units=state["size_units"],
            nav_at_entry=state["nav_at_entry"],
        ))
        nav += pnl_usd

    return BacktestResult(
        trades=trades,
        equity=equity,
        starting_capital=starting_capital,
        final_equity=nav,
    )