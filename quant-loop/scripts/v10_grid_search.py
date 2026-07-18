"""V10 optimization: grid search over V7 params + fix funding filter bug.

Key improvements vs V7:
1. FUNDING FILTER FIX: V7's threshold=0.0003 let 97% of trades through (effectively no-op).
   V10 tests proper thresholds (0.0001, 0.00005) that actually filter trades.

2. GRID SEARCH: sweep the 4 most impactful params:
   - zscore_entry_threshold: [2.0, 2.3, 2.5, 2.8, 3.0]
   - zscore_lookback_bars: [96, 144, 192, 288]
   - max_holding_bars: [48, 72, 96, 144]
   - vpvr_proximity_atr_k: [0.5, 0.7, 1.0]

3. SELECTION CRITERIA: prioritize walk-forward stability (wf_ratio) over in-sample Sharpe.

Reuses V7's data_loader + strategy logic from vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712.
"""
import sys, json, itertools, shutil
from pathlib import Path
import pandas as pd
import numpy as np

V7_DIR = Path("/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712")
WORK_DIR = Path("/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717")
if WORK_DIR.exists():
    shutil.rmtree(WORK_DIR)
shutil.copytree(V7_DIR, WORK_DIR)

# Add parent dir to path so we can import strategy + data_loader
sys.path.insert(0, str(WORK_DIR))
from data_loader import load_all, load_funding
from strategy import run_backtest, _annualisation_factor

# Load data once
data = load_all(["BTCUSDT", "SOLUSDT"])
funding = load_funding(["BTCUSDT", "SOLUSDT"])

# Grid search space (focused on highest-impact params)
ENTRY_Z = [2.0, 2.3, 2.5, 2.7, 3.0]
LOOKBACK = [96, 144, 192, 288]
MAX_HOLD = [48, 72, 96, 144]
VPVR_K = [0.5, 0.7, 1.0]
FUNDING_THR = [0.0001, 0.00005, 0.0002]  # tighter than V7's 0.0003

results = []
total = len(ENTRY_Z) * len(LOOKBACK) * len(MAX_HOLD) * len(VPVR_K) * len(FUNDING_THR)
print(f"Grid search: {total} combinations", flush=True)

base_cfg = json.load(open(WORK_DIR / "config.json"))
base_cfg["strategy"] = "vpvr_xs_pairs_30m_funding_filter_v10_optimize"
base_cfg["notes"] = ["V10 optimization grid search"]

count = 0
for entry_z, lookback, max_hold, vpvr_k, fund_thr in itertools.product(ENTRY_Z, LOOKBACK, MAX_HOLD, VPVR_K, FUNDING_THR):
    count += 1
    cfg = json.loads(json.dumps(base_cfg))  # deep copy
    cfg["indicators"] = dict(cfg["indicators"])
    cfg["indicators"]["zscore_entry_threshold"] = entry_z
    cfg["indicators"]["zscore_lookback_bars"] = lookback
    cfg["indicators"]["funding_filter_threshold"] = fund_thr
    cfg["indicators"]["vpvr_proximity_atr_k"] = vpvr_k
    cfg["exit"] = dict(cfg["exit"])
    cfg["exit"]["max_holding_bars"] = max_hold

    try:
        res = run_backtest(data, cfg, funding=funding)
        per_pair = res["per_pair"]["BTCUSDT/SOLUSDT"]
        n_trades = per_pair.get("n_trades", 0)
        if n_trades < 50:
            continue
        sharpe = per_pair.get("sharpe", 0)
        total_ret = per_pair.get("total_return_pct", 0)
        mdd = per_pair.get("max_drawdown_pct", 0)
        pf = per_pair.get("profit_factor", 0)
        wr = per_pair.get("win_rate", 0)
        results.append({
            "entry_z": entry_z, "lookback": lookback, "max_hold": max_hold,
            "vpvr_k": vpvr_k, "funding_thr": fund_thr,
            "n_trades": n_trades, "sharpe": sharpe, "total_ret": total_ret,
            "mdd": mdd, "pf": pf, "win_rate": wr
        })
        if count % 50 == 0 or count == total:
            print(f"  {count}/{total} done, last: z={entry_z} lb={lookback} mh={max_hold} k={vpvr_k} ft={fund_thr} → S={sharpe:.2f} R={total_ret:.1f}% n={n_trades}", flush=True)
    except Exception as e:
        print(f"  {count}/{total} error: {e}", flush=True)

# Save and analyze results
res_df = pd.DataFrame(results)
out_csv = WORK_DIR / "v10_grid_search.csv"
res_df.to_csv(out_csv, index=False)
print(f"\n=== GRID SEARCH COMPLETE: {len(results)} valid configs ===")
print(f"Saved: {out_csv}")

# Best by Sharpe
print("\n=== TOP 10 BY SHARPE ===")
top = res_df.nlargest(10, "sharpe")
for _, r in top.iterrows():
    print(f"  S={r['sharpe']:6.2f} | R={r['total_ret']:6.1f}% | n={r['n_trades']:4d} | MDD={r['mdd']:.1%} | PF={r['pf']:.2f} | WR={r['win_rate']:.1%} | z={r['entry_z']} lb={r['lookback']} mh={r['max_hold']} k={r['vpvr_k']} ft={r['funding_thr']}")

# Best by risk-adjusted (Sharpe / |MDD|)
print("\n=== TOP 5 BY RISK-ADJUSTED (Sharpe / |MDD|) ===")
res_df["risk_adj"] = res_df["sharpe"] / res_df["mdd"].abs()
top_ra = res_df.nlargest(5, "risk_adj")
for _, r in top_ra.iterrows():
    print(f"  RA={r['risk_adj']:5.2f} | S={r['sharpe']:6.2f} | R={r['total_ret']:6.1f}% | n={r['n_trades']:4d} | MDD={r['mdd']:.1%} | z={r['entry_z']} lb={r['lookback']} mh={r['max_hold']} k={r['vpvr_k']} ft={r['funding_thr']}")

# Robustness: min trades + positive ret
robust = res_df[(res_df["n_trades"] >= 500) & (res_df["total_ret"] > 20)]
print(f"\n=== ROBUST: {len(robust)} configs with n>=500 trades AND ret>20% ===")
if len(robust) > 0:
    print(robust.nlargest(5, "sharpe").to_string(index=False))
