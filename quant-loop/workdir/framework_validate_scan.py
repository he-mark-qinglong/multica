#!/usr/bin/env python3
"""Scan quant-loop strategies for framework-validate eligibility.

Selection rules (per autopilot 51e7cb03):
  1. Pick strategies whose metrics.json tag/status indicates a terminal
     "done" outcome (PROFITABLE or NOT-PROFITABLE).
  2. Skip strategies that have been cross-validated by ALL 6 frameworks
     within the past 7 days.
  3. For each candidate, pick the next framework from rotating list:
        freqtrade → backtrader → vectorbt → jesse → nautilus_trader
        → zipline-reloaded
     that hasn't been recorded for the strategy.

The framework CV record lives in `results/framework_cv_<framework>.json`
(or for some old runs, a single `results/framework_cv.json`).
The framework adapter lives in `framework_adapter_<framework>.py`.
The CV timestamp is the mtime of the per-framework CV file.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STRATEGIES_ROOT = Path("/home/smark/quant-loop/strategies")
if not STRATEGIES_ROOT.is_dir():
    STRATEGIES_ROOT = Path("/home/smark/multica/quant-loop/strategies")

FRAMEWORK_ROTATION = [
    "freqtrade",
    "backtrader",
    "vectorbt",
    "jesse",
    "nautilus_trader",
    "zipline-reloaded",
]

FRAMEWORK_ALIAS = {
    "nautilus_trader": "nautilus_trader",
    "nautilus": "nautilus_trader",
    "zipline-reloaded": "zipline-reloaded",
    "zipline": "zipline-reloaded",
    "freqtrade": "freqtrade",
    "backtrader": "backtrader",
    "vectorbt": "vectorbt",
    "jesse": "jesse",
}


def normalize_framework(s: str) -> str | None:
    s = (s or "").strip().lower().replace(".json", "")
    return FRAMEWORK_ALIAS.get(s)


def discover_cv_records(strat_dir: Path) -> list[dict]:
    """Return list of CV records: {framework, ts, source}."""
    records: list[dict] = []

    # 1) Per-framework CV result files (results/ and data/ both)
    results_dir = strat_dir / "results"
    data_dir = strat_dir / "data"
    search_dirs = [d for d in (results_dir, data_dir) if d.is_dir()]
    for search_dir in search_dirs:
        for cv_file in sorted(search_dir.glob("framework_cv_*.json")):
            # framework_cv_freqtrade.json → "freqtrade"
            stem = cv_file.stem  # framework_cv_freqtrade
            fw_part = stem[len("framework_cv_"):]
            fw = normalize_framework(fw_part)
            if fw:
                records.append({
                    "framework": fw,
                    "ts": datetime.fromtimestamp(cv_file.stat().st_mtime, tz=timezone.utc),
                    "source": cv_file,
                })
        # 2) Single framework_cv.json (legacy) — try to infer engine
        consolidated = results_dir / "framework_cv.json"
        if consolidated.is_file():
            try:
                m = json.loads(consolidated.read_text())
                eng = m.get("engine") or m.get("framework_name") or ""
                fw = normalize_framework(eng)
                if fw and not any(r["framework"] == fw and r["source"] == consolidated
                                  for r in records):
                    records.append({
                        "framework": fw,
                        "ts": datetime.fromtimestamp(consolidated.stat().st_mtime, tz=timezone.utc),
                        "source": consolidated,
                    })
            except Exception:
                pass

    # 3) Adapter files (lightweight evidence — they exist even before a CV run finishes)
    for adapter in strat_dir.glob("framework_adapter_*.py"):
        fw = normalize_framework(adapter.stem[len("framework_adapter_"):])
        if fw and not any(r["framework"] == fw for r in records):
            records.append({
                "framework": fw,
                "ts": datetime.fromtimestamp(adapter.stat().st_mtime, tz=timezone.utc),
                "source": adapter,
                "note": "adapter-only",
            })
    return records


def scan() -> tuple[list[dict], datetime]:
    today = datetime.now(timezone.utc)
    cutoff = today - timedelta(days=7)
    rows: list[dict] = []
    for strat_dir in sorted(STRATEGIES_ROOT.iterdir()):
        if not strat_dir.is_dir() or strat_dir.name.startswith("_"):
            continue
        mfile = strat_dir / "results" / "metrics.json"
        if not mfile.is_file():
            continue
        try:
            m = json.loads(mfile.read_text())
        except Exception as e:
            print(f"[warn] {strat_dir.name}: bad metrics.json ({e})", file=sys.stderr)
            continue
        if not isinstance(m, dict):
            continue

        tag = m.get("tag") or m.get("status") or ""
        if not isinstance(tag, str):
            tag = str(tag)
        # Terminal states: PROFITABLE / NOT-PROFITABLE
        # Also treat "done" / explicit "FAIL_*" / "PASS_*" as terminal
        if tag not in {"PROFITABLE", "NOT-PROFITABLE"} and not (
            tag.startswith("FAIL_") or tag.startswith("PASS_") or tag == "done"
        ):
            continue

        cv_records = discover_cv_records(strat_dir)
        recent = [c for c in cv_records if c["ts"] >= cutoff]
        used = {c["framework"] for c in cv_records}

        rows.append({
            "strategy": strat_dir.name,
            "tag": tag,
            "metrics_path": mfile,
            "cv_records": cv_records,
            "recent_cv": recent,
            "used_frameworks": used,
        })
    return rows, cutoff


def choose_framework(used: set[str]) -> str | None:
    for fw in FRAMEWORK_ROTATION:
        if fw not in used:
            return fw
    return None


def main() -> int:
    rows, cutoff = scan()
    if not rows:
        print("No terminal-status strategies found.")
        return 1

    print(f"Found {len(rows)} terminal strategies (cutoff = {cutoff.isoformat()})")
    print(f"Rotation: {' → '.join(FRAMEWORK_ROTATION)}\n")

    eligible = []
    skipped_recent = []
    for r in rows:
        # If all 6 frameworks recently CV'd → skip
        if r["recent_cv"] and len({c["framework"] for c in r["recent_cv"]}) >= len(FRAMEWORK_ROTATION):
            r["verdict"] = "skip_recent_full_coverage"
            skipped_recent.append(r)
            continue
        fw = choose_framework(r["used_frameworks"])
        if not fw:
            # All 6 used; fall back to oldest in rotation
            fw = FRAMEWORK_ROTATION[
                len(r["cv_records"]) % len(FRAMEWORK_ROTATION)
            ]
            r["verdict"] = "eligible_replay_cycle"
        else:
            r["verdict"] = "eligible_new_framework"
        r["chosen_framework"] = fw
        eligible.append(r)

    # Priority: no recent CV first, then fewest CVs total
    eligible.sort(key=lambda r: (
        len(r["recent_cv"]),
        len(r["cv_records"]),
        r["strategy"],
    ))

    print("=" * 88)
    print(f"ELIGIBLE ({len(eligible)} strategies):")
    print("=" * 88)
    for r in eligible[:40]:
        recent_dates = ", ".join(c["ts"].date().isoformat() for c in r["recent_cv"]) or "<none>"
        used = ",".join(sorted(r["used_frameworks"])) or "<none>"
        print(
            f"  {r['strategy']:<58} tag={r['tag']:<15} "
            f"chosen={r['chosen_framework']:<17} "
            f"used=[{used}] recent=[{recent_dates}]"
        )

    if skipped_recent:
        print()
        print(f"SKIPPED — recent full coverage ({len(skipped_recent)}):")
        for r in skipped_recent[:10]:
            used = ",".join(sorted(r["used_frameworks"]))
            print(f"  {r['strategy']:<58} frameworks=[{used}]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
