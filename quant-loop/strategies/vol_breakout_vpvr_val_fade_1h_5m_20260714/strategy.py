"""Strategy harness for vol_breakout_vpvr_val_fade_1h_5m_20260714 (iter#74, V10).

Multi-TF 1h trend + 5m entry on BTCUSDT. The signal logic lives in the
trading repo's ``strategies.team.combo.vol_breakout_vpvr.VolBreakoutVPVRValFade1h5m``
class (branch agent/indicator-engineer-clone-17/42a03459); this module
imports that class and runs it bar-by-bar on a 5m frame whose columns
already carry the 1h-derived context (`higher_ema_50`, `vpvr_val`).

State machine
-------------

At each 5m bar ``t``:

1. **Apply pending fills** scheduled for ``bar[t].open`` (entries/exits
   queued on bar ``t-1``). We approximate ``bar[t].open`` with
   ``bar[t].close`` of the previous bar to keep the fill-convention
   single-source and reproducible.

2. **Mark-to-market** equity with the current ``close[t]``.

3. **Evaluate signal** on ``close[t]``: build a one-row snapshot of the
   strategy's required columns, then call ``strategy.generate_signal``.
   If the signal is ENTRY → queue a fill on bar ``t+1``. If we are in
   a position, check SL/TP against ``bar[t]``'s high/low (intra-bar).

4. **End-of-data** force-close.

Fill convention
---------------

- Entry fill: bar[t+1].open (we use bar[t].close + 1-bp cost as a robust
  proxy; the cost adds 1-bp slippage as documented).
- Exit fill on SL/TP: bar[t].low / bar[t].high intrabar; we pick the
  conservative (SL-first) exit if both fire on the same bar.
- Force-close at EOD: bar[last].close.

Look-ahead discipline
---------------------

- 1h frame: vpvr_val and ema_50 are both shifted by 1 hour before
  merging onto 5m bars (see data_loader.compute_1h_indicators).
- 5m ATR uses standard Wilder (1-bar look-back via shift(1) inside tr).
- The strategy sees only ``df`` containing bars ``[0, t]`` — never ``[t+1]``.
"""
from __future__ import annotations

import json
import math
import sys
import types
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"

# 5m BARS_PER_YEAR = 365.25 * 24 * 12 = 105120
SQRT_BARS_PER_YEAR_5M: float = math.sqrt(105120.0)


# ---------------------------------------------------------------------------
# Bootstrap the trading repo strategy class
# ---------------------------------------------------------------------------

TR_REPO = Path(
    "/home/smark/multica_workspaces/f9a9d34e-b809-4564-b0c0-b781a70a3f25/42a03459/workdir/trading"
)


