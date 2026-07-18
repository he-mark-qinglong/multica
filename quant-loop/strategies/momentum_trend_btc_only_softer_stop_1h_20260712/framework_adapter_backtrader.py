"""
Backtrader adapter for momentum_trend_btc_only_softer_stop_1h_20260712 (V12).

Implements the exact SPEC.md §3-§7 logic:
- Long: ema50_4h_slope > 0 AND rsi14 cross 50 up AND adx14 > 20
- Short: ema50_4h_slope < 0 AND rsi14 cross 50 down AND adx14 > 20
- Exit (priority): 4h reversal, RSI cross back, or -3.5 ATR entry-anchored stop
- Sizing: 1% risk per 1-ATR move, capped at 5% notional, 5% gross exposure
- Costs: 1bp fee + 1bp slippage per side (= 2 bp/side round-trip cost)

Reads parquet from the strategy's data/ dir. Writes results.json.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Use the framework from cache (do not modify!)
sys.path.insert(0, "/tmp/framework-cache/backtrader-b853d7c9")
import backtrader as bt

STRATEGY_DIR = Path("/home/smark/multica/quant-loop/strategies/momentum_trend_btc_only_softer_stop_1h_20260712")
DATA_1H = STRATEGY_DIR / "data" / "BTCUSDT__1h.parquet"
DATA_4H = STRATEGY_DIR / "data" / "BTCUSDT__4h.parquet"
OUT = Path("/tmp/fwvalidate-2026-07-13-0437/results.json")

# Cost model per SPEC §8: fees 1bp + slippage 1bp per side
FEE_BPS = 0.0001   # 1 bp
SLIP_BPS = 0.0001  # 1 bp
COST_PER_SIDE = FEE_BPS + SLIP_BPS  # 2 bp/side

# Sizing
RISK_PER_SIGNAL = 0.01      # 1% of equity per 1-ATR move
MAX_NOTIONAL_PCT = 0.05     # 5% NAV cap per signal
MAX_GROSS_PCT = 0.05        # 5% NAV gross exposure cap

INITIAL_CASH = 100_000.0


# ---------------------------------------------------------------------------
# Indicator helpers (Wilder smoothed), matching SPEC §3
# ---------------------------------------------------------------------------
def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr


def wilder_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return adx


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------------
# Pre-compute the 4h filter onto 1h grid (forward-fill)
# ---------------------------------------------------------------------------
def prepare_signals(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> pd.DataFrame:
    out = df_1h.copy()
    out["atr14"] = wilder_atr(out, 14)
    out["adx14"] = wilder_adx(out, 14)
    out["rsi14"] = wilder_rsi(out["close"], 14)

    ema50_4h = ema(df_4h["close"], 50)
    slope = ema50_4h.diff() / ema50_4h.shift(1)
    slope = slope.shift(1)  # shift(1) per SPEC §3 look-ahead discipline
    ema4h_ff = ema50_4h.shift(1).reindex(out.index, method="ffill")
    slope_ff = slope.reindex(out.index, method="ffill")
    out["ema50_4h"] = ema4h_ff
    out["ema50_4h_slope"] = slope_ff

    rsi = out["rsi14"]
    out["rsi_cross_up"] = (rsi.shift(1) < 50) & (rsi >= 50)
    out["rsi_cross_dn"] = (rsi.shift(1) > 50) & (rsi <= 50)

    out["long_entry"] = (
        (out["ema50_4h_slope"] > 0)
        & out["rsi_cross_up"]
        & (out["adx14"] > 20)
    )
    out["short_entry"] = (
        (out["ema50_4h_slope"] < 0)
        & out["rsi_cross_dn"]
        & (out["adx14"] > 20)
    )
    out["entry_signal"] = out["long_entry"] | out["short_entry"]

    out["exit_4h_reversal_long"] = out["ema50_4h_slope"] < 0
    out["exit_4h_reversal_short"] = out["ema50_4h_slope"] > 0
    out["exit_rsi_cross_back_long"] = out["rsi_cross_dn"]
    out["exit_rsi_cross_back_short"] = out["rsi_cross_up"]
    return out


# ---------------------------------------------------------------------------
# Backtrader data feed from a prepared frame
# ---------------------------------------------------------------------------
class SignalData(bt.feeds.PandasData):
    """1h data feed with extra lines: atr14, rsi14, adx14, ema50_4h_slope,
    long_entry, short_entry, exit_4h_reversal_long, exit_4h_reversal_short,
    exit_rsi_cross_back_long, exit_rsi_cross_back_short."""
    lines = (
        "atr14", "rsi14", "adx14",
        "ema50_4h_slope",
        "long_entry", "short_entry",
        "exit_4h_rev_long", "exit_4h_rev_short",
        "exit_rsi_long", "exit_rsi_short",
    )
    params = (
        ("datetime", None),
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
        ("volume", "volume"),
        ("openinterest", -1),
        ("atr14", "atr14"),
        ("rsi14", "rsi14"),
        ("adx14", "adx14"),
        ("ema50_4h_slope", "ema50_4h_slope"),
        ("long_entry", "long_entry"),
        ("short_entry", "short_entry"),
        ("exit_4h_rev_long", "exit_4h_reversal_long"),
        ("exit_4h_rev_short", "exit_4h_reversal_short"),
        ("exit_rsi_long", "exit_rsi_cross_back_long"),
        ("exit_rsi_short", "exit_rsi_cross_back_short"),
    )


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class V12Strategy(bt.Strategy):
    """Bypass broker buy/sell — track equity in-strategy exactly like
    in-house does (equity += pnl_abs at trade close). Backtrader's broker
    mark-to-market between trades is a difference vs the in-house
    flat-between-trades approach; this bypass keeps the comparison apples
    to apples.
    """
    params = dict(
        risk_per_signal=RISK_PER_SIGNAL,
        max_notional_pct=MAX_NOTIONAL_PCT,
        max_gross_pct=MAX_GROSS_PCT,
        atr_stop_mult=3.5,  # V12 softer stop
        cost_per_side=COST_PER_SIDE,
    )

    def __init__(self):
        self.dataclose = self.datas[0].close
        self.atr = self.datas[0].atr14
        self.rsi = self.datas[0].rsi14
        self.adx = self.datas[0].adx14
        self.slope = self.datas[0].ema50_4h_slope
        self.long_entry = self.datas[0].long_entry
        self.short_entry = self.datas[0].short_entry
        self.exit_4h_rev_long = self.datas[0].exit_4h_rev_long
        self.exit_4h_rev_short = self.datas[0].exit_4h_rev_short
        self.exit_rsi_long = self.datas[0].exit_rsi_long
        self.exit_rsi_short = self.datas[0].exit_rsi_short

        self.entry_price = None
        self.entry_atr = None
        self.entry_dir = None
        self.entry_size = 0.0
        self.bar_in_trade = 0

        self.trades_log: list[dict] = []
        self.equity_curve: list[tuple] = []

    def next(self):
        price = float(self.dataclose[0])
        atr_val = float(self.atr[0]) if not math.isnan(float(self.atr[0])) else None

        # Equity = broker cash (no mark-to-market since we don't hold a
        # broker-tracked position).
        equity = float(self.broker.getcash())

        dt = bt.num2date(self.datas[0].datetime[0]).isoformat()
        self.equity_curve.append((dt, equity))

        pos_size = self.entry_size if self.entry_dir is not None else 0.0
        in_long = self.entry_dir == +1
        in_short = self.entry_dir == -1
        in_trade = in_long or in_short

        if in_trade:
            self.bar_in_trade += 1
            exit_reason = None
            exit_px = None

            if in_long:
                if bool(self.exit_4h_rev_long[0]):
                    exit_reason = "4h_reversal"
                elif bool(self.exit_rsi_long[0]):
                    exit_reason = "rsi_cross_back"
                elif atr_val is not None and self.entry_atr is not None and price < self.entry_price - self.p.atr_stop_mult * self.entry_atr:
                    exit_reason = "atr_stop"
                if exit_reason is None:
                    return
                # entry_price includes cost: price*(1+c); exit_net = price*(1-c)
                # pnl_pct = exit_net / entry - 1
                exit_px = price * (1.0 - self.p.cost_per_side)
                pnl_pct = exit_px / self.entry_price - 1.0
                notional = self.entry_size * self.entry_price / (1.0 + self.p.cost_per_side)
                # notional is the price-at-entry gross; cost was already baked
                # into entry_price, so use size * entry_price / (1+c) to get
                # the gross.
                pnl_usd = pnl_pct * notional
                self.broker.add_cash(pnl_usd)
                self._record_trade("long", exit_reason, self.entry_price, exit_px)
                self._reset_state()
                return

            else:  # short
                if bool(self.exit_4h_rev_short[0]):
                    exit_reason = "4h_reversal"
                elif bool(self.exit_rsi_short[0]):
                    exit_reason = "rsi_cross_back"
                elif atr_val is not None and self.entry_atr is not None and price > self.entry_price + self.p.atr_stop_mult * self.entry_atr:
                    exit_reason = "atr_stop"
                if exit_reason is None:
                    return
                # short: pnl_pct = entry / exit_net - 1; entry = price*(1+c),
                # exit_net = price*(1-c).
                exit_px = price * (1.0 - self.p.cost_per_side)
                pnl_pct = self.entry_price / exit_px - 1.0
                notional = self.entry_size * self.entry_price / (1.0 + self.p.cost_per_side)
                pnl_usd = pnl_pct * notional
                self.broker.add_cash(pnl_usd)
                self._record_trade("short", exit_reason, self.entry_price, exit_px)
                self._reset_state()
                return

        # ENTRY
        if in_trade:
            return

        long_sig = bool(self.long_entry[0])
        short_sig = bool(self.short_entry[0])

        if long_sig and not short_sig:
            self._enter("long", price, atr_val, equity)
        elif short_sig and not long_sig:
            self._enter("short", price, atr_val, equity)

    def _enter(self, side: str, price: float, atr_val: float | None, equity: float):
        if atr_val is None or atr_val <= 0:
            return
        risk_quote = self.p.risk_per_signal * equity
        notional_risk = risk_quote / max(atr_val / price, 1e-9)
        notional_cap = self.p.max_notional_pct * equity
        notional_gross_cap = self.p.max_gross_pct * equity
        notional = min(notional_risk, notional_cap, notional_gross_cap)
        size = notional / price  # notional in quote currency = price * size
        if size <= 0:
            return
        # We track positions virtually (no broker buy/sell). Entry cost is
        # already baked into entry_price = price*(1+c), and the notional
        # we "deploy" is computed from that to keep pnl math consistent
        # with in-house (which doesn't separately track cost-paid cash).
        entry_px = price * (1.0 + self.p.cost_per_side)
        if side == "long":
            self.entry_dir = +1
        else:
            self.entry_dir = -1
        self.entry_price = entry_px
        self.entry_atr = atr_val
        self.entry_size = size
        self.bar_in_trade = 0

    def _reset_state(self):
        self.entry_price = None
        self.entry_atr = None
        self.entry_dir = None
        self.entry_size = 0.0
        self.bar_in_trade = 0

    def _record_trade(self, side: str, reason: str, entry_px: float, exit_px: float):
        # Match in-house strategy.py formulas exactly:
        #   long  pnl_pct = exit_net / entry - 1
        #   short pnl_pct = entry / exit_net - 1
        if side == "long":
            pnl_pct = exit_px / entry_px - 1.0
        else:
            pnl_pct = entry_px / exit_px - 1.0
        entry_dt = bt.num2date(self.datas[0].datetime[-self.bar_in_trade]).date().isoformat()
        exit_dt = bt.num2date(self.datas[0].datetime[0]).date().isoformat()
        self.trades_log.append({
            "side": side,
            "reason": reason,
            "entry_px": entry_px,
            "exit_px": exit_px,
            "pnl_pct": pnl_pct,
            "bars_held": self.bar_in_trade,
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
        })


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run() -> dict:
    df1 = pd.read_parquet(DATA_1H)
    df4 = pd.read_parquet(DATA_4H)
    df1 = df1.sort_index()
    df4 = df4.sort_index()
    sig = prepare_signals(df1, df4)
    sig = sig.dropna(subset=["atr14", "ema50_4h_slope", "adx14", "rsi14"])
    # bool → int for backtrader lines
    for c in ["long_entry", "short_entry",
              "exit_4h_reversal_long", "exit_4h_reversal_short",
              "exit_rsi_cross_back_long", "exit_rsi_cross_back_short"]:
        sig[c] = sig[c].astype(int)

    cerebro = bt.Cerebro(stdstats=False)
    feed = SignalData(dataname=sig)
    cerebro.adddata(feed)
    cerebro.addstrategy(V12Strategy)
    cerebro.broker.setcash(INITIAL_CASH)
    # Per SPEC: cost_per_side = 1bp fee + 1bp slippage = 2bp.
    # We apply ALL cost inside the strategy via entry/exit price slip, so
    # disable backtrader's built-in commission to avoid double-counting.
    cerebro.broker.setcommission(commission=0.0)
    cerebro.broker.set_slippage_perc(0.0)
    # Cheat-on-close: market orders fill at current bar's close (matching
    # in-house "fill price: bar.close of the signal/exit bar" convention).
    cerebro.broker.set_coc(True)
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="timereturn", timeframe=bt.TimeFrame.NoTimeFrame)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    results = cerebro.run()
    strat = results[0]

    final_equity = float(cerebro.broker.getvalue())
    total_return = final_equity / INITIAL_CASH - 1.0

    # Compute Sharpe ourselves from the per-bar equity curve (most reliable
    # cross-version path; backtrader's SharpeRatio analyzer is sensitive to
    # timeframe wiring and the value can come back None for sub-day data).
    eq_series = pd.Series(
        [v for _, v in strat.equity_curve],
        index=pd.to_datetime([d for d, _ in strat.equity_curve]),
    )
    bar_ret = eq_series.pct_change().fillna(0.0)
    if bar_ret.std() > 0:
        # 1h bars: 8760 bars/year; per-spec bars_per_year for sharpe
        sharpe_out = float(bar_ret.mean() / bar_ret.std() * math.sqrt(8760.0))
    else:
        sharpe_out = 0.0

    dd = strat.analyzers.drawdown.get_analysis()
    max_dd = float(dd.max.drawdown) / 100.0  # backtrader reports as percent

    ta = strat.analyzers.trades.get_analysis()
    total_closed = int(ta.total.closed) if "total" in ta and "closed" in ta.total else len(strat.trades_log)
    won = int(ta.won.total) if "won" in ta and "total" in ta.won else 0
    if won == 0 and strat.trades_log:
        won = sum(1 for t in strat.trades_log if t["pnl_pct"] > 0)
    win_rate = (won / total_closed) if total_closed > 0 else 0.0

    out = {
        "framework": "backtrader",
        "version": bt.__version__,
        "git_sha": "b853d7c9",
        "strategy": "momentum_trend_btc_only_softer_stop_1h_20260712",
        "metrics": {
            "sharpe": sharpe_out,
            "max_drawdown": -abs(max_dd),
            "total_return": total_return,
            "n_trades": total_closed,
            "win_rate": win_rate,
            "final_equity": final_equity,
        },
        "config": {
            "initial_cash": INITIAL_CASH,
            "fee_bps": FEE_BPS * 1e4,
            "slip_bps": SLIP_BPS * 1e4,
            "atr_stop_mult": 3.5,
            "risk_per_signal_pct": RISK_PER_SIGNAL * 100,
            "max_notional_pct": MAX_NOTIONAL_PCT * 100,
            "max_gross_pct": MAX_GROSS_PCT * 100,
        },
        "internal_trades_log_count": len(strat.trades_log),
        "n_bars": len(sig),
    }
    OUT.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    res = run()
    print(json.dumps(res, indent=2))