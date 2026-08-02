"""Hedged grid strategy v1 — ER-gated rolling-range grid, delta-hedged with perp.

Mechanics (per symbol, 1h bars):

1. Grid: the trailing ``range_days`` window's high/low defines [L, U], split
   into ``n_levels`` slots. Target spot inventory is the number of slots
   below the current price: fully invested at the range bottom, flat at the
   top. Buying low slots and selling high slots harvests oscillation.
2. ER gate: grid actions are only allowed while ER(er_period) <
   ``er_threshold``. ER is Kaufman's efficiency ratio,
   ER(n) = |P_t − P_{t−n}| / Σ|P_i − P_{i−1}| — low = choppy (grid friendly),
   high = trending (grid toxic). Reference: Kaufman (1995), "Smarter
   Trading: Improving Performance in Changing Markets".
3. Hedge: a short perp position mirrors spot inventory and is rebalanced
   only when net delta drifts beyond ``hedge_band`` of FULL grid capacity
   (= one slot at the default 10 levels × 10% band). The lag lets grid
   round-trip profits survive instead of being cancelled trade-by-trade,
   while residual delta stays bounded by the band. The short leg collects
   funding every 8h whenever the funding rate is positive.

Inventory hedging of a market-making/grid book per Guéant, Lehalle &
Fernandez-Tapia (2013), "Dealing with the inventory risk".

The core is a pure state machine: :func:`hedged_grid_step` maps a frozen
:class:`GridState` plus bar inputs to a new state. No I/O outside
:func:`load_symbol_data`.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = Path(__file__).parent / "config.json"

BARS_PER_DAY = 24


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SymbolConfig:
    """Per-symbol parameters (immutable)."""
    symbol: str
    er_threshold: float = 0.3
    er_period: int = 24
    n_levels: int = 10
    range_days: int = 30
    hedge_band: float = 0.10
    spot_fee_bp: float = 10.0
    perp_fee_bp: float = 5.0
    capital_fraction: float = 1.0

    @property
    def range_bars(self) -> int:
        return self.range_days * BARS_PER_DAY


@dataclass(frozen=True)
class GridConfig:
    """Whole-strategy config: one SymbolConfig per symbol + shared capital."""
    symbols: tuple[SymbolConfig, ...]
    initial_capital: float = 10_000.0

    @classmethod
    def from_json(cls, path: Path = CONFIG_PATH) -> "GridConfig":
        raw = json.loads(path.read_text())
        syms = tuple(
            SymbolConfig(
                symbol=s["symbol"],
                er_threshold=s["er_threshold"],
                er_period=s.get("er_period", 24),
                n_levels=s["n_levels"],
                range_days=s["range_days"],
                hedge_band=s["hedge_band"],
                spot_fee_bp=s["spot_fee_bp"],
                perp_fee_bp=s["perp_fee_bp"],
                capital_fraction=s.get("capital_fraction", 1.0),
            )
            for s in raw["symbols"]
        )
        return cls(symbols=syms,
                   initial_capital=raw.get("initial_capital", 10_000.0))


# ---------------------------------------------------------------------------
# State (immutable) + pure step function
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GridState:
    """Position/ledger state. All quantities in base or quote units.

    ``spot_basis`` / ``hedge_basis`` are Σ dq·price over all fills, so
    unrealized PnL at price P is qty·P − basis for each leg.
    """
    spot_qty: float = 0.0
    spot_basis: float = 0.0
    hedge_qty: float = 0.0        # ≤ 0 (short perp)
    hedge_basis: float = 0.0
    fees_paid: float = 0.0
    funding_received: float = 0.0


def equity(state: GridState, price: float, initial_capital: float) -> float:
    """Mark-to-market equity: capital + spot PnL + perp PnL − fees + funding."""
    spot_pnl = state.spot_qty * price - state.spot_basis
    perp_pnl = state.hedge_qty * price - state.hedge_basis
    return (initial_capital + spot_pnl + perp_pnl
            - state.fees_paid + state.funding_received)


def net_delta(state: GridState) -> float:
    """Net base-asset exposure (spot + perp). 0 = perfectly hedged."""
    return state.spot_qty + state.hedge_qty


def grid_target_slots(price: float, low: float, high: float,
                      n_levels: int) -> int:
    """Slots to hold at ``price`` within range [low, high].

    Full inventory (n_levels) at/below the range bottom, 0 at/above the top.
    """
    if not (math.isfinite(low) and math.isfinite(high)) or high <= low:
        return 0
    frac = (high - price) / (high - low)
    return int(min(max(math.floor(frac * n_levels), 0), n_levels))


def hedged_grid_step(
    state: GridState,
    *,
    price: float,
    spot_target_qty: float,
    grid_active: bool,
    hedge_tol_qty: float,
    spot_fee_bp: float,
    perp_fee_bp: float,
    funding_rate: float = 0.0,
) -> GridState:
    """Advance the state machine by one bar (pure).

    Order: (1) settle funding on the current hedge, (2) move spot inventory
    toward the grid target if the ER gate is open, (3) rebalance the perp
    hedge toward −spot only when net delta drifts beyond ``hedge_tol_qty``.

    The tolerance is an absolute base-asset quantity (caller sets it to
    ``hedge_band`` × full grid capacity). Measuring the band against full
    capacity — not the current book — is what decouples hedge frequency from
    grid frequency: the hedge may lag the grid by up to the band, so grid
    round-trip profits are not instantly cancelled by an offsetting perp
    trade, while residual delta stays bounded by the band.
    """
    s = state

    # 1. Funding: short perp receives when rate > 0 (hedge_qty ≤ 0).
    funding = -s.hedge_qty * price * funding_rate
    if funding != 0.0:
        s = replace(s, funding_received=s.funding_received + funding)

    # 2. Grid action (gated).
    if grid_active:
        dq = spot_target_qty - s.spot_qty
        if dq != 0.0:
            s = replace(
                s,
                spot_qty=spot_target_qty,
                spot_basis=s.spot_basis + dq * price,
                fees_paid=s.fees_paid + abs(dq) * price * spot_fee_bp / 1e4,
            )

    # 3. Hedge rebalance: target = −spot, trade only outside the band.
    target_hedge = -s.spot_qty
    drift = abs(s.hedge_qty - target_hedge)
    if drift > hedge_tol_qty:
        dq = target_hedge - s.hedge_qty
        s = replace(
            s,
            hedge_qty=target_hedge,
            hedge_basis=s.hedge_basis + dq * price,
            fees_paid=s.fees_paid + abs(dq) * price * perp_fee_bp / 1e4,
        )
    return s


# ---------------------------------------------------------------------------
# Indicators (vectorized, no lookahead)
# ---------------------------------------------------------------------------

def efficiency_ratio(close: pd.Series, period: int) -> pd.Series:
    """Kaufman efficiency ratio ER(n) = |ΔP over n| / Σ|ΔP| over n bars.

    ER → 1 in a clean trend, → 0 in pure chop. NaN where undefined
    (warmup or zero-volatility window).
    """
    direction = (close - close.shift(period)).abs()
    volatility = close.diff().abs().rolling(period).sum()
    return direction / volatility.replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_symbol_data(symbol: str, root: Path = ROOT
                     ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load (1h spot bars, funding events) for one symbol.

    Bars are indexed by UTC open time with float high/low/close; funding is
    a sorted frame of (ts, fundingRate).
    """
    spot = pd.read_parquet(root / "data" / "spot" / f"{symbol}_1h.parquet")
    bars = pd.DataFrame({
        "high": spot["high"].astype(float).to_numpy(),
        "low": spot["low"].astype(float).to_numpy(),
        "close": spot["close"].astype(float).to_numpy(),
    }, index=pd.to_datetime(spot["open_time"], unit="ms", utc=True))
    bars = bars.sort_index()

    fund = pd.read_parquet(root / "data" / "funding" / f"{symbol}.parquet")
    fund = fund[["ts", "fundingRate"]].copy()
    fund["ts"] = pd.to_datetime(fund["ts"], utc=True).dt.as_unit("ns")
    fund = fund.sort_values("ts").reset_index(drop=True)
    return bars, fund


