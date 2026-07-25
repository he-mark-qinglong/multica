"""SMA-34936 cross-framework validation harness.

After the sizing sweep picks winners, write each winner's per-pair
trades CSV (so freqtrade + backtrader adapters can replay them with
divergent fee models / fill conventions). This script writes the
trades for ONE named variant; rerun per winner.

The same per-pair trades are produced by ``sizing_sweep.walk_forward_variants``
but retained in-memory — here we re-execute the backtest end-to-end
for the winning variant on the FULL data and emit a single trades CSV
that the framework adapters can consume.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "_indicators"))

from mtf_xs_pairs_base_20260718 import _backtest_pair, build_portfolio  # type: ignore

from sizing_sweep import (  # type: ignore
    compute_sizing_signals,
    sizing_baseline_atr,
    sizing_atr_multiplier,
    sizing_vol_target,
    sizing_regime_conditional,
    kelly_size,
    variant_scale_fn,
    build_variant_list,
    VariantSpec,
)
from data_loader import load_all, load_funding  # type: ignore


def render_size_scale_for_pair(spec: VariantSpec, sigs: dict, cfg: dict,
                                trade_log_baseline: list):
    fn = variant_scale_fn(spec, sigs, cfg, trade_log_baseline)
    return fn  # caller invokes with (pair_signals, pair)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True,
                    help="variant name as printed by sizing_sweep.py")
    ap.add_argument("--tag", default=None,
                    help="output tag (defaults to variant)")
    args = ap.parse_args()

    cfg = json.loads((_HERE / "config.json").read_text())
    syms = cfg["instruments"]
    d1m = load_all(syms)
    funding = load_funding(syms)

    sigs_full = compute_sizing_signals(d1m, funding, cfg)
    floor_ceil = {"size_floor": float(cfg["sizing"]["size_floor"]),
                  "size_ceiling": float(cfg["sizing"]["size_ceiling"])}

    # baseline trade log for Kelly win-rate stats
    from mtf_xs_pairs_base_20260718 import _backtest_pair  # type: ignore
    fee_bps = float(cfg["fees_bps_per_side"])
    slip_bps = float(cfg["slippage_bps_per_side"])
    baseline_trades = []
    for pair, sig in sigs_full["out"].items():
        size_scale = sizing_baseline_atr(sigs_full["atr_b_1m"], sigs_full["atr_med"], floor_ceil)
        res = _backtest_pair(sig, pair, sizing_scale=size_scale, fee_bps=fee_bps, slip_bps=slip_bps)
        baseline_trades.extend(res["trades"])

    variants = {v.name: v for v in build_variant_list(cfg)}
    if args.variant not in variants:
        raise SystemExit("unknown variant: " + args.variant)
    spec = variants[args.variant]

    scale_fn = variant_scale_fn(spec, sigs_full, cfg, baseline_trades)
    per_pair = []
    for pair, sig in sigs_full["out"].items():
        size_scale = scale_fn(sig, pair)
        per_pair.append(_backtest_pair(sig, pair, sizing_scale=size_scale,
                                        fee_bps=fee_bps, slip_bps=slip_bps))

    rows = []
    for pp in per_pair:
        for t in pp["trades"]:
            row = dict(t)
            row["pair"] = pp["pair"]
            rows.append(row)
    df = pd.DataFrame(rows)
    df.sort_values(["entry_ts", "pair"], inplace=True)
    out_tag = args.tag or spec.name
    out_path = _HERE / "results" / f"trades_winner_{out_tag}.csv"
    df.to_csv(out_path, index=False)
    print(f"wrote {len(df)} trades -> {out_path}")

    # equity curve (1m, large) + 1d committable
    starting_capital = float(cfg["sizing"]["starting_capital_usd"])
    port = build_portfolio(per_pair, starting_capital=starting_capital)
    n_bars = port["n_bars"]
    idx = d1m["BTCUSDT"].index[:n_bars]
    df_1m = pd.DataFrame({
        "bar_index": np.arange(n_bars),
        "timestamp": idx,
        "equity": np.asarray(port["equity"], dtype=float),
        "bar_return": np.asarray(port["bar_return"], dtype=float),
    })
    df_1d = df_1m.set_index("timestamp").resample("1D").last().dropna().reset_index()
    df_1d["daily_return"] = df_1d["equity"].pct_change().fillna(0.0)
    df_1d["bar_index"] = df_1d.index
    df_1d = df_1d[["bar_index", "timestamp", "equity", "daily_return"]]
    df_1d.to_csv(_HERE / "results" / f"equity_winner_{out_tag}_1d.csv", index=False)
    print("wrote 1d equity ->", _HERE / "results" / f"equity_winner_{out_tag}_1d.csv")


if __name__ == "__main__":
    main()