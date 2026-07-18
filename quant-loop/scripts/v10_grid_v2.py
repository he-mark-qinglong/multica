"""V10 optimization: focused grid search (60 combinations)."""
import sys, json, itertools, shutil
from pathlib import Path
import pandas as pd
import numpy as np

V7_DIR = Path("/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712")
WORK_DIR = Path("/home/smark/multica/quant-loop/strategies/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717")
if WORK_DIR.exists():
    shutil.rmtree(WORK_DIR)
shutil.copytree(V7_DIR, WORK_DIR)

# Patch for pandas compat
sp = WORK_DIR / "strategy.py"
c = open(sp).read()
c = c.replace('return allow, ema_bars.rename("funding_8h_ema")', 'ema_bars.name = "funding_8h_ema"\n    return allow, ema_bars')
open(sp, "w").write(c)

sys.path.insert(0, str(WORK_DIR))
from data_loader import load_all, load_funding
from strategy import run_backtest
from run_backtest import _summarise_pair

data = load_all(["BTCUSDT", "SOLUSDT"])
funding = load_funding(["BTCUSDT", "SOLUSDT"])

# Focused grid (24 combos) — focus on highest-impact params
ENTRY_Z = [2.3, 2.5, 3.0]
LOOKBACK = [96, 144]
MAX_HOLD = [48, 96]
FUNDING_THR = [0.0001, 0.00005]  # tighter than V7's 0.0003

# = 3*2*2*2 = 24 combos

results = []
total = len(ENTRY_Z) * len(LOOKBACK) * len(MAX_HOLD) * len(FUNDING_THR)
print(f"Grid: {total} combos", flush=True)

base_cfg = json.load(open(WORK_DIR / "config.json"))
base_cfg["strategy"] = "vpvr_xs_pairs_30m_funding_filter_v10_optimize"

count = 0
for entry_z, lookback, max_hold, fund_thr in itertools.product(ENTRY_Z, LOOKBACK, MAX_HOLD, FUNDING_THR):
    count += 1
    cfg = json.loads(json.dumps(base_cfg))
    cfg["indicators"] = dict(cfg["indicators"])
    cfg["indicators"]["zscore_entry_threshold"] = entry_z
    cfg["indicators"]["zscore_lookback_bars"] = lookback
    cfg["indicators"]["funding_filter_threshold"] = fund_thr
    cfg["exit"] = dict(cfg["exit"])
    cfg["exit"]["max_holding_bars"] = max_hold

    try:
        res = run_backtest(data, cfg, funding=funding)
        per_pair = res["per_pair"]
        if isinstance(per_pair, list):
            per_pair = per_pair[0] if per_pair else {}
        m = _summarise_pair(per_pair, cfg)
        n_trades = m["n_trades"]
        if n_trades < 50:
            continue
        results.append({
            "entry_z": entry_z, "lookback": lookback, "max_hold": max_hold,
            "fund_thr": fund_thr,
            "n_trades": n_trades, "sharpe": m["sharpe"], "total_ret": m["total_return_pct"],
            "mdd": m["max_drawdown_pct"], "pf": m["profit_factor"], "win_rate": m["win_rate"]
        })
        if count % 5 == 0:
            print(f"  {count}/{total} done", flush=True)
    except Exception as e:
        # print(f"err: {e}")
        pass

res_df = pd.DataFrame(results)
out_csv = WORK_DIR / "v10_grid_search.csv"
res_df.to_csv(out_csv, index=False)
print(f"\n=== COMPLETE: {len(results)} valid configs ===")
print(f"Saved: {out_csv}")

if len(res_df) > 0:
    print("\n=== TOP 8 BY SHARPE (n>=300 trades) ===")
    top = res_df[res_df["n_trades"] >= 300].nlargest(8, "sharpe")
    for _, r in top.iterrows():
        print(f"  S={r['sharpe']:6.2f} | R={r['total_ret']:6.1%} | n={int(r['n_trades']):4d} | MDD={r['mdd']:.1%} | PF={r['pf']:.2f} | WR={r['win_rate']:.1%} | z={r['entry_z']} lb={r['lookback']} mh={r['max_hold']} ft={r['fund_thr']}")

    print("\n=== TOP 5 BY SHARPE / |MDD| (risk-adj) ===")
    res_df["risk_adj"] = res_df["sharpe"] / res_df["mdd"].abs()
    top_ra = res_df[res_df["n_trades"] >= 300].nlargest(5, "risk_adj")
    for _, r in top_ra.iterrows():
        print(f"  RA={r['risk_adj']:5.2f} | S={r['sharpe']:6.2f} | R={r['total_ret']:6.1%} | n={int(r['n_trades']):4d} | z={r['entry_z']} lb={r['lookback']} mh={r['max_hold']} ft={r['fund_thr']}")

    # Save best config
    best = top.iloc[0]
    best_cfg = json.loads(json.dumps(base_cfg))
    best_cfg["indicators"] = dict(best_cfg["indicators"])
    best_cfg["indicators"]["zscore_entry_threshold"] = float(best["entry_z"])
    best_cfg["indicators"]["zscore_lookback_bars"] = int(best["lookback"])
    best_cfg["indicators"]["funding_filter_threshold"] = float(best["fund_thr"])
    best_cfg["exit"] = dict(best_cfg["exit"])
    best_cfg["exit"]["max_holding_bars"] = int(best["max_hold"])
    best_cfg["notes"] = [f"V10 optimized. Sharpe={best['sharpe']:.2f}, ret={best['total_ret']:.1f}%, MDD={best['mdd']:.1%}, n={int(best['n_trades'])}"]
    json.dump(best_cfg, open(WORK_DIR / "config.json", "w"), indent=2)
    print(f"\n✅ Best config saved: z={best['entry_z']} lb={best['lookback']} mh={best['max_hold']} ft={best['fund_thr']}")
    print(f"   Sharpe={best['sharpe']:.2f}, ret={best['total_ret']:.1f}%, MDD={best['mdd']:.1%}")
