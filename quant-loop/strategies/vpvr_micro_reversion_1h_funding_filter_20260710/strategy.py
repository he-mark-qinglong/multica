"""VPVR micro-reversion 1h strategy on BTCUSDT with a funding-rate-proxy filter.

Design summary
---------------

For each 1h bar ``t`` we compute, from data in ``[t-W, t-1]`` only:

- ``atr[t]``              = Wilder ATR over ``atr_period`` bars (1h).
- ``vpvr_poc[t]``         = Point of Control of a coarse 24-bin volume profile
                           built from the last ``vpvr_lookback_hours`` bars.
- ``vpvr_vah[t]``         = Value Area High (70% upper bound of the same
                           profile).
- ``vpvr_val[t]``         = Value Area Low (70% lower bound of the same
                           profile).
- ``funding_proxy[t]``    = rolling ``rolling_return_lookback``-bar return on
                           close, used as a proxy for the perpetual funding
                           rate (positive return => long funding drag, negative
                           => short funding drag). Funding rate series is not
                           present in the canonical 1h parquet, so the proxy
                           must be substituted explicitly and its limit noted
                           in ``config.json``.

Indicator warm-up: every bar before ``max(atr_period, vpvr_lookback_hours,
rolling_return_lookback)`` is NaN and therefore cannot fire a signal.

Entry conditions (micro-reversion):

  LONG at bar t:
      close[t] < vpvr_val[t]              # price below the value area
      funding_proxy[t] <= funding_proxy_min  # funding not bullish
      ATR / close filter passes (see config)

  SHORT at bar t:
      close[t] > vpvr_vah[t]
      funding_proxy[t] >= funding_proxy_max  # funding not bearish

Cooldown: at least ``cooldown_bars`` between consecutive entries (per direction).

Sizing: ``per_signal_weight_pct`` of current equity per signal, with a global
gross-exposure cap of ``max_gross_exposure_pct`` across all open positions.
Only one position at a time per symbol.

Exits (first triggered wins):

  1. Target-to-POC: close[t] >= vpvr_poc[t] (long) or close[t] <= vpvr_poc[t]
     (short).
  2. Stop-loss: close[t] < entry - stop_loss_atr_k * atr[t] (long) /
     close[t] > entry + stop_loss_atr_k * atr[t] (short).
  3. Take-profit: close[t] >= entry + take_profit_atr_k * atr[t] (long) /
     close[t] <= entry - take_profit_atr_k * atr[t] (short).
  4. Time stop: bars_in_position > time_stop_bars.

Costs: ``fees_bps_per_side + slippage_bps_per_side`` applied at entry and
exit as a price multiplier.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------------------
# Indicators — pure functions over an OHLCV frame.
# ---------------------------------------------------------------------------

def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True range series, length N. First bar degenerates to high - low."""
    prev_close = np.concatenate([[close[0]], close[:-1]])
    hi_lo = high - low
    hi_pc = np.abs(high - prev_close)
    lo_pc = np.abs(low - prev_close)
    return np.maximum(np.maximum(hi_lo, hi_pc), lo_pc)


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR. The first ``period`` bars are NaN."""
    tr = true_range(df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy())
    s = pd.Series(tr, index=df.index)
    return s.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def funding_proxy(close: pd.Series, lookback: int) -> pd.Series:
    """Cumulative simple return over the last ``lookback`` bars, used as a
    proxy for the perpetual funding rate. First ``lookback`` bars are NaN."""
    return close.pct_change(periods=lookback)


def vpvr_profile(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    lookback: int,
    n_bins: int = 24,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rolling volume-profile POC/VAH/VAL.

    For each bar ``t`` we use bars in ``[t-lookback+1, t]`` (inclusive) and
    build a coarse ``n_bins``-bin histogram on the (low, high) range. The bin
    with the peak volume is the POC; VAH/VAL bracket 70% of cumulative
    volume (35% each side).

    Returns three length-N float arrays: ``(poc, vah, val)``. Bars before
    ``lookback`` are NaN.
    """
    n = len(close)
    poc = np.full(n, np.nan)
    vah = np.full(n, np.nan)
    val = np.full(n, np.nan)

    # Per-bar bar typical price used as a "where did the volume land" proxy.
    typical = (high + low + close) / 3.0

    if n < lookback:
        return poc, vah, val

    # Cumulative volume per typical price via simple binning per window.
    # The naive per-window histogram is O(n * lookback); we accept that here
    # because the lookback is fixed and small (168h) and there are at most a
    # few thousand bars per symbol.
    for t in range(lookback - 1, n):
        win_lo = low[t - lookback + 1: t + 1]
        win_hi = high[t - lookback + 1: t + 1]
        win_typ = typical[t - lookback + 1: t + 1]
        win_vol = volume[t - lookback + 1: t + 1]
        wmin = float(win_lo.min())
        wmax = float(win_hi.max())
        if not np.isfinite(wmin) or not np.isfinite(wmax) or wmax <= wmin:
            continue
        # Bin each bar's typical price; weight the bar's total volume across
        # the bins it spans (its high-low range). This is the standard
        # TPO-style "where volume landed" approximation.
        edges = np.linspace(wmin, wmax, n_bins + 1)
        # Bin index per bar (the bin where its typical price falls).
        typ_idx = np.clip(np.searchsorted(edges, win_typ, side="right") - 1, 0, n_bins - 1)
        # Allocate bar volume to the range of bins between its low and high.
        lo_idx = np.clip(np.searchsorted(edges, win_lo, side="right") - 1, 0, n_bins - 1)
        hi_idx = np.clip(np.searchsorted(edges, win_hi, side="right") - 1, 0, n_bins - 1)
        hist = np.zeros(n_bins)
        for i in range(len(win_typ)):
            span = max(hi_idx[i] - lo_idx[i], 1)
            share = win_vol[i] / span
            hist[lo_idx[i]: hi_idx[i] + 1] += share
        # POC: argmax of the histogram.
        peak = int(np.argmax(hist))
        poc[t] = (edges[peak] + edges[peak + 1]) / 2.0
        # VAH / VAL: expand outward from POC until 70% of total volume
        # is bracketed.
        total = float(hist.sum())
        if total <= 0:
            continue
        target = 0.7 * total
        cumul = float(hist[peak])
        lo_b, hi_b = peak, peak
        while cumul < target and (lo_b > 0 or hi_b < n_bins - 1):
            left = hist[lo_b - 1] if lo_b > 0 else -1.0
            right = hist[hi_b + 1] if hi_b < n_bins - 1 else -1.0
            if right >= left and hi_b < n_bins - 1:
                hi_b += 1
                cumul += hist[hi_b]
            elif lo_b > 0:
                lo_b -= 1
                cumul += hist[lo_b]
            else:
                break
        vah[t] = (edges[hi_b] + edges[hi_b + 1]) / 2.0
        val[t] = (edges[lo_b] + edges[lo_b + 1]) / 2.0
    return poc, vah, val


