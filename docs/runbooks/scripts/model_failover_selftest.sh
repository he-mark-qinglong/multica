#!/usr/bin/env bash
# scripts/model_failover_selftest.sh
#
# Self-test for `docs/runbooks/model-failover.md`. Validates BOTH:
#   (A) runbook structure — all 8 § sections + 2 appendices present (§7 in doc references this script)
#   (B) live preflight probes per runbook §3 — healthcheck, daemon, default_model, model-endpoint reachability, CLI, abort-rate
#
# This script does NOT switch models or rotate keys. It only reads state and exits non-zero on failures.
# Mirrors runbook §3 §4 §5 commands exactly (commands keep in sync with the doc).
#
# Usage:
#   bash ~/multica/docs/runbooks/scripts/model_failover_selftest.sh
#
# Exit code = number of [FAIL] probes (0 = fully clean).
# Output: one line per probe, prefixed [OK] / [FAIL] / [INFO] / [WARN].

set -u

# Anchor: this file's sibling runbook.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNBOOK="${SCRIPT_DIR}/../model-failover.md"
RUNBOOK_ABS="$(cd "$(dirname "$RUNBOOK")" && pwd)/$(basename "$RUNBOOK")"

EXPECTED_SECTIONS=(
  "## 1. 用途与范围"
  "## 2. 模型失效信号定义"
  "## 3. Preflight"
  "## 4. 快速诊断"
  "## 5. 分层动作"
  "## 6. 升级路径"
  "## 7. 自检脚本"
  "## 8. 故障线索索引"
  "## 附录 A：相关代码"
  "## 附录 B：环境变量"
)

fail_count=0
probe_count=0

ok()    { printf '[OK]    %s\n' "$*"; }
bad()   { printf '[FAIL]  %s\n' "$*"; fail_count=$((fail_count + 1)); }
info()  { printf '[INFO]  %s\n' "$*"; }
warn()  { printf '[WARN]  %s\n' "$*"; }
probe() { probe_count=$((probe_count + 1)); }

# ----- (A) Runbook structure -----
probe "structure: runbook file exists at ${RUNBOOK_ABS}"
if [[ -f "$RUNBOOK_ABS" ]]; then ok "$RUNBOOK_ABS"; else bad "missing — write model-failover.md first"; fi

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
  if grep -qF "model_failover_selftest.sh" "$RUNBOOK_ABS"; then
    ok "runbook §7 references this script"
  else
    bad "runbook §7 does not reference this script — drift"
  fi
fi

# ----- (B) Live preflight probes (mirror runbook §3) -----

# §3.1 — local healthcheck.sh (mirrors agent-stall-ops.md §3.1 note: tail docker probes are legacy drift)
probe "preflight §3.1a: ~/.multica/healthcheck.sh exits 0"
if [[ -x "${HOME}/.multica/healthcheck.sh" ]]; then
  if "${HOME}/.multica/healthcheck.sh" >/tmp/model_hc.$$.out 2>&1; then
    n_ok=$(grep -c '^HEALTHCHECK OK:' /tmp/model_hc.$$.out || true)
    ok "healthcheck.sh — ${n_ok} OK lines"
  else
    if grep -qE 'multica-(postgres|backend|redis)-1 not found' /tmp/model_hc.$$.out; then
      info "healthcheck.sh reported docker container drift (Postgres/Backend on LAN 192.168.0.105) — not counted as FAIL"
    else
      failing=$(grep '^HEALTHCHECK FAIL:' /tmp/model_hc.$$.out | head -3 || true)
      bad "healthcheck.sh exited non-zero: ${failing:-no FAIL line}"
    fi
  fi
  rm -f /tmp/model_hc.$$.out
else
  bad "~/.multica/healthcheck.sh missing or not executable"
fi

# §3.1b — LAN backend health (cross-environment; WARN not FAIL if unreachable from this Mac)
probe "preflight §3.1b: LAN backend http://192.168.0.105:8090/healthz"
LAN_HEALTHZ="$(curl -sf --max-time 5 http://192.168.0.105:8090/healthz 2>/dev/null || true)"
if [[ -z "$LAN_HEALTHZ" ]]; then
  warn "LAN backend /healthz unreachable from this Mac — not counted as FAIL (cross-environment)"
elif echo "$LAN_HEALTHZ" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='ok' else 1)" 2>/dev/null; then
  ok "LAN backend /healthz reports status:ok"
else
  warn "LAN backend /healthz reports non-ok — see deploy-server-105.md §5: ${LAN_HEALTHZ:0:200}"
fi

# §3.2 — daemon status + model error keywords in daemon.log
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

probe "preflight §3.2: daemon.log has model-error keywords (401/403/429/5xx/aborted) in last 200 lines"
if [[ -r "${HOME}/.multica/daemon.log" ]]; then
  err_count=$(tail -200 "${HOME}/.multica/daemon.log" 2>/dev/null \
    | grep -cE '401|403|429|upstream_error|kimi cancelled the prompt|agent_error' || true)
  if [[ "${err_count:-0}" -lt 5 ]]; then
    ok "model-error count low (${err_count}/200 lines)"
  else
    bad "model-error count high (${err_count}/200 lines) — likely model-side outage, jump to runbook §5"
  fi
