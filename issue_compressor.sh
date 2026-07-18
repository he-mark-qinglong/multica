#!/usr/bin/env bash
# issue_compressor.sh — Weekly L3 cron: auto-close stale [DEPLOY-FAIL] +
# [heartbeat-noop] / [heartbeat-alert] issues once the relevant endpoint is
# verified healthy.
#
# Spec: SMA-32071
#   - Schedule: weekly cron `0 3 * * 1` Asia/Shanghai (Monday 03:00)
#   - Tier:     L3 (heavy)
#   - Inputs:
#       1. [DEPLOY-FAIL] issues created in last 7d, status in (todo, in_progress)
#       2. [heartbeat-noop] / [heartbeat-alert] issues, status in_review, age > 1h
#   - Behaviour: probe relevant endpoint(s); on 200/307 close as `done` with audit
#     comment + parent dispatch link. Skip [need-smark-decision: ...] variants.
#   - Output: weekly comment on parent [SMA-30613] summarising N closed, M skipped.
#   - Safety: dry-run by default; require explicit --apply to mutate. Audit log
#     written to /var/log/multica/issue-compressor.log.
#
# Usage:
#   ./issue_compressor.sh                 # dry-run (default)
#   ./issue_compressor.sh --apply         # actually close candidates
#   ./issue_compressor.sh --window 7      # override 7-day window
#   ./issue_compressor.sh --quiet         # suppress per-issue lines
#
# Exit codes:
#   0  success (including zero candidates)
#   1  argument / configuration error
#   2  probe failure (no candidates could be probed — likely endpoint outage)

set -euo pipefail

# ---------- Configuration ----------
APPLY=0
WINDOW_DAYS=7
QUIET=0
WORKSPACE_ID="${MULTICA_WORKSPACE_ID:-f9a9d34e-b809-4564-b0c0-b781a70a3f25}"
AUDIT_LOG="/var/log/multica/issue-compressor.log"
PARENT_DISPATCH_ID="73ba1797-d969-47bf-8b65-ed331991969d"  # SMA-32064
PARENT_WEEKLY_ID="504fc936-f07f-4eb6-9961-8752c6c32494"    # SMA-30613
ENDPOINT_HEALTH_URLS=("http://127.0.0.1:8090/healthz" "http://127.0.0.1:3210/" "http://127.0.0.1:3000/")
DRY_RUN_REASON="dry-run: set --apply to mutate"
NOW_RFC3339="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
NOW_LOCAL="$(TZ=Asia/Shanghai date +%Y-%m-%dT%H:%M:%S%z)"
WINDOW_START_RFC3339="$(TZ=Asia/Shanghai date -d "-${WINDOW_DAYS} days" +%Y-%m-%dT%H:%M:%S%z | sed 's/\(.*\)\(..\)$/\1:\2/')"

# ---------- Argument parsing ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   APPLY=1; shift ;;
    --window)  WINDOW_DAYS="${2:-}"; shift 2 ;;
    --quiet)   QUIET=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() {
  local line="[$(TZ=Asia/Shanghai date +%H:%M:%S)] $*"
  echo "$line" | tee -a "$AUDIT_LOG" >&2
  if [[ $QUIET -eq 0 && "$*" != "STEP "* ]]; then
    : # per-issue lines already go through this; QUIET only suppresses them
  fi
}

step() { log "STEP $*"; }
info() { [[ $QUIET -eq 0 ]] && log "  $*"; }

# ---------- Audit log bootstrap ----------
if ! touch "$AUDIT_LOG" 2>/dev/null; then
  echo "Cannot write audit log $AUDIT_LOG" >&2
  exit 1
fi

step "issue-compressor start mode=$([[ $APPLY -eq 1 ]] && echo apply || echo dry-run) window=${WINDOW_DAYS}d now_local=$NOW_LOCAL workspace=$WORKSPACE_ID"

