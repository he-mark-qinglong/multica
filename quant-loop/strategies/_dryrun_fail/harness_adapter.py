"""harness_adapter for the _dryrun_fail CI dry-run fixture.

Emits a deliberately-broken native run so the harness produces a FAIL
verdict on multiple gates (mdd > 25%, profit_factor < 1.5, Sharpe < 1,
negative annualized return). Used only by the dry-run test script.

Same trade-spacing layout as _dryrun_pass (10 non-overlapping entries,
1-bar gap) so freqtrade replays every trade. The trade pnl_magnitudes
are inverted vs the data drift so backtrader / freqtrade replays all
see a losing book — keeps G5 in FAIL along with G1-G4 / G6-G7.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_HOLD_DAYS = 5
_GAP_DAYS = 1
_STRIDE = _HOLD_DAYS + _GAP_DAYS
_N_TRADES = 10
_EQUITY_START = 100_000.0
_LOSER_RATE = 0.70
_LOSER_PNL = -0.05
_WINNER_PNL = 0.02
_CRASH_FRACTION = 0.55  # mid-window 55% drawdown → MDD ≈ 60%
_SEED = 349622


def run(df: pd.DataFrame, cfg: dict, symbol: str) -> tuple[pd.Series, list[dict]]:
    rng = np.random.default_rng(_SEED + 1)
    closes = df["close"].to_numpy()
    idx = df.index
    n = len(closes)
    # Trade count scales with window length so every window gets the
    # full 10-trade schedule regardless of whether the harness is run
    # with --windows 1, 3, or 5.
    n_trades = min(_N_TRADES, max(3, n // (_STRIDE * 2)))

    entry_offsets = [_STRIDE * i for i in range(n_trades)]
    # Drop trailing trades that would exit past the end of the slice
    entry_offsets = [o for o in entry_offsets if o + _HOLD_DAYS < n]
    is_loser = rng.random(len(entry_offsets)) < _LOSER_RATE
    trades: list[dict] = []
    for i, off in enumerate(entry_offsets):
        ep = float(closes[off])
        xp_idx = off + _HOLD_DAYS
        if is_loser[i]:
            xp = ep * (1.0 + _LOSER_PNL)
            pnl = _LOSER_PNL
        else:
            xp = ep * (1.0 + _WINNER_PNL)
            pnl = _WINNER_PNL
        trades.append({
            "symbol": symbol,
            "direction": "long",
            "entry_date": idx[off],
            "entry_price": ep,
            "exit_date": idx[xp_idx],
            "exit_price": float(xp),
            "pnl_pct": float(pnl),
        })

    # Build an equity curve that ramps downward across losing trades,
    # upward across winners, and inserts a sharp mid-window crash so
    # MDD blows past the G4 25% threshold even on the partial-recovery
    # tail of the curve.
    eq_vals = np.full(n, _EQUITY_START, dtype=float)
    prev_val = _EQUITY_START
    for i, t in enumerate(trades):
        e = int(idx.get_loc(t["entry_date"]))
        x = int(idx.get_loc(t["exit_date"]))
        target = prev_val * (1.0 + t["pnl_pct"])
        if i == 4 and i < len(entry_offsets) - 1:
            mid_idx = (e + x) // 2
            mid_val = target * (1.0 - _CRASH_FRACTION)
            ramp = np.concatenate([
                np.linspace(prev_val, mid_val, mid_idx - e + 1),
                np.linspace(mid_val, target, x - mid_idx),
            ])
        else:
            ramp = np.linspace(prev_val, target, x - e + 1)
        eq_vals[e:x + 1] = ramp
        prev_val = target

    # tail of the curve (after last trade exit) holds prev_val so the
    # equity series is well-defined for every bar in the slice.
    if trades:
        last_x = int(idx.get_loc(trades[-1]["exit_date"]))
        eq_vals[last_x + 1:] = prev_val

    equity = pd.Series(eq_vals, index=idx, name="equity")
    return equity, trades