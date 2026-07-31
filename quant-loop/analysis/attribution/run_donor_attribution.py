"""Evidence run for Research #87 / T14 — performance attribution donors.

Donor 1: mtf_xs_pairs H3 BTC/SOL trades_all.csv (44,845 pair trades) —
         the family sealed 2026-07-26 on cost. Attribution must reproduce
         the seal mechanically (gates A3/A5/A6).
Donor 2: trend_multi_tf BTC (245 single trades, ledger Sharpe -3.63) —
         mechanism-kill example (gate A5).
"""
import json
import sys
from pathlib import Path

import pandas as pd

QL = Path.home() / "multica/quant-loop"
sys.path.insert(0, str(QL / "_shared/attribution"))

from decompose import CostSpec, attribute, normalize_trades, write_report  # noqa: E402

OUT = QL / "analysis/attribution"
OUT.mkdir(parents=True, exist_ok=True)

# --- Donor 1: H3 pair ledger -------------------------------------------------
h3_path = QL / "strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/trades_all.csv"
h3_raw = pd.read_csv(h3_path)
h3 = normalize_trades(h3_raw)

# Gate A3: reconstructed net at config cost must reproduce ledger sum(pnl_pct).
config = CostSpec(1, 1, fills_per_round_trip=4)  # 8bp RT pair, as coded in the strategy
ledger_net_sum = float(h3_raw["pnl_pct"].sum())
recon_net_sum = float(h3["gross_ret"].sum()) - len(h3) * config.cost_frac
a3_abs_err = abs(recon_net_sum - ledger_net_sum)
print(f"A3: ledger_sum={ledger_net_sum:.12f} recon={recon_net_sum:.12f} abs_err={a3_abs_err:.3e}")
assert a3_abs_err < 1e-9, "A3 reproduction gate FAILED"

h3_report = attribute(
    h3,
    [
        ("config_2bp_side_8bpRT", config),
        ("t10_maker2_taker5", CostSpec(2, 5, fills_per_round_trip=4)),
        ("ratified_11bp_side_44bpRT", CostSpec(4, 7, fills_per_round_trip=4)),
        ("curator_24bpRT", CostSpec(6, 6, fills_per_round_trip=4)),
        ("curator_60bpRT", CostSpec(15, 15, fills_per_round_trip=4)),
    ],
    reference="ratified_11bp_side_44bpRT",
)
h3_report["gate_a3"] = {
    "ledger_net_sum": ledger_net_sum,
    "reconstructed_net_sum_at_config_cost": recon_net_sum,
    "abs_err": a3_abs_err,
    "pass": a3_abs_err < 1e-9,
}
write_report(h3_report, str(OUT / "mtf_h3_btcsol_attribution.json"))

print("\n=== H3 scenarios ===")
for s in h3_report["scenarios"]:
    print(
        f"{s['scenario']:32s} gross={s['gross_sum']:9.4f} cost={s['cost_sum']:8.4f} "
        f"net={s['net_sum']:9.4f} drag={s['cost_drag_ratio'] if s['cost_drag_ratio'] is not None else float('nan'):7.3f} "
        f"sharpe_net={s['daily_sharpe_net']:7.3f} be_bps_side={s['break_even_bps_per_side']:6.2f} {s['verdict']}"
    )
print("\n=== H3 cuts (ratified) ===")
for cut, table in h3_report["cuts"].items():
    print(f"-- {cut}")
    for k, v in table.items():
        print(f"   {k:24s} n={v['n_trades']:6d} gross={v['gross_sum']:9.4f} net={v['net_sum']:9.4f} mean_net_bp={v['mean_net_bp']:8.3f} wr={v['win_rate_net']:.3f}")

# --- Donor 2: trend_multi BTC single ledger ----------------------------------
tm_path = QL / "strategies/trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/results/trades_BTCUSDT.csv"
tm = normalize_trades(pd.read_csv(tm_path))
tm_report = attribute(
    tm,
    [
        ("zero_cost", CostSpec(0, 0, fills_per_round_trip=2)),
        ("ratified_11bp_side_22bpRT", CostSpec(4, 7, fills_per_round_trip=2)),
    ],
    reference="ratified_11bp_side_22bpRT",
)
write_report(tm_report, str(OUT / "trend_multi_btc_attribution.json"))

print("\n=== trend_multi BTC scenarios ===")
for s in tm_report["scenarios"]:
    print(
        f"{s['scenario']:32s} gross={s['gross_sum']:9.4f} cost={s['cost_sum']:8.4f} "
        f"net={s['net_sum']:9.4f} sharpe_net={s['daily_sharpe_net']:7.3f} {s['verdict']}"
    )

# --- Gate A5/A6 summary -------------------------------------------------------
h3_verdicts = {s["scenario"]: s["verdict"] for s in h3_report["scenarios"]}
a5_h3 = h3_verdicts["ratified_11bp_side_44bpRT"] == "COST_CAP_KILL"
a5_tm = tm_report["scenarios"][1]["verdict"] == "MECHANISM_KILL"
a6 = (
    h3_report["scenarios"][0]["net_sum"] > 0
    and h3_report["scenarios"][2]["net_sum"] < 0
)
print(f"\nA5 (H3 ratified = COST_CAP_KILL): {'PASS' if a5_h3 else 'FAIL'}")
print(f"A5 (trend_multi = MECHANISM_KILL): {'PASS' if a5_tm else 'FAIL'}")
print(f"A6 (H3 sign flip config>0, ratified<0): {'PASS' if a6 else 'FAIL — see finding'}")
if not a6:
    print(
        "A6 FINDING: H3 ledger is net-NEGATIVE even at its own config cost "
        f"(8bp RT): net_sum={h3_report['scenarios'][0]['net_sum']:.4f}. "
        "The 'PROFITABLE' tag in results/summary.json was computed on a "
        "cost-FREE per-bar equity path (mtf_xs_pairs_base_20260718.py:560 "
        "pnl_per_bar has no cost term; cost exists only in the trade log, "
        "line 576) — the SMA-36566 fee-shock bug class. Attribution "
        "reconstruction surfaces this mechanically. Direction of the "
        "family seal (cost kill) is confirmed; break-even is "
        f"{h3_report['scenarios'][0]['break_even_bps_per_side']:.3f} bps/side "
        "in trade-log units (curator equity-path estimate: 20bps RT — "
        "different notional/compounding convention, see T14 thread)."
    )
    h3_report["gate_a6"] = {
        "pass": False,
        "finding": "net_sum negative even at config cost (8bp RT); "
        "summary.json PROFITABLE tag derived from cost-free per-bar equity path "
        "(SMA-36566 fee-shock bug class)",
    }
    write_report(h3_report, str(OUT / "mtf_h3_btcsol_attribution.json"))
assert a5_h3 and a5_tm, "donor classification gates FAILED"
print("\nA3 + A5 PASS; A6 pre-registered premise false (finding recorded).")
