#!/usr/bin/env bash
# dump_autopilots.sh — autopilot inventory snapshot
#
# One-shot markdown dump of every autopilot registered on the current
# multica server, intended for three consumers:
#   1. Recovery decisions (which autpilots exist, which are paused)
#   2. "Don't reinvent" check (see existing titles before creating new ones)
#   3. Pre-flight collision check for the epoch triple-trigger apply (T3)
#
# Output: a sorted markdown table of every autopilot the multica CLI
# returns, with a status breakdown footer. The full inventory goes to a
# file so agents / humans can `grep`, diff, or paste it into issues.
#
# Style: mirrors scripts/quant_disk_quota_alert.sh — `set -euo pipefail`,
# while/case flag parsing, env-overridable defaults.
#
# Flags:
#   --out PATH       output file (default: ops-reports/autopilot-inventory.md,
#                    relative to repo root)
#   --raw-json PATH  also write the raw `multica autopilot list` JSON
#                    payload to PATH (useful for downstream tooling)
#
# Env overrides:
#   PY_BIN           python interpreter (default:
#                    /Users/mark/sdk/mamba-envs/trading/bin/python3)
#   MULTICA_BIN      multica CLI (default: multica, resolves via PATH)

set -euo pipefail

OUT_PATH="ops-reports/autopilot-inventory.md"
RAW_JSON_PATH=""
PY_BIN="${PY_BIN:-/Users/mark/sdk/mamba-envs/trading/bin/python3}"
MULTICA_BIN="${MULTICA_BIN:-multica}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)        OUT_PATH="$2"; shift 2 ;;
    --raw-json)   RAW_JSON_PATH="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

# Locate repo root regardless of where the caller invoked us from.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# If --out is relative, anchor it at the repo root so cron-style callers
# get a deterministic path rather than CWD-relative drift.
case "$OUT_PATH" in
  /*) ;;
  *)  OUT_PATH="$REPO_ROOT/$OUT_PATH" ;;
esac
if [[ -n "$RAW_JSON_PATH" && "$RAW_JSON_PATH" != /* ]]; then
  RAW_JSON_PATH="$REPO_ROOT/$RAW_JSON_PATH"
fi

OUT_DIR="$(dirname "$OUT_PATH")"
mkdir -p "$OUT_DIR"

# Pull the live autopilot list. We capture stdout/stderr separately so a
# stray warning on stderr doesn't leak into the markdown body.
raw_json_tmp="$(mktemp)"
trap 'rm -f "$raw_json_tmp"' EXIT

if ! "$MULTICA_BIN" autopilot list --output json >"$raw_json_tmp" 2>/tmp/dump_autopilots.stderr; then
  echo "ERROR: '$MULTICA_BIN autopilot list --output json' failed" >&2
  cat /tmp/dump_autopilots.stderr >&2 || true
  rm -f /tmp/dump_autopilots.stderr
  exit 1
fi
rm -f /tmp/dump_autopilots.stderr

json="$(cat "$raw_json_tmp")"

if [[ -n "$RAW_JSON_PATH" ]]; then
  printf '%s\n' "$json" > "$RAW_JSON_PATH"
fi

# Render via python. The heredoc would shadow positional `$json` so we
# funnel the payload through INVENTORY_JSON (mirrors the env-pass
# recipe noted in w5-s1-epoch-glue.md §T2).
INVENTORY_JSON="$json" OUT_PATH="$OUT_PATH" \
  "$PY_BIN" - <<'PYEOF'
import datetime as _dt
import json as _json
import os as _os
import sys as _sys

try:
    payload = _json.loads(_os.environ["INVENTORY_JSON"])
except Exception as exc:
    print(f"ERROR: malformed autopilot list JSON: {exc}", file=_sys.stderr)
    sys_exit = 1
    raise SystemExit(sys_exit)

autopilots = payload.get("autopilots", [])
total = payload.get("total", len(autopilots))

def _short(value, default="—"):
    if value is None or value == "":
        return default
    text = str(value)
    return text[:8]

status_counts: dict[str, int] = {}
mode_counts: dict[str, int] = {}
title_by_status: dict[str, list[str]] = {}
rows = []
for entry in autopilots:
    aid = _short(entry.get("id"))
    title = entry.get("title") or ""
    status = entry.get("status") or "unknown"
    mode = entry.get("execution_mode") or ""
    assignee = _short(entry.get("assignee_id"))
    last_run = entry.get("last_run_at") or "—"
    rows.append((title, aid, status, mode, assignee, last_run))
    status_counts[status] = status_counts.get(status, 0) + 1
    mode_counts[mode] = mode_counts.get(mode, 0) + 1
    title_by_status.setdefault(status, []).append(title)

# Sort by title (case-insensitive) so output is stable across runs.
rows.sort(key=lambda r: (r[0].casefold(), r[0]))

now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
active = status_counts.get("active", 0)
paused = status_counts.get("paused", 0)

out_path = _os.environ["OUT_PATH"]
lines: list[str] = []
lines.append("# Autopilot Inventory")
lines.append("")
lines.append(f"> Generated: {now} by scripts/dump_autopilots.sh")
lines.append(f"> Total: {total} (active: {active}, paused: {paused})")
lines.append("")
lines.append("| ID | Title | Status | Mode | Assignee | Last run |")
lines.append("| --- | --- | --- | --- | --- | --- |")
for title, aid, status, mode, assignee, last_run in rows:
    safe_title = title.replace("|", "\\|")
    lines.append(f"| {aid} | {safe_title} | {status} | {mode} | {assignee} | {last_run} |")

lines.append("")
lines.append("## Status breakdown")
lines.append("")
for status in sorted(status_counts.keys()):
    lines.append(f"### {status} ({status_counts[status]})")
    lines.append("")
    for t in sorted(title_by_status.get(status, []), key=str.casefold):
        lines.append(f"- {t}")
    lines.append("")

body = "\n".join(lines)
if not body.endswith("\n"):
    body += "\n"

with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(body)

print(f"wrote {len(rows)} autopilot rows to {out_path}")
PYEOF
