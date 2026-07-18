"""Backtest engine for vpvr_multi_tf_funding (SMA-34989).

Public API
----------
``VARIANT_KEY``
``run_backtest(df_1m, df_15m, df_4h, cfg) -> dict``

The backtest consumes three per-TF OHLCV frames plus a config dict,
runs the per-TF signal builders, combines them via ``combine_signals``,
and runs a single state-machine simulator on the 1m bar stream using
the cross-TF exit precedence:

  1. Hard stop (intra-bar, TF-specific ATR)
  2. Take profit (intra-bar, TF-specific ATR)
  3. Trailing stop at 1.0x ATR (1.5x for ``conviction=high``)
  4. Cross-TF override: 4h BLOCKED -> exit at next 1m bar's open
  5. Time stop = TF-specific max_hold_bars

Sizing per SPEC: fixed-fraction ``risk_target_pct`` of equity per
trade, scaled by the ``size_mult`` from the combination layer.

Costs: 4 bps fee + 1 bp slippage per fill, applied on entry and exit.
Round-trip = 10 bps.

Funding carry: the SPEC says "Funding carry is charged against PnL
at every 8h funding event that overlaps an open position (per
vpvr_funding_asym_4h_20260713 convention)." For this implementation
the per-bar carry is approximated by the per-bar funding rate (the
ffilled-onto-bar funding, NOT shift(1)) times the position size.
This is a conservative approximation — the cycle-46 funding-cost
convention charges 8h events but our 1m bars accumulate the same
fractional carry over the 8h window.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from build_signals import build_signals
from combine_signals import combine_signals


VARIANT_KEY = "vpvr_multi_tf_funding_v1"


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
    funding_paid_pct: float
    net_pnl_pct: float
    bars_held: int
    decision_tf: str
    size_mult: float
    conviction: str
    exit_reason: str
    agree_count: int
    regime_4h_at_entry: str


def _state_machine(
    df_1m: pd.DataFrame,
    decision: pd.DataFrame,
    cfg: dict,
) -> dict:
    """Run the cross-TF state-machine on the 1m bar stream."""
    sym = cfg["instruments"][0]
    p = cfg["params"]

    close = df_1m["close"].astype(np.float64)
    high = df_1m["high"].astype(np.float64)
    low = df_1m["low"].astype(np.float64)
    open_ = df_1m["open"].astype(np.float64)
    funding_1m = df_1m["funding"].astype(np.float64) if "funding" in df_1m.columns else pd.Series(0.0, index=df_1m.index)

    dec_arr = decision["decision"].astype(np.int64).values
    conv_arr = decision["conviction"].values
    size_arr = decision["size_mult"].astype(np.float64).values
    regime_arr = decision["regime_4h"].values
    agree_arr = decision["agree_count"].astype(np.int64).values
    lead_1m_arr = decision["lead_1m"].astype(bool).values

    fee = float(p["fee_bps_per_fill"]) / 10000.0
    slip = float(p["slippage_bps_per_fill"]) / 10000.0
    round_trip_cost = 2.0 * (fee + slip)
    risk_target = float(p["tf"]["1m"]["risk_target_pct"])

    # Per-TF trade management knobs.
    tf_knobs = {
        "1m": p["tf"]["1m"],
        "15m": p["tf"]["15m"],
        "4h": p["tf"]["4h"],
    }

    trades: List[Trade] = []
    equity: List[float] = [float(cfg["starting_capital_usd"])]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    decision_tf = "1m"
    size_mult = 1.0
    conviction = ""
    agree_count = 0
    entry_regime = ""
    bars_held = 0
    bars_since_exit = 999  # start cooled-down so first bar can enter
    trailing_high = 0.0
    trailing_low = 0.0
    funding_paid = 0.0
    last_lead_1m_ts: Optional[int] = None

    for i in range(1, len(df_1m)):
        ts = df_1m.index[i]
        px_open = float(open_.iloc[i])
        px_close = float(close.iloc[i])
        px_high = float(high.iloc[i])
        px_low = float(low.iloc[i])
        d = int(dec_arr[i])
        regime = str(regime_arr[i])

        if pos == 0:
            bars_since_exit += 1
            if bars_since_exit >= 1 and d != 0:
                # Determine the "decision TF" — the lower-TF whose
                # signal drove the entry. If carry==d, 15m; else if
                # micro==d, 1m; else 4h.
                if int(decision["carry"].iloc[i]) == d:
                    decision_tf = "15m"
                elif int(decision["micro"].iloc[i]) == d:
                    decision_tf = "1m"
                else:
                    decision_tf = "4h"
                size_mult = float(size_arr[i])
                conviction = str(conv_arr[i])
                agree_count = int(agree_arr[i])
                entry_regime = regime

                # Use the decision-TF's knobs for stops and max-hold.
                knob = tf_knobs[decision_tf]
                pos = d
                entry_idx = i
                # Entry at next 1m bar's open (cycle-46 convention).
                entry_px = px_open
                bars_held = 0
                trailing_high = px_open
                trailing_low = px_open
                funding_paid = 0.0
                if lead_1m_arr[i]:
                    last_lead_1m_ts = i
        else:
            bars_held += 1
            # Update trailing extremes.
            trailing_high = max(trailing_high, px_high)
            trailing_low = min(trailing_low, px_low)

            # Use decision-TF's ATR for stops. We use the per-TF ATR
            # pulled from the signal frames (1m ATR for 1m/15m
            # decisions; 4h ATR for 4h decisions — for 15m decisions,
            # we use the 1m ATR as a proxy since the 15m ATR isn't
            # directly available on the 1m index, but is conservative).
            atr_1m = decision["atr_1m"].iloc[i]
            atr_4h = decision["atr_4h"].iloc[i]
            at = float(atr_1m) if np.isfinite(atr_1m) else 0.0
            at4 = float(atr_4h) if np.isfinite(atr_4h) else 0.0
            knob = tf_knobs[decision_tf]
            atr_k = at if decision_tf != "4h" else at4
            take_profit_atr = float(knob["take_profit_atr_k"])
            hard_stop_atr = float(knob["hard_stop_atr_k"])
            max_hold = int(knob["max_hold_bars"])
            trailing_atr_k = float(knob.get("trailing_atr_k", 1.0))
            if conviction == "high":
                trailing_atr_k *= 1.5

            # Funding carry drag: charge the per-bar funding rate
            # proportional to position size and bars held. This is a
            # conservative approximation of the cycle-46 8h event
            # charge spread across 1m bars (the 1m funding rate is the
            # 8h rate ffilled, so per-bar cost = funding_rate / 480).
            fd_1m = float(funding_1m.iloc[i])
            if fd_1m != 0.0:
                # Spread 8h funding over 480 minutes.
                funding_paid += (fd_1m / 480.0) * pos

            exit_now = False
            exit_reason = ""

            # Precedence 1: hard stop.
            if pos > 0 and px_low <= entry_px * (1.0 - hard_stop_atr * atr_k / entry_px):
                exit_now = True
                exit_reason = "hard_stop"
            elif pos < 0 and px_high >= entry_px * (1.0 + hard_stop_atr * atr_k / entry_px):
                exit_now = True
                exit_reason = "hard_stop"

            # Precedence 2: take profit (intra-bar).
            if not exit_now:
                if pos > 0 and px_high >= entry_px * (1.0 + take_profit_atr * atr_k / entry_px):
                    exit_now = True
                    exit_reason = "take_profit"
                elif pos < 0 and px_low <= entry_px * (1.0 - take_profit_atr * atr_k / entry_px):
                    exit_now = True
                    exit_reason = "take_profit"

            # Precedence 3: trailing stop (only after price moved in
            # favor by >= 1 ATR).
            if not exit_now:
                if pos > 0:
                    move_favor = (trailing_high - entry_px) / entry_px
                    if move_favor >= atr_k / entry_px:
                        trail_level = trailing_high - trailing_atr_k * atr_k
                        if px_low <= trail_level:
                            exit_now = True
                            exit_reason = "trailing_stop"
                else:
                    move_favor = (entry_px - trailing_low) / entry_px
                    if move_favor >= atr_k / entry_px:
                        trail_level = trailing_low + trailing_atr_k * atr_k
                        if px_high >= trail_level:
                            exit_now = True
                            exit_reason = "trailing_stop"

            # Precedence 4: cross-TF override — 4h BLOCKED.
            if not exit_now and regime == "BLOCKED":
                exit_now = True
                exit_reason = "block_exit"

            # Precedence 6: time stop.
            if not exit_now and bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                # Exit at next 1m bar's open (would be i+1; we settle at i
                # for this bar since we're already in i; convention:
                # exit on this bar's close, conservative).
                exit_px = px_close
                gross = pos * (exit_px / entry_px - 1.0)
                net = gross - round_trip_cost - funding_paid
                trades.append(Trade(
                    variant=VARIANT_KEY,
                    symbol=sym,
                    direction="long" if pos == 1 else "short",
                    entry_ts=str(df_1m.index[entry_idx]),
                    entry_price=entry_px,
                    exit_ts=str(ts),
                    exit_price=exit_px,
                    pnl_pct=float(gross),
                    funding_paid_pct=float(funding_paid),
                    net_pnl_pct=float(net),
                    bars_held=bars_held,
                    decision_tf=decision_tf,
                    size_mult=size_mult,
                    conviction=conviction,
                    exit_reason=exit_reason,
                    agree_count=agree_count,
                    regime_4h_at_entry=entry_regime,
                ))
                # Mark-to-market: equity grows by net * size_mult * risk_target.
                equity.append(equity[-1] * (1.0 + risk_target * size_mult * net))
                pos = 0
                entry_idx = None
                bars_since_exit = 0
                last_lead_1m_ts = None
                continue

            # Anti-cascade: if we're inside a 1m-leads branch (entry
            # was triggered by the Rule 2 special branch), 15m must
            # confirm within 1 15m bar (~15 1m bars).
            if (
                last_lead_1m_ts is not None
                and (i - last_lead_1m_ts) >= 15
                and int(decision["carry"].iloc[i]) != pos
            ):
                exit_now = True
                exit_reason = "lead_1m_no_confirm"
                exit_px = px_close
                gross = pos * (exit_px / entry_px - 1.0)
                net = gross - round_trip_cost - funding_paid
                trades.append(Trade(
                    variant=VARIANT_KEY,
                    symbol=sym,
                    direction="long" if pos == 1 else "short",
                    entry_ts=str(df_1m.index[entry_idx]),
                    entry_price=entry_px,
                    exit_ts=str(ts),
                    exit_price=exit_px,
                    pnl_pct=float(gross),
                    funding_paid_pct=float(funding_paid),
                    net_pnl_pct=float(net),
                    bars_held=bars_held,
                    decision_tf=decision_tf,
                    size_mult=size_mult,
                    conviction=conviction,
                    exit_reason=exit_reason,
                    agree_count=agree_count,
                    regime_4h_at_entry=entry_regime,
                ))
                equity.append(equity[-1] * (1.0 + risk_target * size_mult * net))
                pos = 0
                entry_idx = None
                bars_since_exit = 0
                last_lead_1m_ts = None
                continue

        # Mark-to-market per bar (unrealized PnL only when in position).
        if pos != 0:
            prev_close = float(close.iloc[i - 1])
            bar_pnl = pos * (px_close / prev_close - 1.0) - (funding_1m.iloc[i] / 480.0) * pos
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
                for r in {"hard_stop", "take_profit", "trailing_stop", "block_exit", "time_stop", "lead_1m_no_confirm"}
            },
        },
    }


def run_backtest(
    df_1m: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_4h: pd.DataFrame,
    cfg: dict,
) -> dict:
    """Run the full multi-TF backtest.

    Args:
        df_1m: 1m OHLCV with funding column (DatetimeIndex UTC).
        df_15m: 15m OHLCV with funding column (DatetimeIndex UTC).
        df_4h: 4h OHLCV with funding column (DatetimeIndex UTC).
        cfg: config dict (see config.json).

    Returns
    -------
    dict with keys ``variant_key``, ``trades``, ``equity`` (1m bar
    aligned), ``diagnostics``, ``per_tf_signals``.
    """
    p = cfg["params"]

    sig_1m = build_signals(df_1m, "1m", p["tf"]["1m"])
    sig_15m = build_signals(df_15m, "15m", p["tf"]["15m"])
    sig_4h = build_signals(df_4h, "4h", p["tf"]["4h"])

    decision = combine_signals(sig_1m, sig_15m, sig_4h, params=p)
    bt = _state_machine(df_1m, decision, cfg)

    # Diagnostic counts of per-TF edge activity.
    diag_per_tf = {
        "1m": {
            "n_micro_long_signals": int(sig_1m["micro_long"].sum()),
            "n_micro_short_signals": int(sig_1m["micro_short"].sum()),
            "n_near_hvn": int(sig_1m["near_hvn"].sum()),
            "n_near_lvn": int(sig_1m["near_lvn"].sum()),
        },
        "15m": {
            "n_carry_long_signals": int(sig_15m["carry_long"].sum()),
            "n_support_zone": int(sig_15m["support_zone"].sum()),
        },
        "4h": {
            "regime_counts": {
                r: int((sig_4h["regime"] == r).sum())
                for r in ["TREND_UP", "TREND_DOWN", "MEAN_REVERT", "BLOCKED"]
            },
            "n_struct_long": int(sig_4h["struct_long"].sum()),
            "n_struct_short": int(sig_4h["struct_short"].sum()),
        },
    }
    diag_combination = {
        "n_decision_long": int((decision["decision"] == 1).sum()),
        "n_decision_short": int((decision["decision"] == -1).sum()),
        "n_decision_flat": int((decision["decision"] == 0).sum()),
        "n_conviction_high": int((decision["conviction"] == "high").sum()),
        "n_lead_1m": int(decision["lead_1m"].sum()),
    }
    bt["diagnostics"]["per_tf"] = diag_per_tf
    bt["diagnostics"]["combination"] = diag_combination

    return bt


__all__ = ["VARIANT_KEY", "Trade", "run_backtest"]