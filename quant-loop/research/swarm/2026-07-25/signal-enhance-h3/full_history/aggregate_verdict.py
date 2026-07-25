"""Aggregate signal-enhance-h3 evidence into the W4-T15 verdict pack.

Inputs:
  - results/se_h3_wf_window_{0..6}.json   (7 WF windows)
  - results/se_h3_full_history_metrics.json
  - results/se_h3_fee_shock.json
  - SPEC_signal_enhance_h3_fullhist.md    (falsification conditions)
  - ../H3-baseline-repro/metrics.json     (baseline anchor)

Outputs:
  - results/se_h3_metrics.json            (aggregate metrics + gate mapping)
  - VERDICT.md                            (human-readable evidence pack)

Card reference: docs/plans/infra-sprint-2026-07-25/round2/w4-s5-window-exec-aggregate.md
(plan not present on disk; spec is mirrored inline in the W4-T15 issue description.)

Only the canonical gate module is imported; this script does not modify any
production / shared / pipeline code. Path manipulation is local to the script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

FH = Path(__file__).resolve().parent                    # .../signal-enhance-h3/full_history
QL = FH.parents[4]                                      # .../quant-loop

# Fee-shock methodology freeze (2026-07-26 orchestrator re-check, comment 36f3e053…).
# Set to False once SMA-36566 (issue 5645fc85-0d53-4c83-ac47-fd4451bcde69) lands and
# `se_h3_fee_shock.json` is regenerated with the corrected replay basis.
FEE_SHOCK_UNTRUSTED = True
FEE_SHOCK_FREEZE_REF = "SMA-36566"
RES = FH / "results"
BASELINE_PATH = QL / "research/swarm/2026-07-25/H3-baseline-repro/metrics.json"
SPEC_PATH = FH / "SPEC_signal_enhance_h3_fullhist.md"

sys.path.insert(0, str(QL / "_shared" / "gates"))
from enforce import certify_metrics  # noqa: E402  (intentional path-sys.path entry above)


# --- 1) 7 windows ---------------------------------------------------------
PER_WINDOW = []
for k in range(7):
    w = json.load(open(RES / f"se_h3_wf_window_{k}.json"))
    PER_WINDOW.append(w)

SHARPES = np.array([w["sharpe_daily_resampled"] for w in PER_WINDOW])
ANNS = np.array([w["annualized_return_daily_resampled"] for w in PER_WINDOW])
MDDS = np.array([w["max_drawdown_pct"] for w in PER_WINDOW])
PFS = np.array([w["profit_factor"] for w in PER_WINDOW])
N_TRADES_TOTAL = int(sum(w["n_trades"] for w in PER_WINDOW))

# Bootstrap CI — verbatim structure from H3-variants-h1h2h4/run_btcsol_variants_fixed.py:
# seed=42, resamples=10000, percentile method, sample size = len(sharpes).
rng = np.random.default_rng(42)
boot = np.empty(10000)
for i in range(10000):
    boot[i] = SHARPES[rng.integers(0, len(SHARPES), size=len(SHARPES))].mean()
CI_LO = float(np.percentile(boot, 2.5))
CI_HI = float(np.percentile(boot, 97.5))

OOS = {
    "n_windows": int(len(PER_WINDOW)),
    "per_window": PER_WINDOW,
    "oos_sharpe_mean_daily_resampled": float(np.mean(SHARPES)),
    "oos_annualized_mean_daily": float(np.mean(ANNS)),
    "oos_max_drawdown_worst_pct": float(np.min(MDDS)),
    "oos_profit_factor_mean": float(np.mean(np.where(np.isfinite(PFS), PFS, 0.0))),
    "bootstrap_ci_lower": CI_LO,
    "bootstrap_ci_upper": CI_HI,
    "bootstrap_seed": 42,
    "bootstrap_resamples": 10000,
    "n_trades_total": N_TRADES_TOTAL,
}

# --- 2) full history + fee shock (T07) ------------------------------------
FULL = json.load(open(RES / "se_h3_full_history_metrics.json"))
FEE = json.load(open(RES / "se_h3_fee_shock.json"))

# --- 3) gate mapping; G5/G7 deliberately absent → relabel NOT_RUN ---------
# Keys intentionally match enforce.py's .get() lookups
# (G1:sharpe_daily, G2:annualized_return, G3:max_drawdown_pct, G4:profit_factor,
#  G5:cpcv_mean_oos_sharpe, G6:bootstrap_ci95_lower, G7:deflated_sharpe, T1:n_trades).
GATE_INPUT = {
    "sharpe_daily": OOS["oos_sharpe_mean_daily_resampled"],
    "annualized_return": OOS["oos_annualized_mean_daily"],
    "max_drawdown_pct": OOS["oos_max_drawdown_worst_pct"],
    "profit_factor": OOS["oos_profit_factor_mean"],
    "bootstrap_ci95_lower": OOS["bootstrap_ci_lower"],
    "n_trades": N_TRADES_TOTAL,
}
res = certify_metrics(GATE_INPUT, strict=True)
gate_status = {g: ("FAIL" if g in res.failed_gates else "PASS")
               for g in ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "T1"]}
gate_status["G5"] = "NOT_RUN"
gate_status["G7"] = "NOT_RUN"

# --- 4) baseline reference -------------------------------------------------
BASELINE = json.load(open(BASELINE_PATH))

OUT = {
    "strategy": "signal-enhance-h3 full-history validation",
    "params": {
        "slope_lookback": 4,
        "slope_sign": "favorable",
        "adverse_stop_z": 0.7,
        "regime_break": 9.0,
        "z_entry": 2.5,
        "z_exit": 0.5,
        "max_hold": 240,
        "fee_bps_per_side": 1.0,
        "slip_bps_per_side": 1.0,
    },
    "oos": OOS,
    "full_history": FULL,
    "fee_sensitivity": FEE,
    "fee_sensitivity_untrusted": FEE_SHOCK_UNTRUSTED,
    "fee_sensitivity_freeze_note": (
        "Orchestrator re-check 2026-07-26T01:40+08 (issue comment 36f3e053…) "
        "found T07 fee-shock replay deducts cost at 0.5% nominal basis while "
        "the equity curve is full-nominal — the '60 bps Sharpe 10.41' headline "
        "is a unit-of-measurement artifact. Numbers preserved here for audit "
        "trail but flagged untrusted; do not propagate downstream until "
        f"{FEE_SHOCK_FREEZE_REF} lands. Re-run aggregate_verdict.py after the "
        "fix to auto-refresh."
    ),
    "pending_review": [FEE_SHOCK_FREEZE_REF],
    "gates": {
        "status": gate_status,
        "raw_reasons": res.reasons,
        "gate_input": GATE_INPUT,
        "note": (
            "G5/G7 NOT_RUN by design (CPCV + DSR live in downstream workstreams); "
            "raw enforce.py reasons preserved above for provenance."
        ),
    },
    "baseline_reference": {
        "walk_forward_oos": BASELINE["walk_forward_oos"],
        "full_history": BASELINE["full_history"],
        "fee_sensitivity": BASELINE["fee_sensitivity"],
    },
    "environment": {
        "windows_0_3": "mac pandas 2.2.3 (Python 3.10, NumPy 2.2.6)",
        "windows_4_6": "server-105 pandas 3.0.3 (Python 3.12, NumPy 2.4.6) — see per-window env.txt for windows 2,4,5,6",
        "mixed_env": (
            "7 windows were computed across two environments (Mac vs .105). "
            "All boundary assertions locked against H3-baseline-repro/metrics.json "
            "walk_forward_oos.per_window ISO timestamps PASSED on the executing host. "
            "Mean Sharpe is an arithmetic average of 7 per-window daily-resampled Sharpes "
            "(numerically identical across environments to the limits of float64)."
        ),
    },
    "spec_path": str(SPEC_PATH.relative_to(FH.parent.parent.parent.parent.parent)),
}

(RES / "se_h3_metrics.json").write_text(json.dumps(OUT, indent=2))


# --- 5) VERDICT.md (Markdown) ---------------------------------------------
def fmt(v, n=4):
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:.2f}"
        return f"{v:.{n}f}"
    return str(v)


baseline_oos = BASELINE["walk_forward_oos"]
baseline_full = BASELINE["full_history"]
baseline_fee = BASELINE["fee_sensitivity"]

md = []
md.append("# VERDICT — signal-enhance-h3 full-history validation (W4-T15, 2026-07-25)")
md.append("")
if FEE_SHOCK_UNTRUSTED:
    md.append("> ## ⚠️ FREEZE NOTICE — 2026-07-26 (orchestrator evidence review, comment 36f3e053…)")
    md.append(">")
    md.append("> **Section 3 (Fee-shock table) and SPEC falsification condition #3 are NOT")
    md.append("> currently trustworthy.** Orchestrator re-check found that the T07 fee-shock")
    md.append("> replay (`se_h3_fee_shock.json`) uses a 0.5% nominal per-trade cost basis")
    md.append("> while the equity curve is on the full-nominal basis, so the headline")
    md.append("> \"60 bps Sharpe still 10.41\" is a unit-of-measurement artifact rather than a")
    md.append(f"> real survival claim. Detailed repro and fix scope live in [{FEE_SHOCK_FREEZE_REF}](mention://issue/5645fc85-0d53-4c83-ac47-fd4451bcde69).")
    md.append(">")
    md.append("> **Consume rule (until " + FEE_SHOCK_FREEZE_REF + " lands):**")
    md.append("> 1. **Do not** hand the Section 3 numbers, the SPEC condition #3 verdict, or")
    md.append(">    the `fee_sensitivity` block of `se_h3_metrics.json` to the decision-maker.")
    md.append("> 2. The freeze is signalled in `se_h3_metrics.json` via")
    md.append(">    `fee_sensitivity_untrusted: true` and `pending_review: [\"" + FEE_SHOCK_FREEZE_REF + "\"]` —")
    md.append(">    machine-readable so downstream gates can refuse to consume them.")
    md.append("> 3. Sections 1, 2, 5, 7 (windows, aggregate, gates, raw enforce.py reasons)")
    md.append(">    are unaffected by this freeze — they aggregate over the per-window")
    md.append(">    Sharpe / n_trades / MDD / PF fields, none of which depend on the")
    md.append(">    fee-shock replay path.")
    md.append("> 4. When " + FEE_SHOCK_FREEZE_REF + " lands, re-run `aggregate_verdict.py` with the corrected")
    md.append(">    `se_h3_fee_shock.json` and the Section 3 / condition #3 verdict will be")
    md.append(">    auto-refreshed (deterministic — same code path, different input).")
    md.append("")
md.append("")
md.append(
    "**Evidence summary:** 7-window OOS mean daily-resampled Sharpe "
    f"**{OOS['oos_sharpe_mean_daily_resampled']:.4f}** vs H3 baseline "
    f"**{baseline_oos['oos_sharpe_mean_daily_resampled']:.4f}**; "
    f"bootstrap CI95 = **[{OOS['bootstrap_ci_lower']:.4f}, {OOS['bootstrap_ci_upper']:.4f}]** "
    f"(seed 42 / 10000); 60bps pair-RT fee-shock Sharpe "
    f"**{FEE['backtrader_60bps_rt']['sharpe_daily_resampled']:.4f}** vs baseline "
    f"**{baseline_fee['backtrader_60bps_rt']['sharpe_daily_resampled']:.4f}**; "
    "KEEP/KILL verdict is reserved for the research main line."
)
md.append("")
md.append("> KEEP/KILL verdict intentionally omitted (per task card §T15). "
          "The research main line owns the KEEP/KILL call against the SPEC's "
          "falsification conditions; this document only supplies the evidence.")
md.append("")
md.append("## 1. Per-window table (se_h3 locked enhancement vs H3 baseline)")
md.append("")
md.append("| win | test_start → test_end | se_h3 Sharpe | baseline Sharpe | se_h3 n_trades | baseline n_trades | se_h3 MDD | se_h3 PF |")
md.append("|---:|:---|---:|---:|---:|---:|---:|---:|")
baseline_per = {bw["window_id"]: bw for bw in baseline_oos["per_window"]}
for w in PER_WINDOW:
    k = w["window_id"]
    b = baseline_per.get(k, {})
    md.append(
        f"| {k} | {w['test_start_iso']} → {w['test_end_iso']} | "
        f"{w['sharpe_daily_resampled']:.4f} | "
        f"{b.get('sharpe_daily_resampled', float('nan')):.4f} | "
        f"{w['n_trades']} | {b.get('n_trades', '?')} | "
        f"{w['max_drawdown_pct']*100:.2f}% | {w['profit_factor']:.4f} |"
    )
md.append("")
md.append("## 2. Aggregate table")
md.append("")
md.append("| metric | se_h3 | H3 baseline |")
md.append("|:---|---:|---:|")
md.append(
    f"| OOS mean Sharpe (daily-resampled) | {OOS['oos_sharpe_mean_daily_resampled']:.4f} | "
    f"{baseline_oos['oos_sharpe_mean_daily_resampled']:.4f} |"
)
md.append(
    f"| OOS bootstrap CI95 lower | {OOS['bootstrap_ci_lower']:.4f} | "
    f"{baseline_oos['bootstrap_ci_lower']:.4f} |"
)
md.append(
    f"| OOS bootstrap CI95 upper | {OOS['bootstrap_ci_upper']:.4f} | "
    f"{baseline_oos['bootstrap_ci_upper']:.4f} |"
)
md.append(
    f"| OOS worst MDD | {OOS['oos_max_drawdown_worst_pct']*100:.2f}% | "
    f"{baseline_oos['oos_max_drawdown_worst_pct']*100:.2f}% |"
)
md.append(
    f"| OOS mean PF | {OOS['oos_profit_factor_mean']:.4f} | "
    f"{baseline_oos['oos_profit_factor_mean']:.4f} |"
)
md.append(
    f"| OOS mean annualized return | {OOS['oos_annualized_mean_daily']*100:.2f}% | "
    f"{baseline_oos['oos_annualized_mean_daily']*100:.2f}% |"
)
md.append(
    f"| total trades (7 windows) | {OOS['n_trades_total']} | "
    f"{sum(bw['n_trades'] for bw in baseline_oos['per_window'])} |"
)
md.append(
    f"| full-history Sharpe (daily-resampled) | {FULL['full_history']['sharpe_daily_resampled']:.4f} | "
    f"{baseline_full['sharpe_daily_resampled']:.4f} |"
)
md.append(
    f"| full-history MDD | {FULL['full_history']['max_drawdown_pct']*100:.2f}% | "
    f"{baseline_full['max_drawdown_pct']*100:.2f}% |"
)
md.append(
    f"| full-history PF | {FULL['full_history']['profit_factor_daily_method']:.4f} | "
    f"{baseline_full['profit_factor']:.4f} |"
)
md.append("")

md.append("## 3. Fee-shock table (pair round-trip)" + (" — ⚠️ DO NOT TRUST" if FEE_SHOCK_UNTRUSTED else ""))
md.append("")
if FEE_SHOCK_UNTRUSTED:
    md.append("> **Freeze in effect.** Numbers in this table come from "
              "`se_h3_fee_shock.json` (T07 artifact), which the orchestrator re-check "
              "(2026-07-26T01:40+08) flagged as a unit-of-measurement artifact: cost drag "
              "was deducted at a 0.5% nominal basis while the equity curve is on the full "
              f"nominal basis. The \"60 bps Sharpe 10.41\" headline is therefore not a real "
              f"survival claim. **Do not propagate these values downstream.** Fix scope: "
              f"[{FEE_SHOCK_FREEZE_REF}](mention://issue/5645fc85-0d53-4c83-ac47-fd4451bcde69).")
    md.append(">")
    md.append("> Reproduced here only so the audit trail is complete; please mark as "
              "`untrusted` if reading programmatically.")
    md.append("")
md.append("| cost tier | se_h3 Sharpe" + (" (UNTRUSTED)" if FEE_SHOCK_UNTRUSTED else "") +
          " | H3 baseline Sharpe" + (" (UNTRUSTED)" if FEE_SHOCK_UNTRUSTED else "") + " |")
md.append("|:---|---:|---:|")
for label, key in [
    ("inhouse 4 bps", "inhouse_4bps_rt"),
    ("freqtrade 24 bps", "freqtrade_24bps_rt"),
    ("backtrader 60 bps", "backtrader_60bps_rt"),
]:
    s = FEE[key]["sharpe_daily_resampled"]
    b = baseline_fee[key]["sharpe_daily_resampled"]
    md.append(f"| {label} | {s:.4f} | {b:.4f} |")
md.append("")

md.append("## 4. SPEC falsification conditions (verbatim from "
          "`SPEC_signal_enhance_h3_fullhist.md`)")
md.append("")
md.append("| # | condition (verbatim) | threshold | observed | verdict |")
md.append("|---:|:---|:---|---:|:---:|")
oos_mean = OOS["oos_sharpe_mean_daily_resampled"]
ci_lo = OOS["bootstrap_ci_lower"]
fee_60 = FEE["backtrader_60bps_rt"]["sharpe_daily_resampled"]
md.append(
    f"| 1 | 7 窗 OOS mean Sharpe（daily-resampled）< 1.0 | 1.0 | "
    f"{oos_mean:.4f} | "
    f"{'TRUE (KILL 证据)' if oos_mean < 1.0 else 'FALSE'} |"
)
md.append(
    f"| 2 | bootstrap CI lower（seed=42，resamples=10000）< 0.5 | 0.5 | "
    f"{ci_lo:.4f} | "
    f"{'TRUE (KILL 证据)' if ci_lo < 0.5 else 'FALSE'} |"
)
md.append(
    f"| 3 | 60 bps pair-RT fee-shock Sharpe ≤ 0 | 0 | " +
    (f"~~{fee_60:.4f}~~ (UNTRUSTED)" if FEE_SHOCK_UNTRUSTED else f"{fee_60:.4f}") + " | " +
    ("**PENDING** — freeze per Section 3 / " if FEE_SHOCK_UNTRUSTED else "") +
    (f"[{FEE_SHOCK_FREEZE_REF}](mention://issue/5645fc85-0d53-4c83-ac47-fd4451bcde69)" if FEE_SHOCK_UNTRUSTED else "") +
    (" |" if FEE_SHOCK_UNTRUSTED else f"{'TRUE (KILL 证据)' if fee_60 <= 0 else 'FALSE'} |")
)
md.append(
    "| 4 | parity 测试（T05/T06）不通过 | n/a | not applicable (T05/T06 already in_review per upstream) | n/a |"
)
md.append("")

md.append("## 5. Gate result (G1-G7 + T1)")
md.append("")
md.append("Run via `certify_metrics` imported from `_shared.gates.enforce`. "
          "G5/G7 are deliberately NOT_RUN by design (CPCV + DSR live in downstream "
          "workstreams); raw enforce.py reasons are preserved in `se_h3_metrics.json` "
          "for provenance.")
md.append("")
md.append("| gate | criterion | observed | status |")
md.append("|:---:|:---|---:|:---:|")
crit = {
    "G1": "Sharpe ≥ 1.0",
    "G2": "Annualized return ≥ 0.15",
    "G3": "Max drawdown > -25%",
    "G4": "Profit factor > 1.5",
    "G5": "CPCV mean OOS Sharpe ≥ 1.0",
    "G6": "Bootstrap CI95 lower ≥ 0.5",
    "G7": "Deflated Sharpe Ratio > 0",
    "T1": "n_trades ≥ 30",
}
for g in ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "T1"]:
    if g == "G1":
        observed = OOS["oos_sharpe_mean_daily_resampled"]
    elif g == "G2":
        observed = OOS["oos_annualized_mean_daily"]
    elif g == "G3":
        observed = OOS["oos_max_drawdown_worst_pct"]
    elif g == "G4":
        observed = OOS["oos_profit_factor_mean"]
    elif g == "G5":
        observed = "n/a"
    elif g == "G6":
        observed = OOS["bootstrap_ci_lower"]
    elif g == "G7":
        observed = "n/a"
    else:  # T1
        observed = N_TRADES_TOTAL
    md.append(f"| {g} | {crit[g]} | {observed} | **{gate_status[g]}** |")
md.append("")

md.append("## 6. Environment")
md.append("")
md.append(OUT["environment"]["mixed_env"])
md.append("")
md.append("- Windows 0-3: Mac, `/Users/mark/sdk/mamba-envs/trading/bin/python3` "
          "(pandas 2.2.3, NumPy 2.2.6).")
md.append("- Windows 4-6: server-105 (`smark@192.168.0.105`), `/usr/bin/python3` "
          "(pandas 3.0.3, NumPy 2.4.6, Python 3.12.3). "
          "Per-window `se_h3_wf_window_{2,4,5,6}.env.txt` carries "
          "script/common/loop/signals.py md5 + data file sizes for "
          "win6 × baseline md5 cross-validation, per task-card §0.1 risk mitigation.")
md.append("- Windows 0, 1, 3 do not have an `.env.txt` (Mac runs pre-dated the "
          "§0.1 env.txt protocol; the per-window boundary assertion `test_start_iso` "
          "still locked against `H3-baseline-repro/metrics.json walk_forward_oos.per_window` "
          "and PASSED on every window).")
md.append("")

md.append("## 7. Cross-cuts (raw enforce.py reasons)")
md.append("")
if res.reasons:
    md.append("```")
    for r in res.reasons:
        md.append(f"- {r}")
    md.append("```")
else:
    md.append("(no FAIL reasons returned by `certify_metrics` — pass through)")
md.append("")

md.append("## 8. KEEP/KILL verdict")
md.append("")
md.append("**Deferred to the research main line.** This evidence pack is "
          "deterministic aggregation only; it intentionally does not emit a "
          "KEEP/KILL call against the SPEC's falsification conditions.")
md.append("")

(FH / "VERDICT.md").write_text("\n".join(md))

# stdout summary
print("AGGREGATE_SHARPE", OOS["oos_sharpe_mean_daily_resampled"])
print("CI_LO", OOS["bootstrap_ci_lower"])
print("GATES", gate_status)
print("WROTE", RES / "se_h3_metrics.json")
print("WROTE", FH / "VERDICT.md")
