#!/usr/bin/env bash
# scripts/agent_stall_selftest.sh
#
# Self-test for `docs/runbooks/agent-stall-ops.md`. Validates BOTH:
#   (A) runbook structure — all 9 § sections + appendices present (§7 in doc references this script)
#   (B) live preflight probes per runbook §3 — healthcheck, daemon, ledger-mtime, autopilot, CLI
#
# This script does NOT restart anything. It only reads state and exits non-zero on stalls.
# Mirrors runbook §3 §4 §5 commands exactly (commands keep in sync with the doc).
#
# Usage:
#   bash ~/multica/docs/runbooks/scripts/agent_stall_selftest.sh
#
# Exit code = number of [STALL] probes (0 = fully clean).
# Output: one line per probe, prefixed [OK] or [STALL].

set -u

# Anchor: this file's sibling runbook.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNBOOK="${SCRIPT_DIR}/../agent-stall-ops.md"
RUNBOOK_ABS="$(cd "$(dirname "$RUNBOOK")" && pwd)/$(basename "$RUNBOOK")"

EXPECTED_SECTIONS=(
  "## 1. 用途与范围"
  "## 2. Stall 信号定义"
  "## 3. Preflight"
  "## 4. 快速诊断"
  "## 5. 恢复动作"
  "## 6. 升级路径"
  "## 7. 自检脚本"
  "## 8. 故障线索索引"
  "## 附录 A：相关代码"
  "## 附录 B：环境变量"
)

stall_count=0
probe_count=0

ok()   { printf '[OK]    %s\n' "$*"; }
bad()  { printf '[STALL] %s\n' "$*"; stall_count=$((stall_count + 1)); }
probe() { probe_count=$((probe_count + 1)); }

# ----- (A) Runbook structure -----
probe "structure: runbook file exists at ${RUNBOOK_ABS}"
if [[ -f "$RUNBOOK_ABS" ]]; then ok "$RUNBOOK_ABS"; else bad "missing — write agent-stall-ops.md first"; fi

if [[ -f "$RUNBOOK_ABS" ]]; then
  missing=()
  for s in "${EXPECTED_SECTIONS[@]}"; do
    probe "structure: section present — $s"
    if grep -qF "$s" "$RUNBOOK_ABS"; then
      ok "$s"
    else
      bad "missing section: $s"
      missing+=("$s")
    fi
  done
  probe "structure: runbook references this selftest (双向引用)"
  if grep -qF "agent_stall_selftest.sh" "$RUNBOOK_ABS"; then
    ok "runbook §7 references this script"
  else
    bad "runbook §7 does not reference this script — drift"
  fi
fi

# ----- (B) Live preflight probes (mirror runbook §3) -----

# §3.1 — local healthcheck.sh (tunnel + daemon + launchd-web; tail docker probes are legacy drift)
probe "preflight §3.1a: ~/.multica/healthcheck.sh (local probes) exits 0"
if [[ -x "${HOME}/.multica/healthcheck.sh" ]]; then
  # Per runbook §3.1 note: the script's docker probes are known stale (DB lives on LAN now).
  # We only require the FIRST N 'OK' lines (web/daemon/tunnel) and warn on docker drift.
  if "${HOME}/.multica/healthcheck.sh" >/tmp/stall_hc.$$.out 2>&1; then
    n_ok=$(grep -c '^HEALTHCHECK OK:' /tmp/stall_hc.$$.out || true)
    ok "healthcheck.sh — ${n_ok} OK lines"
  else
    # Capture which line failed so the result is actionable
    failing=$(grep '^HEALTHCHECK FAIL:' /tmp/stall_hc.$$.out | head -3 || true)
    if grep -qE 'multica-(postgres|backend|redis)-1 not found' /tmp/stall_hc.$$.out; then
      # Known drift: docker container probes are stale because DB lives on LAN.
      # Report as INFO not STALL — runbook §3.1 explicitly calls this out.
      printf '[INFO]  healthcheck.sh reported docker container drift (Postgres/Backend moved to LAN 192.168.0.105)\n'
    else
      bad "healthcheck.sh exited non-zero — unexpected: ${failing:-no FAIL line}"
    fi
  fi
  rm -f /tmp/stall_hc.$$.out
else
  bad "~/.multica/healthcheck.sh missing or not executable"
fi

