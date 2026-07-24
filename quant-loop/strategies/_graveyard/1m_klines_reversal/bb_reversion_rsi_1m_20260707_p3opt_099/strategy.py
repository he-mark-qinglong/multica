"""Bollinger Band + RSI 1m mean-reversion strategy on BTCUSDT.

Design summary
---------------

For each 1m bar ``t`` we compute, from data in ``[t-W, t-1]`` only:

- ``bb_mid[t]``     = SMA(close, ``bb_period``)
- ``bb_std[t]``     = rolling stdev(close, ``bb_period``)
- ``bb_upper[t]``   = ``bb_mid`` + ``bb_k`` * ``bb_std``
- ``bb_lower[t]``   = ``bb_mid`` - ``bb_k`` * ``bb_std``
- ``rsi[t]``        = Wilder RSI over ``rsi_period`` bars
- ``atr[t]``        = Wilder ATR over ``atr_period`` bars
- ``vol_ma[t]``     = SMA(volume, ``volume_ma_period``)

Indicator warm-up: every bar before ``max(bb_period, rsi_period, atr_period,
volume_ma_period)`` is NaN and therefore cannot fire a signal.

Entry conditions (mean reversion):

  LONG  at bar t:
      close[t] < bb_lower[t]                  # price below lower band
      rsi[t]   < rsi_oversold                 # momentum oversold
      volume[t] >= volume_ratio_min * vol_ma[t]   # participation

  SHORT at bar t:
      close[t] > bb_upper[t]
      rsi[t]   > rsi_overbought
      volume[t] >= volume_ratio_min * vol_ma[t]

Cooldown: at least ``cooldown_bars`` between consecutive entries (per direction).

Sizing: ``per_signal_weight_pct`` of current equity per signal, with a global
gross-exposure cap of ``max_gross_exposure_pct`` across all open positions.
Only one position at a time per symbol.

Exits (first triggered wins):

  1. RSI midpoint: rsi[t] crosses back above ``exit_rsi_mid`` (long)
     or below ``exit_rsi_mid`` (short).
  2. Mean reversion to mid: close[t] >= bb_mid[t] (long) or
     close[t] <= bb_mid[t] (short).
  3. Stop-loss: close[t] < entry - stop_loss_atr_k * atr[t] (long) /
     close[t] > entry + stop_loss_atr_k * atr[t] (short).
  4. Take-profit: close[t] >= entry + take_profit_atr_k * atr[t] (long) /
     close[t] <= entry - take_profit_atr_k * atr[t] (short).
  5. Time stop: bars_in_position > time_stop_bars.
  6. Slow revert cap: bars_in_position > bars_to_mid_max.

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


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI on a price series. First ``period`` bars are NaN."""
    delta = close.diff()
    up = delta.clip(lower=0.0)
    dn = (-delta).clip(lower=0.0)
    avg_up = up.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_dn = dn.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_up / avg_dn.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(avg_dn != 0.0, 100.0)  # if avg_dn == 0 → all gains → 100
    return rsi


