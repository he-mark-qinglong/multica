#!/usr/bin/env bash
# quant_backtest_cleanup.sh — prune old backtest logs / temp artifacts
#
# DRY-RUN BY DEFAULT — prints what would be deleted, deletes nothing.
# Pass --apply to actually remove files.
#
# Safety: only matches log/temp patterns. Result/data files
# (.parquet .csv .json .txt .feather .sha256) are NEVER matched.
#
# Flags:
#   --apply      really delete (default: dry-run)
#   --days N     prune files older than N days (default: 30; 0 = any age)
#   --dir PATH   root to prune (default: ~/multica/quant-loop/backtests)
#
# Cron example (weekly dry-run report; add --apply once reviewed):
#   17 9 * * 1 /home/smark/multica/scripts/quant_backtest_cleanup.sh
set -euo pipefail

TARGET_DIR="${HOME}/multica/quant-loop/backtests"
DAYS=30
APPLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --days)  DAYS="$2"; shift 2 ;;
    --dir)   TARGET_DIR="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

[[ -d "$TARGET_DIR" ]] || { echo "ERROR: $TARGET_DIR does not exist" >&2; exit 1; }

# Candidate patterns: logs and temp/backup junk only. Never data/results.
find_candidates() {
  find "$TARGET_DIR" \
    \( -path '*/__pycache__/*' -o -path '*/.pytest_cache/*' \) -prune -o \
    -type f -mtime +"$DAYS" \
    \( -name '*.log' -o -name '*.log.*' -o -name '*.tmp' -o -name '*.temp' \
       -o -name '*.bak' -o -name '*.old' -o -name '*.swp' \
       -o -name 'nohup.out' -o -name 'core' \) \
    -print
}

MODE="DRY-RUN"
[[ "$APPLY" -eq 1 ]] && MODE="APPLY"

echo "=== quant_backtest_cleanup ($MODE) ==="
echo "dir:          $TARGET_DIR"
echo "older than:   $DAYS day(s)"
echo "patterns:     *.log *.log.* *.tmp *.temp *.bak *.old *.swp nohup.out core"
echo "never touches: .parquet .csv .json .txt .feather .sha256 (not in pattern list)"
echo

CANDIDATES="$(find_candidates)"

if [[ -z "$CANDIDATES" ]]; then
  echo "candidates: 0 — nothing to prune"
  exit 0
fi

COUNT=0
TOTAL_KB=0
echo "candidates:"
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  SIZE_KB="$(du -k "$f" | cut -f1)"
  MTIME="$(date -u -d @"$(stat -c %Y "$f")" +%Y-%m-%d 2>/dev/null || stat -c %y "$f" | cut -d' ' -f1)"
  printf '  %8d KB  %s  %s\n' "$SIZE_KB" "$MTIME" "$f"
  COUNT=$((COUNT + 1))
  TOTAL_KB=$((TOTAL_KB + SIZE_KB))
done <<< "$CANDIDATES"

echo
echo "total: $COUNT file(s), $((TOTAL_KB / 1024)) MB reclaimable"

if [[ "$APPLY" -eq 1 ]]; then
  echo
  echo "deleting..."
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    rm -v -- "$f"
  done <<< "$CANDIDATES"
  echo "done: $COUNT file(s) removed"
else
  echo
  echo "dry-run only — re-run with --apply to delete"
fi
