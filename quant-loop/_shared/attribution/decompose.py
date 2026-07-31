"""Performance attribution — strategy-level PnL decomposition.

Research #87 (SMA-35757) / T14. Opt-in library, same convention as
``_shared/sizing`` and ``_shared/regime``: no auto-wiring into strategies.

Decomposes a closed-trade ledger into gross signal PnL minus execution
cost, swept over cost scenarios, and classifies the ledger:

  VIABLE_AT_COST   gross_sum > 0 and net_sum > 0 at the scenario
  COST_CAP_KILL    gross_sum > 0 but net_sum <= 0 (cost ate the edge)
  MECHANISM_KILL   gross_sum <= 0 (cost is irrelevant)

Gross return is ALWAYS recomputed from entry/exit prices — the ledger's own
``pnl_pct`` embeds whatever cost convention the strategy author happened to
use (H3: 8bp RT pair; trend_multi: ~0) and is not trustworthy across
strategies.

Sentinels (T11 lesson, round-1 + round-2: validate the measurement before
trusting the numbers): reject exit_ts < entry_ts, non-positive prices,
unknown direction, missing/NaN required columns.

Pure functions: no I/O except ``write_report``. Deterministic output.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

DAYS_PER_YEAR = 365

# Ratified research-wide cost (SMA-34900 / SMA-34913; COST_CONVENTION.md):
# 4bp fee + 7bp pure slippage per side = 22bp round trip per instrument.
RATIFIED = None  # bound after CostSpec is defined

SINGLE_DIRECTIONS = {"long": 1.0, "short": -1.0}
PAIR_DIRECTIONS = {"long_a_short_b": 1.0, "short_a_long_b": -1.0}

_TS_ALIASES = {"entry_date": "entry_ts", "exit_date": "exit_ts"}


@dataclass(frozen=True)
class CostSpec:
    """Per-side bps + fill count. ``fills_per_round_trip`` is 2 for a
    single-instrument round trip (entry+exit), 4 for a pair (2 legs)."""

    fee_bps_per_side: float
    slippage_bps_per_side: float
    fills_per_round_trip: int = 2

    @property
    def cost_frac(self) -> float:
        return self.fills_per_round_trip * (
            self.fee_bps_per_side + self.slippage_bps_per_side
        ) / 10_000.0

    @property
    def bps_per_side(self) -> float:
        return self.fee_bps_per_side + self.slippage_bps_per_side


RATIFIED = CostSpec(fee_bps_per_side=4.0, slippage_bps_per_side=7.0, fills_per_round_trip=2)


class LedgerError(ValueError):
    """Raised when a trade ledger violates the input contract (sentinels)."""


def normalize_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Detect schema, compute gross returns from prices, enforce sentinels.

    Returns a frame with columns:
      kind ('single'|'pair'), label, direction, entry_ts, exit_ts,
      gross_ret, bars_held, exit_reason (may be empty strings).
    Raises LedgerError on any sentinel violation.
    """
    df = df.rename(columns={k: v for k, v in _TS_ALIASES.items() if k in df.columns})
    cols = set(df.columns)

    if {"entry_price_a", "entry_price_b", "exit_price_a", "exit_price_b"} <= cols:
        kind = "pair"
        label_col = "pair" if "pair" in cols else None
        directions = PAIR_DIRECTIONS
    elif {"entry_price", "exit_price"} <= cols:
        kind = "single"
        label_col = "symbol" if "symbol" in cols else None
        directions = SINGLE_DIRECTIONS
    else:
        raise LedgerError(
            f"cannot detect schema: need pair price columns or single price columns, got {sorted(cols)}"
        )

    required = {"direction", "entry_ts", "exit_ts"}
    missing = required - cols
    if missing:
        raise LedgerError(f"missing required columns: {sorted(missing)}")

    out = pd.DataFrame(index=df.index)
    out["kind"] = kind
    out["label"] = df[label_col].astype(str) if label_col else kind
    out["direction"] = df["direction"].astype(str)
    out["entry_ts"] = pd.to_datetime(df["entry_ts"])
    out["exit_ts"] = pd.to_datetime(df["exit_ts"])
    out["bars_held"] = (
        pd.to_numeric(df["bars_held"], errors="coerce") if "bars_held" in cols else np.nan
    )
    reason_col = "exit_reason" if "exit_reason" in cols else ("reason" if "reason" in cols else None)
    out["exit_reason"] = df[reason_col].astype(str) if reason_col else ""

    # --- sentinels (A1) ---
    if out[["entry_ts", "exit_ts"]].isna().any().any():
        raise LedgerError("NaT in entry_ts/exit_ts after parsing")
    if (out["exit_ts"] < out["entry_ts"]).any():
        n = int((out["exit_ts"] < out["entry_ts"]).sum())
        raise LedgerError(f"{n} trades with exit_ts < entry_ts (look-ahead / data corruption)")
    bad_dir = ~out["direction"].isin(directions)
    if bad_dir.any():
        raise LedgerError(
            f"unknown direction values: {sorted(out.loc[bad_dir, 'direction'].unique())}"
        )

    if kind == "pair":
        prices = df[["entry_price_a", "entry_price_b", "exit_price_a", "exit_price_b"]].astype(float)
        if (prices <= 0).any().any() or prices.isna().any().any():
            raise LedgerError("non-positive or NaN price in pair ledger")
        ret_a = prices["exit_price_a"] / prices["entry_price_a"] - 1.0
        ret_b = prices["exit_price_b"] / prices["entry_price_b"] - 1.0
        sign = out["direction"].map(directions)
        out["gross_ret"] = sign * (ret_a - ret_b)
    else:
        prices = df[["entry_price", "exit_price"]].astype(float)
        if (prices <= 0).any().any() or prices.isna().any().any():
            raise LedgerError("non-positive or NaN price in single ledger")
        ret = prices["exit_price"] / prices["entry_price"] - 1.0
        out["gross_ret"] = out["direction"].map(directions) * ret

    return out.reset_index(drop=True)