# ---------------------------------------------------------------------------
# Backtest loop (thin wrapper around the pure step)
# ---------------------------------------------------------------------------

def run_symbol(bars: pd.DataFrame, funding: pd.DataFrame,
               cfg: SymbolConfig, initial_capital: float) -> dict:
    """Run one symbol → dict with equity curve, state trace, and metrics."""
    close = bars["close"]
    n = len(bars)

    # Rolling range from PRIOR bars only (shift(1): no lookahead).
    # Grid range from CLOSE prices (matching the validated prototype: close
    # ranges are tighter than high/low ranges). min_periods=2 so the range
    # becomes valid after just 2 bars — the prototype forms a grid as soon
    # as any spread exists; the default (full window) would leave the book
    # UNHEDGED for the first 30 days (this exact bug cost -11.46% on the
    # 2021-11-26 crash before the fix). shift(1): PRIOR bars only.
    roll_high = bars["close"].rolling(cfg.range_bars, min_periods=2).max().shift(1)
    roll_low = bars["close"].rolling(cfg.range_bars, min_periods=2).min().shift(1)
    er = efficiency_ratio(close, cfg.er_period)

    # Map funding events onto bar indices (event known at its timestamp).
    fund_rate = np.zeros(n)
    if len(funding):
        bar_ts = bars.index
        pos = bar_ts.searchsorted(funding["ts"].to_numpy(), side="right")
        for p, rate in zip(pos, funding["fundingRate"].to_numpy()):
            if p < n and math.isfinite(rate):
                fund_rate[p] += rate

    spot_fee = cfg.spot_fee_bp / 1e4
    perp_fee = cfg.perp_fee_bp / 1e4

    cash = initial_capital * 0.5
    inv_qty = (initial_capital * 0.5) / float(close.iloc[0])
    hedge_qty = 0.0          # short perp, positive quantity
    perp_entry_val = 0.0     # Σ short entry notional
    funding_income = 0.0
    fees_paid = 0.0

    eq = np.full(n, np.nan)
    spot_q = np.zeros(n)
    hedge_q = np.zeros(n)
    deltas = np.zeros(n)
    n_grid_trades = 0
    n_rebalances = 0

    c = close.values.astype(float)
    er_v = er.values
    hi_s = roll_high.values
    lo_s = roll_low.values

    def total_equity(px: float) -> float:
        # Fees are embedded in cash/inventory accounting (spot fees reduce
        # received qty/proceeds; perp fees debited from cash on rebalance) —
        # fees_paid is a REPORTING counter only, do NOT subtract it here.
        perp_pnl = perp_entry_val - hedge_qty * px
        return cash + inv_qty * px + perp_pnl + funding_income

    # Static-range-with-breakout-reset grid (validated prototype mechanics):
    # the range is FROZEN when established and only re-anchored on ±1%
    # breakout. Re-anchoring every bar makes slot targets drift on flat
    # prices — phantom trades whose fees destroyed v1's naive port.
    i = 0
    while i < n - 1:
        if not (math.isfinite(hi_s[i]) and math.isfinite(lo_s[i])) or hi_s[i] <= lo_s[i] * 1.01:
            i += 1
            eq[i] = total_equity(c[i])
            spot_q[i] = inv_qty
            hedge_q[i] = -hedge_qty
            deltas[i] = inv_qty - hedge_qty
            continue
        lo, hi = float(lo_s[i]), float(hi_s[i])
        levels = np.linspace(lo, hi, cfg.n_levels + 1)
        ref = c[i]

        j = i
        while j < n - 1:
            j += 1
            p0, p1 = c[j - 1], c[j]
            er_ok = math.isfinite(er_v[j]) and er_v[j] < cfg.er_threshold
            if er_ok:
                for lv in levels:
                    # cross DOWN through a level below ref → buy a slot
                    if p0 > lv >= p1 and lv < ref:
                        spend = min(initial_capital * 0.5 / cfg.n_levels,
                                    cash * 0.95)
                        if spend > 1e-9:
                            inv_qty += spend / lv * (1.0 - spot_fee)
                            cash -= spend
                            fees_paid += spend * spot_fee
                            n_grid_trades += 1
                    # cross UP through a level above ref → sell 1/n of inv
                    if p0 < lv <= p1 and lv > ref:
                        qty = inv_qty / cfg.n_levels
                        if qty * lv > 1e-9:
                            cash += qty * lv * (1.0 - spot_fee)
                            inv_qty -= qty
                            fees_paid += qty * lv * spot_fee
                            n_grid_trades += 1
            # hedge rebalance: only when delta drift exceeds the band — the
            # lag stops the hedge cancelling each grid round-trip instantly.
            avg_inv = initial_capital * 0.5 / p1
            if avg_inv > 0 and abs(inv_qty - hedge_qty) > cfg.hedge_band * avg_inv:
                trade_qty = inv_qty - hedge_qty
                fee = abs(trade_qty) * p1 * perp_fee
                fees_paid += fee
                cash -= fee
                if hedge_qty > 0 and trade_qty < 0:
                    close_qty = -trade_qty
                    entry_avg = perp_entry_val / hedge_qty
                    cash += (entry_avg - p1) * close_qty
                    perp_entry_val -= entry_avg * close_qty
                    hedge_qty -= close_qty
                elif trade_qty > 0:
                    perp_entry_val += trade_qty * p1
                    hedge_qty += trade_qty
                n_rebalances += 1
            # funding on hedge notional at event bars
            if fund_rate[j] != 0.0:
                funding_income += hedge_qty * p1 * fund_rate[j]
            eq[j] = total_equity(p1)
            if p1 < lo * 0.99 or p1 > hi * 1.01:
                break
        i = j
        eq[i] = total_equity(c[i])
        spot_q[i] = inv_qty
        hedge_q[i] = -hedge_qty
        deltas[i] = inv_qty - hedge_qty

    for k in range(i + 1, n):
        eq[k] = total_equity(c[k])
        spot_q[k] = inv_qty
        hedge_q[k] = -hedge_qty
        deltas[k] = inv_qty - hedge_qty

    curve = pd.Series(eq, index=bars.index, name=cfg.symbol)
    trace = pd.DataFrame({
        "spot_qty": spot_q,
        "hedge_qty": hedge_q,
        "net_delta": deltas,
    }, index=bars.index)
    metrics = compute_metrics(curve, initial_capital)
    metrics["n_grid_trades"] = n_grid_trades
    metrics["n_rebalances"] = n_rebalances
    metrics["funding_received"] = funding_income
    metrics["fees_paid"] = fees_paid
    return {"equity": curve, "trace": trace, "metrics": metrics}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(curve: pd.Series, initial_capital: float) -> dict:
    """Annualized return / max drawdown / Calmar from an equity curve."""
    curve = curve.dropna()
    days = max((curve.index[-1] - curve.index[0]).total_seconds() / 86400.0,
               1e-9)
    total_ret = curve.iloc[-1] / initial_capital - 1.0
    ann_ret = total_ret / days * 365.0

    dd = curve / curve.cummax() - 1.0
    max_dd = float(dd.min())
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else float("inf")

    return {
        "days": round(days, 1),
        "total_return": total_ret,
        "annualized_return": ann_ret,
        "max_drawdown_pct": max_dd,
        "calmar": calmar,
    }


