#!/bin/bash
# Extend aggTrades backfill to full 1m-kline window [2020-08-11, 2026-07-18).
# Per-symbol invocations contain failures; script resumes completed partitions.
# Aborts if / free space drops below 15GB.
set -u
LOG=/home/smark/multica/quant-loop/data/trades/backfill_run.log
PY=/home/smark/multica/quant-loop/scripts/backfill_aggtrades_vision.py
OUT=/home/smark/multica/quant-loop/data/trades

run_sym() {
  local sym=$1 start=$2
  local free_gb
  free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  if [ "$free_gb" -lt 15 ]; then
    echo "[$(date -u +%FT%T)] ABORT: only ${free_gb}GB free on /, stopping before $sym" >> "$LOG"
    exit 3
  fi
  python3 "$PY" --symbols "$sym" --start "$start" --end 2026-07-18 --out-dir "$OUT" >> "$LOG" 2>&1
  rc=$?
  echo "[$(date -u +%FT%T)] $sym finished rc=$rc" >> "$LOG"
  return $rc
}

run_sym BTCUSDT  2020-08-11
run_sym ETHUSDT  2020-08-11
run_sym SOLUSDT  2020-09-01
run_sym BNBUSDT  2020-08-11
run_sym DOGEUSDT 2020-08-11
run_sym AVAXUSDT 2020-09-01
run_sym LINKUSDT 2020-08-11

echo "[$(date -u +%FT%T)] FULL HISTORY WRAPPER DONE" >> "$LOG"