def _daily_sharpe(rets: pd.Series, exit_ts: pd.Series) -> float:
    """Sharpe of daily-aggregated closed-trade returns (sum by exit day).

    Approximation of the mark-to-market equity curve — documented in SPEC §7.
    """
    daily = rets.groupby(exit_ts.dt.date).sum()
    if len(daily) < 2:
        return 0.0
    sd = float(daily.std(ddof=1))
    if sd < 1e-15:
        return 0.0
    return float(daily.mean() / sd * math.sqrt(DAYS_PER_YEAR))


def _verdict(gross_sum: float, net_sum: float) -> str:
    if gross_sum <= 0:
        return "MECHANISM_KILL"
    if net_sum <= 0:
        return "COST_CAP_KILL"
    return "VIABLE_AT_COST"


def _scenario_stats(trades: pd.DataFrame, name: str, spec: CostSpec) -> Dict:
    gross = trades["gross_ret"]
    n = int(len(gross))
    gross_sum = float(gross.sum())
    cost_sum = n * spec.cost_frac
    net = gross - spec.cost_frac
    net_sum = float(net.sum())

    # A0 accounting identity (construction guarantees it; assert anyway).
    assert abs(net_sum - (gross_sum - cost_sum)) < 1e-12, "A0 identity violated"

    breakeven_bps_side = (
        gross_sum * 10_000.0 / (n * spec.fills_per_round_trip) if n > 0 else 0.0
    )
    return {
        "scenario": name,
        "bps_per_side": spec.bps_per_side,
        "cost_rt_bps_effective": spec.cost_frac * 10_000.0,
        "n_trades": n,
        "gross_sum": gross_sum,
        "cost_sum": cost_sum,
        "net_sum": net_sum,
        "cost_drag_ratio": (cost_sum / gross_sum) if gross_sum > 0 else None,
        "win_rate_gross": float((gross > 0).mean()) if n else 0.0,
        "win_rate_net": float((net > 0).mean()) if n else 0.0,
        "mean_gross_bp": float(gross.mean() * 10_000.0) if n else 0.0,
        "mean_net_bp": float(net.mean() * 10_000.0) if n else 0.0,
        "daily_sharpe_gross": _daily_sharpe(gross, trades["exit_ts"]),
        "daily_sharpe_net": _daily_sharpe(net, trades["exit_ts"]),
        "break_even_bps_per_side": breakeven_bps_side,
        "verdict": _verdict(gross_sum, net_sum),
    }


