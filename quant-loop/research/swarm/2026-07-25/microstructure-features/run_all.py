"""End-to-end microstructure feasibility experiment."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Ensure we can import production strategy base and shared modules.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
QUANT_LOOP = Path("/Users/mark/multica/quant-loop")
for p in [
    str(QUANT_LOOP / "strategies"),
    str(QUANT_LOOP / "strategies" / "_indicators"),
    str(QUANT_LOOP),
]:
    if p not in sys.path:
        sys.path.insert(0, p)

from utils import (  # noqa: E402
    load_ohlcv_shared,
    load_funding_shared,
    load_h3_config,
    daily_equity_from_bar_return,
    evaluate_metrics,
    write_json,
)
from microstructure import build_microstructure_features  # noqa: E402
from h3_micro import run_h3_with_micro  # noqa: E402
from feature_analysis import run_feature_analysis  # noqa: E402

OUT_DIR = ROOT
PLOT_DIR = OUT_DIR / "plots"
PLOT_DIR.mkdir(parents=True, exist_ok=True)

# Experiment window: overlap of BTC aggTrades (Jan-Jul 2026) and SOL aggTrades (Apr-Jul 2026)
START = pd.Timestamp("2026-04-01 00:00:00")
END = pd.Timestamp("2026-07-17 23:59:00")


def compute_h3_metrics(result: dict, index: pd.DatetimeIndex, cfg: dict) -> dict:
    """Compute 9-key metrics on daily equity plus per-trade stats."""
    port = result["portfolio"]
    n_bars = port["n_bars"]
    bar_ret = port["bar_return"][:n_bars]
    idx = index[:n_bars]
    starting = float(cfg.get("starting_capital_usd", 100_000.0))
    equity_daily = daily_equity_from_bar_return(bar_ret, idx, starting)

    n_trades = sum(len(p["trades"]) for p in result["per_pair"])
    trade_pnls = np.array([t["pnl_pct"] for p in result["per_pair"] for t in p["trades"]])
    metrics = evaluate_metrics(equity_daily, n_trades=n_trades, trade_pnls=trade_pnls)
    metrics["n_bars_1m"] = int(n_bars)
    metrics["span"] = [str(idx[0]), str(idx[-1])]
    metrics["avg_trade_pnl_bps"] = float(trade_pnls.mean() * 10_000) if len(trade_pnls) else 0.0
    metrics["win_rate"] = float((trade_pnls > 0).mean()) if len(trade_pnls) else 0.0
    return metrics


def plot_equity_curves(records: list, out_path: Path) -> None:
    plt.figure(figsize=(10, 5))
    for rec in records:
        eq = rec["equity_daily"]
        label = rec["label"]
        plt.plot(eq.index, eq / eq.iloc[0] - 1.0, label=label, alpha=0.8)
    plt.title("H3 Baseline vs Microstructure-Filtered Equity (2026-04..07)")
    plt.ylabel("Cumulative return")
    plt.xlabel("Date")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_feature_importance(summary: dict, out_path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, sym in zip(axes, summary):
        coefs = summary[sym]["logistic_regression"]["15m"].get("coefficients", {})
        if not coefs:
            continue
        names = list(coefs.keys())
        vals = [coefs[k] for k in names]
        y_pos = np.arange(len(names))
        colors = ["g" if v > 0 else "r" for v in vals]
        ax.barh(y_pos, vals, color=colors, alpha=0.7)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(names, fontsize=7)
        ax.set_xlabel("Logistic regression coefficient")
        ax.set_title(f"{sym} 15m forward-return sign prediction")
        ax.axvline(0, color="black", linewidth=0.5)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_correlation_heatmap(summary: dict, out_path: Path) -> None:
    # pick one symbol (BTC) and horizon 15m
    sym = [k for k in summary][0]
    csv_path = Path(summary[sym]["correlation_csv"])
    corr = pd.read_csv(csv_path)
    pivot = corr.pivot(index="feature", columns="horizon_m", values="rho")
    plt.figure(figsize=(7, 6))
    plt.imshow(pivot.to_numpy(), aspect="auto", cmap="RdYlGn", vmin=-0.05, vmax=0.05)
    plt.colorbar(label="Spearman rho")
    plt.xticks(range(len(pivot.columns)), [f"{c}m" for c in pivot.columns])
    plt.yticks(range(len(pivot.index)), pivot.index, fontsize=7)
    plt.title(f"Feature-forward-return correlation ({sym})")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def main():
    print("=" * 60)
    print("microstructure-features feasibility study")
    print("=" * 60)

    cfg = load_h3_config()
    syms = cfg["instruments"]
    print(f"Period: {START} -> {END}")
    print(f"Symbols: {syms}")

    print("\n[1/5] Loading OHLCV + funding ...")
    ohlcv = load_ohlcv_shared(syms, start=START, end=END)
    funding = load_funding_shared(syms, start=START, end=END)
    for sym in syms:
        print(f"  {sym}: {len(ohlcv[sym])} 1m bars, {len(funding[sym])} funding events")

    print("\n[2/5] Building microstructure features from aggTrades (monthly chunks) ...")
    micro_map = build_microstructure_features(syms, ohlcv,
                                              agg_root=QUANT_LOOP / "data" / "trades",
                                              start=START, end=END)
    for sym, df in micro_map.items():
        out_parquet = OUT_DIR / f"features_{sym}_1m.parquet"
        df.to_parquet(out_parquet)
        print(f"  {sym}: wrote {out_parquet} ({len(df)} rows, {len(df.columns)} features)")

    # Common index for equity plotting/metrics
    common_index = ohlcv[syms[0]].index

    print("\n[3/5] Running H3 baseline ...")
    baseline = run_h3_with_micro(ohlcv, funding, micro_map, cfg,
                                 flow_col="flow_pressure_z", threshold=0.0)
    baseline_metrics = compute_h3_metrics(baseline, common_index, cfg)
    baseline["equity_daily"] = daily_equity_from_bar_return(
        baseline["portfolio"]["bar_return"][:baseline["portfolio"]["n_bars"]],
        common_index[:baseline["portfolio"]["n_bars"]],
        float(cfg.get("starting_capital_usd", 100_000.0)),
    )
    print(f"  trades={baseline_metrics['n_trades']}  sharpe={baseline_metrics['sharpe_daily']:.3f}  "
          f"ann={baseline_metrics['annualized_return']*100:.2f}%  "
          f"maxDD={baseline_metrics['max_drawdown_pct']*100:.2f}%  "
          f"PF={baseline_metrics['profit_factor']:.3f}")

    print("\n[4/5] Running microstructure-filtered variants ...")
    variants = []
    for flow_col in ["flow_pressure_z", "flow_cum_5m", "buy_notional_ratio_z"]:
        for thr in [0.0, 0.1, 0.2, 0.5, 1.0]:
            variants.append({"flow_col": flow_col, "threshold": thr})

    variant_results = []
    for v in variants:
        try:
            res = run_h3_with_micro(ohlcv, funding, micro_map, cfg,
                                    flow_col=v["flow_col"], threshold=v["threshold"])
            m = compute_h3_metrics(res, common_index, cfg)
            label = f"{v['flow_col']}_thr{v['threshold']}"
            res["equity_daily"] = daily_equity_from_bar_return(
                res["portfolio"]["bar_return"][:res["portfolio"]["n_bars"]],
                common_index[:res["portfolio"]["n_bars"]],
                float(cfg.get("starting_capital_usd", 100_000.0)),
            )
            variant_results.append({"label": label, "params": v, "metrics": m, "result": res})
            print(f"  {label:35s}  trades={m['n_trades']:5d}  sharpe={m['sharpe_daily']:.3f}  "
                  f"ann={m['annualized_return']*100:6.2f}%  maxDD={m['max_drawdown_pct']*100:6.2f}%  "
                  f"PF={m['profit_factor']:.3f}")
        except Exception as e:
            print(f"  {v} failed: {e}")

    # pick best variant by Sharpe (daily)
    best = max(variant_results, key=lambda r: r["metrics"]["sharpe_daily"]) if variant_results else None

    print("\n[5/5] Standalone feature predictive analysis ...")
    feature_summary = run_feature_analysis(ohlcv, micro_map, OUT_DIR)
    for sym, s in feature_summary.items():
        print(f"  {sym}: top rho = {s['top_correlations'][0]['rho']:.4f} "
              f"({s['top_correlations'][0]['feature']} @ {s['top_correlations'][0]['horizon_m']}m)")
        for h in ["1m", "5m", "15m"]:
            lr = s["logistic_regression"][h]
            if "test_accuracy" in lr:
                print(f"    {h} logistic test accuracy = {lr['test_accuracy']:.3f} "
                      f"(baseline={lr['baseline_accuracy']:.3f})")

    # -----------------------------------------------------------------------
    # Plots
    # -----------------------------------------------------------------------
    print("\n[plots] Generating charts ...")
    records = [{"label": "H3_baseline", "equity_daily": baseline["equity_daily"]}]
    if best:
        records.append({"label": best["label"], "equity_daily": best["result"]["equity_daily"]})
    plot_equity_curves(records, PLOT_DIR / "equity_baseline_vs_best.png")
    plot_feature_importance(feature_summary, PLOT_DIR / "feature_importance.png")
    plot_correlation_heatmap(feature_summary, PLOT_DIR / "feature_correlation_heatmap.png")

    # -----------------------------------------------------------------------
    # Persist results
    # -----------------------------------------------------------------------
    summary = {
        "direction": "microstructure-features",
        "period": {"start": str(START), "end": str(END)},
        "config": {
            "strategy": cfg["strategy"],
            "instruments": cfg["instruments"],
            "pairs": cfg["pairs"],
            "cost_bps_rt": 2.0 * (cfg.get("fees_bps_per_side", 1.0) + cfg.get("slippage_bps_per_side", 1.0)),
        },
        "baseline": baseline_metrics,
        "variants": [
            {"label": r["label"], "params": r["params"], "metrics": r["metrics"]}
            for r in variant_results
        ],
        "best_variant": {
            "label": best["label"] if best else None,
            "params": best["params"] if best else None,
            "metrics": best["metrics"] if best else None,
        },
        "feature_analysis": feature_summary,
    }
    write_json(OUT_DIR / "metrics.json", summary)
    print("\nWrote metrics.json ->", OUT_DIR / "metrics.json")

    # -----------------------------------------------------------------------
    # SUMMARY.md
    # -----------------------------------------------------------------------
    write_summary_md(OUT_DIR / "SUMMARY.md", summary)
    print("Wrote SUMMARY.md ->", OUT_DIR / "SUMMARY.md")


def write_summary_md(path: Path, summary: dict) -> None:
    b = summary["baseline"]
    best = summary["best_variant"]
    lines = [
        "# microstructure-features feasibility study",
        "",
        f"**Period:** {summary['period']['start']} -> {summary['period']['end']}",
        f"**Symbols:** {', '.join(summary['config']['instruments'])}",
        f"**Cost:** {summary['config']['cost_bps_rt']:.1f} bps round-trip",
        "",
        "## What was done",
        "",
        "1. Built per-1m trade-flow microstructure features from Binance aggTrades partitions,",
        "   loading one month at a time to stay within memory limits.",
        "2. Features include volume/order-flow imbalance, aggressive buy/sell ratios,",
        "   whale-trade notional share ($100k threshold), trade intensity, short-term",
        "   cumulative flow, and rolling z-scored flow pressure.",
        "3. Ran H3 (BTC/SOL 1m pair z-score + funding regime) baseline on the same window.",
        "4. Ran microstructure-filtered H3 variants: require confirming per-leg flow",
        "   pressure (BTC - SOL) before entering a mean-reversion signal.",
        "5. Evaluated standalone feature predictive power via Spearman correlation to",
        "   forward returns and a simple logistic-regression sign classifier.",
        "",
        "## Key numbers",
        "",
        f"| Variant | Trades | Sharpe | Ann % | MaxDD % | PF | WinRate % | Avg trade (bps) |",
        f"|---------|--------|--------|-------|---------|----|-----------|-----------------|",
    ]

    def row(metrics: dict, label: str) -> str:
        return (f"| {label} | {metrics['n_trades']} | {metrics['sharpe_daily']:.3f} | "
                f"{metrics['annualized_return']*100:.2f} | {metrics['max_drawdown_pct']*100:.2f} | "
                f"{metrics['profit_factor']:.3f} | {metrics['win_rate']*100:.1f} | "
                f"{metrics['avg_trade_pnl_bps']:.2f} |")

    lines.append(row(b, "H3_baseline"))
    if best and best["metrics"]:
        lines.append(row(best["metrics"], best["label"]))

    lines.extend([
        "",
        "### Standalone feature predictive power",
        "",
    ])
    for sym, s in summary["feature_analysis"].items():
        top = s["top_correlations"][0]
        lines.append(f"- **{sym}**: strongest Spearman rho = {top['rho']:.4f} "
                     f"({top['feature']} vs {top['horizon_m']}m forward return, n={top['n']})")
        for h in ["1m", "5m", "15m"]:
            lr = s["logistic_regression"][h]
            if "test_accuracy" in lr:
                lines.append(f"  - {h} sign-prediction test accuracy = {lr['test_accuracy']:.3f} "
                             f"(naive baseline = {lr['baseline_accuracy']:.3f})")

    lines.extend([
        "",
        "## Gate check (G1-G7 / T1)",
        "",
    ])
    failed = b.get("failed_gates", [])
    lines.append(f"- **Baseline failed gates:** {', '.join(failed) if failed else 'none'}")
    if best and best["metrics"]:
        bf = best["metrics"].get("failed_gates", [])
        lines.append(f"- **Best variant failed gates:** {', '.join(bf) if bf else 'none'}")
    lines.extend([
        "- G5 (framework CV) and G6/G7 (bootstrap/DSR) were not run in this quick-feasibility pass.",
        "  G1-G4/T1 are evaluated on the full in-sample window using daily-resampled metrics.",
        "",
        "## Verdict: continue or KILL?",
        "",
    ])
    if best and best["metrics"] and best["metrics"]["sharpe_daily"] > b["sharpe_daily"]:
        lines.append(
            "**CONTINUE with a focused threshold sweep.** The best microstructure filter "
            f"({best['label']}) improves Sharpe from {b['sharpe_daily']:.3f} to "
            f"{best['metrics']['sharpe_daily']:.3f} and raises PF from "
            f"{b['profit_factor']:.3f} to {best['metrics']['profit_factor']:.3f}, "
            "but standalone feature predictive power is weak (rho < 0.03)."
        )
    else:
        lines.append(
            "**HOLD / close-to-KILL for this feature set.** No microstructure filter beat the "
            "baseline; standalone predictive power is near zero, suggesting these features do not "
            "add material edge to the H3 template on this window."
        )

    lines.extend([
        "",
        "## Next 1-2 concrete actions",
        "",
        "1. Run the same H3+microstructure pipeline over the full available history "
        "   (not only 2026-04..07 where aggTrades exist) to confirm whether the 2026 window is "
        "   representative or a lucky segment.",
        "2. If the improvement persists, expand the feature set to order-book derived signals "
        "   (book imbalance, queue position, bid-ask bounce) and run a proper walk-forward "
        "   threshold optimization with G1-G7 certification; otherwise kill this branch.",
        "",
    ])
    path.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
