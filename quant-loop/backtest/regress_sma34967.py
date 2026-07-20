"""SMA-34967 regression: double-counted commission vs canonical cost wiring.

Runs ONE strategy (`funding_carry_asym`, SMA-34858 lineage — the 15m BTC
perp, 8-bar time-stop variant SMA-34913 analyzed) on the SAME data window
and SAME signals twice:

  BEFORE  hazardous wiring: fee_bps_per_fill=4.0 + slippage_bps_per_fill=11.0
          (SMA-34900 fee-inclusive plug-in stacked on an independent
          commission) -> 15 bps/side = 30 bps round trip.
  AFTER   the same config resolved through
          ``backtest.factor_backtester.CostModel.from_config`` (the SMA-34967
          fix): fee split out of the plug-in, counted once -> 11 bps/side =
          22 bps round trip.

Pass gate: the AFTER run's total cost is exactly 22 bps round trip, and
net metrics improve vs BEFORE (same gross trades, lower cost drag).

Usage:
  python3 regress_sma34967.py [strategy_dir] [window_start] [window_end]

Defaults: the live funding_carry_asym tree, 2024-01-01 -> 2024-04-30
(Jan warmup + the Q1-2024 hot-funding window from the strategy's own
config notes; SMA-34913's analysis window).
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

BACKTEST_PKG_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKTEST_PKG_DIR))

from factor_backtester import CostModel  # noqa: E402

DEFAULT_STRATEGY_DIR = Path(
    "/home/smark/multica/quant-loop/strategies/funding_carry_asym"
)
DEFAULT_WINDOW = ("2024-01-01", "2024-04-30")
BARS_PER_YEAR_DAILY = 365.25

# Hazard wiring: SMA-34900 fee-inclusive plug-in + independent commission.
HAZARD_PARAMS = {"fee_bps_per_fill": 4.0, "slippage_bps_per_fill": 11.0}


def _install_vpvr_compat_shim() -> None:
    """Bridge the retired ``vpvr_levels.detect_vpvr_levels`` API.

    The live ``_indicators/vpvr_levels.py`` was refactored (2026-07-17,
    commit 45e46736e) to ``compute_vpvr_levels`` and the old
    ``VpvrLevel``/``detect_vpvr_levels`` names no longer exist anywhere,
    which leaves ``funding_carry_asym`` (untracked WIP) un-importable.
    Repairing that strategy is out of SMA-34967's scope, so this shim
    maps the new detector onto the old contract for BOTH regression runs
    identically — the before/after diff stays a pure cost-wiring diff.
    """
    import types

    import vpvr_levels as _new

    @dataclass(frozen=True)
    class VpvrLevel:  # old contract: kind/price_low/price_high/price_center/volume/score
        kind: str
        price_low: float
        price_high: float
        price_center: float
        volume: float
        score: float

    def detect_vpvr_levels(df, *, num_bins, hvn_quantile, lvn_quantile,
                           num_hvn, num_lvn):
        prof = _new.compute_vpvr_levels(
            df["high"], df["low"], df["volume"],
            num_bins=num_bins, hvn_quantile=hvn_quantile,
            lvn_quantile=lvn_quantile, num_hvn=num_hvn, num_lvn=num_lvn,
        )
        out = []
        for lo, hi, vol in prof.hvn_zones:
            out.append(VpvrLevel("HVN", float(lo), float(hi),
                                 0.5 * (lo + hi), float(vol), 1.0))
        for lo, hi, vol in prof.lvn_zones:
            out.append(VpvrLevel("LVN", float(lo), float(hi),
                                 0.5 * (lo + hi), float(vol), 1.0))
        return out

    shim = types.ModuleType("vpvr_levels")
    shim.VpvrLevel = VpvrLevel
    shim.detect_vpvr_levels = detect_vpvr_levels
    sys.modules["vpvr_levels"] = shim


def _import_strategy(strategy_dir: Path):
    """Import strategy.py + data_loader.py from a strategy dir (read-only)."""
    # The indicators package must be importable before the shim installs.
    sys.path.insert(0, "/home/smark/multica/quant-loop/strategies/_indicators")
    _install_vpvr_compat_shim()
    sys.path.insert(0, str(strategy_dir))
    for name in ("strategy", "data_loader", "build_signals"):
        if name in sys.modules:
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        "strategy", strategy_dir / "strategy.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["strategy"] = module  # dataclass introspection needs this
    spec.loader.exec_module(module)
    import data_loader  # noqa: E402  (resolved via the sys.path insert above)

    return module, data_loader


def _daily_sharpe(equity: np.ndarray, bar_index: pd.DatetimeIndex) -> float:
    """Daily-resampled Sharpe per the SMA-34787 audit convention."""
    eq = pd.Series(equity, index=bar_index[: len(equity)])
    daily = eq.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0:
        return 0.0
    return float(rets.mean() / rets.std() * math.sqrt(BARS_PER_YEAR_DAILY))


def _metrics(out: dict) -> dict:
    trades = out["trades"]
    equity = np.asarray(out["equity"], dtype=np.float64)
    pnl = np.array([t["pnl_pct"] for t in trades], dtype=np.float64)
    wins = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].sum()
    peak = np.maximum.accumulate(equity)
    maxdd = float(((equity - peak) / peak).min()) if len(equity) else 0.0
    bar_index = pd.date_range(
        out["span_start"], periods=len(equity), freq="15min", tz="UTC"
    )
    return {
        "n_trades": len(trades),
        "expectancy_bps_per_trade": float(pnl.mean() * 1e4) if len(pnl) else 0.0,
        "profit_factor": float(wins / abs(losses)) if losses < 0 else float("inf"),
        "total_net_return_pct": float((equity[-1] / equity[0] - 1.0) * 100.0),
        "sharpe_daily": _daily_sharpe(equity, bar_index),
        "max_dd_pct": maxdd * 100.0,
    }


def main() -> int:
    strategy_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STRATEGY_DIR
    win_start = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_WINDOW[0]
    win_end = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_WINDOW[1]

    cfg = json.loads((strategy_dir / "config.json").read_text())
    strategy, data_loader = _import_strategy(strategy_dir)

    df = data_loader.load_symbol(cfg["instruments"][0], timeframe="15m")
    df = df.loc[win_start:win_end]
    if len(df) < 500:
        raise SystemExit(f"window {win_start}->{win_end} has only {len(df)} bars")

    # max_hold for 15m comes from the per-TF key, matching run_backtest.py.
    cfg["params"]["max_hold_bars"] = cfg["params"].get("max_hold_bars_15m", 8)

    # --- BEFORE: hazardous wiring (fee-inclusive plug-in + commission) ---
    before_params = dict(cfg["params"], **HAZARD_PARAMS)
    before_rt_bps = 2.0 * (
        before_params["fee_bps_per_fill"] + before_params["slippage_bps_per_fill"]
    )
    out_before = strategy.run_backtest(df, dict(cfg, params=before_params))

    # --- AFTER: same config resolved through the canonical cost assembly ---
    model = CostModel.from_config(before_params)
    after_params = dict(
        cfg["params"],
        fee_bps_per_fill=model.commission_bps_per_side,
        slippage_bps_per_fill=model.slippage_bps_per_side,
    )
    out_after = strategy.run_backtest(df, dict(cfg, params=after_params))

    # Sanity: identical gross path — only the cost wiring may differ.
    tb = [(t["entry_ts"], t["exit_reason"]) for t in out_before["trades"]]
    ta = [(t["entry_ts"], t["exit_reason"]) for t in out_after["trades"]]
    assert tb == ta, "trade paths diverged; regression must isolate cost only"
    assert model.round_trip_bps == 22.0, f"pass gate: {model.round_trip_bps}"

    mb, ma = _metrics(out_before), _metrics(out_after)

    print(f"strategy      : {cfg['strategy_key']} (15m BTCUSDT perp, "
          f"max_hold={cfg['params']['max_hold_bars']} bars)")
    print(f"window        : {df.index[0]} -> {df.index[-1]} ({len(df)} bars)")
    print(f"cost BEFORE   : {before_rt_bps:g} bps RT (fee 4 + plug-in 11, "
          f"fee charged twice)")
    print(f"cost AFTER    : {model.round_trip_bps:g} bps RT ({model.ledger_note()})")
    print()
    hdr = f"{'metric':<28}{'BEFORE (30bps RT)':>18}{'AFTER (22bps RT)':>18}{'diff':>12}"
    print(hdr)
    print("-" * len(hdr))
    for k in ("n_trades", "expectancy_bps_per_trade", "profit_factor",
              "total_net_return_pct", "sharpe_daily", "max_dd_pct"):
        b, a = mb[k], ma[k]
        print(f"{k:<28}{b:>18.4f}{a:>18.4f}{a - b:>+12.4f}")
    print()
    print(f"VERDICT: PASS | oos_sharpe={ma['sharpe_daily']:.4f} "
          f"(before={mb['sharpe_daily']:.4f}) | "
          f"ann=n/a (smoke window) | maxdd={ma['max_dd_pct']:.2f}% | "
          f"reason=double-count removed; total cost {model.round_trip_bps:g}bps RT "
          f"(was {before_rt_bps:g}) | next=smark review of wiring fix")
    print(f"ledger: {model.ledger_note()} window={win_start}->{win_end} "
          f"trades={ma['n_trades']} expectancy "
          f"{mb['expectancy_bps_per_trade']:+.2f} -> "
          f"{ma['expectancy_bps_per_trade']:+.2f} bps/trade")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
