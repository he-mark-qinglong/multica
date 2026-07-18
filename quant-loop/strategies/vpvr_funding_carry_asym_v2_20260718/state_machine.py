"""State machine for vpvr_funding_carry_asym_v2 (SMA-34990).

Drives entries/exits on the 1m bar stream given the per-bar decision
frame. Costs use the shared ``cost_model.apply_cost``; sizing uses
the shared ``vol_target.apply_vol_target``; metrics use the shared
``validators.metrics_validator.validate_metrics``.

Public API
----------
``run_backtest(df_1m, decision, cfg)`` → dict
"""
from __future__ import annotations

import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

QUANT_LOOP = Path("/home/smark/multica/quant-loop")
sys.path.insert(0, str(QUANT_LOOP))

from _shared.execution.cost_model import BINANCE_FUTURES, apply_cost  # noqa: E402
from _shared.sizing.vol_target import apply_vol_target  # noqa: E402
from _shared.validators.metrics_validator import safe_validate  # noqa: E402


VARIANT_KEY = "vpvr_funding_carry_asym_v2_20260718"


@dataclass
class Trade:
    variant: str
    symbol: str
    direction: str
    entry_ts: str
    entry_price: float
    exit_ts: str
    exit_price: float
    gross_pnl_pct: float
    cost_pct: float
    net_pnl_pct: float
    bars_held: int
    exit_reason: str
    funding_ema_at_entry: float
    half_at_entry: str
    slope_4h_at_entry: float


