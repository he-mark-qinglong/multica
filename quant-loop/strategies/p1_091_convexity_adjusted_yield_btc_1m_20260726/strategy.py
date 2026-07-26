"""Convexity Adjusted Yield strategy (SMA-36109).

Hypothesis
----------
In fixed-income theory, the *convexity adjustment* to a forward yield
corrects for the non-linear payoff of a forward contract. For a
perpetuals position the analogue is the variance drag: a delta-hedged
leveraged position earns the funding rate LESS the variance tax
(``0.5 × sigma^2``), because position notional scales with the
underlying's volatility.

The funding rate, taken alone, is procyclical — high funding clusters
with high realised vol (the same crowded longs that push funding up
also drive the variance). The CAY strips out this procyclicality:

    ``CAY_apr = funding_apr - 0.5 * sigma_apr ** 2``

For BTC with sigma_apr ~ 0.5, the convexity tax is ~12.5% APR.
Funding historically oscillates between -10% and +30% APR. So CAY is
*negative* most of the time (carry doesn't beat the vol drag) and
turns *positive* only when funding surges above ~12.5%.

Public API
----------
``VARIANT_KEY``
``run_backtest(bars_1m, cfg) -> dict``
``compute_signals_for_cpcv(bars_1m, cfg, candidate_params) -> pd.Series``

Costs: 24bp round-trip applied directly inside the state machine as
``net = gross - 0.0024``. Funding is observed as an indicator but not
added to P&L (we trade directional CAY extremes; the funding leg is
informational).

No-look-ahead
-------------
All rolling windows (``rv``, ``sigma``, ``z_score``, ``atr``) are
explicitly ``shift(1)`` so bar ``t``'s indicator only sees data from
``[t-w, t-1]``. Funding is loaded via forward-fill from a 8h-resolution
series; the **funding value at bar t** is the most recent funding event
that fired on or before the bar's index, which is observable at the
time of the bar (a backtest treats the 8h event as having settled at the
event timestamp). The CAY indicator is then ``shift(1)`` so the entry
decision at bar ``t`` sees CAY only through ``t-1``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

VARIANT_KEY = "p1_091_convexity_adjusted_yield_btc_1m_20260726"


# ---------------------------------------------------------------------------
# Indicator helpers.
# ---------------------------------------------------------------------------

def _rolling_rv(returns: pd.Series, window: int) -> pd.Series:
    """Rolling realised variance (sum of squared returns), NaN until
    ``window`` bars of history are available."""
    sq = returns.astype(np.float64).pow(2.0)
    return sq.rolling(window=window, min_periods=window).sum()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Standard rolling-mean ATR with ``close.shift(1)`` so today's
    range cannot leak into today's ATR (cycle-46 convention)."""
    h = high.astype(np.float64)
    l = low.astype(np.float64)
    c = close.astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _cay_indicators(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Compute the convexity-adjusted-yield indicators on a 1m bar frame.

    Returns a DataFrame indexed like ``bars`` with columns:
      - ``funding_8h``     — current 8h funding rate (ffill, shift-1)
      - ``funding_apr``   — annualised funding (× funding_annualization)
      - ``rv``            — rolling realised variance over ``rv_window_bars``
      - ``sigma_apr``     — annualised vol = sqrt(rv / win × 525600)
      - ``vol_drag``      — 0.5 × sigma_apr**2 (the convexity tax)
      - ``cay``           — funding_apr − vol_drag (the CAY signal)
      - ``cay_z``         — rolling z of CAY over ``z_window`` bars
      - ``atr``           — rolling ATR (shift-1)
      - ``warmup``        — boolean, True until enough history exists
    """
    close = bars["close"].astype(np.float64)
    high = bars["high"].astype(np.float64)
    low = bars["low"].astype(np.float64)
    ret = close.pct_change().fillna(0.0)

    w_rv = int(params.get("rv_window_bars", 240))
    w_z = int(params.get("z_window", 1440))
    w_atr = int(params.get("atr_window", 60))
    fund_ann = float(params.get("funding_annualization", 1095.75))
    vol_drag_k = float(params.get("vol_drag_coef", 0.5))
    ppy = float(params.get("_periods_per_year", 525600))

    rv = _rolling_rv(ret, w_rv).shift(1)
    sigma_apr = np.sqrt(rv / float(w_rv) * ppy)

    vol_drag = vol_drag_k * sigma_apr.pow(2.0)

    # Funding: forward-filled at load time; we shift(1) so today's bar
    # only sees the funding that was applicable from the previous bar.
    funding_8h = bars["fundingRate"].astype(np.float64).shift(1)
    funding_apr = funding_8h * fund_ann

    cay = funding_apr - vol_drag

    # Z-score uses CAY through bar t-1 only — the rolling mean/std are
    # explicitly shifted so the entry decision at bar t cannot see
    # today's funding or vol. Without the shift, ``cay_z[t]`` would
    # include ``cay[t]`` in its own baseline (look-ahead, would
    # inflate OOS Sharpe by an order of magnitude on noisy CAY).
    cay_mean = cay.rolling(window=w_z, min_periods=w_z).mean().shift(1)
    cay_std = cay.rolling(window=w_z, min_periods=w_z).std(ddof=1).shift(1)
    cay_z = (cay.shift(1) - cay_mean) / cay_std.replace(0.0, np.nan)

    atr = _atr(high, low, close, w_atr).shift(1)

    warmup = (
        rv.isna()
        | cay.isna()
        | cay_z.isna()
        | atr.isna()
        | cay_std.isna()
        | funding_8h.isna()
    )

    out = pd.DataFrame(
        {
            "rv": rv,
            "sigma_apr": sigma_apr,
            "vol_drag": vol_drag,
            "funding_8h": funding_8h,
            "funding_apr": funding_apr,
            "cay": cay,
            "cay_z": cay_z,
            "atr": atr,
            "warmup": warmup,
        },
        index=bars.index,
    )
    return out


# ---------------------------------------------------------------------------
# State machine (entry / exit).
# ---------------------------------------------------------------------------

def _state_machine(
    bars_1m: pd.DataFrame,
    ind: pd.DataFrame,
    cfg: dict,
) -> dict:
    """Run the convexity-adjusted-yield mean-reversion state machine on
    the 1m bars."""
    p = cfg["params"]
    cp = cfg["candidate_params"]
    sym = cfg["instruments"][0]
    starting = float(cfg.get("starting_capital_usd", 100_000.0))

    z_entry = float(cp["z_entry"])
    z_exit = float(cp["z_exit"])
    hold_bars = int(cp["hold_bars"])
    vol_target = float(p["vol_target"])
    max_size = float(p["max_size_fraction"])
    hard_stop_atr_k = float(p.get("hard_stop_atr_k", 2.0))
    cost_bps_rt = float(p["cost_bps_rt"])
    w_rv = int(p["rv_window_bars"])
    periods_per_year = float(p.get("_periods_per_year", 525600))

    open_ = bars_1m["open"].astype(np.float64)
    close = bars_1m["close"].astype(np.float64)
    high = bars_1m["high"].astype(np.float64)
    low = bars_1m["low"].astype(np.float64)
    rv = ind["rv"].astype(np.float64)
    cay_z = ind["cay_z"].astype(np.float64)
    atr = ind["atr"].astype(np.float64)
    warmup = ind["warmup"].astype(bool)

    trades: List[dict] = []
    equity: List[float] = [starting]
    cash = starting
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    size_frac = 0.0
    bars_held = 0

    n = len(bars_1m)
    for i in range(1, n):
        ts = bars_1m.index[i]
        px_open = float(open_.iloc[i])
        px_close = float(close.iloc[i])
        px_high = float(high.iloc[i])
        px_low = float(low.iloc[i])

        rv_s = float(rv.iloc[i]) if np.isfinite(rv.iloc[i]) else 0.0
        z = float(cay_z.iloc[i]) if np.isfinite(cay_z.iloc[i]) else 0.0
        a = float(atr.iloc[i]) if np.isfinite(atr.iloc[i]) else 0.0

        # Mark-to-market next-bar-open convention: cash plus open-position pnl.
        if pos != 0 and entry_idx is not None:
            unreal = pos * size_frac * (px_close / entry_px - 1.0) * cash
            equity.append(cash + unreal)
        else:
            equity.append(cash)

        if pos == 0:
            bars_held = 0
            if not bool(warmup.iloc[i]):
                # FADE the CAY extreme. Positive CAY_z (funding rich vs vol
                # drag) is over-extended; expect mean-reversion DOWN.
                # Negative CAY_z (funding poor vs vol drag) is over-extended
                # short; expect mean-reversion UP.
                direction = 0
                if z >= z_entry:
                    direction = -1  # SHORT
                elif z <= -z_entry:
                    direction = +1  # LONG

                if direction != 0:
                    # Vol-target sizing.
                    if rv_s > 0.0 and np.isfinite(rv_s):
                        per_bar_var = rv_s / float(w_rv)
                        if per_bar_var > 0.0:
                            ann_vol = float(np.sqrt(per_bar_var * periods_per_year))
                            if ann_vol > 1e-6:
                                size = vol_target / ann_vol
                                size_frac = float(min(max_size, max(0.05, size)))
                            else:
                                size_frac = max_size
                        else:
                            size_frac = max_size
                    else:
                        size_frac = max_size

                    pos = direction
                    entry_idx = i
                    entry_px = px_open
                    bars_held = 0
        else:
            bars_held += 1
            exit_now = False
            exit_reason = ""

            # Time stop.
            if bars_held >= hold_bars:
                exit_now = True
                exit_reason = "time_stop"

            # Mean-reversion exit: cross z_exit from the entry side.
            if not exit_now:
                if pos > 0 and z >= z_exit:  # LONG entry on negative side, exit when crosses back
                    exit_now = True
                    exit_reason = "cay_reverted"
                elif pos < 0 and z <= z_exit:  # SHORT entry on positive side, exit when crosses back
                    exit_now = True
                    exit_reason = "cay_reverted"

            # Hard stop (intra-bar): trade against us by hard_stop_atr_k * ATR.
            if not exit_now and a > 0.0:
                if pos > 0 and px_low <= entry_px * (1.0 - hard_stop_atr_k * a / entry_px):
                    exit_now = True
                    exit_reason = "hard_stop"
                elif pos < 0 and px_high >= entry_px * (1.0 + hard_stop_atr_k * a / entry_px):
                    exit_now = True
                    exit_reason = "hard_stop"

            if exit_now:
                exit_px = px_close
                gross = pos * (exit_px / entry_px - 1.0)
                net = gross - (cost_bps_rt / 10000.0)
                cash += pos * size_frac * net * cash
                cash = max(cash, 1.0)
                trades.append({
                    "variant": VARIANT_KEY,
                    "symbol": sym,
                    "direction": "long" if pos > 0 else "short",
                    "entry_ts": bars_1m.index[entry_idx],
                    "entry_price": entry_px,
                    "exit_ts": ts,
                    "exit_price": exit_px,
                    "pnl_pct": gross,
                    "net_pnl_pct": net,
                    "size_fraction": size_frac,
                    "bars_held": bars_held,
                    "exit_reason": exit_reason,
                    "cay_z_at_entry": float(cay_z.iloc[entry_idx]) if np.isfinite(cay_z.iloc[entry_idx]) else 0.0,
                    "cay_z_at_exit": z,
                })
                pos = 0
                entry_idx = None

    return {
        "equity": np.asarray(equity, dtype=np.float64),
        "trades": trades,
        "n_bars": n,
        "span_start": bars_1m.index[0],
        "span_end": bars_1m.index[-1],
    }


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

def run_backtest(bars_1m: pd.DataFrame, cfg: dict) -> dict:
    """Run the strategy on the 1m bar stream.

    The cfg dict must contain a ``candidate_params`` sub-dict with
    z_entry, z_exit, hold_bars (the pre-registered candidate's params).
    """
    p = dict(cfg["params"])
    ppy = float(cfg.get("cpcv", {}).get("periods_per_year", 525600))
    p["_periods_per_year"] = ppy
    local_cfg = {**cfg, "params": p}
    ind = _cay_indicators(bars_1m, local_cfg["params"])
    return _state_machine(bars_1m, ind, local_cfg)


_SIGNAL_CACHE: Dict[Any, pd.Series] = {}


def compute_signals_for_cpcv(
    bars_1m: pd.DataFrame,
    cfg: dict,
    candidate_params: dict,
) -> pd.Series:
    """CPCV adapter — return per-bar simple returns on ``bars_1m.index``.

    Per the shared CPCV contract, ``strategy_fn(data_train, data_full)``
    must return per-bar returns for ALL bars of ``data_full``. We compute
    on the full bar stream (the indicators use rolling windows that are
    shift-1; the harness slices test bars and applies purge/embargo).

    The harness re-invokes ``strategy_fn`` for every CPCV path (15
    paths × 3 candidates = 45 calls). Each call is independent of
    ``data_train`` because the indicators are shift-1, so we memoise
    the full backtest per (data_id, candidate_params) pair — the only
    thing that differs across paths is which bars the harness slices,
    which happens downstream.
    """
    cache_key = (id(bars_1m), tuple(sorted(candidate_params.items())))
    cached = _SIGNAL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    local_cfg = {**cfg, "candidate_params": candidate_params}
    result = run_backtest(bars_1m, local_cfg)
    eq = pd.Series(result["equity"], index=bars_1m.index, dtype=np.float64)
    rets = eq.pct_change().fillna(0.0)
    _SIGNAL_CACHE[cache_key] = rets
    return rets


__all__ = [
    "VARIANT_KEY",
    "run_backtest",
    "_cay_indicators",
    "compute_signals_for_cpcv",
]
