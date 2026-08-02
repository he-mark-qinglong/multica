"""Cash-and-carry combo strategy — delta-neutral funding harvest.

Structure (per symbol):
    LONG spot + SHORT perp, equal notional → delta = 0
    Collect funding every 8h (positive funding = shorts receive).
    PnL = Σ funding + basis_change − entry/exit costs.

Regime filter (SOLUSDT by default):
    When trailing ~30d funding sum < 0, close the position (stop paying
    funding); re-enter when it turns positive again.

This module is data-in/data-out: no I/O beyond what run_backtest.py does.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CarryConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    leverage: float = 2.0
    perp_fee_bp: float = 5.0          # per side
    spot_fee_bp: float = 10.0         # per side
    filter_symbols: tuple[str, ...] = ("SOLUSDT",)
    filter_window_events: int = 90    # ≈30 days of 8h events
    match_tolerance_min: int = 30

    @property
    def entry_cost_bp(self) -> float:
        return (self.perp_fee_bp + self.spot_fee_bp) * self.leverage

    @classmethod
    def from_json(cls, path: Path = CONFIG_PATH) -> "CarryConfig":
        cfg = json.loads(path.read_text())
        return cls(
            symbols=tuple(cfg["symbols"]),
            leverage=cfg["leverage"],
            perp_fee_bp=cfg["cost_model"]["perp_fee_bp_per_side"],
            spot_fee_bp=cfg["cost_model"]["spot_fee_bp_per_side"],
            filter_symbols=tuple(cfg["regime_filter"]["enabled_symbols"]),
            filter_window_events=cfg["regime_filter"]["trailing_window_events"],
            match_tolerance_min=cfg["data"]["match_tolerance_minutes"],
        )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_symbol_data(symbol: str, cfg: CarryConfig, root: Path = ROOT) -> pd.DataFrame:
    """Funding events joined to spot close at the same timestamp."""
    fund = pd.read_parquet(root / "data" / "funding" / f"{symbol}.parquet")
    fund["ts"] = pd.to_datetime(fund["ts"], utc=True).dt.as_unit("ns")
    fund = fund.sort_values("ts").reset_index(drop=True)

    spot = pd.read_parquet(root / "data" / "spot" / f"{symbol}_1h.parquet")
    spot["ts"] = pd.to_datetime(spot["open_time"], unit="ms", utc=True).dt.as_unit("ns")

    m = pd.merge_asof(
        fund, spot[["ts", "close"]].sort_values("ts"),
        on="ts", tolerance=pd.Timedelta(minutes=cfg.match_tolerance_min),
    ).dropna(subset=["markPrice", "close"]).reset_index(drop=True)

    m["fund_bp"] = m["fundingRate"] * 10_000.0
    m["basis_bp"] = (m["markPrice"] - m["close"]) / m["close"] * 10_000.0
    return m[["ts", "fund_bp", "basis_bp"]]


# ---------------------------------------------------------------------------
# Core equity computation (pure, testable)
# ---------------------------------------------------------------------------

def symbol_equity_curve(
    fund_bp: pd.Series,
    basis_bp: pd.Series,
    leverage: float,
    entry_exit_cost_bp: float,
    use_filter: bool = False,
    filter_window: int = 90,
) -> pd.Series:
    """Equity curve (cumulative bp) for one symbol's carry position.

    equity_t = lev · [ Σ funding_active + (basis_in − basis_t)·active ] − costs

    When the regime filter is enabled, the position is flat whenever the
    trailing-window funding sum is ≤ 0: no income, no basis exposure.
    """
    if use_filter:
        trailing = fund_bp.rolling(filter_window, min_periods=1).sum()
        active = (trailing > 0).astype(float)
    else:
        active = pd.Series(1.0, index=fund_bp.index)

    income = (fund_bp * active).cumsum()
    basis_pnl = (basis_bp.iloc[0] - basis_bp) * active
    eq = leverage * (income + basis_pnl) - entry_exit_cost_bp
    return eq


def compute_metrics(equity: pd.Series, ts: pd.Series) -> dict:
    """Return + drawdown metrics (relaxed evaluation: return & DD only)."""
    days = max(1, (ts.iloc[-1] - ts.iloc[0]).days)
    total_ret_bp = float(equity.iloc[-1])
    ann_ret = total_ret_bp / days * 365 / 10_000.0

    peak = equity.cummax()
    dd_series = equity - peak
    max_dd_bp = float(dd_series.min())
    max_dd_pct = max_dd_bp / 10_000.0

    calmar = ann_ret / abs(max_dd_pct) if max_dd_pct < 0 else float("inf")

    return {
        "days": days,
        "total_return_bp": total_ret_bp,
        "total_return_pct": total_ret_bp / 100.0,
        "annualized_return": ann_ret,
        "max_drawdown_bp": max_dd_bp,
        "max_drawdown_pct": max_dd_pct,
        "calmar": calmar,
    }


def run_symbol(df: pd.DataFrame, cfg: CarryConfig) -> tuple[pd.Series, dict]:
    """Run one symbol → (equity curve, metrics)."""
    use_filter = False  # set per symbol by caller
    eq = symbol_equity_curve(
        df["fund_bp"], df["basis_bp"],
        leverage=cfg.leverage,
        entry_exit_cost_bp=cfg.entry_cost_bp * 2,  # entry + exit
        use_filter=use_filter,
    )
    return eq, compute_metrics(eq, df["ts"])


# ---------------------------------------------------------------------------
# Combo runner
# ---------------------------------------------------------------------------

def run_backtest(cfg: CarryConfig | None = None, root: Path = ROOT) -> dict:
    """Full combo backtest: per-symbol equity → equal-weight combo."""
    cfg = cfg or CarryConfig.from_json()

    per_symbol: dict[str, dict] = {}
    equities: dict[str, pd.Series] = {}
    ts_ref: pd.Series | None = None

    for sym in cfg.symbols:
        df = load_symbol_data(sym, cfg, root)
        use_filter = sym in cfg.filter_symbols
        eq = symbol_equity_curve(
            df["fund_bp"], df["basis_bp"],
            leverage=cfg.leverage,
            entry_exit_cost_bp=cfg.entry_cost_bp * 2,
            use_filter=use_filter,
            filter_window=cfg.filter_window_events,
        )
        equities[sym] = eq
        per_symbol[sym] = compute_metrics(eq, df["ts"])
        per_symbol[sym]["filter_applied"] = use_filter
        per_symbol[sym]["n_events"] = len(df)
        if ts_ref is None or len(df["ts"]) > len(ts_ref):
            ts_ref = df["ts"]

    # Equal-weight combo (aligned on common index via outer join + ffill)
    eq_df = pd.DataFrame(equities).ffill().dropna()
    combo_eq = eq_df.mean(axis=1)
    combo_ts = eq_df.index.to_series()
    # combo_ts here is integer index; rebuild from first symbol's ts
    first_sym = cfg.symbols[0]
    df0 = load_symbol_data(first_sym, cfg, root)
    combo_metrics = compute_metrics(combo_eq.reset_index(drop=True), df0["ts"].iloc[:len(combo_eq)].reset_index(drop=True))

    return {
        "config": {
            "symbols": list(cfg.symbols),
            "leverage": cfg.leverage,
            "filter_symbols": list(cfg.filter_symbols),
        },
        "per_symbol": per_symbol,
        "combo": combo_metrics,
        "combo_equity_bp": combo_eq.tolist(),
    }