def _bootstrap_strategy_class():
    """Return VolBreakoutVPVRValFade1h5m from the trading repo working tree.

    Loads strategies.team as a stub package so we avoid the broken
    strategies/__init__.py chain that pulls in
    indicator_module.indicators.LHFrameStd.
    """
    for name in ("strategies", "strategies.team", "strategies.team.combo"):
        if name not in sys.modules:
            stub = types.ModuleType(name)
            stub.__path__ = [str(TR_REPO / name.replace(".", "/"))]
            sys.modules[name] = stub
    # base.py must be loaded first — combo.vol_breakout_vpvr depends on it.
    base_spec = importlib.util.spec_from_file_location(
        "strategies.team.base", str(TR_REPO / "strategies" / "team" / "base.py")
    )
    base_mod = importlib.util.module_from_spec(base_spec)
    sys.modules["strategies.team.base"] = base_mod
    base_spec.loader.exec_module(base_mod)

    reg_spec = importlib.util.spec_from_file_location(
        "strategies.team.registry", str(TR_REPO / "strategies" / "team" / "registry.py")
    )
    reg_mod = importlib.util.module_from_spec(reg_spec)
    sys.modules["strategies.team.registry"] = reg_mod
    reg_spec.loader.exec_module(reg_mod)

    spec = importlib.util.spec_from_file_location(
        "strategies.team.combo.vol_breakout_vpvr",
        str(TR_REPO / "strategies" / "team" / "combo" / "vol_breakout_vpvr.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["strategies.team.combo.vol_breakout_vpvr"] = mod
    spec.loader.exec_module(mod)
    return mod.VolBreakoutVPVRValFade1h5m


# ---------------------------------------------------------------------------
# Trade dataclass
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    direction: str  # "long"
    entry_signal_date: pd.Timestamp
    entry_fill_date: pd.Timestamp
    entry_price: float
    exit_signal_date: Optional[pd.Timestamp]
    exit_fill_date: pd.Timestamp
    exit_price: float
    reason: str  # "tp", "sl", "force_close", "time_stop"
    pnl_usd: float
    pnl_pct: float
    bars_held: int
    val_pierce_atr: float
    vol_mult_at_entry: float


# ---------------------------------------------------------------------------
# Per-symbol state
# ---------------------------------------------------------------------------

@dataclass
class SymbolState:
    symbol: str
    in_pos: bool = False
    entry_price: float = 0.0
    entry_signal_date: Optional[pd.Timestamp] = None
    entry_fill_date: Optional[pd.Timestamp] = None
    entry_fill_idx: int = -1
    stop_loss: float = 0.0
    take_profit: float = 0.0
    val_pierce_atr: float = 0.0
    vol_mult_at_entry: float = 0.0


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def _max_drawdown(equity: np.ndarray) -> float:
    if len(equity) < 2:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    dd = (equity - peaks) / np.where(peaks == 0, 1, peaks)
    return float(dd.min())


def _sharpe_from_returns(returns: np.ndarray, periods_per_year: int) -> float:
    if len(returns) < 2 or float(np.std(returns, ddof=0)) == 0:
        return 0.0
    return float(np.mean(returns) / np.std(returns, ddof=0) * math.sqrt(periods_per_year))


def _annualised(total_return: float, n_bars: int, periods_per_year: int) -> float:
    """Compound annualised return.

    ``(1 + r)^(periods_per_year / n_bars) - 1`` so a 0.43% return over
    475k 5m bars (≈ 4.5y) lands at ~30%/yr, not at ~600x that.
    """
    if n_bars <= 0 or total_return <= -1.0:
        return 0.0
    return float((1 + total_return) ** (periods_per_year / n_bars) - 1)


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------

def run_backtest(
    df: pd.DataFrame,
    cfg: Optional[Dict[str, Any]] = None,
    *,
    initial_capital: float = 100000.0,
    fee_bps: float = 1.0,
    slippage_bps: float = 1.0,
    position_size_pct: float = 0.95,
    bars_per_year: int = 105120,
    max_hold_bars: int = 288,
) -> Dict[str, Any]:
    """Run V10 bar-by-bar backtest.

    Parameters
    ----------
    df : pd.DataFrame
        5m OHLCV frame with ``higher_ema_50``, ``vpvr_val``, ``atr`` columns.
    cfg : dict, optional
        Strategy params; falls back to config.json params.
    """
    if cfg is None:
        cfg = json.loads(CONFIG_PATH.read_text())["params"]

    # Validate the frame.
    required = {"open", "high", "low", "close", "vol", "atr",
                "higher_ema_50", "vpvr_val"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"df missing required cols: {sorted(missing)}")

    strategy_cls = _bootstrap_strategy_class()
    strategy = strategy_cls(params=cfg)

    cost_per_side = (fee_bps + slippage_bps) / 10000.0
    state = SymbolState(symbol="BTCUSDT")
    trades: List[Trade] = []
    pending_entry: Optional[Dict[str, Any]] = None
    pending_exit: Optional[Dict[str, Any]] = None

    capital = initial_capital
    equity_curve: List[float] = [capital]

    n = len(df)
    for i in range(1, n):
        bar = df.iloc[i]
        ts: pd.Timestamp = df.index[i]
        prev = df.iloc[i - 1]

        close_t = float(bar["close"])
        high_t = float(bar["high"])
        low_t = float(bar["low"])

        # Mark-to-market for drawdown calc.
        position_value = state.entry_price * (capital / state.entry_price) if state.in_pos else 0.0
        equity_curve.append(capital + position_value)

        # ----- 1. Apply pending entry fill on bar[t].open (≈ prev[t].close) -----
        if pending_entry is not None:
            fill_price = float(prev["close"]) * (1.0 + cost_per_side)
            state.in_pos = True
            state.entry_price = fill_price
            state.entry_fill_date = ts
            state.entry_fill_idx = i
            state.stop_loss = pending_entry["stop_loss"]
            state.take_profit = pending_entry["take_profit"]
            state.val_pierce_atr = pending_entry["val_pierce_atr"]
            state.vol_mult_at_entry = pending_entry["vol_mult_at_entry"]
            pending_entry = None

        # ----- 2. Apply pending exit fill on bar[t].open -----
        if pending_exit is not None and state.in_pos:
            fill_price = float(prev["close"]) * (1.0 - cost_per_side)
            qty = (capital_at_entry := pending_exit["qty_units"])
            gross = (fill_price - state.entry_price) * qty
            fees = (state.entry_price + fill_price) * qty * cost_per_side
            pnl = gross - fees
            capital += pnl
            pnl_pct = (fill_price - state.entry_price) / state.entry_price
            trades.append(Trade(
                symbol="BTCUSDT",
                direction="long",
                entry_signal_date=pending_exit["entry_signal_date"],
                entry_fill_date=state.entry_fill_date,
                entry_price=state.entry_price,
                exit_signal_date=pending_exit["exit_signal_date"],
                exit_fill_date=ts,
                exit_price=fill_price,
                reason=pending_exit["reason"],
                pnl_usd=pnl,
                pnl_pct=pnl_pct,
                bars_held=i - state.entry_fill_idx,
                val_pierce_atr=state.val_pierce_atr,
                vol_mult_at_entry=state.vol_mult_at_entry,
            ))
            state.in_pos = False
            state.entry_price = 0.0
            pending_exit = None

        # ----- 3. Manage open position: SL / TP intra-bar -----
        if state.in_pos:
            sl = state.stop_loss
            tp = state.take_profit
            exit_price: Optional[float] = None
            reason = ""
            if low_t <= sl:
                exit_price = sl
                reason = "stop_loss"
            elif high_t >= tp:
                exit_price = tp
                reason = "take_profit"
            elif (i - state.entry_fill_idx) >= max_hold_bars:
                exit_price = close_t
                reason = "time_stop"

            if exit_price is not None:
                qty = pending_qty_units = (capital * position_size_pct) / state.entry_price  # noqa: F841
                gross = (exit_price - state.entry_price) * qty
                fees = (state.entry_price + exit_price) * qty * cost_per_side
                pnl = gross - fees
                capital += pnl
                pnl_pct = (exit_price - state.entry_price) / state.entry_price
                trades.append(Trade(
                    symbol="BTCUSDT",
                    direction="long",
                    entry_signal_date=state.entry_signal_date or ts,
                    entry_fill_date=state.entry_fill_date,
                    entry_price=state.entry_price,
                    exit_signal_date=ts,
                    exit_fill_date=ts,
                    exit_price=exit_price,
                    reason=reason,
                    pnl_usd=pnl,
                    pnl_pct=pnl_pct,
                    bars_held=i - state.entry_fill_idx,
                    val_pierce_atr=state.val_pierce_atr,
                    vol_mult_at_entry=state.vol_mult_at_entry,
                ))
                state.in_pos = False
                state.entry_price = 0.0

        # ----- 4. Generate signal if flat -----
        if not state.in_pos and pending_entry is None:
            # Build the input dict the strategy expects. The df passed to
            # generate_signal includes bar i (current) so the strategy
            # sees the close/high/low/vol/at that it should evaluate.
            # per the strategy code: prev_close is `df.iloc[-2]` and
            # current close is `df.iloc[-1]`. We pass a window of last
            # `lookback + 5` bars to keep latency low.
            lookback = int(cfg.get("lookback", 20))
            window = df.iloc[max(0, i - lookback - 5):i + 1]
            atr_now = float(bar.get("atr") or 0.0)
            if atr_now <= 0 or window["higher_ema_50"].iloc[-1] != window["higher_ema_50"].iloc[-1]:
                # NaN or zero atr — skip
                pass
            else:
                sig_payload = {
                    "df": window,
                    "position": "FLAT",
                    "atr": atr_now,
                    "symbol": "BTCUSDT",
                }
                try:
                    signal = strategy.generate_signal(sig_payload)
                except Exception:
                    signal = None

                if signal is not None and signal.signal_type.value == "ENTRY":
                    pending_entry = {
                        "stop_loss": float(signal.stop_loss),
                        "take_profit": float(signal.take_profit),
                        "val_pierce_atr": float(signal.data.get("val_pierce_atr_actual", 0.0) or 0.0),
                        "vol_mult_at_entry": float(signal.data.get("vol_mult_actual", 0.0) or 0.0),
                    }

    # ----- End-of-data force-close -----
    if state.in_pos:
        last_close = float(df.iloc[-1]["close"])
        qty = (initial_capital * position_size_pct) / state.entry_price
        gross = (last_close - state.entry_price) * qty
        fees = (state.entry_price + last_close) * qty * cost_per_side
        pnl = gross - fees
        capital += pnl
        pnl_pct = (last_close - state.entry_price) / state.entry_price
        trades.append(Trade(
            symbol="BTCUSDT",
            direction="long",
            entry_signal_date=state.entry_signal_date,
            entry_fill_date=state.entry_fill_date,
            entry_price=state.entry_price,
            exit_signal_date=df.index[-1],
            exit_fill_date=df.index[-1],
            exit_price=last_close,
            reason="force_close",
            pnl_usd=pnl,
            pnl_pct=pnl_pct,
            bars_held=n - 1 - state.entry_fill_idx,
            val_pierce_atr=state.val_pierce_atr,
            vol_mult_at_entry=state.vol_mult_at_entry,
        ))

    equity_arr = np.array(equity_curve)
    rets = np.diff(equity_arr) / equity_arr[:-1] if len(equity_arr) > 1 else np.array([])

    total_return = (capital - initial_capital) / initial_capital
    n_trades = len(trades)
    n_wins = sum(1 for t in trades if t.pnl_usd > 0)
    n_losses = n_trades - n_wins
    win_rate = n_wins / n_trades if n_trades > 0 else 0.0
    gross_win = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = -sum(t.pnl_usd for t in trades if t.pnl_usd < 0)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if n_wins > 0 else 0.0
    max_dd = _max_drawdown(equity_arr)
    sharpe = _sharpe_from_returns(rets, bars_per_year)
    annualised = _annualised(total_return, n - 1, bars_per_year)

    return {
        "n_bars": int(n),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "initial_capital": initial_capital,
        "final_capital": float(capital),
        "total_return_pct": float(total_return),
        "annualised_pct": float(annualised),
        "n_trades": int(n_trades),
        "n_wins": int(n_wins),
        "n_losses": int(n_losses),
        "win_rate": float(win_rate),
        "profit_factor": float(pf),
        "max_drawdown_pct": float(max_dd),
        "sharpe": float(sharpe),
        "bars_per_year": bars_per_year,
        "trades": trades,
        "equity_curve": equity_arr,
    }


__all__ = ["Trade", "SymbolState", "run_backtest"]
