"""Loop parity dual anchor for the SE-H3 backtest.

Card W4-T06 contract (intent):
  - backtest_pair_se(signals, pair, sizing_scale=None, fee_bps=1.0, slip_bps=1.0,
                     slope=<pd.Series|None>, adverse_stop_z=<float|None>,
                     regime_break=<float=3.0>) -> dict

T04 (se_h3_loop.py) actual shipped signature (cannot be modified per card directive):
  - backtest_pair_se(signals, pair, sizing_scale=None, fee_bps=1.0, slip_bps=1.0,
                     slope_sign=<str|None>="favorable", adverse_stop_z=<float|None>=0.7,
                     regime_break=<float|None>=None) -> dict

P3 mapping (semantic equivalence, this file adopts):
  Card "slope=<series>" + "favorable"  ==  T04 "slope_sign="favorable"" + signals["z_slope_fav_4"]
    The favorable-filter logic is identical (run_experiments.py L185-190 ==
    se_h3_loop.py L160-166).
  Card "slope=None"                    ==  T04 "slope_sign=None" (filter skipped at L158).
  Card "regime_break=3.0"              ==  T04 "regime_break=3.0" (None-default falls back
    to params.get("regime_break", 3.0); passing 3.0 explicitly is equivalent).
  Card signal key "z_slope_4"          ==  T03 (se_h3_signals.py SLOPE_KEY) "z_slope_fav_4"
    Both are the same z_15m zscore_slope(., 4); key rename avoids collision with the
    base H1 ADVERSE hook on "z_slope_15m".
  Card ref "net_pct"                   ==  T04 trade dict "pnl_pct" (same field, NET pnl).
  Card ref "backtest_variant(..., PARAMS)" with slope_filter+adverse_stop+regime_break
                                       ==  run_experiments.backtest_variant reproduces
    704 / Sharpe 8.0735 on the 2024 slice when fed the same data_loader_patch slice.

Anchors:
  (a) Filter-OFF  : backtest_pair_se(slope_sign=None, adverse_stop_z=None, regime_break=3.0)
                    bit-identical to base _backtest_pair on the SE-H3 2024 signals.
  (b) Filter-ON   : backtest_pair_se(slope_sign="favorable", adverse_stop_z=0.7,
                                       regime_break=9.0) reproduces
                    quick_verify slope_fav_4_stop_0_7 (704 trades, Sharpe 8.0735).

Failure dump path: FH/results/t06_loop_parity_failure.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# sys.path bootstrap (mirror se_h3_common + se_h3_loop convention).
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
SE_H3_DIR = HERE.parent                          # signal-enhance-h3/
VARIANTS_DIR = HERE.parent.parent / "H3-variants-h1h2h4"
QL_ROOT = HERE.parents[4]                       # full_history -> ... -> quant-loop
for p in (str(HERE), str(SE_H3_DIR), str(VARIANTS_DIR),
          str(QL_ROOT / "strategies"),
          str(QL_ROOT / "strategies" / "_indicators")):
    if p not in sys.path:
        sys.path.insert(0, p)

import data_loader_patch as dlp  # noqa: E402  (sys.path: SE_H3_DIR)
from run_experiments import (  # noqa: E402
    backtest_variant,
    enhance_signals,
    load_config,
)
import se_h3_common as C  # noqa: E402  (sys.path: HERE)
import se_h3_signals as S  # noqa: E402  (sys.path: HERE)
from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    _backtest_pair,
    sharpe_daily_resampled,
)
from se_h3_loop import backtest_pair_se  # noqa: E402  (sys.path: HERE)

PAIR = "BTCUSDT/SOLUSDT"
RESULTS_DIR = HERE / "results"
ANCHORS = ("slope_fav_4", "adverse_stop_z", "regime_break")
QUICK_VERIFY_ANCHOR_TRADES = 704
QUICK_VERIFY_ANCHOR_SHARPE = 8.0735


def _dump_failure(payload: dict) -> None:
    """Write diagnostic JSON to FH/results/ for any parity failure."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "t06_loop_parity_failure.json"
    out.write_text(json.dumps(payload, indent=2, default=str))
    print(f"[FAIL] diagnostic dumped to {out}")


def _slice_2024(d1m: dict, funding: dict) -> tuple[dict, dict]:
    """闭区间 mask 裁 2024（同 T05 步骤 3 用法）。"""
    mask_d = {sym: df.loc["2024-01-01":"2024-12-31"].copy() for sym, df in d1m.items()}
    mask_f = {sym: f.loc["2024-01-01":"2024-12-31"].copy() for sym, f in funding.items()}
    return mask_d, mask_f