else
  bad "~/.multica/daemon.log not readable"
fi

# §3.3 — current model routing: default_model + provider keys in daemon.env
probe "preflight §3.3a: ~/.kimi-code/config.toml has default_model"
DEFAULT_MODEL="$(grep '^default_model' ~/.kimi-code/config.toml 2>/dev/null | head -1 || true)"
if [[ -n "$DEFAULT_MODEL" ]]; then
  ok "default_model set: ${DEFAULT_MODEL}"
else
  bad "default_model missing in config.toml"
fi

probe "preflight §3.3b: ~/.multica/daemon.env has at least one provider key"
if [[ -r "${HOME}/.multica/daemon.env" ]]; then
  n_keys=$(grep -cE '^(ANTHROPIC|OPENAI|OPENROUTER|CAOCAO|MINIMAX)_.*=.+' ~/.multica/daemon.env || true)
  if [[ "${n_keys:-0}" -ge 1 ]]; then
    ok "${n_keys} provider key(s) non-empty in daemon.env"
  else
    bad "no provider keys found in daemon.env — all models will 401"
  fi
else
  bad "~/.multica/daemon.env not readable"
fi

probe "preflight §3.3c: config.toml registers the default model under [models.*]"
if [[ -n "$DEFAULT_MODEL" ]]; then
  # Extract the value between quotes, e.g. `default_model = "kimi-tang/k3"`
  model_id=$(sed -nE 's/^default_model[[:space:]]*=[[:space:]]*"([^"]+)"/\1/p' ~/.kimi-code/config.toml)
  if [[ -n "$model_id" ]]; then
    if grep -qE "^\[models\\.${model_id//./\\.}\]" ~/.kimi-code/config.toml \
       || grep -qE "^\[models\\.\"${model_id//\//\\/}\"\]" ~/.kimi-code/config.toml \
       || grep -qE "^\[models\\.${model_id}\]" ~/.kimi-code/config.toml; then
      ok "model '${model_id}' is registered in config.toml"
    else
      bad "model '${model_id}' not registered under [models.*] — daemon will fail"
    fi
  else
    bad "could not parse default_model value"
  fi
fi

# §3.4 — model-endpoint HTTP probe (does NOT consume tokens, only checks reachability)
probe "preflight §3.4: model endpoint reachable (HTTP probe, no token spend)"
probe_url=""
case "$model_id" in
  kimi-tang/*|kimi-smark/*|managed:kimi-tang/*|managed:kimi-smark/*)
    probe_url="https://api.kimi.com/coding/v1/models" ;;
  minimax*)
    probe_url="https://api.minimax.io/anthropic/v1/models" ;;
  glm-5.2*)
    probe_url="https://open.bigmodel.cn/api/anthropic/v1/models" ;;
  caocao-*)
    probe_url="http://127.0.0.1:18091/v1/models" ;;
  *)
    probe_url="" ;;
esac

if [[ -z "$probe_url" ]]; then
  warn "could not map default_model '${model_id:-?}' to a probe URL — manual §3.4 needed"
else
  probe_http="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$probe_url" 2>/dev/null || echo 000)"
  case "$probe_http" in
    200|401|403) ok "endpoint ${probe_url} reachable (http=${probe_http}, 401/403 = endpoint OK, token issue — see runbook §5.2a)" ;;
    000)         warn "endpoint ${probe_url} connection failed — cross-environment or DNS" ;;
    429)         bad "endpoint ${probe_url} rate-limited (http=429)" ;;
    5*)          bad "endpoint ${probe_url} upstream error (http=${probe_http})" ;;
    *)           warn "endpoint ${probe_url} unexpected http=${probe_http} — inspect manually" ;;
  esac
fi

# §3.5 — CLI pathway
probe "preflight §3.5: multica issue list --status in_progress returns valid JSON"
if command -v multica >/dev/null 2>&1; then
  out="$(multica issue list --status in_progress --limit 1 --output json 2>&1 || true)"
  if echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if isinstance(d,dict) and 'issues' in d else 1)" 2>/dev/null; then
    ok "CLI pathway healthy"
  else
    bad "CLI did not return valid JSON — daemon or DB broken (jump to agent-stall-ops.md §5)"
  fi
else
  bad "multica CLI not on PATH"
fi

# §3.6 — abort rate (model-induced)
probe "preflight §3.6: 'kimi cancelled the prompt' count in last 200 daemon.log lines < 5"
if [[ -r "${HOME}/.multica/daemon.log" ]]; then
  abort_count=$(tail -200 "${HOME}/.multica/daemon.log" 2>/dev/null \
    | grep -c 'kimi cancelled the prompt' || true)
  if [[ "${abort_count:-0}" -lt 5 ]]; then
    ok "abort rate low (${abort_count}/200)"
  else
    bad "abort rate high (${abort_count}/200) — model likely refusing; see runbook §5.3"
  fi
else
  bad "~/.multica/daemon.log not readable"
fi

echo
echo "==== summary ===="
echo "probes: $probe_count, ok: $((probe_count - fail_count)), fail: $fail_count"
exit "$fail_count"