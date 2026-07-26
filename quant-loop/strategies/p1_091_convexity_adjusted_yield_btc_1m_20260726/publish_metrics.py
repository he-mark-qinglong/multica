"""publish_metrics.py — flatten metrics.json into one-line summary,
with --check-stale guard.

Used to summarize the Convexity Adjusted Yield strategy (SMA-36109).
Surfaces the headline Sharpe / annualized return / max_drawdown_pct /
profit_factor / n_trades and the CPCV OOS mean Sharpe for the issue
comment, and refuses to publish if ``metrics.json`` is older than the
source code (catches the case where someone edited the strategy but
forgot to re-run ``python3 run_cpcv.py``).

Usage
-----
    python3 publish_metrics.py               # one-line summary
    python3 publish_metrics.py --verbose     # multi-line summary
    python3 publish_metrics.py --check-stale # exit 1 if metrics.json
                                              is older than source code
    python3 publish_metrics.py --json        # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = REPO_ROOT / "results"
QUANT_LOOP = REPO_ROOT.parents[1]
METRICS_PATH = RESULTS_DIR / "metrics.json"
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "p1_091_convexity_adjusted_yield_btc_1m_20260726"


def _load_metrics() -> dict:
    if METRICS_PATH.exists():
        return json.loads(METRICS_PATH.read_text())
    if (TOPLEVEL_RESULTS / "metrics.json").exists():
        return json.loads((TOPLEVEL_RESULTS / "metrics.json").read_text())
    raise FileNotFoundError(f"metrics.json not found at {METRICS_PATH} or {TOPLEVEL_RESULTS}")


def _flatten(metrics: dict) -> dict:
    """Extract the headline numbers from the metrics envelope."""
    g = metrics.get("gates", {})
    agg = metrics.get("aggregate", {}) if isinstance(metrics.get("aggregate"), dict) else {}
    cpcv = (metrics.get("cpcv_per_symbol") or {}).get("PORTFOLIO", {})
    return {
        "variant_key": metrics.get("variant_key"),
        "iteration": metrics.get("iteration"),
        "verdict": metrics.get("verdict"),
        "parent_issue_verdict": metrics.get("parent_issue_verdict"),
        "headline": {
            "mean_sharpe_daily_wf": metrics.get("mean_sharpe_daily_wf"),
            "worst_max_drawdown_pct": metrics.get("worst_max_drawdown_pct"),
            "min_profit_factor_wf": metrics.get("min_profit_factor_wf"),
            "n_trades_total": metrics.get("n_trades_total"),
            "n_folds_total": metrics.get("n_folds_total"),
            "annualized_return": metrics.get("annualized_return"),
        },
        "cpcv": {
            "mean_oos_sharpe": cpcv.get("mean_oos_sharpe"),
            "dsr": cpcv.get("dsr"),
            "n_paths_total": cpcv.get("n_paths_total"),
            "n_paths_valid": cpcv.get("n_paths_valid"),
        },
        "gates": g,
    }


def _check_stale(metrics: dict) -> int:
    """Refuse to publish if the metrics.json mtime is older than any
    of the source-code files (strategy / run_cpcv / config.json /
    data_loader.py)."""
    source_files = [
        REPO_ROOT / "strategy.py",
        REPO_ROOT / "run_cpcv.py",
        REPO_ROOT / "config.json",
        REPO_ROOT / "data_loader.py",
    ]
    metrics_mtime = METRICS_PATH.stat().st_mtime
    for src in source_files:
        if not src.exists():
            continue
        src_mtime = src.stat().st_mtime
        if src_mtime > metrics_mtime:
            print(
                f"STALE: {src.name} (mtime {datetime.fromtimestamp(src_mtime, timezone.utc).isoformat()}) "
                f"is newer than metrics.json ({datetime.fromtimestamp(metrics_mtime, timezone.utc).isoformat()}). "
                f"Re-run python3 run_cpcv.py.",
                file=sys.stderr,
            )
            return 1
    return 0


def _one_line(metrics: dict) -> str:
    h = _flatten(metrics)
    sharpe = h["headline"]["mean_sharpe_daily_wf"]
    ann = h["headline"]["annualized_return"]
    mdd = h["headline"]["worst_max_drawdown_pct"]
    pf = h["headline"]["min_profit_factor_wf"]
    nt = h["headline"]["n_trades_total"]
    cpcv = h["cpcv"]["mean_oos_sharpe"]
    dsr = h["cpcv"]["dsr"]
    n_paths = h["cpcv"]["n_paths_valid"]
    verdict = h["verdict"]
    parent_verdict = h["parent_issue_verdict"]
    return (
        f"[{h['variant_key']}] verdict={verdict} parent={parent_verdict} "
        f"wf_sharpe_daily={sharpe} ann_ret={ann} cpcv_mean_oos_sharpe={cpcv} "
        f"dsr={dsr} worst_mdd={mdd} pf_min={pf} "
        f"n_trades={nt} n_cpcv_paths={n_paths}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-stale", action="store_true",
                        help="exit 1 if metrics.json older than source code")
    parser.add_argument("--verbose", action="store_true",
                        help="multi-line summary")
    parser.add_argument("--json", action="store_true",
                        help="print flattened metrics as JSON")
    args = parser.parse_args()

    metrics = _load_metrics()
    if args.check_stale:
        if not METRICS_PATH.exists():
            print("STALE: metrics.json missing — re-run python3 run_cpcv.py",
                  file=sys.stderr)
            return 1
        rc = _check_stale(metrics)
        if rc != 0:
            return rc
        print("OK: metrics.json is fresh", file=sys.stderr)

    if args.json:
        print(json.dumps(_flatten(metrics), indent=2, default=str))
    elif args.verbose:
        flat = _flatten(metrics)
        h = flat["headline"]
        c = flat["cpcv"]
        g = flat["gates"]
        print(f"variant_key:        {flat['variant_key']}")
        print(f"verdict:            {flat['verdict']}")
        print(f"parent_verdict:     {flat['parent_issue_verdict']}")
        print(f"WF Sharpe (daily):  {h['mean_sharpe_daily_wf']}")
        print(f"WF annualized ret:  {h['annualized_return']}")
        print(f"CPCV mean OOS Sharpe: {c['mean_oos_sharpe']}")
        print(f"DSR:                {c['dsr']}")
        print(f"worst MDD (%):      {h['worst_max_drawdown_pct']}")
        print(f"min profit factor:  {h['min_profit_factor_wf']}")
        print(f"n_trades_total:     {h['n_trades_total']}")
        print(f"n_cpcv_paths (valid): {c['n_paths_valid']}/{c['n_paths_total']}")
        print(f"G1 (sharpe>=0.5):   {g.get('G1_pass')}")
        print(f"G2 (worst>=0):      {g.get('G2_pass')}")
        print(f"G3 (DSR>0):         {g.get('G3_pass')}")
        print(f"G4 (n_trades>=30):  {g.get('G4_pass')}")
    else:
        print(_one_line(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())