def _normalize_ts(value) -> pd.Timestamp:
    return pd.Timestamp(value)


def _compare_trades(ref_trades: list, new_trades: list, atol_pnl: float,
                    label: str, ref_pnl_key: str = "pnl_pct",
                    new_pnl_key: str = "pnl_pct",
                    skip_entry_ts: bool = False) -> None:
    """Per-trade parity: count + entry_ts + exit_ts + direction + exit_reason + pnl/gross.

    skip_entry_ts=True: skip entry_ts comparison (use when the reference side has a
    known bug setting entry_ts=exit_ts, e.g. run_experiments.backtest_variant L251).
    """
    n_r, n_n = len(ref_trades), len(new_trades)
    if n_r == 0:
        raise AssertionError(f"{label}: reference produced 0 trades")
    if n_r != n_n:
        raise AssertionError(f"{label}: n_trades ref={n_r} new={n_n}")
    mismatches = []
    for k, (r, n) in enumerate(zip(ref_trades, new_trades)):
        try:
            if not skip_entry_ts:
                assert _normalize_ts(r["entry_ts"]) == _normalize_ts(n["entry_ts"]), \
                    f"trade {k} entry_ts {r['entry_ts']} vs {n['entry_ts']}"
            else:
                # Sanity: new entry_ts must be <= new exit_ts (proper hold >= 0).
                assert _normalize_ts(n["entry_ts"]) <= _normalize_ts(n["exit_ts"]), \
                    f"trade {k} new entry_ts > exit_ts (bad hold)"
            assert _normalize_ts(r["exit_ts"]) == _normalize_ts(n["exit_ts"]), \
                f"trade {k} exit_ts {r['exit_ts']} vs {n['exit_ts']}"
            assert r["direction"] == n["direction"], \
                f"trade {k} direction {r['direction']} vs {n['direction']}"
            assert r["exit_reason"] == n["exit_reason"], \
                f"trade {k} exit_reason {r['exit_reason']} vs {n['exit_reason']}"
            assert abs(r[ref_pnl_key] - n[new_pnl_key]) <= atol_pnl, \
                f"trade {k} net pnl ref={r[ref_pnl_key]} new={n[new_pnl_key]}"
            # gross_pct is only on the new side (base _backtest_pair has no gross_pct field).
            if "gross_pct" in r and "gross_pct" in n:
                assert abs(r["gross_pct"] - n["gross_pct"]) <= max(atol_pnl, 1e-12), \
                    f"trade {k} gross pnl ref={r['gross_pct']} new={n['gross_pct']}"
        except AssertionError as e:
            if len(mismatches) < 5:
                mismatches.append({"trade_index": k, "msg": str(e),
                                   "ref_value": {k2: r.get(k2) for k2 in
                                                 ("entry_ts", "exit_ts", "direction",
                                                  "exit_reason", ref_pnl_key, "gross_pct")
                                                 if k2 in r},
                                   "new_value": {k2: n.get(k2) for k2 in
                                                 ("entry_ts", "exit_ts", "direction",
                                                  "exit_reason", new_pnl_key, "gross_pct")
                                                 if k2 in n}})
            else:
                break
    if mismatches:
        raise AssertionError(f"{label}: {len(mismatches)}+ trade mismatches; first 5: {mismatches}")


def anchor_a() -> None:
    """(a) Filter-OFF: backtest_pair_se == base _backtest_pair on the SE-H3 2024 slice."""
    print("=== ANCHOR (a): filter-OFF == base engine ===")
    t0 = time.time()
    cfg = C.load_se_h3_config()
    d1m, funding, _ = C.load_aligned_data()
    d1m, funding = _slice_2024(d1m, funding)
    sigs = S.build_se_h3_signals(d1m, cfg, funding)
    sig = sigs[PAIR]

    # Base reference (T04 anchor (a) sample: filters OFF == base _backtest_pair).
    ref = _backtest_pair(sig, PAIR, sizing_scale=sig["size_scale"],
                         fee_bps=1.0, slip_bps=1.0)

    # New loop (P3 mapping: slope=<None> -> slope_sign=None).
    new = backtest_pair_se(sig, PAIR, sizing_scale=sig["size_scale"],
                           fee_bps=1.0, slip_bps=1.0,
                           slope_sign=None, adverse_stop_z=None, regime_break=3.0)

    _compare_trades(ref["trades"], new["trades"], atol_pnl=1e-15, label="anchor(a)",
                     ref_pnl_key="pnl_pct", new_pnl_key="pnl_pct")
    if not np.allclose(ref["bar_return"], new["bar_return"], atol=1e-15, rtol=0.0):
        raise AssertionError(
            f"anchor(a): bar_return mismatch max-diff="
            f"{float(np.max(np.abs(ref['bar_return'] - new['bar_return'])))}")
    print(f"LOOP PARITY (a) OK vs base engine ({len(new['trades'])} trades, "
          f"elapsed={time.time()-t0:.1f}s)")