# ---------------------------------------------------------------------------
# Annotated frame — indicators + entry signals.
# ---------------------------------------------------------------------------

def annotate(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Return ``df`` with indicator columns + boolean ``long_entry`` /
    ``short_entry`` series. Signals are pre-exit; the backtest loop decides
    whether to act on them (sizing/gross-cap + cooldown)."""
    ind = cfg["indicators"]
    atr_p = int(ind["atr_period"])
    vpvr_lb = int(ind["vpvr_lookback_hours"])
    fr_lb = int(ind["rolling_return_lookback"])
    fr_min = float(ind["funding_proxy_min"])
    fr_max = float(ind["funding_proxy_max"])

    out = df.copy()
    out["atr"] = wilder_atr(out, atr_p)
    out["funding_proxy"] = funding_proxy(out["close"], fr_lb)
    poc, vah, val = vpvr_profile(
        out["high"].to_numpy(),
        out["low"].to_numpy(),
        out["close"].to_numpy(),
        out["volume"].to_numpy(),
        vpvr_lb,
    )
    out["vpvr_poc"] = pd.Series(poc, index=out.index)
    out["vpvr_vah"] = pd.Series(vah, index=out.index)
    out["vpvr_val"] = pd.Series(val, index=out.index)

    have_indicators = (
        out["atr"].notna()
        & out["funding_proxy"].notna()
        & out["vpvr_poc"].notna()
        & out["vpvr_vah"].notna()
        & out["vpvr_val"].notna()
    )

    out["long_entry"] = (
        (out["close"] < out["vpvr_val"])
        & (out["funding_proxy"] <= fr_min)
        & have_indicators
    )
    out["short_entry"] = (
        (out["close"] > out["vpvr_vah"])
        & (out["funding_proxy"] >= fr_max)
        & have_indicators
    )
    out["entry_signal"] = out["long_entry"] | out["short_entry"]
    return out


# ---------------------------------------------------------------------------
# Backtest primitives.
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    direction: str  # "long" or "short"
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    reason: str
    pnl: float  # dollar PnL on the notional
    pnl_pct: float  # return on the entry price (after costs)
    bars_held: int
    atr_at_entry: float


@dataclass
class BacktestResult:
    symbol: str
    n_trades: int
    win_rate: float
    profit_factor: float
    avg_holding_bars: float
    total_return: float
    annualized_sharpe: float
    annualized_sortino: float
    max_drawdown: float
    turnover_per_year: float
    equity_curve: pd.Series = field(default_factory=pd.Series)
    trades: List[Trade] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Vectorized backtest — single Python loop over numpy arrays.
# ---------------------------------------------------------------------------

def run_backtest(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    """Bar-by-bar VPVR micro-reversion backtest on 1h bars.

    Position sizing is ``per_signal_weight_pct`` of current equity per signal
    and a gross-exposure cap of ``max_gross_exposure_pct`` so multiple
    correlated entries cannot stack beyond that. Here we run a single-symbol
    loop; the gross cap is therefore equivalent to one open position at a
    time (which is also enforced by ``in_pos``).
    """
    df = annotate(df, cfg)
    cost_per_side = (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0
    cooldown = cfg["entry"]["cooldown_bars"]
    exit_cfg = cfg["exit"]
    per_signal = cfg["sizing"]["per_signal_weight_pct"]
    max_gross = cfg["sizing"]["max_gross_exposure_pct"]
    starting_equity = cfg["starting_capital_usd"]
    symbol = cfg.get("_symbol", "BTCUSDT")

    # Hot numpy arrays — avoid .iloc inside the loop.
    close = df["close"].to_numpy()
    poc = df["vpvr_poc"].to_numpy()
    atr = df["atr"].to_numpy()
    long_entry = df["long_entry"].to_numpy()
    short_entry = df["short_entry"].to_numpy()
    dates = df.index.to_numpy()
    n = len(df)

    equity = starting_equity
    in_pos: Optional[str] = None
    entry_price = 0.0
    entry_idx = 0
    atr_at_entry = 0.0
    cooldown_until = -1
    open_notional_pct = 0.0

    trades: List[Trade] = []
    equity_path: List[Tuple[pd.Timestamp, float]] = []

    for i in range(n):
        ts = pd.Timestamp(dates[i])
        price = float(close[i])

        if equity_path and equity_path[-1][0] == ts:
            equity_path[-1] = (ts, equity)
        else:
            equity_path.append((ts, equity))

        if in_pos is None:
            if i < cooldown_until:
                continue
            if bool(long_entry[i]) and (open_notional_pct + per_signal) <= max_gross + 1e-12:
                entry_price = price * (1 + cost_per_side)
                in_pos = "long"
                atr_at_entry = float(atr[i]) if not math.isnan(atr[i]) else 0.0
                entry_idx = i
                open_notional_pct += per_signal
                cooldown_until = i + cooldown
            elif bool(short_entry[i]) and (open_notional_pct + per_signal) <= max_gross + 1e-12:
                entry_price = price * (1 + cost_per_side)
                in_pos = "short"
                atr_at_entry = float(atr[i]) if not math.isnan(atr[i]) else 0.0
                entry_idx = i
                open_notional_pct += per_signal
                cooldown_until = i + cooldown
            continue

        # In position — check exits in the order:
        bars_held = i - entry_idx
        atr_now = float(atr[i]) if not math.isnan(atr[i]) else atr_at_entry
        exit_now = False
        reason = ""

        if in_pos == "long":
            # 1. Target-to-POC
            if not math.isnan(poc[i]) and price >= float(poc[i]):
                exit_now, reason = True, "target_to_poc"
            # 2. Stop-loss
            elif price < entry_price - exit_cfg["stop_loss_atr_k"] * atr_now:
                exit_now, reason = True, f"stop_loss<entry-{exit_cfg['stop_loss_atr_k']}*ATR"
            # 3. Take-profit
            elif price >= entry_price + exit_cfg["take_profit_atr_k"] * atr_now:
                exit_now, reason = True, f"take_profit>=entry+{exit_cfg['take_profit_atr_k']}*ATR"
            # 4. Time stop
            elif bars_held >= exit_cfg["time_stop_bars"]:
                exit_now, reason = True, f"time_stop>={exit_cfg['time_stop_bars']}bars"
        else:  # short
            if not math.isnan(poc[i]) and price <= float(poc[i]):
                exit_now, reason = True, "target_to_poc"
            elif price > entry_price + exit_cfg["stop_loss_atr_k"] * atr_now:
                exit_now, reason = True, f"stop_loss>entry+{exit_cfg['stop_loss_atr_k']}*ATR"
            elif price <= entry_price - exit_cfg["take_profit_atr_k"] * atr_now:
                exit_now, reason = True, f"take_profit<=entry-{exit_cfg['take_profit_atr_k']}*ATR"
            elif bars_held >= exit_cfg["time_stop_bars"]:
                exit_now, reason = True, f"time_stop>={exit_cfg['time_stop_bars']}bars"

        if exit_now:
            exit_price_net = price * (1 - cost_per_side)
            pnl_pct = (exit_price_net / entry_price - 1.0) * (
                1.0 if in_pos == "long" else -1.0
            )
            pnl_abs = pnl_pct * open_notional_pct * equity
            trades.append(
                Trade(
                    symbol=symbol,
                    direction=in_pos,
                    entry_date=pd.Timestamp(dates[entry_idx]),
                    entry_price=entry_price,
                    exit_date=ts,
                    exit_price=exit_price_net,
                    reason=reason,
                    pnl=pnl_abs,
                    pnl_pct=pnl_pct,
                    bars_held=bars_held,
                    atr_at_entry=atr_at_entry,
                )
            )
            equity += pnl_abs
            equity_path[-1] = (ts, equity)
            in_pos = None
            open_notional_pct = 0.0

    # Force-close any open position at last close.
    if in_pos is not None:
        last = n - 1
        lp = float(close[last]) * (1 - cost_per_side)
        pnl_pct = (lp / entry_price - 1.0) * (1.0 if in_pos == "long" else -1.0)
        pnl_abs = pnl_pct * open_notional_pct * equity
        trades.append(
            Trade(
                symbol=symbol, direction=in_pos,
                entry_date=pd.Timestamp(dates[entry_idx]),
                entry_price=entry_price,
                exit_date=pd.Timestamp(dates[last]),
                exit_price=lp,
                reason="force_close_eod",
                pnl=pnl_abs, pnl_pct=pnl_pct,
                bars_held=last - entry_idx,
                atr_at_entry=atr_at_entry,
            )
        )
        equity += pnl_abs
        if equity_path and equity_path[-1][0] == pd.Timestamp(dates[last]):
            equity_path[-1] = (pd.Timestamp(dates[last]), equity)
        else:
            equity_path.append((pd.Timestamp(dates[last]), equity))

    eq = pd.Series(
        [v for _, v in equity_path],
        index=[d for d, _ in equity_path],
        name="equity",
    )
    if eq.empty:
        eq = pd.Series([starting_equity], index=[df.index[0]], name="equity")

    return _summarize(symbol, trades, eq, df.index, cfg)


# ---------------------------------------------------------------------------
# Summary statistics.
# ---------------------------------------------------------------------------

def _bars_per_year(timeframe: str) -> float:
    tf = timeframe.lower()
    if tf.endswith("m"):
        return float(int(tf[:-1])) * 60 * 24 * 365
    if tf.endswith("h"):
        return float(int(tf[:-1])) * 24 * 365
    if tf.endswith("d"):
        return float(int(tf[:-1])) * 365
    return 252.0


def _summarize(
    symbol: str,
    trades: List[Trade],
    equity: pd.Series,
    dates: pd.DatetimeIndex,
    cfg: dict,
) -> BacktestResult:
    starting = cfg["starting_capital_usd"]
    n = len(trades)
    bars_per_year = _bars_per_year(cfg["timeframe"])
    if n == 0:
        return BacktestResult(
            symbol=symbol, n_trades=0, win_rate=0.0, profit_factor=0.0,
            avg_holding_bars=0.0, total_return=0.0,
            annualized_sharpe=0.0, annualized_sortino=0.0,
            max_drawdown=0.0, turnover_per_year=0.0,
            equity_curve=pd.Series([starting], index=[dates[0]]),
            trades=[],
        )
    pnls = np.array([t.pnl_pct for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    win_rate = float(len(wins)) / n
    profit_factor = (
        float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    )
    avg_hold = float(np.mean([t.bars_held for t in trades]))

    eq = equity.copy()
    reindexed = eq.reindex(dates).ffill().fillna(starting)
    bar_ret = reindexed.pct_change().fillna(0.0)
    sigma = float(bar_ret.std(ddof=0))
    if sigma == 0:
        sharpe = 0.0
        sortino = 0.0
    else:
        sharpe = float(bar_ret.mean() / sigma * math.sqrt(bars_per_year))
        downside = bar_ret[bar_ret < 0]
        dstd = float(downside.std(ddof=0)) if len(downside) > 1 else sigma
        sortino = (
            float(bar_ret.mean() / dstd * math.sqrt(bars_per_year))
            if dstd and dstd > 0 else 0.0
        )
    rolling_max = reindexed.cummax()
    drawdown = (reindexed - rolling_max) / rolling_max
    max_dd = float(drawdown.min())
    total_ret = float(reindexed.iloc[-1] / starting - 1)
    years = max((dates[-1] - dates[0]).days / 365.25, 1.0 / 365.25)
    turnover = n / years

    return BacktestResult(
        symbol=symbol, n_trades=n, win_rate=win_rate, profit_factor=profit_factor,
        avg_holding_bars=avg_hold, total_return=total_ret,
        annualized_sharpe=sharpe, annualized_sortino=sortino,
        max_drawdown=max_dd, turnover_per_year=turnover,
        equity_curve=reindexed, trades=trades,
    )


def baseline_hold(df: pd.DataFrame, cfg: dict) -> BacktestResult:
    """Buy on the first bar, sell on the last. Sanity check."""
    starting = cfg["starting_capital_usd"]
    cost_per_side = (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0
    sym = cfg.get("_symbol", "BTCUSDT")
    if len(df) < 2:
        return BacktestResult(
            symbol=sym, n_trades=0, win_rate=0.0, profit_factor=0.0,
            avg_holding_bars=0.0, total_return=0.0,
            annualized_sharpe=0.0, annualized_sortino=0.0,
            max_drawdown=0.0, turnover_per_year=0.0,
            equity_curve=pd.Series([starting], index=[df.index[0]]),
            trades=[],
        )
    first_close = float(df["close"].iloc[0]) * (1 + cost_per_side)
    last_close = float(df["close"].iloc[-1]) * (1 - cost_per_side)
    pnl_pct = last_close / first_close - 1.0
    trades = [
        Trade(sym, "long", df.index[0], first_close, df.index[-1], last_close,
              "buyhold", pnl_pct * starting, pnl_pct, len(df) - 1, 0.0)
    ]
    eq = pd.Series([starting, starting * (1 + pnl_pct)], index=[df.index[0], df.index[-1]])
    reindexed = eq.reindex(df.index).ffill().fillna(starting)
    return BacktestResult(
        symbol=sym, n_trades=1,
        win_rate=1.0 if pnl_pct > 0 else 0.0,
        profit_factor=float("inf") if pnl_pct > 0 else 0.0,
        avg_holding_bars=len(df) - 1, total_return=pnl_pct,
        annualized_sharpe=0.0, annualized_sortino=0.0,
        max_drawdown=0.0,
        turnover_per_year=1.0 / max((df.index[-1] - df.index[0]).days / 365.25, 1.0),
        equity_curve=reindexed, trades=trades,
    )