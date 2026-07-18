#!/bin/bash
# v2: Resume full-history aggTrades backfill to match the 1m/30m kline windows.
#  - BTCUSDT from 2019-09-01, ETHUSDT from 2019-11-01, SOLUSDT from 2020-09-01
#    (true 1m kline coverage: data starts at each symbol's perp listing).
#  - BNB/DOGE/AVAX/LINK from 2022-01-01 (their kline coverage is 30m from 2022-01).
#  - Recent window 2026-04..2026-07 already present for all 7 (90-day pull).
# Idempotent per month-partition; aborts whole run if / free space < 15GB.
# Final step always rewrites the canonical 7-symbol verify report.
set -u
LOG=/home/smark/multica/quant-loop/data/trades/backfill_run.log
PY=/home/smark/multica/quant-loop/scripts/backfill_aggtrades_vision.py
OUT=/home/smark/multica/quant-loop/data/trades
END=2026-07-18
ALL=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT

run_sym() {
  local sym=$1 start=$2
  local free_gb
  free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  if [ "$free_gb" -lt 15 ]; then
    echo "[$(date -u +%FT%T)] ABORT: only ${free_gb}GB free on /, stopping before $sym" >> "$LOG"
    return 3
  fi
  echo "[$(date -u +%FT%T)] === v2 leg $sym start=$start (free ${free_gb}GB) ===" >> "$LOG"
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
echo "[$(date -u +%FT%T)] V2 FULL HISTORY WRAPPER DONE" >> "$LOG"
