"""Backtest engine for vpvr_edge_zscore_multi_tf (SMA-34991).

Public API
----------
``VARIANT_KEY``
``run_backtest(df_1m, df_15m, df_2h, cfg) -> dict``

The backtest consumes three per-TF OHLCV frames plus a config dict,
runs the per-TF signal builders (15m primary, 2h trend filter, 1m
execution reads both), and runs a single state-machine simulator on
the 1m bar stream with the cross-TF exit precedence:

  1. Hard stop (intra-bar, 1m ATR)
  2. Take profit (intra-bar, 1m ATR)
  3. Trailing stop at 1.0x ATR (1.5x for ``conviction=high``)
  4. 2h trend reversal -> exit at next 1m bar's open
  5. 15m zscore exits back to ``|z| < zscore_exit_threshold`` (0.5)
  6. Time stop = TF-specific max_hold_bars

Sizing per SPEC: fixed-fraction ``risk_target_pct`` of equity per
trade. Position-weight is vol-targeted via ``_shared.sizing.vol_target``
to a target annualized vol of 0.15 (15%).

Costs: applied via ``_shared.execution.cost_model.apply_cost`` with
``venue=BINANCE_SPOT`` (10bp taker + slippage). Round-trip = 2x single
leg.

The 1m ATR drives stop placement (1m bars are the execution horizon).
The 15m edge / 2h trend frame is ffill-aligned onto the 1m index inside
``build_signals_1m``.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    from _shared.paths import quant_loop_root
except ImportError:  # bare-script mode
    _QL = str(Path(__file__).resolve().parents[2])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import quant_loop_root

QUANT_LOOP = quant_loop_root()
_SHARED_DIR = QUANT_LOOP / "_shared"
for _p in (str(_SHARED_DIR / "execution"), str(_SHARED_DIR / "sizing")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cost_model import BINANCE_SPOT, apply_cost  # noqa: E402
from vol_target import vol_target_weights  # noqa: E402

from build_signals import build_signals  # noqa: E402


VARIANT_KEY = "vpvr_edge_zscore_multi_tf_v1_20260720"


@dataclass
class Trade:
    variant: str
    symbol: str
    direction: str
    entry_ts: str
    entry_price: float
    exit_ts: str
    exit_price: float
    pnl_pct: float
    cost_pct: float
    net_pnl_pct: float
    bars_held: int
    decision_tf: str
    size_mult: float
    conviction: str
    exit_reason: str
    z_entry: float
    z_exit: float


# ---------------------------------------------------------------------------
# 1m state-machine simulator.
# ---------------------------------------------------------------------------

def _state_machine(
    df_1m: pd.DataFrame,
    decision: pd.DataFrame,
    z_15m: pd.Series,
    cfg: dict,
) -> dict:
    """Run the cross-TF state-machine on the 1m bar stream.

    The 1m index is the global clock. Per-bar alignment of the 15m
    signal and the 2h trend comes from ``decision`` (built inside
    ``build_signals_1m`` via ffill).
    """
    sym = cfg["instruments"][0]
    p = cfg["params"]

    close = df_1m["close"].astype(np.float64)
    high = df_1m["high"].astype(np.float64)
    low = df_1m["low"].astype(np.float64)
    open_ = df_1m["open"].astype(np.float64)

    dec_arr = decision["decision"].astype(np.int64).values
    conv_arr = decision["conviction"].values
    atr_arr = decision["atr"].astype(np.float64).values
    trend_arr = decision["trend_dir_a"].astype(np.int64).values
    z15_a_arr = z_15m.reindex(df_1m.index, method="ffill").astype(np.float64).values

    fee = float(p["fee_bps_per_fill"]) / 10000.0
    slip = float(p["slippage_bps_per_fill"]) / 10000.0
    round_trip_cost = 2.0 * (fee + slip)
    risk_target = float(p["risk_target_pct"])
    z_exit_th = float(p["zscore_exit_threshold"])

    take_profit_atr_k = float(p["tf"]["1m"]["take_profit_atr_k"])
    hard_stop_atr_k = float(p["tf"]["1m"]["hard_stop_atr_k"])
    max_hold = int(p["tf"]["1m"]["max_hold_bars"])
    cooldown = int(p["tf"]["1m"]["cooldown_bars"])
    trailing_atr_k = float(p.get("trailing_atr_k", 1.0))

    trades: List[Trade] = []
    equity: List[float] = [float(cfg["starting_capital_usd"])]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    size_mult = 1.0
    conviction = ""
    bars_held = 0
    bars_since_exit = cooldown  # cooled-down so first bar can enter
    trailing_high = 0.0
    trailing_low = 0.0
    z_at_entry = 0.0
    last_trend_at_entry = 0

    for i in range(1, len(df_1m)):
        ts = df_1m.index[i]
        px_open = float(open_.iloc[i])
        px_close = float(close.iloc[i])
        px_high = float(high.iloc[i])
        px_low = float(low.iloc[i])
        d = int(dec_arr[i])
        atr = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0
        trend_now = int(trend_arr[i])
        z15_now = float(z15_a_arr[i]) if np.isfinite(z15_a_arr[i]) else 0.0
        exit_handled_this_bar = False

        if pos == 0:
            bars_since_exit += 1
            if bars_since_exit >= cooldown and d != 0:
                pos = d
                entry_idx = i
                entry_px = px_open
                size_mult = 1.0
                conviction = str(conv_arr[i]) if conv_arr[i] else ""
                if conviction == "high":
                    size_mult = 1.0  # already aligned via confirm gate
                bars_held = 0
                trailing_high = px_open
                trailing_low = px_open
                z_at_entry = z15_now
                last_trend_at_entry = trend_now
        else:
            bars_held += 1
            trailing_high = max(trailing_high, px_high)
            trailing_low = min(trailing_low, px_low)

            exit_now = False
            exit_reason = ""

            # Precedence 1: hard stop.
            if pos > 0 and px_low <= entry_px * (1.0 - hard_stop_atr_k * atr / entry_px):
                exit_now = True
                exit_reason = "hard_stop"
            elif pos < 0 and px_high >= entry_px * (1.0 + hard_stop_atr_k * atr / entry_px):
                exit_now = True
                exit_reason = "hard_stop"

            # Precedence 2: take profit (intra-bar).
            if not exit_now:
                if pos > 0 and px_high >= entry_px * (1.0 + take_profit_atr_k * atr / entry_px):
                    exit_now = True
                    exit_reason = "take_profit"
                elif pos < 0 and px_low <= entry_px * (1.0 - take_profit_atr_k * atr / entry_px):
                    exit_now = True
                    exit_reason = "take_profit"

            # Precedence 3: trailing stop (only after price moved in
            # favor by >= 1 ATR).
            if not exit_now:
                trail_k = trailing_atr_k * (1.5 if conviction == "high" else 1.0)
                if pos > 0:
                    move_favor = (trailing_high - entry_px) / entry_px
                    if move_favor >= atr / entry_px:
                        trail_level = trailing_high - trail_k * atr
                        if px_low <= trail_level:
                            exit_now = True
                            exit_reason = "trailing_stop"
                else:
                    move_favor = (entry_px - trailing_low) / entry_px
                    if move_favor >= atr / entry_px:
                        trail_level = trailing_low + trail_k * atr
                        if px_high >= trail_level:
                            exit_now = True
                            exit_reason = "trailing_stop"

            # Precedence 4: 2h trend reversal.
            if not exit_now and trend_now != 0 and trend_now != last_trend_at_entry:
                exit_now = True
                exit_reason = "trend_reversal_2h"

            # Precedence 5: 15m zscore mean-reversion (z has crossed
            # back through 0 and is within exit threshold).
            if not exit_now:
                if pos > 0 and z15_now > -z_exit_th:
                    exit_now = True
                    exit_reason = "z_exit_15m"
                elif pos < 0 and z15_now < z_exit_th:
                    exit_now = True
                    exit_reason = "z_exit_15m"

            # Precedence 6: time stop.
            if not exit_now and bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            exit_handled_this_bar = exit_now
            if exit_now:
                exit_px = px_close
                gross = pos * (exit_px / entry_px - 1.0)
                net = gross - round_trip_cost
                trades.append(Trade(
                    variant=VARIANT_KEY,
                    symbol=sym,
                    direction="long" if pos == 1 else "short",
                    entry_ts=str(df_1m.index[entry_idx]),
                    entry_price=entry_px,
                    exit_ts=str(ts),
                    exit_price=exit_px,
                    pnl_pct=float(gross),
                    cost_pct=float(round_trip_cost),
                    net_pnl_pct=float(net),
                    bars_held=bars_held,
                    decision_tf="15m",
                    size_mult=size_mult,
                    conviction=conviction,
                    exit_reason=exit_reason,
                    z_entry=float(z_at_entry),
                    z_exit=float(z15_now),
                ))
                equity.append(equity[-1] * (1.0 + risk_target * size_mult * net))
                pos = 0
                entry_idx = None
                bars_since_exit = 0

        # Mark-to-market per bar (unrealized PnL only when in position).
        # Skip when the exit branch above already appended — prevents
        # the equity array from growing by 1 extra element per trade.
        if not exit_handled_this_bar:
            if pos != 0:
                prev_close = float(close.iloc[i - 1])
                bar_pnl = pos * (px_close / prev_close - 1.0)
                equity.append(equity[-1] * (1.0 + risk_target * size_mult * bar_pnl))
            else:
                equity.append(equity[-1])

    return {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "symbol": sym,
        "n_bars": len(df_1m),
        "span_start": str(df_1m.index[0]),
        "span_end": str(df_1m.index[-1]),
        "trades": [asdict(t) for t in trades],
        "equity": np.asarray(equity, dtype=np.float64),
        "diagnostics": {
            "n_long_entries": sum(1 for t in trades if t.direction == "long"),
            "n_short_entries": sum(1 for t in trades if t.direction == "short"),
            "n_conviction_high": sum(1 for t in trades if t.conviction == "high"),
            "exit_reasons": {
                r: sum(1 for t in trades if t.exit_reason == r)
                for r in {"hard_stop", "take_profit", "trailing_stop",
                          "trend_reversal_2h", "z_exit_15m", "time_stop"}
            },
        },
    }


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------

def run_backtest(
    df_1m: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_2h: pd.DataFrame,
    cfg: dict,
) -> dict:
    """Run the full multi-TF backtest.

    Args:
        df_1m: 1m OHLCV (DatetimeIndex, UTC).
        df_15m: 15m OHLCV (DatetimeIndex, UTC).
        df_2h: 2h OHLCV (DatetimeIndex, UTC).
        cfg: config dict (see config.json).

    Returns
    -------
    dict with keys ``variant_key``, ``trades``, ``equity``,
    ``diagnostics``, ``per_tf_signals``.
    """
    p = cfg["params"]

    sigs = build_signals(df_1m, df_15m, df_2h, p)
    sig_1m = sigs["1m"]
    sig_15m = sigs["15m"]
    sig_2h = sigs["2h"]

    bt = _state_machine(df_1m, sig_1m, sig_15m["z_15m"], cfg)

    # Vol-target overlay on the equity curve (size risk).
    target_vol = float(p["vol_target_target_vol"])
    lookback = int(p["vol_target_lookback"])
    floor = float(p["vol_target_floor"])
    cap = float(p["vol_target_cap"])
    eq = bt["equity"]
    if len(eq) > lookback:
        eq_idx = pd.DatetimeIndex(pd.date_range(
            start=bt["span_start"], periods=len(eq), freq="1min", tz="UTC"
        ))
        eq_s = pd.Series(eq, index=eq_idx, dtype=np.float64)
        rets = eq_s.pct_change().fillna(0.0)
        weights = vol_target_weights(
            rets, target_vol=target_vol, lookback=lookback, floor=floor, cap=cap,
            periods_per_year=365 * 24 * 60,  # 1m bars
        )
        sized_rets = rets * weights
        new_eq = (1 + sized_rets).cumprod().to_numpy().copy()
        new_eq *= eq[0] / new_eq[0]
        bt["equity"] = new_eq
        bt["diagnostics"]["vol_target_weights_mean"] = float(weights.mean())
        bt["diagnostics"]["vol_target_weights_max"] = float(weights.max())

    diag_per_tf = {
        "1m": {
            "n_decision_long": int((sig_1m["decision"] == 1).sum()),
            "n_decision_short": int((sig_1m["decision"] == -1).sum()),
            "n_decision_flat": int((sig_1m["decision"] == 0).sum()),
            "n_conviction_high": int((sig_1m["conviction"] == "high").sum()),
        },
        "15m": {
            "n_edge_long": int((sig_15m["edge_15m"] == 1).sum()),
            "n_edge_short": int((sig_15m["edge_15m"] == -1).sum()),
            "z_15m_mean": float(sig_15m["z_15m"].mean()),
            "z_15m_std": float(sig_15m["z_15m"].std()),
        },
        "2h": {
            "trend_counts": {
                k: int((sig_2h["trend_dir_2h"] == v).sum())
                for k, v in [("up", 1), ("down", -1), ("neutral", 0)]
            },
            "ema_slope_mean_bps": float(sig_2h["trend_slope_bps"].mean()),
            "z_2h_mean": float(sig_2h["z_2h"].mean()),
        },
    }
    bt["diagnostics"]["per_tf"] = diag_per_tf
    bt["per_tf_signals"] = {
        "1m_cols": list(sig_1m.columns),
        "15m_cols": list(sig_15m.columns),
        "2h_cols": list(sig_2h.columns),
    }
    return bt


__all__ = ["VARIANT_KEY", "Trade", "run_backtest"]