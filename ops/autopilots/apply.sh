#!/usr/bin/env bash
# ops/autopilots/apply.sh — idempotent plan/apply for the epoch triple-trigger
# manifest set under ops/autopilots/. Mirrors the style of
# scripts/quant_disk_quota_alert.sh and scripts/dump_autopilots.sh:
# `set -euo pipefail`, while/case flag parsing, env-overridable defaults.
#
# Default mode is dry-run (prints the plan, exits 0 without mutating the
# server). Pass --apply to actually create/update autopilots and triggers.
#
# Flags:
#   --apply                 Execute the plan (writes to multica).
#                          Without this flag the script only prints the plan.
#   --dir <path>            Manifest directory (default: ops/autopilots,
#                          relative to repo root).
#   --help                  Show this header and exit 0.
#
# Env overrides:
#   PY_BIN                  python interpreter used to parse manifest JSON
#                          (default: /Users/mark/sdk/mamba-envs/trading/bin/python3).
#   MULTICA_BIN             multica CLI binary (default: multica, resolved via PATH).

set -euo pipefail

MODE="dry-run"
MANIFEST_DIR=""
PY_BIN="${PY_BIN:-/Users/mark/sdk/mamba-envs/trading/bin/python3}"
MULTICA_BIN="${MULTICA_BIN:-multica}"

print_help() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)    MODE="apply"; shift ;;
    --dry-run)  shift ;; # dry-run is the default; keep the flag for clarity.
    --dir)      MANIFEST_DIR="$2"; shift 2 ;;
    -h|--help)  print_help; exit 0 ;;
    *)          echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "$MANIFEST_DIR" ]]; then
  MANIFEST_DIR="ops/autopilots"