def _state_machine(
    df_1m: pd.DataFrame,
    decision: pd.DataFrame,
    cfg: dict,
) -> dict:
    sym = cfg["instruments"][0]
    p = cfg["params"]
    adv_usd = float(cfg.get("adv_usd_default", 10_000_000_000.0))
    notional = float(p["notional_per_trade_usd"])

    close = df_1m["close"].astype(np.float64)
    high = df_1m["high"].astype(np.float64)
    low = df_1m["low"].astype(np.float64)
    open_ = df_1m["open"].astype(np.float64)

    dec_arr = decision["decision"].astype(np.int64).values
    half_arr = decision["half"].values
    funding_ema_arr = decision["funding_ema"].astype(np.float64).values
    slope_arr = decision["slope_4h"].astype(np.float64).values
    atr_arr = decision["atr_1m"].astype(np.float64).values

    tp_atr_k = float(p["take_profit_atr_k_1m"])
    sl_atr_k = float(p["hard_stop_atr_k_1m"])
    max_hold = int(p["max_hold_bars_1m"])
    cooldown = int(p["cooldown_bars_1m"])

    trades: List[Trade] = []
    equity: List[float] = [float(cfg.get("starting_capital_usd", 100_000.0))]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    entry_cost = 0.0
    bars_held = 0
    bars_since_exit = cooldown  # start cooled down
    entry_funding_ema = 0.0
    entry_half = ""
    entry_slope = 0.0

    n_bars = len(df_1m)
    for i in range(1, n_bars):
        # Compute px_close unconditionally so the mark-to-market block
        # at the bottom of the loop always sees it (including the very
        # first bar after entry, when we entered inside the pos==0
        # branch and skipped the pos!=0 branch above).
        px_close = float(close.iloc[i])
        if pos == 0:
            bars_since_exit += 1
            d = int(dec_arr[i])
            at = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0
            if bars_since_exit >= cooldown and d != 0 and at > 0:
                pos = d
                entry_idx = i
                # Enter at the next bar's open — but for simplicity
                # at this bar's close (1m lookahead avoided by using
                # signal computed before bar; the state machine is
                # end-of-bar, so entry at close is the convention).
                entry_px = px_close
                # Cost: round-trip entry + exit (half of round-trip at
                # entry; exit cost settled on close).
                rt_cost = apply_cost(
                    notional_usd=notional,
                    adv_usd=adv_usd,
                    venue=BINANCE_FUTURES,
                    side="taker",
                )
                entry_cost = rt_cost / 2.0
                bars_held = 0
                bars_since_exit = 0
                entry_funding_ema = (
                    float(funding_ema_arr[i])
                    if np.isfinite(funding_ema_arr[i]) else 0.0
                )
                entry_half = str(half_arr[i])
                entry_slope = (
                    float(slope_arr[i]) if np.isfinite(slope_arr[i]) else 0.0
                )
        else:
            bars_held += 1
            px_high = float(high.iloc[i])
            px_low = float(low.iloc[i])
            at = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0

            move = pos * (px_close / entry_px - 1.0)
            exit_now = False
            exit_reason = ""

            if at > 0 and entry_px > 0:
                # TP: intra-bar high exceeds target.
                if pos > 0 and px_high >= entry_px * (1.0 + tp_atr_k * at / entry_px):
                    exit_now = True
                    exit_reason = "take_profit"
                elif pos < 0 and px_low <= entry_px * (1.0 - tp_atr_k * at / entry_px):
                    exit_now = True
                    exit_reason = "take_profit"
                # SL: intra-bar low breaches stop.
                elif pos > 0 and px_low <= entry_px * (1.0 - sl_atr_k * at / entry_px):
                    exit_now = True
                    exit_reason = "hard_stop"
                elif pos < 0 and px_high >= entry_px * (1.0 + sl_atr_k * at / entry_px):
                    exit_now = True
                    exit_reason = "hard_stop"

            if not exit_now and bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                gross = pos * (px_close / entry_px - 1.0)
                # Settle the second half of round-trip cost on exit.
                rt_cost = apply_cost(
                    notional_usd=notional,
                    adv_usd=adv_usd,
                    venue=BINANCE_FUTURES,
                    side="taker",
                )
                exit_cost = rt_cost / 2.0
                total_cost = entry_cost + exit_cost
                net = gross - (total_cost / max(notional, 1e-9))
                trades.append(Trade(
                    variant=VARIANT_KEY,
                    symbol=sym,
                    direction="long" if pos > 0 else "short",
                    entry_ts=str(df_1m.index[entry_idx]),
                    entry_price=entry_px,
                    exit_ts=str(df_1m.index[i]),
                    exit_price=px_close,
                    gross_pnl_pct=float(gross),
                    cost_pct=float(total_cost / max(notional, 1e-9)),
                    net_pnl_pct=float(net),
                    bars_held=bars_held,
                    exit_reason=exit_reason,
                    funding_ema_at_entry=entry_funding_ema,
                    half_at_entry=entry_half,
                    slope_4h_at_entry=entry_slope,
                ))
                equity.append(equity[-1] * (1.0 + net))
                pos = 0
                entry_idx = None
                bars_since_exit = 0
                continue

        # Mark-to-market each bar (unrealized when in position).
        if pos != 0:
            prev_close = float(close.iloc[i - 1])
            bar_pnl = pos * (px_close / prev_close - 1.0)
            equity.append(equity[-1] * (1.0 + bar_pnl))
        else:
            equity.append(equity[-1])

    return {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "symbol": sym,
        "n_bars": n_bars,
        "span_start": str(df_1m.index[0]),
        "span_end": str(df_1m.index[-1]),
        "trades": [asdict(t) for t in trades],
        "equity": np.asarray(equity, dtype=np.float64),
        "diagnostics": {
            "n_long_entries": sum(1 for t in trades if t.direction == "long"),
            "n_short_entries": sum(1 for t in trades if t.direction == "short"),
            "exit_reasons": {
                r: sum(1 for t in trades if t.exit_reason == r)
                for r in {"take_profit", "hard_stop", "time_stop"}
            },
        },
    }


