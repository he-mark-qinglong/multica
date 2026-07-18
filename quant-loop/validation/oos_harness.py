"""OOS validation harness — CLI orchestrator.

Usage (from the quant-loop directory):

    python3 -m validation.oos_harness --variant vpvr_reversion_1m_kama_reversal_20260709
    python3 -m validation.oos_harness --variant strategies/<name> --windows 3

Exit codes: 0 = all G1-G7 gates PASS (merge allowed)
            1 = at least one gate FAIL (merge blocked)
            2 = harness error (variant unsupported, data missing, framework crash)

On a new variant commit, ci/validate_changed_variants.sh runs this module for
every changed variant directory; a non-zero exit blocks the merge.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import pandas as pd

from . import metrics as M
from .adapters.backtrader_replay import run_backtrader_replay
from .adapters.freqtrade_replay import run_freqtrade_replay
from .adapters.native_engine import NativeEngineAdapter, UnsupportedVariantError
from .gates import evaluate_gates
from .windows import compute_oos_windows

QUANT_LOOP_ROOT = Path(__file__).resolve().parent.parent
STRATEGIES_ROOT = QUANT_LOOP_ROOT / "strategies"


def _resolve_variant(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.is_dir():
        return p.resolve()
    candidate = STRATEGIES_ROOT / name_or_path
    if candidate.is_dir():
        return candidate.resolve()
    raise SystemExit(f"variant not found: {name_or_path} (tried {candidate})")


def _slice(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df.loc[(df.index >= start) & (df.index <= end)]


def run_validation(variant_dir: Path, n_windows: int, frameworks: list[str],
                   output_dir: Path | None = None, keep_ft_dir: bool = False) -> tuple[bool, dict]:
    adapter = NativeEngineAdapter(variant_dir)
    cfg = adapter.config
    fees_bps = float(cfg.get("fees_bps_per_side", 1.0))
    slippage_bps = float(cfg.get("slippage_bps_per_side", 1.0))
    commission = (fees_bps + slippage_bps) / 1e4
    starting_capital = float(cfg.get("starting_capital_usd", 100_000.0))
    sizing = cfg.get("sizing", {})
    weight = float(sizing.get("per_signal_weight_pct", 0.01))
    max_gross = float(sizing.get("max_gross_exposure_pct", 0.05))
    max_open = max(1, round(max_gross / max(weight, 1e-9)))

    print(f"[harness] variant={variant_dir.name} tf={adapter.timeframe} "
          f"symbols={adapter.symbols} windows={n_windows} frameworks={frameworks}")
    data = adapter.load_data()

    span_start = min(df.index[0] for df in data.values())
    span_end = max(df.index[-1] for df in data.values())
    windows = compute_oos_windows(span_start, span_end, n_windows)
    print(f"[harness] data span {span_start} .. {span_end}")
    for w in windows:
        print(f"[harness]   {w.label}")

    report: dict = {"variant": variant_dir.name, "timeframe": adapter.timeframe,
                    "windows": [w.label for w in windows], "symbols": {}}

    # ---- full-period native runs (G1/G2/G3/G4) -----------------------------
    full_metrics_by_symbol: dict[str, dict] = {}
    if "native" in frameworks:
        for sym, df in data.items():
            run = adapter.run(df, sym)
            m = M.metrics_from_run(run.equity, run.trade_pnls)
            full_metrics_by_symbol[sym] = m
            print(f"[harness] full native {sym}: sharpe={m['sharpe']:.3f} "
                  f"ann={m['annualized_return']:.3f} mdd={m['max_drawdown']:.3f} "
                  f"pf={m['profit_factor']:.3f} trades={m['n_trades']}")

    # ---- per-window runs (native + framework CV) ----------------------------
    window_native, window_bt, window_ft = [], [], []
    pooled_oos_daily: dict[str, list[pd.Series]] = {s: [] for s in data}
    pooled_oos_pnls: list[float] = []

    for w in windows:
        for sym, df in data.items():
            dfs = _slice(df, w.start, w.end)
            if dfs.empty:
                print(f"[harness] {w.label} {sym}: no data in window, skipped")
                continue
            native = adapter.run(dfs, sym)
            m_nat = M.metrics_from_run(native.equity, native.trade_pnls)
            window_native.append(m_nat)
            pooled_oos_daily[sym].append(m_nat["daily_returns"])
            pooled_oos_pnls.extend(native.trade_pnls)
            print(f"[harness] {w.label} {sym} native: sharpe={m_nat['sharpe']:.3f} "
                  f"trades={m_nat['n_trades']}")

            if "backtrader" in frameworks:
                bt_run = run_backtrader_replay(
                    dfs, native.trades, symbol=sym,
                    starting_cash=starting_capital, commission=commission, weight=weight)
                m_bt = M.metrics_from_run(bt_run.equity, bt_run.trade_pnls)
                window_bt.append(m_bt)
                print(f"[harness] {w.label} {sym} backtrader: sharpe={m_bt['sharpe']:.3f} "
                      f"trades={m_bt['n_trades']}")

            if "freqtrade" in frameworks:
                ft_run = run_freqtrade_replay(
                    dfs, native.trades, symbol=sym, timeframe=adapter.timeframe,
                    starting_wallet=starting_capital,
                    stake_per_trade=starting_capital * weight,
                    max_open_trades=max_open, fee=commission, keep_dir=keep_ft_dir)
                m_ft = M.metrics_from_run(ft_run.equity, ft_run.trade_pnls)
                window_ft.append(m_ft)
                print(f"[harness] {w.label} {sym} freqtrade: sharpe={m_ft['sharpe']:.3f} "
                      f"trades={m_ft['n_trades']}")

            report["symbols"].setdefault(sym, {}).setdefault(w.label, {
                "native": M.public_metrics(m_nat),
                **({"backtrader": M.public_metrics(m_bt)} if "backtrader" in frameworks else {}),
                **({"freqtrade": M.public_metrics(m_ft)} if "freqtrade" in frameworks else {}),
            })

    # ---- pooled OOS series for G6/G7 ----------------------------------------
    per_symbol_daily = []
    for sym, series_list in pooled_oos_daily.items():
        if series_list:
            per_symbol_daily.append(pd.concat(series_list).sort_index())
    if per_symbol_daily:
        daily_df = pd.concat(per_symbol_daily, axis=1)
        pooled_daily = daily_df.mean(axis=1).dropna()
    else:
        pooled_daily = pd.Series(dtype=float)

    # ---- gates ---------------------------------------------------------------
    if not full_metrics_by_symbol:
        # gates G1-G4 need the native full run; synthesize from windows if absent
        full_metrics_by_symbol = {
            sym: M.metrics_from_run(pd.Series(dtype=float), []) for sym in data}
    verdict = evaluate_gates(
        variant_dir.name,
        full_metrics_by_symbol=full_metrics_by_symbol,
        window_native=window_native,
        window_backtrader=window_bt,
        window_freqtrade=window_ft,
        pooled_oos_daily_returns=pooled_daily,
        pooled_oos_trade_pnls=pooled_oos_pnls,
    )

    report["full_native"] = {s: M.public_metrics(m) for s, m in full_metrics_by_symbol.items()}
    report["gates"] = [vars(g) for g in verdict.gates]
    report["verdict"] = "PASS" if verdict.passed else "FAIL"

    out = output_dir or (variant_dir / "results" / "validation")
    out.mkdir(parents=True, exist_ok=True)
    (out / "verdict.json").write_text(json.dumps(report, indent=2, default=float))
    md = "\n".join(verdict.summary_lines()) + "\n"
    (out / "verdict.md").write_text(md)
    print(f"[harness] verdict written to {out}")
    print(md)
    return verdict.passed, report


def main() -> int:
    ap = argparse.ArgumentParser(description="OOS validation harness (G1-G7)")
    ap.add_argument("--variant", required=True, help="variant name or directory")
    ap.add_argument("--windows", type=int, default=3, help="number of OOS windows")
    ap.add_argument("--frameworks", default="native,backtrader,freqtrade",
                    help="comma-separated subset of native,backtrader,freqtrade")
    ap.add_argument("--output", default=None, help="override output directory")
    ap.add_argument("--keep-ft-dir", action="store_true",
                    help="keep freqtrade temp userdirs for debugging")
    args = ap.parse_args()

    frameworks = [f.strip() for f in args.frameworks.split(",") if f.strip()]
    unknown = set(frameworks) - {"native", "backtrader", "freqtrade"}
    if unknown:
        print(f"[harness] unknown frameworks: {sorted(unknown)}", file=sys.stderr)
        return 2

    variant_dir = _resolve_variant(args.variant)
    try:
        passed, _ = run_validation(
            variant_dir, args.windows, frameworks,
            Path(args.output) if args.output else None, args.keep_ft_dir)
        return 0 if passed else 1
    except UnsupportedVariantError as e:
        print(f"[harness] UNSUPPORTED: {e}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
