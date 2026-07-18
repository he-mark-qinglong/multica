#!/usr/bin/env bash
# quant_disk_quota_alert.sh — disk quota guard for ~/multica/quant-loop/backtests
#
# Alerts (log line + comment on SMA-34918) when the filesystem hosting the
# backtests directory crosses the usage threshold (default 70%).
# A previous quota outage corrupted strategy data files; this is the
# preventive guard (SMA-34918).
#
# State-transition only: posts to the issue once on OK -> BREACH and once on
# BREACH -> OK (recovery), so a cron schedule never spams the issue.
#
# Cron example (every 30 min):
#   */30 * * * * /home/smark/multica/scripts/quant_disk_quota_alert.sh >/dev/null 2>&1
#
# Flags:
#   --dir PATH          directory to watch    (default: ~/multica/quant-loop/backtests)
#   --threshold PCT     alert when usage >= PCT (default: 70)
#   --issue ID          Multica issue id      (default: SMA-34918 uuid)
#   --simulate-use PCT  pretend usage is PCT; exercises the alert path without
#                       touching the state file or posting to the issue
#   --no-comment        log/print only, never post to the issue
set -euo pipefail

WATCH_DIR="${HOME}/multica/quant-loop/backtests"
THRESHOLD=70
ISSUE_ID="1c9d9fc0-c55e-4d60-89d7-88049e45e07d"   # SMA-34918
SIMULATE_USE=""
NO_COMMENT=0

LOG_DIR="${HOME}/multica/ops-reports"
LOG_FILE="${LOG_DIR}/disk-quota-alert.log"
STATE_FILE="${LOG_DIR}/.disk-quota-alert.state"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)          WATCH_DIR="$2"; shift 2 ;;
    --threshold)    THRESHOLD="$2"; shift 2 ;;
    --issue)        ISSUE_ID="$2";  shift 2 ;;
    --simulate-use) SIMULATE_USE="$2"; shift 2 ;;
    --no-comment)   NO_COMMENT=1; shift ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$LOG_DIR"

if [[ -n "$SIMULATE_USE" ]]; then
  USE_PCT="$SIMULATE_USE"
  SOURCE="simulated"
else
  [[ -d "$WATCH_DIR" ]] || { echo "ERROR: $WATCH_DIR does not exist" >&2; exit 1; }
  USE_PCT="$(df -P "$WATCH_DIR" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
  SOURCE="measured"
fi

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PREV_STATE="OK"
[[ -f "$STATE_FILE" ]] && PREV_STATE="$(cat "$STATE_FILE")"

log_line() {
  echo "$1" | tee -a "$LOG_FILE"
}

post_comment() {
  # $1 = comment body. Written to a temp file in cwd; the multica CLI
  # rejects --content-file paths outside its workdir (MUL-4252), so we
  # cd to $HOME first and use a ./ relative temp file.
  local body="$1" tmp
  tmp="$(cd "$HOME" && mktemp ./disk-quota-alert.XXXXXX.md)"
  printf '%s\n' "$body" > "$tmp"
  ( cd "$HOME" && multica issue comment add "$ISSUE_ID" --content-file "$tmp" )
  rm -f "${HOME}/${tmp#./}"
}

if [[ "$USE_PCT" -ge "$THRESHOLD" ]]; then
  NEW_STATE="BREACH"
  log_line "$TS ALERT use=${USE_PCT}% (${SOURCE}) >= threshold=${THRESHOLD}% dir=${WATCH_DIR} prev=${PREV_STATE}"
  if [[ -n "$SIMULATE_USE" ]]; then
    if [[ "$PREV_STATE" != "BREACH" ]]; then
      echo "--- would post breach comment to ${ISSUE_ID} (prev=${PREV_STATE}) ---"
      echo "[disk-quota-alert] ${WATCH_DIR} filesystem at ${USE_PCT}% (threshold ${THRESHOLD}%). Free space or run quant_backtest_cleanup.sh."
    else
      echo "--- already in BREACH state; would NOT re-post (dedup) ---"
    fi
  elif [[ "$PREV_STATE" != "BREACH" ]]; then
    echo "$NEW_STATE" > "$STATE_FILE"
    if [[ "$NO_COMMENT" -eq 0 ]]; then
      post_comment "[disk-quota-alert] \`${WATCH_DIR}\` filesystem is at **${USE_PCT}%**, crossing the ${THRESHOLD}% quota guard.

$(df -h "$WATCH_DIR")

Suggested action: free space or run \`$(dirname "$0")/quant_backtest_cleanup.sh\` (dry-run first, then \`--apply\`)."
    fi
  else
    echo "$NEW_STATE" > "$STATE_FILE"
  fi
else
  NEW_STATE="OK"
  log_line "$TS OK use=${USE_PCT}% (${SOURCE}) < threshold=${THRESHOLD}% dir=${WATCH_DIR} prev=${PREV_STATE}"
  if [[ -n "$SIMULATE_USE" ]]; then
    if [[ "$PREV_STATE" == "BREACH" ]]; then
      echo "--- would post recovery comment to ${ISSUE_ID} ---"
    else
      echo "--- no transition; no comment ---"
    fi
  elif [[ "$PREV_STATE" == "BREACH" ]]; then
    echo "$NEW_STATE" > "$STATE_FILE"
    if [[ "$NO_COMMENT" -eq 0 ]]; then
      post_comment "[disk-quota-alert] RECOVERED: \`${WATCH_DIR}\` filesystem back to **${USE_PCT}%** (below the ${THRESHOLD}% guard)."
    fi
  else
    echo "$NEW_STATE" > "$STATE_FILE"
  fi
fi