# ---------------------------------------------------------------------------
# Combo runner
# ---------------------------------------------------------------------------

def run_backtest(cfg: GridConfig | None = None, root: Path = ROOT) -> dict:
    """Full backtest: per-symbol runs → equal-weight combo equity."""
    cfg = cfg or GridConfig.from_json()

    per_symbol: dict[str, dict] = {}
    curves: dict[str, pd.Series] = {}

    for sym_cfg in cfg.symbols:
        bars, funding = load_symbol_data(sym_cfg.symbol, root)
        res = run_symbol(bars, funding, sym_cfg, cfg.initial_capital)
        per_symbol[sym_cfg.symbol] = res["metrics"]
        # Normalize to returns so equal-weight combo is scale-free.
        curves[sym_cfg.symbol] = res["equity"] / cfg.initial_capital - 1.0

    eq_df = pd.DataFrame(curves).ffill().dropna()
    combo_ret = eq_df.mean(axis=1)
    combo_curve = (1.0 + combo_ret) * cfg.initial_capital
    combo_metrics = compute_metrics(combo_curve, cfg.initial_capital)

    return {
        "config": {
            "symbols": [s.symbol for s in cfg.symbols],
            "initial_capital": cfg.initial_capital,
        },
        "per_symbol": per_symbol,
        "combo": combo_metrics,
    }
