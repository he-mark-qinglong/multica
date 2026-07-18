#!/usr/bin/env python3
"""Finalize aggTrades backfill: render coverage report, post issue comments.

Reads the canonical verify report (fetch_report_aggtrades.json), checks each
symbol's coverage against the expected kline windows, renders a markdown
summary, and posts it to SMA-35007 (full report) and SMA-34992 (availability
notice + storage path). Flips SMA-35007 to in_review only when every symbol
covers its expected window.

Idempotent: skips posting if an identical final-report comment already exists.

Usage:
    python3 finalize_aggtrades_report.py            # render + post
    python3 finalize_aggtrades_report.py --dry-run  # render only, no posting
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

TRADES_DIR = Path("/home/smark/multica/quant-loop/data/trades")
REPORT_JSON = TRADES_DIR / "fetch_report_aggtrades.json"
REPORT_MD = TRADES_DIR / "aggtrades_final_report.md"

ISSUE_SMA35007 = "a6578fd2-8b8f-4552-8c5a-c78d3896e988"
ISSUE_SMA34992 = "2cc83173-9467-4ade-aac1-37f37b1234b2"
MARKER = "aggTrades backfill — final report"

# Expected coverage per symbol (inclusive start, required last day).
# BTC/ETH/SOL mirror the perp_1m windows, but note Binance only publishes
# futures aggTrades from 2020-01 (vision) — the REST API retains ~1 year —
# so the pre-2020 kline tail (BTC 2019-09-08+, ETH 2019-11-27+) is
# source-side unavailable and accepted as a documented gap.
EXPECTED = {
    "BTCUSDT": {"start_not_after": "2020-01-02", "note": "aggTrades from 2020-01 (1m starts 2019-09-08; pre-2020 not published)"},
    "ETHUSDT": {"start_not_after": "2020-01-02", "note": "aggTrades from 2020-01 (1m starts 2019-11-27; pre-2020 not published)"},
    "SOLUSDT": {"start_not_after": "2020-09-16", "note": "1m window from 2020-09-14"},
    "BNBUSDT": {"start_not_after": "2022-01-05", "note": "30m window from 2022-01"},
    "DOGEUSDT": {"start_not_after": "2022-01-05", "note": "30m window from 2022-01"},
    "AVAXUSDT": {"start_not_after": "2022-01-05", "note": "30m window from 2022-01"},
    "LINKUSDT": {"start_not_after": "2022-01-05", "note": "30m window from 2022-01"},
}
REQUIRED_LAST_DAY = "2026-07-17"  # klines end 2026-07-17/18 (END exclusive 2026-07-18)

SYMBOL_ORDER = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT"]


def run_cli(*args: str) -> tuple[int, str]:
    rc = subprocess.run(["multica", *args], capture_output=True, text=True)
    return rc.returncode, (rc.stdout or "") + (rc.stderr or "")


def symbol_status(sym: str, entry: dict | None) -> tuple[str, bool]:
    """(status string, complete?) for one symbol."""
    exp = EXPECTED[sym]
    if entry is None or entry.get("rows", 0) == 0:
        return "MISSING", False
    first = (entry.get("first_ts") or "")[:10]
    last = (entry.get("last_ts") or "")[:10]
    ok_first = first and first <= exp["start_not_after"]
    ok_last = last and last >= REQUIRED_LAST_DAY
    if ok_first and ok_last:
        return "COMPLETE", True
    gaps = []
    if not ok_first:
        gaps.append(f"starts {first}, expected ≤ {exp['start_not_after']} ({exp['note']})")
    if not ok_last:
        gaps.append(f"ends {last}, expected ≥ {REQUIRED_LAST_DAY}")
    return "PARTIAL (" + "; ".join(gaps) + ")", False


def render(report: dict) -> tuple[str, bool]:
    lines = []
    lines.append(f"[ops-worker-1 {MARKER} — {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC]")
    lines.append("")
    lines.append("Storage: `~/multica/quant-loop/data/trades/<SYMBOL>_aggtrades.parquet/` "
                 "(hive-partitioned `year=YYYY/month=M/data.parquet`; schema: `ts` ms-UTC, "
                 "`symbol`, `agg_id`, `price`, `qty`, `first_id`, `last_id`, `is_buyer_maker`). "
                 "Joinable with perp_1m/perp_30m/funding on symbol + ts.")
    lines.append("")
    lines.append("Note: Binance publishes futures aggTrades only from 2020-01 (vision archive; the REST "
                 "endpoint retains ~1 year). The BTCUSDT/ETHUSDT pre-2020 kline tail (2019-09 / 2019-11 → "
                 "2019-12) is source-side unavailable — raw (non-aggregated) trades for 2019 do exist on "
                 "vision if a reconstruction is ever wanted.")
    lines.append("")
    lines.append("| symbol | rows | first ts | last ts | size | partitions | status |")
    lines.append("|---|---:|---|---|---:|---:|---|")
    total_rows = 0
    all_complete = True
    for sym in SYMBOL_ORDER:
        entry = report.get(sym)
        status, complete = symbol_status(sym, entry)
        all_complete = all_complete and complete
        if entry is None:
            lines.append(f"| {sym} | 0 | — | — | 0 MB | 0 | {status} |")
            continue
        total_rows += entry["rows"]
        first = (entry.get("first_ts") or "—")[:23]
        last = (entry.get("last_ts") or "—")[:23]
        mb = entry.get("bytes", 0) / 1e6
        lines.append(
            f"| {sym} | {entry['rows']:,} | {first} | {last} | {mb:,.0f} MB "
            f"| {len(entry.get('partitions', {}))} | {status} |"
        )
    lines.append("")
    lines.append(f"**Total: {total_rows:,} rows.**")
    if all_complete:
        lines.append("All 7 symbols cover their expected kline windows through 2026-07-17. "
                     "aggTrades data gap closed; SMA-34992 tape-reading pipeline unblocked.")
    else:
        lines.append("⚠️ Coverage incomplete for one or more symbols (see status column). "
                     "Backfill is idempotent per month-partition — re-run "
                     "`scripts/run_aggtrades_full_history_v3.sh` after freeing disk to resume.")
    return "\n".join(lines), all_complete


def render_sma34992_notice(report: dict) -> str:
    lines = []
    lines.append(f"[ops-worker-1] aggTrades tape backfill finished — see "
                 f"[SMA-35007](mention://issue/{ISSUE_SMA35007}) for the full report.")
    lines.append("")
    lines.append("- Path: `~/multica/quant-loop/data/trades/<SYMBOL>_aggtrades.parquet/` "
                 "(hive-partitioned `year=YYYY/month=M/`, `pd.read_parquet` on the symbol dir reads all)")
    lines.append("- Schema: `ts` (ms, UTC), `symbol`, `agg_id`, `price`, `qty`, `first_id`, `last_id`, "
                 "`is_buyer_maker` (True ⇒ seller-initiated); raw ms timestamps preserved")
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        entry = report.get(sym) or {}
        first = (entry.get("first_ts") or "?")[:10]
        last = (entry.get("last_ts") or "?")[:10]
        rows = entry.get("rows", 0)
        lines.append(f"- {sym}: {rows:,} rows, {first} → {last}")
    lines.append("- 90-day window (2026-04-19 → 2026-07-17) additionally available for "
                 "BNB/DOGE/AVAX/LINK (plus any completed full-history months — see report).")
    return "\n".join(lines)


COMPLETE_MARKER = "aggTrades data gap closed"


def prior_posts(issue_id: str) -> tuple[bool, bool]:
    """(any final report posted before, a COMPLETE report posted before)."""
    rc, out = run_cli("issue", "comment", "list", issue_id, "--output", "json")
    if rc != 0:
        return False, False
    return MARKER in out, COMPLETE_MARKER in out


def post(issue_id: str, md_path: Path) -> bool:
    rc, out = run_cli("issue", "comment", "add", issue_id, "--content-file", str(md_path))
    if rc != 0:
        print(f"comment add failed on {issue_id}: {out}", file=sys.stderr)
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    report = json.loads(REPORT_JSON.read_text())
    body, all_complete = render(report)
    REPORT_MD.write_text(body + "\n")
    print(body)
    print(f"\nall_complete={all_complete}")
    if args.dry_run:
        return

    posted_any, posted_complete = prior_posts(ISSUE_SMA35007)
    # Post a partial report at most once; always post the completion report
    # (e.g. after the pre-2020 REST fill finishes following a partial post).
    if (all_complete and not posted_complete) or (not all_complete and not posted_any):
        if not post(ISSUE_SMA35007, REPORT_MD):
            sys.exit(1)
    else:
        print("SMA-35007 report already posted for this state, skipping")

    if all_complete and not posted_complete:
        notice_path = TRADES_DIR / "aggtrades_sma34992_notice.md"
        notice_path.write_text(render_sma34992_notice(report) + "\n")
        if not post(ISSUE_SMA34992, notice_path):
            sys.exit(1)

    if all_complete:
        rc, out = run_cli("issue", "status", ISSUE_SMA35007, "in_review")
        print(f"status in_review rc={rc} {out.strip()}")
    else:
        print("coverage incomplete — leaving SMA-35007 status unchanged")


if __name__ == "__main__":
    main()