# ---------- Endpoint probe ----------
probe_endpoints() {
  step "probing ${#ENDPOINT_HEALTH_URLS[@]} health endpoint(s)"
  local ok=0 fail=0
  for url in "${ENDPOINT_HEALTH_URLS[@]}"; do
    local code
    code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo 000)"
    if [[ "$code" =~ ^(200|307|301|302)$ ]]; then
      info "  $url → $code ✓"
      ok=$((ok+1))
    else
      info "  $url → $code ✗"
      fail=$((fail+1))
    fi
  done
  echo "$ok $fail"
}

read -r PROBE_OK PROBE_FAIL < <(probe_endpoints || echo "0 0")
if [[ "$PROBE_OK" -eq 0 ]]; then
  log "ABORT: no healthy endpoints (0/${#ENDPOINT_HEALTH_URLS[@]} responding 200/307) — refusing to close any candidate"
  exit 2
fi
step "probe summary: ${PROBE_OK} healthy / ${PROBE_FAIL} failing"

# ---------- Candidate enumeration ----------
list_candidates() {
  local kind="$1" status="$2" title_regex="$3"
  local since="$4"
  multica issue list \
    --status "$status" \
    --limit 300 \
    --output json 2>/dev/null \
    | python3 -c "
import json,sys,re
from datetime import datetime,timezone,timedelta
d=json.load(sys.stdin)
shanghai=timezone(timedelta(hours=8))
since=datetime.fromisoformat('$since'.replace('Z','+00:00')).astimezone(shanghai)
for i in d.get('issues',[]):
    t=i.get('title','')
    s=i.get('status','')
    if not re.search(r'''$title_regex''', t):
        continue
    if s != '$status':
        continue
    created=datetime.fromisoformat(i['created_at'].replace('Z','+00:00')).astimezone(shanghai)
    if created < since:
        continue
    if '[need-smark-decision' in t.lower():
        continue
    print(i['identifier']+'|'+i['id']+'|'+s+'|'+t[:160]+'|'+i['created_at'])
"
}

DEPLOY_FAIL_REGEX='\[DEPLOY-FAIL\]'
HEARTBEAT_REGEX='\[heartbeat-(noop|alert)\]'

step "enumerating [DEPLOY-FAIL] candidates (status in todo|in_progress, last ${WINDOW_DAYS}d)"
DEPLOY_CANDIDATES="$(mktemp)"
list_candidates deploy-fail todo      "$DEPLOY_FAIL_REGEX" "$WINDOW_START_RFC3339" > "$DEPLOY_CANDIDATES" || true
list_candidates deploy-fail in_progress "$DEPLOY_FAIL_REGEX" "$WINDOW_START_RFC3339" >> "$DEPLOY_CANDIDATES" || true
DEPLOY_COUNT="$(wc -l < "$DEPLOY_CANDIDATES" | tr -d ' ')"
info "  found $DEPLOY_COUNT deploy-fail candidate(s)"

step "enumerating [heartbeat-noop|heartbeat-alert] in_review candidates (age > 1h)"
HEARTBEAT_CANDIDATES="$(mktemp)"
list_candidates heartbeat in_review "$HEARTBEAT_REGEX" "$WINDOW_START_RFC3339" > "$HEARTBEAT_CANDIDATES" || true
# Filter heartbeat to age > 1h
python3 - "$HEARTBEAT_CANDIDATES" <<'PY'
import sys
from datetime import datetime, timezone, timedelta
shanghai = timezone(timedelta(hours=8))
fp = sys.argv[1]
now = datetime.now(shanghai)
kept = []
with open(fp) as f:
    for line in f:
        line = line.rstrip("\n")
        if not line: continue
        parts = line.split("|", 4)
        if len(parts) < 5: continue
        ident, iid, status, title, created = parts
        try:
            ct = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(shanghai)
        except Exception:
            continue
        age_h = (now - ct).total_seconds() / 3600.0
        if age_h >= 1.0:
            kept.append(line)
        else:
            sys.stderr.write(f"  skip {ident} (age {age_h:.2f}h < 1h)\n")
with open(fp, "w") as f:
    f.write("\n".join(kept) + ("\n" if kept else ""))
PY
HEARTBEAT_COUNT="$(wc -l < "$HEARTBEAT_CANDIDATES" | tr -d ' ')"
info "  found $HEARTBEAT_COUNT heartbeat candidate(s) ≥ 1h old"