def anchor_b() -> None:
    """(b) Filter-ON: backtest_pair_se reproduces quick_verify slope_fav_4_stop_0_7."""
    print("=== ANCHOR (b): filter-ON == quick_verify 704 / 8.0735 ===")
    t0 = time.time()
    cfg = load_config()

    # Reference side: quick_verify's own code path, fresh slice 2024 (per card: do not trust
    # the old json; regenerate in-process for cross-check).
    d1m_all = dlp.load_all()
    fund_all = dlp.load_funding()
    d1m_qv, fund_qv = dlp.slice_by_date(d1m_all, fund_all, "2024-01-01", "2024-12-31")
    sigs_qv = enhance_signals(d1m_qv, cfg, fund_qv)  # legacy: key "z_slope_4"
    sig_qv_ref = sigs_qv[PAIR]
    PARAMS_QV = {
        "slope_filter": {"lookback": 4, "sign": "favorable"},
        "adverse_stop_z": 0.7,
        "regime_break": 9.0,
    }  # quick_verify.py L42

    ref = backtest_variant(sigs_qv, cfg, PARAMS_QV)
    n_ref = len(ref["trades"])
    if n_ref != QUICK_VERIFY_ANCHOR_TRADES:
        raise AssertionError(
            f"anchor(b): reference (backtest_variant on quick_verify path) regenerated "
            f"n_trades={n_ref}, expected {QUICK_VERIFY_ANCHOR_TRADES} — quick_verify "
            f"reference path itself drifted; STOP, this is a data-layer issue not a T04 bug.")

    # New side: SE-H3 signals (key "z_slope_fav_4") + P3-mapped call.
    sigs_new = S.build_se_h3_signals(d1m_qv, cfg, fund_qv)
    sig_new = sigs_new[PAIR]
    new = backtest_pair_se(sig_new, PAIR, sizing_scale=sig_new["size_scale"],
                           fee_bps=1.0, slip_bps=1.0,
                           slope_sign="favorable",
                           adverse_stop_z=0.7,
                           regime_break=9.0)
    if len(new["trades"]) != QUICK_VERIFY_ANCHOR_TRADES:
        raise AssertionError(
            f"anchor(b): new loop n_trades={len(new['trades'])}, "
            f"expected {QUICK_VERIFY_ANCHOR_TRADES} (slope/adverse_stop hook logic suspect)")

    _compare_trades(ref["trades"], new["trades"], atol_pnl=1e-12, label="anchor(b)",
                     ref_pnl_key="net_pct", new_pnl_key="pnl_pct",
                     skip_entry_ts=True)  # ref entry_ts known-buggy (backtest_variant L251: common[i] instead of common[entry_idx])

    # Sharpe anchor (daily-resampled; base L760 sharpe_daily_resampled).
    sr = sharpe_daily_resampled(new["bar_return"], sig_new["a"].index)["sharpe_daily_resampled"]
    if abs(sr - QUICK_VERIFY_ANCHOR_SHARPE) > 1e-3:
        raise AssertionError(f"anchor(b): Sharpe {sr:.4f} off anchor {QUICK_VERIFY_ANCHOR_SHARPE}")
    print(f"LOOP PARITY (b) OK vs quick_verify ({len(new['trades'])} trades, "
          f"Sharpe={sr:.4f}, elapsed={time.time()-t0:.1f}s)")


def main() -> int:
    print(f"RUN_ID: {time.strftime('%Y-%m-%dT%H:%M:%S+08:00')}")
    print(f"WORKDIR: {HERE}")
    print(f"ANCHORS: {ANCHORS}")
    print(f"PAIR: {PAIR}")
    try:
        anchor_a()
        anchor_b()
    except AssertionError as e:
        _dump_failure({"error": str(e), "ts": time.time()})
        return 1
    print("LOOP PARITY DUAL ANCHOR PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())