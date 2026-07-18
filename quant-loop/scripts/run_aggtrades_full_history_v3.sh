#!/bin/bash
# v3: Resume full-history aggTrades backfill to match the kline windows, then
# verify + post the final report to SMA-35007 / SMA-34992 (finalize epilogue).
#  - BTCUSDT from 2019-09-01, ETHUSDT from 2019-11-01, SOLUSDT from 2020-09-01
#    (true perp_1m coverage: data starts at each symbol's perp listing).
#  - BNB/DOGE/AVAX/LINK from 2022-01-01 (their perp_30m coverage start).
#  - Recent window 2026-04..2026-07 already present for all 7 (90-day pull).
# Idempotent per month-partition; skips a leg if / free space < 15GB.
# Safe to re-run: completed partitions are skipped, .tmp files are cleaned.
set -u
LOG=/home/smark/multica/quant-loop/data/trades/backfill_run.log
PY=/home/smark/multica/quant-loop/scripts/backfill_aggtrades_vision.py
FINALIZE=/home/smark/multica/quant-loop/scripts/finalize_aggtrades_report.py
OUT=/home/smark/multica/quant-loop/data/trades
END=2026-07-18
ALL=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT

# Clean stale temp partitions from previously killed runs.
find "$OUT" -name '*.tmp' -delete 2>/dev/null

run_sym() {
  local sym=$1 start=$2
  local free_gb
  free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  if [ "$free_gb" -lt 15 ]; then
    echo "[$(date -u +%FT%T)] ABORT: only ${free_gb}GB free on /, skipping $sym leg" >> "$LOG"
    return 3
  fi
  echo "[$(date -u +%FT%T)] === v3 leg $sym start=$start (free ${free_gb}GB) ===" >> "$LOG"
  python3 "$PY" --symbols "$sym" --start "$start" --end "$END" --out-dir "$OUT" >> "$LOG" 2>&1
  local rc=$?
  echo "[$(date -u +%FT%T)] $sym leg finished rc=$rc" >> "$LOG"
  return $rc
}

run_sym BTCUSDT  2019-09-01
run_sym ETHUSDT  2019-11-01
run_sym SOLUSDT  2020-09-01
run_sym BNBUSDT  2022-01-01
run_sym DOGEUSDT 2022-01-01
run_sym AVAXUSDT 2022-01-01
run_sym LINKUSDT 2022-01-01

# Canonical verify across all 7 symbols regardless of per-leg outcomes.
python3 "$PY" --symbols "$ALL" --start 2019-09-01 --end "$END" --out-dir "$OUT" --verify-only >> "$LOG" 2>&1
echo "[$(date -u +%FT%T)] V3 FULL HISTORY WRAPPER DONE" >> "$LOG"

# Post the final report comments (idempotent) and flip status if complete.
python3 "$FINALIZE" >> "$LOG" 2>&1
echo "[$(date -u +%FT%T)] finalize rc=$?" >> "$LOG"
