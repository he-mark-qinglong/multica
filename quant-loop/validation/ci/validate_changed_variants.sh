#!/usr/bin/env bash
# Validate every strategy variant touched by a commit/PR against the G1-G7
# OOS harness. Non-zero exit blocks the merge.
#
# Usage:
#   ci/validate_changed_variants.sh [BASE_REF [HEAD_REF]]
# Defaults: BASE_REF=origin/main HEAD_REF=HEAD.
set -euo pipefail

BASE_REF="${1:-origin/main}"
HEAD_REF="${2:-HEAD}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
QUANT_LOOP="$REPO_ROOT/quant-loop"

# Resolve variant directories from the diff BEFORE we cd into
# quant-loop/ — git diff's path filter is relative to the repo root.
mapfile -t VARIANTS < <(
  git -C "$REPO_ROOT" diff --name-only "$BASE_REF...$HEAD_REF" -- 'quant-loop/strategies/' \
    | cut -d/ -f3 \
    | sort -u \
    | while read -r v; do
        [ -n "$v" ] && [ -d "$QUANT_LOOP/strategies/$v" ] && echo "$v"
      done
)

if [ "${#VARIANTS[@]}" -eq 0 ]; then
  echo "[validate] no strategy variant changes between $BASE_REF and $HEAD_REF"
  exit 0
fi

# Now switch into quant-loop/ so the harness module is on PYTHONPATH
# (`python3 -m validation.oos_harness` requires cwd == quant-loop/).
cd "$QUANT_LOOP"

echo "[validate] changed variants: ${VARIANTS[*]}"
FAILED=()
for v in "${VARIANTS[@]}"; do
  echo "[validate] === $v ==="
  if python3 -m validation.oos_harness --variant "$v"; then
    echo "[validate] $v: PASS"
  else
    rc=$?
    echo "[validate] $v: FAIL/BLOCKED (exit $rc)"
    FAILED+=("$v")
  fi
done

if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "[validate] merge blocked — G1-G7 gate failures in: ${FAILED[*]}"
  exit 1
fi
echo "[validate] all changed variants PASS"