def run_backtest(df_1m: pd.DataFrame, decision: pd.DataFrame, cfg: dict) -> dict:
    """Run the V2 state machine and return a backtest envelope dict.

    The envelope includes trades + equity + diagnostics; the equity
    curve is rescaled by the shared ``apply_vol_target`` layer so the
    baseline shares the cost-aware series.
    """
    out = _state_machine(df_1m, decision, cfg)
    # Vol-target rescaling (risk normalizer). The decision series here
    # is the baseline before vol-targeting; apply_vol_target rebuilds
    # the equity curve from per-bar returns under the shared sizing
    # module (cycle-46 convention: target_vol=0.20, lookback=60 bars).
    eq = pd.Series(out["equity"], index=pd.DatetimeIndex(
        pd.date_range(start=out["span_start"], periods=len(out["equity"]),
                      freq="1min", tz="UTC")
    ))
    eq_vt = apply_vol_target(
        eq,
        target_vol=float(cfg["params"]["target_vol_annualized"]),
        lookback=int(cfg["params"]["vol_target_lookback"]),
        floor=float(cfg["params"]["vol_target_floor"]),
        cap=float(cfg["params"]["vol_target_cap"]),
        periods_per_year=int(cfg["cpcv"]["periods_per_year"]),
    )
    out["equity_vt"] = np.asarray(eq_vt.values, dtype=np.float64)
    return out


def compute_metrics(result: dict, idx: pd.DatetimeIndex) -> dict:
    """Compute daily-resampled Sharpe, total/ann return, MDD, PF, win rate.

    Includes a metrics-validator pass per Wave 2 infra.
    """
    equity = np.asarray(result["equity"], dtype=np.float64)
    eq_vt = np.asarray(result.get("equity_vt", equity), dtype=np.float64)
    trades = result["trades"]
    if len(equity) < 2 or equity[0] <= 0:
        metrics = {
            "n_bars": int(result["n_bars"]),
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sharpe_daily": 0.0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_bars_held": 0.0,
        }
        return metrics

    eq_idx = idx[: len(equity)]
    daily_eq = pd.Series(eq_vt, index=eq_idx).resample("1D").last().dropna()
    daily_rets = daily_eq.pct_change().dropna()

    if len(daily_rets) >= 2 and daily_rets.std() > 0:
        sharpe = float(daily_rets.mean() / daily_rets.std() * math.sqrt(365.25))
    else:
        sharpe = 0.0

    starting = float(eq_vt[0])
    final = float(eq_vt[-1])
    total_return = (final / starting) - 1.0
    if len(daily_eq) >= 2:
        n_days = max(1, (daily_eq.index[-1] - daily_eq.index[0]).days)
        n_years = n_days / 365.25
    else:
        n_years = len(equity) / (365.25 * 1440)
    annualized = (final / starting) ** (1.0 / max(n_years, 1e-9)) - 1.0 if final > 0 else 0.0

    running_max = np.maximum.accumulate(eq_vt)
    drawdowns = (eq_vt - running_max) / running_max
    max_dd_pct = float(np.min(drawdowns)) * 100.0 if drawdowns.size else 0.0

    n_trades = len(trades)
    if n_trades == 0:
        metrics = {
            "n_bars": int(result["n_bars"]),
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sharpe_daily": 0.0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_bars_held": 0.0,
            "_validator_ok": True,
            "_validator_msg": "skipped (no trades)",
        }
        return metrics

    pnls = np.array([t["net_pnl_pct"] for t in trades], dtype=np.float64)
    gross_profit = float(pnls[pnls > 0].sum())
    gross_loss = float(abs(pnls[pnls < 0].sum()))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = float((pnls > 0).sum() / n_trades)
    avg_bars_held = float(np.mean([t["bars_held"] for t in trades]))

    metrics = {
        "n_bars": int(result["n_bars"]),
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if np.isfinite(profit_factor) else float("inf"),
        "sharpe_daily": round(sharpe, 4),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "avg_bars_held": round(avg_bars_held, 2),
    }
    # Run the metrics validator (raises on sentinel / NaN / OOR).
    ok, msg = safe_validate(metrics, strategy_name=VARIANT_KEY)
    metrics["_validator_ok"] = ok
    metrics["_validator_msg"] = msg
    return metrics


__all__ = ["VARIANT_KEY", "Trade", "run_backtest", "compute_metrics"]