fi
case "$MANIFEST_DIR" in
  /*) ;;
  *)  MANIFEST_DIR="$REPO_ROOT/$MANIFEST_DIR" ;;
esac

if [[ ! -d "$MANIFEST_DIR" ]]; then
  echo "manifest directory not found: $MANIFEST_DIR" >&2
  exit 1
fi

shopt -s nullglob
MANIFEST_FILES=( "$MANIFEST_DIR"/*.json )
shopt -u nullglob
if [[ ${#MANIFEST_FILES[@]} -eq 0 ]]; then
  echo "no manifest *.json files under $MANIFEST_DIR" >&2
  exit 1
fi

# Resolve a JSON path -> value via python so the manifest stays the source
# of truth and shell quoting never touches the prompt body.
manifest_get() {
  local file="$1" path="$2"
  "$PY_BIN" -c '
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
path = sys.argv[2].lstrip(".")
parts = path.split(".") if path else []
cur = d
for p in parts:
    if isinstance(cur, dict) and p in cur:
        cur = cur[p]
    else:
        sys.exit(0)
if isinstance(cur, (dict, list)):
    print(json.dumps(cur, ensure_ascii=False))
else:
    print(cur if cur is not None else "")
' "$file" "$path"
}

# Build "title -> id" map once from the live autopilot list. We do this
# rather than grepping per-manifest to keep the script O(N+M) not O(N*M).
echo ":: loading current autopilot inventory (mode=$MODE)" >&2
RAW_LIST="$("$MULTICA_BIN" autopilot list --output json)"
TITLE_MAP="$("$PY_BIN" -c '
import json, sys
data = json.loads(sys.stdin.read())
items = data.get("autopilots") or []
out = {}
for it in items:
    title = it.get("title") or ""
    aid = it.get("id") or ""
    if title and aid:
        out[title] = aid
print(json.dumps(out, ensure_ascii=False))
' <<<"$RAW_LIST")"

lookup_id_for_title() {
  "$PY_BIN" -c '
import json, sys
m = json.loads(sys.argv[1])
print(m.get(sys.argv[2], ""))
' "$TITLE_MAP" "$1"
}

# For one autopilot id, dump a small JSON blob describing the live state
# we want to diff against the manifest. Just the fields the card calls out
# plus the first schedule trigger's cron/timezone.
describe_existing() {
  local autopilot_id="$1"
  "$MULTICA_BIN" autopilot get "$autopilot_id" --output json | "$PY_BIN" -c '
import json, sys
d = json.load(sys.stdin)
ap = d.get("autopilot") or {}
trig = next((t for t in (d.get("triggers") or []) if t.get("kind") == "schedule"), None)
out = {
    "description": ap.get("description") or "",
    "execution_mode": ap.get("execution_mode") or "",
    "issue_title_template": ap.get("issue_title_template") or "",
    "agent_name": (ap.get("assignee") or {}).get("name") if isinstance(ap.get("assignee"), dict) else "",
    "status": ap.get("status") or "",
    "trigger_cron": (trig or {}).get("cron_expression") or "",
    "trigger_timezone": (trig or {}).get("timezone") or "",
    "trigger_id": (trig or {}).get("id") or "",
}
print(json.dumps(out, ensure_ascii=False))
'
}

plan_one_manifest() {
  local mf="$1"
  local title mode agent priority tpl desc cron tz label enabled existing_id existing_json diffs needs_trigger new_trigger_id

  title="$(manifest_get "$mf" "title")"
  if [[ -z "$title" ]]; then
    echo "SKIP $(basename "$mf"): missing title" >&2
    return 0
  fi
  mode="$(manifest_get "$mf" "mode")"
  agent="$(manifest_get "$mf" "agent")"
  priority="$(manifest_get "$mf" "priority")"
  tpl="$(manifest_get "$mf" "issue_title_template")"
  desc="$(manifest_get "$mf" "description")"
  cron="$(manifest_get "$mf" "trigger.cron")"
  tz="$(manifest_get "$mf" "trigger.timezone")"
  label="$(manifest_get "$mf" "trigger.label")"
  enabled="$(manifest_get "$mf" "trigger.enabled")"

  existing_id="$(lookup_id_for_title "$title")"

  if [[ -z "$existing_id" ]]; then
    echo "CREATE $title"
    return 0
  fi

  existing_json="$(describe_existing "$existing_id")"
  diffs="$(
    existing_id="$existing_id" \
    "$PY_BIN" <<PYEOF
import json, os
expected = {
  "description": """$desc""",
  "execution_mode": """$mode""",
  "issue_title_template": """$tpl""",
  "agent_name": """$agent""",
  "trigger_cron": """$cron""",
  "trigger_timezone": """$tz""",
}
existing = json.loads("""$existing_json""")
diffs = []
for k, want in expected.items():
    have = existing.get(k) or ""
    if have != want:
        diffs.append(k)
if not existing.get("trigger_id"):
    diffs.append("+trigger")
print(" ".join(diffs))
PYEOF
  )"

  if [[ -z "$diffs" ]]; then
    echo "SKIP $title"
  else
    echo "UPDATE $title ($diffs)"
  fi
}

apply_one_manifest() {
  local mf="$1"
  local title mode agent priority tpl desc cron tz label enabled existing_id diffs new_id trigger_args tmp

  title="$(manifest_get "$mf" "title")"
  mode="$(manifest_get "$mf" "mode")"
  agent="$(manifest_get "$mf" "agent")"
  priority="$(manifest_get "$mf" "priority")"
  tpl="$(manifest_get "$mf" "issue_title_template")"
  desc="$(manifest_get "$mf" "description")"
  cron="$(manifest_get "$mf" "trigger.cron")"
  tz="$(manifest_get "$mf" "trigger.timezone")"
  label="$(manifest_get "$mf" "trigger.label")"
  enabled="$(manifest_get "$mf" "trigger.enabled")"

  existing_id="$(lookup_id_for_title "$title")"

  if [[ -z "$existing_id" ]]; then
    tmp="$(mktemp -t applydesc.XXXXXX)"
    printf '%s' "$desc" >"$tmp"
    "$MULTICA_BIN" autopilot create \
      --title "$title" \
      --description "$(cat "$tmp")" \
      --agent "$agent" \
      --mode "$mode" \
      --priority "$priority" \
      --issue-title-template "$tpl" \
      --output json >/tmp/apply.create."$$"
    new_id="$("$PY_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("autopilot",{}).get("id",""))' /tmp/apply.create."$$")"
    rm -f "$tmp" /tmp/apply.create."$$"
    [[ -n "$new_id" ]] || { echo "create returned empty id for $title" >&2; exit 1; }
    trigger_args=( --kind schedule --cron "$cron" --timezone "$tz" --label "$label" )
    if [[ "$enabled" == "false" ]]; then
      trigger_args+=( --enabled=false )
    fi
    "$MULTICA_BIN" autopilot trigger-add "$new_id" "${trigger_args[@]}" --output json >/dev/null
    echo "CREATE $title"
    return 0
  fi

  diffs="$(
    existing_id="$existing_id" \
    "$PY_BIN" <<PYEOF
import json
expected = {
  "description": """$desc""",
  "execution_mode": """$mode""",
  "issue_title_template": """$tpl""",
  "agent_name": """$agent""",
  "trigger_cron": """$cron""",
  "trigger_timezone": """$tz""",
}
existing = json.loads("""$existing_json""")
diffs = []
for k, want in expected.items():
    have = existing.get(k) or ""
    if have != want:
        diffs.append(k)
if not existing.get("trigger_id"):
    diffs.append("+trigger")
print(" ".join(diffs))
PYEOF
  )"
  existing_json="$(describe_existing "$existing_id")"

  if [[ -z "$diffs" ]]; then
    echo "SKIP $title"
    return 0
  fi

  local update_args=()
  [[ "$diffs" == *description* ]] && {
    tmp="$(mktemp -t applydesc.XXXXXX)"
    printf '%s' "$desc" >"$tmp"
    update_args+=( --description "$(cat "$tmp")" )
    DESC_TMP="$tmp"
  }
  [[ "$diffs" == *execution_mode* ]] && update_args+=( --mode "$mode" )
  [[ "$diffs" == *issue_title_template* ]] && update_args+=( --issue-title-template "$tpl" )
  [[ "$diffs" == *agent_name* ]] && update_args+=( --agent "$agent" )

  if [[ ${#update_args[@]} -gt 0 ]]; then
    "$MULTICA_BIN" autopilot update "$existing_id" "${update_args[@]}" --output json >/dev/null
  fi

  if [[ "$diffs" == *trigger_cron* || "$diffs" == *trigger_timezone* || "$diffs" == *+trigger* ]]; then
    if [[ -n "${DESC_TMP:-}" ]]; then rm -f "$DESC_TMP"; DESC_TMP=""; fi
    local existing_after_update
    existing_after_update="$(describe_existing "$existing_id")"
    local trigger_id
    trigger_id="$("$PY_BIN" -c 'import json,sys; print(json.loads(sys.argv[1]).get("trigger_id",""))' "$existing_after_update")"
    if [[ -z "$trigger_id" ]]; then
      local trigger_args2=( --kind schedule --cron "$cron" --timezone "$tz" --label "$label" )
      if [[ "$enabled" == "false" ]]; then
        trigger_args2+=( --enabled=false )
      fi
      "$MULTICA_BIN" autopilot trigger-add "$existing_id" "${trigger_args2[@]}" --output json >/dev/null
    else
      local tu_args=( --cron "$cron" --timezone "$tz" --label "$label" )
      if [[ "$enabled" == "false" ]]; then
        tu_args+=( --enabled=false )
      fi
      "$MULTICA_BIN" autopilot trigger-update "$existing_id" "$trigger_id" "${tu_args[@]}" --output json >/dev/null
    fi
  fi

  if [[ -n "${DESC_TMP:-}" ]]; then rm -f "$DESC_TMP"; fi
  echo "UPDATE $title ($diffs)"
}

# Stable ordering across plan + apply so output is reproducible.
IFS=$'\n' MANIFEST_FILES_SORTED=( $(printf '%s\n' "${MANIFEST_FILES[@]}" | sort) )
unset IFS

if [[ "$MODE" == "dry-run" ]]; then
  for mf in "${MANIFEST_FILES_SORTED[@]}"; do
    plan_one_manifest "$mf"
  done
else
  for mf in "${MANIFEST_FILES_SORTED[@]}"; do
    apply_one_manifest "$mf"
  done
fi
