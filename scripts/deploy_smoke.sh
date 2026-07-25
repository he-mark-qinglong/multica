#!/usr/bin/env bash
# deploy_smoke.sh — post-deploy smoke runner (round-2 infra-sprint 2026-07-25, W5-T5).
#
# Two-stage probe runner that complements the inline route smoke at the tail of
# scripts/deploy.sh (:130-134). deploy.sh only checks three user-state routes
# return 401/200; this script additionally probes the /healthz migration
# invariant, a public unauthenticated route, and (when run on the deploy host)
# DB-side invariants and daemon state.
#
# Stage 1 (HTTP, any host):
#   1. healthz              — body must contain "migrations":"ok"
#   2. routes               — /api/tasks, /api/metrics/query, /api/artifacts
#                             must each return 200 or 401 (404 / 000 = FAIL)
#   3. websocket_route      — /api/config GET must return 200
#                             (proves the auth-free mux group is also live;
#                             router.go:474)
#
# Stage 2 (DB / daemon, only when SMOKE_DB_CONTAINER is set):
#   4. gate_invariant       — run_metric rows with gate_status='pass' must NOT
#                             carry a missing-metric note in gate_detail.
#                             Catches the gate skip-pass regression directly.
#   5. autopilot_nonempty   — autopilot table must have at least one row.
#   6. daemon_active        — systemctl --user is-active multica-daemon must
#                             report 'active'.
#   7. ingest_recent        — at least one run_metric in the last
#                             SMOKE_INGEST_MAX_AGE_HOURS hours.
#                             **WARN-only** — a long quiet period is not a
#                             deploy failure (degraded intentionally).
#
# Stage 2 is skipped (each probe prints `PROBE <name> ... SKIP`) when
# SMOKE_DB_CONTAINER is empty. deploy.sh runs this script via ssh on .105
# (where multica-postgres-1 lives) with SMOKE_DB_CONTAINER set, and locally on
# the deployer's host without it.
#
# Exit codes:
#   0  all probes OK (or WARN-only)
#   1  at least one probe FAILed
#
# Env overrides (no hardcoded values; everything has a documented default):
#   SMOKE_HOST                  default: http://192.168.0.105:8080
#   SMOKE_DB_CONTAINER          default: "" (skip stage-2 probes)
#   SMOKE_INGEST_MAX_AGE_HOURS  default: 72
#
# set -u: undefined env vars are caught early.
# set -o pipefail: a failure mid-pipeline is the pipeline's failure.
# No -e: we want every probe to run so a single sweep shows the full picture.
set -uo pipefail

SMOKE_HOST="${SMOKE_HOST:-http://192.168.0.105:8080}"
SMOKE_DB_CONTAINER="${SMOKE_DB_CONTAINER:-}"
SMOKE_INGEST_MAX_AGE_HOURS="${SMOKE_INGEST_MAX_AGE_HOURS:-72}"

FAILS=0

# probe <name> <ok:0|1> [detail]
#   ok="0" → success, prints "PROBE <name> ... OK [detail]"
#   ok="1" → failure, prints "PROBE <name> ... FAIL: <detail>" and bumps FAILS.
probe() {
  local name="$1" ok="$2" detail="${3:-}"
  if [[ "$ok" == "0" ]]; then
    printf 'PROBE %s ... OK %s\n' "$name" "$detail"
  else
    printf 'PROBE %s ... FAIL: %s\n' "$name" "$detail"
    FAILS=$((FAILS + 1))
  fi
}

# skip_db <name> — DB probe is skipped because SMOKE_DB_CONTAINER is empty.
# Does not increment FAILS (we are intentionally running without DB access).
skip_db() {
  printf 'PROBE %s ... SKIP (SMOKE_DB_CONTAINER empty)\n' "$1"
}

# ---------------------------------------------------------------- stage 1: HTTP

# 1. healthz — same invariant as deploy.sh:128 (`"migrations":"ok"`).
if body=$(curl -sf --max-time 10 "$SMOKE_HOST/healthz" 2>/dev/null) \
     && printf '%s' "$body" | grep -q '"migrations":"ok"'; then
  probe healthz 0
else
  probe healthz 1 "curl --max-time 10 $SMOKE_HOST/healthz failed or body lacks \"migrations\":\"ok\""
fi

# 2. routes — every route must return 200 or 401 (404 / 000 = FAIL).
ROUTE_OK=0
ROUTE_FAIL_LIST=""
for route in /api/tasks /api/metrics/query /api/artifacts; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$SMOKE_HOST$route" 2>/dev/null)
  code="${code:-000}"
  if [[ "$code" == "200" || "$code" == "401" ]]; then
    :
  else
    ROUTE_OK=1
    ROUTE_FAIL_LIST="$ROUTE_FAIL_LIST $route=$code"
  fi
done
if [[ "$ROUTE_OK" == "0" ]]; then
  probe routes 0 "all 3 routes returned 200|401"
else
  probe routes 1 "unexpected status:$ROUTE_FAIL_LIST"
