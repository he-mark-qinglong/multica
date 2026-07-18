#!/usr/bin/env bash
# ==========================================================================
# Top-level wrapper around the quant-loop OOS validation harness.
#
# Inputs:
#   - BASE_REF (default: origin/main): the ref to diff against. Any commit ref
#     reachable from the current HEAD works (origin/main, origin/feature/foo,
#     a SHA, a tag).
#   - HEAD_REF (default: HEAD): where to diff to.
#   - DRYRUN_VARIANT (optional): when set, runs the harness against a single
#     variant name instead of mapping the diff. Used by the CI dry-run tests
#     to exercise the harness+exit-code wiring without touching git history.
#
# Outputs:
#   - verdict.json + verdict.md written to each changed variant's
#     results/validation/ directory (see quant-loop/validation/README.md).
#   - Exit code 0 = every changed variant PASSES G1-G7 (merge allowed).
#   - Exit code 1 = at least one variant FAILS at least one gate (block).
#   - Exit code 2 = harness error (unsupported variant, missing data,
#     framework crash) — different from gate failure, surfaces upstream.
#
# Usage:
#   scripts/run_validation.sh                      # diff origin/main..HEAD
#   scripts/run_validation.sh origin/main HEAD    # explicit refs
#   DRYRUN_VARIANT=_dryrun_pass scripts/run_validation.sh   # single variant
#
# Required env (only when the harness actually runs a variant):
#   - freqtrade on PATH (validation/requirements.txt pins >= 2024.9)
#   - python3 with validation/requirements.txt installed
#   - canonical market data mounted at QUANT_LOOP_DATA_ROOT (default
#     /home/smark/services/strategy_display_engine_data). The dry-run
#     fixtures ship their own synthetic data and do not need it.
# ==========================================================================
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT/quant-loop"

if [ -n "${DRYRUN_VARIANT:-}" ]; then
  echo "[run_validation] DRYRUN_VARIANT=$DRYRUN_VARIANT (single-variant mode)"
  exec python3 -m validation.oos_harness --variant "$DRYRUN_VARIANT"
fi

exec bash "$REPO_ROOT/quant-loop/validation/ci/validate_changed_variants.sh" \
  "${1:-origin/main}" "${2:-HEAD}"