def bb_bands(close: pd.Series, period: int = 20, k: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands using simple SMA + sample stdev.

    Returns ``(bb_mid, bb_upper, bb_lower)``. ``bb_upper``/``bb_lower``
    are NaN for the first ``period - 1`` bars.
    """
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    return mid, mid + k * std, mid - k * std


# ---------------------------------------------------------------------------
# Annotated frame — indicators + entry signals.
# ---------------------------------------------------------------------------

def annotate(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Return ``df`` with indicator columns + boolean ``long_entry`` /
    ``short_entry`` series. Signals are pre-exit; the backtest loop decides
    whether to act on them (sizing/gross-cap + cooldown)."""
    ind = cfg["indicators"]
    bb_p = ind["bb_period"]
    bb_k = ind["bb_k"]
    rsi_p = ind["rsi_period"]
    rsi_os = ind["rsi_oversold"]
    rsi_ob = ind["rsi_overbought"]
    atr_p = ind["atr_period"]
    vol_ma_p = ind["volume_ma_period"]
    vol_min = ind["volume_ratio_min"]

    out = df.copy()
    out["bb_mid"], out["bb_upper"], out["bb_lower"] = bb_bands(out["close"], bb_p, bb_k)
    out["rsi"] = wilder_rsi(out["close"], rsi_p)
    out["atr"] = wilder_atr(out, atr_p)
    out["vol_ma"] = out["volume"].rolling(vol_ma_p, min_periods=vol_ma_p).mean()

    have_indicators = (
        out["bb_mid"].notna()
        & out["bb_upper"].notna()
        & out["bb_lower"].notna()
        & out["rsi"].notna()
        & out["atr"].notna()
        & out["vol_ma"].notna()
    )
    vol_ok = out["volume"] >= vol_min * out["vol_ma"]

    out["long_entry"] = (
        (out["close"] < out["bb_lower"])
        & (out["rsi"] < rsi_os)
        & vol_ok
        & have_indicators
    )
    out["short_entry"] = (
        (out["close"] > out["bb_upper"])
        & (out["rsi"] > rsi_ob)
        & vol_ok
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
    """Bar-by-bar reversion backtest.

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
    bb_mid = df["bb_mid"].to_numpy()
    bb_upper = df["bb_upper"].to_numpy()
    bb_lower = df["bb_lower"].to_numpy()
    rsi = df["rsi"].to_numpy()
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
    cooldown_until = -1  # bar index after which entries may fire again
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
            # 1. RSI midpoint
            if not math.isnan(rsi[i]) and rsi[i] >= exit_cfg["exit_rsi_mid"]:
                exit_now, reason = True, f"rsi_cross_mid>={exit_cfg['exit_rsi_mid']}"
            # 2. Mean reversion to mid
            elif not math.isnan(bb_mid[i]) and price >= float(bb_mid[i]):
                exit_now, reason = True, "close>=bb_mid"
            # 3. Stop-loss
            elif price < entry_price - exit_cfg["stop_loss_atr_k"] * atr_now:
                exit_now, reason = True, f"stop_loss<entry-{exit_cfg['stop_loss_atr_k']}*ATR"
            # 4. Take-profit
            elif price >= entry_price + exit_cfg["take_profit_atr_k"] * atr_now:
                exit_now, reason = True, f"take_profit>=entry+{exit_cfg['take_profit_atr_k']}*ATR"
            # 5. Time stop
            elif bars_held >= exit_cfg["time_stop_bars"]:
                exit_now, reason = True, f"time_stop>={exit_cfg['time_stop_bars']}bars"
            # 6. Slow-revert cap
            elif bars_held >= exit_cfg["bars_to_mid_max"]:
                exit_now, reason = True, f"slow_revert>={exit_cfg['bars_to_mid_max']}bars"
        else:  # short
            if not math.isnan(rsi[i]) and rsi[i] <= exit_cfg["exit_rsi_mid"]:
                exit_now, reason = True, f"rsi_cross_mid<={exit_cfg['exit_rsi_mid']}"
            elif not math.isnan(bb_mid[i]) and price <= float(bb_mid[i]):
                exit_now, reason = True, "close<=bb_mid"
            elif price > entry_price + exit_cfg["stop_loss_atr_k"] * atr_now:
                exit_now, reason = True, f"stop_loss>entry+{exit_cfg['stop_loss_atr_k']}*ATR"
            elif price <= entry_price - exit_cfg["take_profit_atr_k"] * atr_now:
                exit_now, reason = True, f"take_profit<=entry-{exit_cfg['take_profit_atr_k']}*ATR"
            elif bars_held >= exit_cfg["time_stop_bars"]:
                exit_now, reason = True, f"time_stop>={exit_cfg['time_stop_bars']}bars"
            elif bars_held >= exit_cfg["bars_to_mid_max"]:
                exit_now, reason = True, f"slow_revert>={exit_cfg['bars_to_mid_max']}bars"

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

def _summarize(
    symbol: str,
    trades: List[Trade],
    equity: pd.Series,
    dates: pd.DatetimeIndex,
    cfg: dict,
) -> BacktestResult:
    starting = cfg["starting_capital_usd"]
    n = len(trades)
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
    # For 1m bars we treat the period as a crypto-perp which trades 24/7/365 —
    # use minute-level returns but annualize via the bars-per-year constant.
    bars_per_year = 60 * 24 * 365
    bar_ret = reindexed.pct_change().fillna(0.0)
    # Use population std (ddof=0) so a single losing bar does not produce NaN.
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
