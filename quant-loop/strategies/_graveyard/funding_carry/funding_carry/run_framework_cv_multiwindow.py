"""SMA-34947 framework-CV per-window wrapper.

Runs the existing backtrader-equivalent and freqtrade IStrategy
adapters against each per-window trades CSV under
``backtests/u5_funding_carry_eth_sol_1m/w<window>/``. The
adapter modules are imported, OUT_DIR/RESULTS_DIR are monkey-patched
to point at the per-window subdirectory, and the adapters'
``main(label_filter)`` is invoked.

Skips variants whose in-house Sharpe < 0.5 (mirrors the existing
adapter guard) so we don't waste cycles on variants the adapter is
designed to skip. Per-window outputs land under
``framework_cv/w<window>/`` so the existing 90-day pct_q05 result is
left untouched.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

QUANT_LOOP = Path("/home/smark/multica/quant-loop")
STRATEGY_DIR = QUANT_LOOP / "strategies" / "funding_carry"
sys.path.insert(0, str(STRATEGY_DIR))

OUT_BASE = QUANT_LOOP / "backtests" / "u5_funding_carry_eth_sol_1m"
WINDOWS = [30, 90, 365]

import framework_adapter_backtrader as fa_bt   # noqa: E402
import framework_adapter_freqtrade as fa_ft   # noqa: E402


def _run_adapter_for_window(adapter_module, framework_name: str,
                            window_days: int, label_filter: str) -> dict:
    """Run one adapter against one per-window out_dir.

    Returns the parsed output dict (or a synthetic stub if the
    adapter skipped every fold).
    """
    per_window_out = OUT_BASE / f"w{window_days}"
    per_window_results = per_window_out / "framework_cv"
    per_window_results.mkdir(parents=True, exist_ok=True)

    # Patch module-level paths so the adapter reads from / writes to
    # the per-window subdirectory.
    adapter_module.OUT_DIR = per_window_out
    adapter_module.RESULTS_DIR = per_window_results

    metrics_path = per_window_out / "u5_metrics.json"
    if not metrics_path.exists():
        return {"skipped": True, "reason": "no_metrics_json", "window_days": window_days,
                "framework": framework_name, "label": label_filter}
    ih = json.loads(metrics_path.read_text())

    # Find the best-Sharpe variant per window that passes the inhouse>=0.5 guard.
    # The adapter mains read sys.argv[1] for the label filter; provide it explicitly.
    print(f"\n[cv:{framework_name}] window={window_days}  label={label_filter}",
          flush=True)
    saved_argv = list(sys.argv)
    sys.argv = [adapter_module.__file__, label_filter]
    try:
        rc = adapter_module.main()
    finally:
        sys.argv = saved_argv
    print(f"[cv:{framework_name}] rc={rc}", flush=True)

    out_path = per_window_results / f"framework_cv_{framework_name}_{label_filter}.json"
    if not out_path.exists():
        return {"skipped": True, "reason": "no_output", "window_days": window_days,
                "framework": framework_name, "label": label_filter}
    return json.loads(out_path.read_text())


def main() -> int:
    print(f"[cv] starting at {datetime.now(timezone.utc).isoformat()}")
    combined: dict = {
        "issue": "SMA-34947",
        "variant_key": "funding_carry_u5_eth_sol_1m",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "windows": WINDOWS,
        "results": [],
    }
    for w in WINDOWS:
        for label_filter in ("pct_q05",):
            # backtrader-equivalent
            bt_res = _run_adapter_for_window(fa_bt, "backtrader", w, label_filter)
            combined["results"].append({"window_days": w, "label": label_filter,
                                        "framework": "backtrader", "result": bt_res})
            # freqtrade
            ft_res = _run_adapter_for_window(fa_ft, "freqtrade", w, label_filter)
            combined["results"].append({"window_days": w, "label": label_filter,
                                        "framework": "freqtrade", "result": ft_res})

    out_path = OUT_BASE / "framework_cv_multiwindow.json"
    out_path.write_text(json.dumps(combined, indent=2, default=str))
    print(f"\n[cv] wrote {out_path}")

    # Human-readable summary
    lines = [f"=== Framework-CV per-window ({combined['issue']}) ==="]
    lines.append(f"windows={WINDOWS}  label=pct_q05")
    lines.append("Adapter guards: only runs if inhouse_sharpe >= 0.5.")
    lines.append("")
    for w in WINDOWS:
        for fw in ("backtrader", "freqtrade"):
            r = next((x for x in combined["results"]
                      if x["window_days"] == w and x["framework"] == fw), None)
            if r is None or r["result"].get("skipped"):
                lines.append(f"  win={w}  fw={fw}: skipped ({r['result'].get('reason') if r else 'no row'})")
                continue
            folds = r["result"].get("folds", [])
            for f in folds:
                in_s = f.get("inhouse_sharpe")
                fw_s = f.get("framework_sharpe")
                div = f.get("max_abs_rel_divergence_pct")
                verdict = f.get("w5_verdict", "?")
                lines.append(
                    f"  win={w}  fw={fw}  sym={f.get('symbol')}  "
                    f"in_sharpe={in_s}  fw_sharpe={fw_s}  "
                    f"div%={div}  verdict={verdict}"
                )
    txt = "\n".join(lines) + "\n"
    (OUT_BASE / "framework_cv_multiwindow.txt").write_text(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())