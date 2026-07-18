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
cd "$REPO_ROOT/quant-loop"

mapfile -t VARIANTS < <(
  git diff --name-only "$BASE_REF...$HEAD_REF" -- 'quant-loop/strategies/' \
    | cut -d/ -f3 \
    | sort -u \
    | while read -r v; do
        [ -n "$v" ] && [ -d "strategies/$v" ] && echo "$v"
      done
)

if [ "${#VARIANTS[@]}" -eq 0 ]; then
  echo "[validate] no strategy variant changes between $BASE_REF and $HEAD_REF"
  exit 0
fi

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