def _cut_stats(g: pd.DataFrame, cost_frac: float) -> Dict:
    gross = g["gross_ret"]
    net = gross - cost_frac
    return {
        "n_trades": int(len(g)),
        "gross_sum": float(gross.sum()),
        "net_sum": float(net.sum()),
        "mean_gross_bp": float(gross.mean() * 10_000.0),
        "mean_net_bp": float(net.mean() * 10_000.0),
        "win_rate_net": float((net > 0).mean()),
    }


def _cuts(trades: pd.DataFrame, spec: CostSpec) -> Dict:
    cf = spec.cost_frac
    cuts: Dict[str, Dict] = {}
    for name, keys in (
        ("by_exit_reason", trades["exit_reason"]),
        ("by_direction", trades["direction"]),
        ("by_year", trades["entry_ts"].dt.year.astype(str)),
    ):
        cuts[name] = {
            str(k): _cut_stats(g, cf) for k, g in trades.groupby(keys, sort=True)
        }
    # holding-time quartiles (edges reported with the labels)
    bh = trades["bars_held"]
    if bh.notna().sum() >= 4 and bh.nunique() >= 4:
        try:
            bucket = pd.qcut(bh, 4, duplicates="drop")
            cuts["by_holding_quartile"] = {
                str(k): _cut_stats(g, cf) for k, g in trades.groupby(bucket, observed=True)
            }
        except ValueError:
            pass  # degenerate distribution — skip the cut, not the run
    return cuts


def attribute(
    trades: pd.DataFrame,
    scenarios: Sequence[tuple],
    reference: str | None = None,
) -> Dict:
    """Full attribution report.

    ``scenarios``: sequence of (name, CostSpec). ``reference``: scenario name
    whose cost is used for the conditional cuts (default: first scenario).
    """
    if not scenarios:
        raise ValueError("at least one cost scenario is required")
    ref_name = reference or scenarios[0][0]
    ref_spec = dict(scenarios).get(ref_name)
    if ref_spec is None:
        raise ValueError(f"reference scenario {ref_name!r} not in scenarios")

    scenario_stats = [_scenario_stats(trades, name, spec) for name, spec in scenarios]
    return {
        "meta": {
            "n_trades": int(len(trades)),
            "kind": str(trades["kind"].iloc[0]) if len(trades) else "unknown",
            "labels": sorted(trades["label"].unique().tolist()),
            "span_start": str(trades["entry_ts"].min()),
            "span_end": str(trades["exit_ts"].max()),
        },
        "scenarios": scenario_stats,
        "cuts_reference_scenario": ref_name,
        "cuts": _cuts(trades, ref_spec),
    }


def alpha_beta(daily_net: pd.Series, market_daily: pd.Series) -> Dict:
    """OLS of daily net strategy returns on a market return series.

    Returns alpha (annualized, simple), beta, r2, n_obs. Answers the
    "is this just market beta?" question (T07-adjacent).
    """
    joined = pd.concat(
        [daily_net.rename("strat"), market_daily.rename("mkt")], axis=1
    ).dropna()
    if len(joined) < 10:
        raise LedgerError(f"alpha_beta needs >= 10 overlapping obs, got {len(joined)}")
    x = joined["mkt"].to_numpy()
    y = joined["strat"].to_numpy()
    beta, alpha_d = np.polyfit(x, y, 1)
    resid = y - (alpha_d + beta * x)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else 0.0
    return {
        "alpha_annualized": float(alpha_d * DAYS_PER_YEAR),
        "beta": float(beta),
        "r2": r2,
        "n_obs": int(len(joined)),
    }


def write_report(report: Dict, path: str) -> str:
    """Byte-deterministic JSON write (sorted keys, fixed separators)."""
    text = json.dumps(report, sort_keys=True, indent=2, default=str) + "\n"
    with open(path, "w") as f:
        f.write(text)
    return path