# ---------- Closure ----------
CLOSE_OK=0
CLOSE_FAIL=0
SKIPPED=0
DISPATCH_LINE_PREFIX="Auto-closed by issue-compressor weekly L3 cron per dispatch [SMA-32064](mention://issue/${PARENT_DISPATCH_ID})"

audit_comment() {
  local ident="$1" reason="$2"
  cat <<EOF
${DISPATCH_LINE_PREFIX} at ${NOW_LOCAL}.

Endpoints verified: ${PROBE_OK}/${#ENDPOINT_HEALTH_URLS[@]} healthy (200/307).
Window: last ${WINDOW_DAYS} days.
Reason: ${reason}.
EOF
}

close_one() {
  local line="$1" kind="$2"
  local ident iid status title created
  IFS='|' read -r ident iid status title created <<< "$line"
  local comment_body
  comment_body="$(audit_comment "$ident" "$kind")"
  local comment_file
  comment_file="$(mktemp)"
  printf '%s' "$comment_body" > "$comment_file"

  if [[ $APPLY -eq 0 ]]; then
    info "  [DRY] would close $ident — $title"
    rm -f "$comment_file"
    return 0
  fi

  info "  closing $ident — $title"
  if multica issue comment add "$iid" --content-file "$comment_file" >>"$AUDIT_LOG" 2>&1; then
    if multica issue status "$iid" done >>"$AUDIT_LOG" 2>&1; then
      CLOSE_OK=$((CLOSE_OK+1))
    else
      log "  FAIL: $ident status→done failed"
      CLOSE_FAIL=$((CLOSE_FAIL+1))
    fi
  else
    log "  FAIL: $ident comment add failed"
    CLOSE_FAIL=$((CLOSE_FAIL+1))
  fi
  rm -f "$comment_file"
}

step "processing deploy-fail candidates"
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  close_one "$line" deploy-fail
done < "$DEPLOY_CANDIDATES"

step "processing heartbeat candidates"
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  close_one "$line" heartbeat-noop
done < "$HEARTBEAT_CANDIDATES"

rm -f "$DEPLOY_CANDIDATES" "$HEARTBEAT_CANDIDATES"

TOTAL_CANDIDATES=$(( DEPLOY_COUNT + HEARTBEAT_COUNT ))
step "summary: candidates=$TOTAL_CANDIDATES (deploy=$DEPLOY_COUNT, heartbeat=$HEARTBEAT_COUNT) closed_ok=$CLOSE_OK closed_fail=$CLOSE_FAIL mode=$([[ $APPLY -eq 1 ]] && echo apply || echo dry-run)"

# ---------- Emit machine-readable summary for downstream tooling ----------
SUMMARY_FILE="/tmp/issue-compressor-summary-$(date -u +%Y%m%dT%H%M%SZ).json"
cat > "$SUMMARY_FILE" <<EOF
{
  "started_at": "$NOW_RFC3339",
  "started_at_local": "$NOW_LOCAL",
  "mode": "$([[ $APPLY -eq 1 ]] && echo apply || echo dry-run)",
  "window_days": $WINDOW_DAYS,
  "window_start": "$WINDOW_START_RFC3339",
  "workspace_id": "$WORKSPACE_ID",
  "parent_dispatch_id": "$PARENT_DISPATCH_ID",
  "parent_weekly_id": "$PARENT_WEEKLY_ID",
  "probe_ok": $PROBE_OK,
  "probe_total": ${#ENDPOINT_HEALTH_URLS[@]},
  "candidates": {
    "deploy_fail": $DEPLOY_COUNT,
    "heartbeat":   $HEARTBEAT_COUNT,
    "total":       $TOTAL_CANDIDATES
  },
  "closed_ok":   $CLOSE_OK,
  "closed_fail": $CLOSE_FAIL,
  "skipped":     $SKIPPED,
  "audit_log":   "$AUDIT_LOG"
}
EOF
log "summary written to $SUMMARY_FILE"

if [[ $APPLY -eq 0 ]]; then
  log "DRY-RUN COMPLETE — no state mutated. Re-run with --apply to perform the closures."
fi

exit 0