fi

# 3. websocket_route (lightweight) — /api/config GET must return 200.
# This route lives outside the auth group (router.go:474), so a 200 here
# confirms that mux group is wired up even if our token is missing.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$SMOKE_HOST/api/config" 2>/dev/null)
code="${code:-000}"
if [[ "$code" == "200" ]]; then
  probe websocket_route 0 "/api/config -> 200"
else
  probe websocket_route 1 "/api/config -> $code (expected 200)"
fi

# ---------------------------------------------------------------- stage 2: DB / daemon

if [[ -z "$SMOKE_DB_CONTAINER" ]]; then
  skip_db gate_invariant
  skip_db autopilot_nonempty
  skip_db daemon_active
  skip_db ingest_recent
else
  # 4. gate_invariant — a row that pass=true with a missing-metric note means
  # the gate is still rubber-stamping incomplete metrics. The note string
  # depends on which gate version wrote it:
  #   - pre-fix (skip-pass bug):  "skipped: no data"
  #   - post-fix (strict gate):   "missing required metric"
  # Both flavours are equally invalid as 'pass', so we flag either.
  gate_count=$(docker exec "$SMOKE_DB_CONTAINER" psql -U multica -tAc \
    "SELECT count(*) FROM run_metric WHERE gate_status='pass' AND EXISTS (SELECT 1 FROM jsonb_array_elements(gate_detail) e WHERE e->>'note' IN ('missing required metric','skipped: no data'))" \
    2>/dev/null | tr -d '[:space:]')
  if [[ -z "$gate_count" || ! "$gate_count" =~ ^[0-9]+$ ]]; then
    probe gate_invariant 1 "psql/docker exec returned empty or non-numeric: '${gate_count:-<empty>}'"
  elif [[ "$gate_count" == "0" ]]; then
    probe gate_invariant 0 "0 rows with gate_status=pass AND missing-metric note"
  else
    probe gate_invariant 1 "found $gate_count row(s) with gate_status=pass carrying a missing-metric note (gate skip-pass regression)"
  fi

  # 5. autopilot_nonempty — the autopilot table must have at least one row.
  autopilot_count=$(docker exec "$SMOKE_DB_CONTAINER" psql -U multica -tAc \
    "SELECT count(*) FROM autopilot" 2>/dev/null | tr -d '[:space:]')
  if [[ -z "$autopilot_count" || ! "$autopilot_count" =~ ^[0-9]+$ ]]; then
    probe autopilot_nonempty 1 "psql/docker exec returned empty or non-numeric: '${autopilot_count:-<empty>}'"
  elif [[ "$autopilot_count" -ge 1 ]]; then
    probe autopilot_nonempty 0 "autopilot count=$autopilot_count"
  else
    probe autopilot_nonempty 1 "autopilot table is empty (count=0)"
  fi

  # 6. daemon_active — must report 'active'. Note: `systemctl --user` only
  # works inside the user's session; deploy.sh sets this up via the ssh heredoc
  # which inherits the right user env.
  daemon_state=$(systemctl --user is-active multica-daemon 2>/dev/null || true)
  if [[ "$daemon_state" == "active" ]]; then
    probe daemon_active 0 "multica-daemon active"
  else
    probe daemon_active 1 "multica-daemon state='${daemon_state:-<unknown>}' (expected 'active')"
  fi

  # 7. ingest_recent — WARN-only. A long quiet period (no fresh run_metric
  # rows) is not a deploy failure; it just means nobody has been running
  # backtests lately. The plan explicitly downgrades this to a warning so the
  # post-deploy smoke can pass on quiet days.
  # Use psql -v for the hours value to keep the SQL literal quoted cleanly.
  ingest_count=$(docker exec "$SMOKE_DB_CONTAINER" psql -U multica -tAc \
    -v "hours=${SMOKE_INGEST_MAX_AGE_HOURS}" \
    "SELECT count(*) FROM run_metric WHERE created_at > now() - make_interval(hours => :'hours')" \
    2>/dev/null | tr -d '[:space:]')
  if [[ -z "$ingest_count" || ! "$ingest_count" =~ ^[0-9]+$ ]]; then
    # Query failure is a real probe failure, not a quiet-period WARN.
    probe ingest_recent 1 "psql/docker exec returned empty or non-numeric: '${ingest_count:-<empty>}'"
  elif [[ "$ingest_count" -ge 1 ]]; then
    probe ingest_recent 0 "$ingest_count run_metric row(s) in last ${SMOKE_INGEST_MAX_AGE_HOURS}h"
  else
    printf 'PROBE ingest_recent ... WARN: 0 run_metric rows in last %sh (quiet period, not a deploy failure)\n' \
      "$SMOKE_INGEST_MAX_AGE_HOURS"
  fi
fi

# ---------------------------------------------------------------- summary

if [[ "$FAILS" == "0" ]]; then
  echo "SMOKE PASS"
  exit 0
else
  echo "SMOKE FAIL ($FAILS probe(s))"
  exit 1
fi