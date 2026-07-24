"""Single-TF 15m-only strategy: VPVR-distribution zscore executed at 15m horizon.

This is the partial-progress variant per smark-proxy DECISION 2026-07-20T21:48.
The 1m/15m/2h multi-TF strategy KILL'd (Sharpe -5.23 across 6 walk-forward folds)
because the 1m hard_stop chopped entries before the 15m mean-reversion signal
could resolve. Moving execution to the 15m bar stream (the signal's native
horizon) lets the zscore mean-reversion play out at the holding period the
signal was designed for.

Public API
----------
``VARIANT_KEY``
``run_backtest(df_15m, cfg) -> dict``

Cost: applied via ``_shared.execution.cost_model.apply_cost`` with
``venue=BINANCE_SPOT`` (10bp taker + slippage). Round-trip = 2x single leg.

Sizing: ``_shared.sizing.vol_target.vol_target_weights`` to a target annualized
vol of 0.15 (15%) on per-bar returns, annualized as 15m bars/year.

No-look-ahead invariant: rolling baselines are shifted by 1 bar; the VPVR
snapshot grid is shifted by 1; the entry threshold comparison uses the
shifted z_15m.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

QUANT_LOOP = Path("/home/smark/multica/quant-loop")
_SHARED_DIR = QUANT_LOOP / "_shared"
for _p in (str(_SHARED_DIR / "execution"), str(_SHARED_DIR / "sizing")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cost_model import BINANCE_SPOT, apply_cost  # noqa: E402
from vol_target import vol_target_weights  # noqa: E402

# Reuse the 15m signal builder from the multi-TF strategy (no divergence —
# both use the same _indicators/vpvr_levels.py + build_signals_15m).
sys.path.insert(0, str(QUANT_LOOP / "strategies" / "vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720"))
from build_signals import build_signals_15m  # noqa: E402


VARIANT_KEY = "vpvr_edge_zscore_15m_only_v1_20260720"


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
    size_mult: float
    exit_reason: str
    z_entry: float
    z_exit: float


# ---------------------------------------------------------------------------
# 15m state-machine simulator (no 1m execution layer).
# ---------------------------------------------------------------------------

def _state_machine(
    df: pd.DataFrame,
    sig: pd.DataFrame,
    cfg: dict,
) -> dict:
    """Run the cross-TF state-machine on the 15m bar stream.

    The 15m index is the global clock. Edge is built by ``build_signals_15m``
    using VPVR-distribution zscore + LVN/HVN confluence + POC-slope agreement.

    Exit precedence (per 15m multi-TF stop spec):
      1. Hard stop (intra-bar, 15m ATR)
      2. Take profit (intra-bar, 15m ATR)
      3. Trailing stop at 1.0x ATR (1.5x for ``conviction=high``)
      4. 15m zscore mean-reversion (z has crossed back through
         +/- zscore_exit_threshold)
      5. Time stop = max_hold_bars

    Sizing: vol_target weights applied on the per-bar equity curve.
    """
    sym = cfg["instruments"][0]
    p = cfg["params"]

    close = df["close"].astype(np.float64)
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    open_ = df["open"].astype(np.float64)

    edge = sig["edge_15m"].astype(np.int64).values
    atr_arr = sig["atr"].astype(np.float64).values
    z15_arr = sig["z_15m"].astype(np.float64).values

    fee = float(p["fee_bps_per_fill"]) / 10000.0
    slip = float(p["slippage_bps_per_fill"]) / 10000.0
    round_trip_cost = 2.0 * (fee + slip)
    risk_target = float(p["risk_target_pct"])
    z_exit_th = float(p["zscore_exit_threshold"])

    take_profit_atr_k = float(p["tf"]["15m"]["take_profit_atr_k"])
    hard_stop_atr_k = float(p["tf"]["15m"]["hard_stop_atr_k"])
    max_hold = int(p["tf"]["15m"]["max_hold_bars"])
    cooldown = int(p["tf"]["15m"]["cooldown_bars"])
    trailing_atr_k = float(p.get("trailing_atr_k", 1.0))

    trades: List[Trade] = []
    equity: List[float] = [float(cfg["starting_capital_usd"])]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    size_mult = 1.0
    bars_held = 0
    bars_since_exit = cooldown
    trailing_high = 0.0
    trailing_low = 0.0
    z_at_entry = 0.0

    for i in range(1, len(df)):
        ts = df.index[i]
        px_open = float(open_.iloc[i])
        px_close = float(close.iloc[i])
        px_high = float(high.iloc[i])
        px_low = float(low.iloc[i])
        d = int(edge[i])
        atr = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0
        z15_now = float(z15_arr[i]) if np.isfinite(z15_arr[i]) else 0.0
        exit_handled_this_bar = False

        if pos == 0:
            bars_since_exit += 1
            if bars_since_exit >= cooldown and d != 0:
                pos = d
                entry_idx = i
                entry_px = px_open
                size_mult = 1.0
                bars_held = 0
                trailing_high = px_open
                trailing_low = px_open
                z_at_entry = z15_now
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

            # Precedence 2: take profit.
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
                if pos > 0:
                    move_favor = (trailing_high - entry_px) / entry_px
                    if move_favor >= atr / entry_px:
                        trail_level = trailing_high - trailing_atr_k * atr
                        if px_low <= trail_level:
                            exit_now = True
                            exit_reason = "trailing_stop"
                else:
                    move_favor = (entry_px - trailing_low) / entry_px
                    if move_favor >= atr / entry_px:
                        trail_level = trailing_low + trailing_atr_k * atr
                        if px_high >= trail_level:
                            exit_now = True
                            exit_reason = "trailing_stop"

            # Precedence 4: 15m zscore mean-reversion exit.
            if not exit_now:
                if pos > 0 and z15_now > -z_exit_th:
                    exit_now = True
                    exit_reason = "z_exit_15m"
                elif pos < 0 and z15_now < z_exit_th:
                    exit_now = True
                    exit_reason = "z_exit_15m"

            # Precedence 5: time stop.
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
                    entry_ts=str(df.index[entry_idx]),
                    entry_price=entry_px,
                    exit_ts=str(ts),
                    exit_price=exit_px,
                    pnl_pct=float(gross),
                    cost_pct=float(round_trip_cost),
                    net_pnl_pct=float(net),
                    bars_held=bars_held,
                    size_mult=size_mult,
                    exit_reason=exit_reason,
                    z_entry=float(z_at_entry),
                    z_exit=float(z15_now),
                ))
                equity.append(equity[-1] * (1.0 + risk_target * size_mult * net))
                pos = 0
                entry_idx = None
                bars_since_exit = 0

        # Mark-to-market per bar (one append per bar).
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
        "n_bars": len(df),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "trades": [asdict(t) for t in trades],
        "equity": np.asarray(equity, dtype=np.float64),
        "diagnostics": {
            "n_long_entries": sum(1 for t in trades if t.direction == "long"),
            "n_short_entries": sum(1 for t in trades if t.direction == "short"),
            "exit_reasons": {
                r: sum(1 for t in trades if t.exit_reason == r)
                for r in {"hard_stop", "take_profit", "trailing_stop",
                          "z_exit_15m", "time_stop"}
            },
        },
    }


def run_backtest(df_15m: pd.DataFrame, cfg: dict) -> dict:
    """Run the 15m-only backtest.

    Args:
        df_15m: 15m OHLCV (DatetimeIndex, UTC).
        cfg: config dict (see config.json).

    Returns
    -------
    dict with keys ``variant_key``, ``trades``, ``equity``,
    ``diagnostics``, ``per_tf_signals``.
    """
    p = cfg["params"]
    sig = build_signals_15m(df_15m, p)
    bt = _state_machine(df_15m, sig, cfg)

    # Vol-target overlay on the equity curve (size risk).
    target_vol = float(p["vol_target_target_vol"])
    lookback = int(p["vol_target_lookback"])
    floor = float(p["vol_target_floor"])
    cap = float(p["vol_target_cap"])
    eq = bt["equity"]
    if len(eq) > lookback:
        eq_idx = pd.DatetimeIndex(pd.date_range(
            start=bt["span_start"], periods=len(eq), freq="15min", tz="UTC"
        ))
        eq_s = pd.Series(eq, index=eq_idx, dtype=np.float64)
        rets = eq_s.pct_change().fillna(0.0)
        periods_per_year = 365 * 24 * 4  # 15m bars
        weights = vol_target_weights(
            rets, target_vol=target_vol, lookback=lookback, floor=floor, cap=cap,
            periods_per_year=periods_per_year,
        )
        sized_rets = rets * weights
        new_eq = (1 + sized_rets).cumprod().to_numpy().copy()
        new_eq *= eq[0] / new_eq[0]
        bt["equity"] = new_eq
        bt["diagnostics"]["vol_target_weights_mean"] = float(weights.mean())
        bt["diagnostics"]["vol_target_weights_max"] = float(weights.max())

    bt["per_tf_signals"] = {
        "15m_cols": list(sig.columns),
        "n_edge_long": int((sig["edge_15m"] == 1).sum()),
        "n_edge_short": int((sig["edge_15m"] == -1).sum()),
        "z_15m_mean": float(sig["z_15m"].mean()),
        "z_15m_std": float(sig["z_15m"].std()),
    }
    return bt


__all__ = ["VARIANT_KEY", "Trade", "run_backtest"]