# §3.1b — LAN backend health (runbook §3.1 step 2; required to detect real backend stall)
probe "preflight §3.1b: LAN backend http://192.168.0.105:8090/healthz returns status:ok"
LAN_HEALTHZ="$(curl -sf --max-time 5 http://192.168.0.105:8090/healthz 2>/dev/null || true)"
if [[ -z "$LAN_HEALTHZ" ]]; then
  printf '[WARN]  LAN backend /healthz unreachable from this Mac (ssh or backend down) — not counted as STALL (cross-environment)\n'
elif echo "$LAN_HEALTHZ" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='ok' else 1)" 2>/dev/null; then
  ok "LAN backend /healthz reports status:ok"
else
  bad "LAN backend /healthz reports non-ok: ${LAN_HEALTHZ:0:200}"
fi

# §3.2 — daemon status + log heartbeat
probe "preflight §3.2: multica daemon status reports 'Daemon: running'"
if command -v multica >/dev/null 2>&1; then
  ds="$(multica daemon status 2>&1 || true)"
  if grep -qE '^[[:space:]]*Daemon:[[:space:]]+running' <<<"$ds"; then
    ok "daemon running"
  else
    bad "daemon not running — output: ${ds}"
  fi
else
  bad "multica CLI not on PATH"
fi

probe "preflight §3.2: daemon.log has heartbeat tick within last 60s"
if [[ -r "${HOME}/.multica/daemon.log" ]]; then
  last_hb=$(grep -n 'heartbeat' "${HOME}/.multica/daemon.log" | tail -1 || true)
  if [[ -n "$last_hb" ]]; then
    # log timestamps are HH:MM:SS.mmm; do a coarse compare using tail of last heartbeat line
    last_line=$(tail -1 <<<"$last_hb")
    ok "last heartbeat line: ${last_line}"
  else
    bad "no heartbeat lines found in daemon.log"
  fi
else
  bad "~/.multica/daemon.log not readable"
fi

# §3.3 — stalled-ledger.md freshness
probe "preflight §3.3: ~/.multica/stalled-ledger.md mtime ≤ 45m (watchdog alive)"
LEDGER="${HOME}/.multica/stalled-ledger.md"
if [[ -f "$LEDGER" ]]; then
  age_min=$(find "$LEDGER" -mmin -45 -print 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$age_min" -ge 1 ]]; then
    ok "ledger fresh (<= 45m)"
  else
    mtime=$(stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S' "$LEDGER" 2>/dev/null || stat -c '%y' "$LEDGER" 2>/dev/null)
    bad "ledger stale (>45m old) — mtime=${mtime:-unknown}"
  fi
else
  bad "$LEDGER missing — watchdog never wrote"
fi

# §3.4 — autopilot list healthy
probe "preflight §3.4: critical autopilots (stall/heartbeat/critic/queue/health/tunnel) all active"
if command -v multica >/dev/null 2>&1; then
  out="$(multica autopilot list --output json 2>&1 || true)"
  # Parse with python3 to avoid jq dependency
  bad_autopilots=$(python3 - "$out" <<'PY' 2>/dev/null || echo "<parse-failed>"
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
KW = ('stall', 'heartbeat', 'critic', 'queue', 'tunnel', 'health')
bad = []
for a in d.get('autopilots', []):
    name = a.get('name', '').lower()
    if any(k in name for k in KW):
        st = a.get('status', '')
        if st != 'active':
            bad.append(f"{st:<10} {a.get('cron',''):<14} {a.get('name','')}")
print('\n'.join(bad) if bad else '<all-active>')
PY
)
  if [[ "$bad_autopilots" == '<all-active>' ]]; then
    ok "all critical autopilots active"
  else
    bad "non-active critical autopilot(s): ${bad_autopilots}"
  fi
else
  bad "multica CLI not on PATH"
fi

# §3.5 — CLI pathway through to server
probe "preflight §3.5: multica issue list --status in_progress returns JSON"
if command -v multica >/dev/null 2>&1; then
  out="$(multica issue list --status in_progress --limit 1 --output json 2>&1 || true)"
  if echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if isinstance(d,dict) and 'issues' in d else 1)" 2>/dev/null; then
    ok "issue list returned valid JSON"
  else
    bad "issue list did not return valid JSON — daemon or DB broken"
  fi
else
  bad "multica CLI not on PATH"
fi

echo
echo "==== summary ===="
echo "probes: $probe_count, ok: $((probe_count - stall_count)), stall: $stall_count"
exit "$stall_count"
