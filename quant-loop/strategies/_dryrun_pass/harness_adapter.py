"""harness_adapter for the _dryrun_pass CI dry-run fixture.

Emits a deterministic, profitable native run so the harness produces a
PASS verdict when run through the full native + backtrader + freqtrade
pipeline.

Trade schedule uses non-overlapping entries with one-bar gaps so
freqtrade's SignalReplay can replay every trade (overlapping entries
get silently dropped because freqtrade refuses to open a second
position while one is still open). Layout (70 bars × 1d, HOLD=5,
STRIDE=6):
  10 entries at offsets 0, 6, 12, …, 54; exits at +5 → 5, 11, …, 59.

Equity grows at a constant compounded daily rate plus a tiny noise
term so the daily-return Sharpe is comfortably above G1's 1.0
threshold (G6 bootstrap CI lower passes with margin; G7 t-test on 10
positive trade pnls gives p ≪ 0.0125).

Each trade's pnl_pct is fixed at +3.0% (override, not close-to-close)
so the profit_factor is well-defined and the framework CV replays
re-price consistent fills.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_HOLD_DAYS = 5
_GAP_DAYS = 1
_STRIDE = _HOLD_DAYS + _GAP_DAYS  # 6
_N_TRADES = 10
_EQUITY_START = 100_000.0
_TRADE_PNL = 0.03  # +3.0% per trade (override, fixed)
_DAILY_RET = 0.0045  # ~0.45% compounded daily target
_DAILY_NOISE = 0.0008  # 0.08% daily noise → Sharpe ≈ 5-6 annualized
_SEED = 349621


def run(df: pd.DataFrame, cfg: dict, symbol: str) -> tuple[pd.Series, list[dict]]:
    rng = np.random.default_rng(_SEED)
    closes = df["close"].to_numpy()
    idx = df.index
    n = len(closes)
    # Scale trade count to slice length so each window gets the full
    # schedule without overflowing.
    n_trades = min(_N_TRADES, max(3, n // (_STRIDE * 2)))

    entry_offsets = [_STRIDE * i for i in range(n_trades)]
    entry_offsets = [o for o in entry_offsets if o + _HOLD_DAYS < n]
    trades: list[dict] = []
    for off in entry_offsets:
        ep = float(closes[off])
        xp_idx = off + _HOLD_DAYS
        xp = float(closes[xp_idx])
        trades.append({
            "symbol": symbol,
            "direction": "long",
            "entry_date": idx[off],
            "entry_price": ep,
            "exit_date": idx[xp_idx],
            "exit_price": xp,
            "pnl_pct": _TRADE_PNL,
        })

    # Compounded equity: starts at _EQUITY_START, grows at DAILY_RET
    # per day plus a tiny noise term. The tail (after the last trade's
    # exit) keeps compounding so equity.iloc[-1] > _EQUITY_START.
    daily_returns = rng.normal(_DAILY_RET, _DAILY_NOISE, n)
    eq_vals = _EQUITY_START * np.cumprod(1.0 + daily_returns)

    equity = pd.Series(eq_vals, index=idx, name="equity")
    return equity, trades