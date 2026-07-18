#!/usr/bin/env bash
# ==========================================================================
# CI hook dry-run test — exercises the entrypoint shell wrapper
# (`ci/validate_changed_variants.sh`) + the OOS harness against two
# pre-baked fixture variants:
#
#   _dryrun_pass → engineered to PASS every G1-G7 gate → exit 0
#   _dryrun_fail → engineered to FAIL several gates  → exit non-zero
#
# Both fixtures live in `quant-loop/strategies/_dryrun_*/` and ship a
# self-contained synthetic data_loader.py + harness_adapter.py so the
# test never reaches into the canonical market-data path (QUANT_LOOP_DATA_ROOT).
# The fixtures are deterministic (fixed numpy seeds) so the test exit
# code is stable across reruns.
#
# Usage:
#   bash quant-loop/validation/tests/test_ci_dryrun.sh
#   make validate-strategy-dryrun
#
# Exit code:
#   0 = both fixtures behaved as expected
#   non-zero = one of the fixtures diverged from its expected verdict,
#              meaning the entrypoint or harness wiring changed in a
#              way that needs investigation.
# ==========================================================================
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT/quant-loop"

PASS_VARIANT="_dryrun_pass"
FAIL_VARIANT="_dryrun_fail"
PASS_OUT="/tmp/dryrun_pass_verdict"
FAIL_OUT="/tmp/dryrun_fail_verdict"

# Windows=1 keeps the freqtrade subprocess count to 1 per fixture
# (sufficient to exercise G1-G7; the full pipeline run with 3 windows
# is covered by real CI runs).
WINDOWS=1
FRAMEWORKS="native,backtrader,freqtrade"

PASS_RC=0
FAIL_RC=0
ANY_FAIL=0

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

# ---- PASS fixture ------------------------------------------------------------
blue "[test] === PASS fixture ($PASS_VARIANT): expect exit 0, verdict PASS ==="
rm -rf "$PASS_OUT"
set +e
python3 -m validation.oos_harness \
  --variant "$PASS_VARIANT" --windows "$WINDOWS" \
  --frameworks "$FRAMEWORKS" --output "$PASS_OUT"
PASS_RC=$?
set -e

PASS_VERDICT="$(python3 -c "
import json
try:
    d = json.load(open('$PASS_OUT/verdict.json'))
except Exception as e:
    print(f'<missing verdict.json: {e}>')
    raise SystemExit(0)
print(d.get('verdict', '?'))
failed = [g['gate'] for g in d.get('gates', []) if not g.get('passed')]
print(f'failed_gates={failed}')
")"

echo "$PASS_VERDICT"
if [ "$PASS_RC" -eq 0 ] && echo "$PASS_VERDICT" | head -n1 | grep -q '^PASS$'; then
  green "[test] PASS fixture: OK (exit 0, verdict PASS)"
else
  red "[test] PASS fixture: FAILED (exit $PASS_RC, verdict: $(echo "$PASS_VERDICT" | head -n1))"
  ANY_FAIL=1
fi

# ---- FAIL fixture ------------------------------------------------------------
blue ""
blue "[test] === FAIL fixture ($FAIL_VARIANT): expect exit non-zero, verdict FAIL ==="
rm -rf "$FAIL_OUT"
set +e
python3 -m validation.oos_harness \
  --variant "$FAIL_VARIANT" --windows "$WINDOWS" \
  --frameworks "$FRAMEWORKS" --output "$FAIL_OUT"
FAIL_RC=$?
set -e

FAIL_VERDICT="$(python3 -c "
import json
try:
    d = json.load(open('$FAIL_OUT/verdict.json'))
except Exception as e:
    print(f'<missing verdict.json: {e}>')
    raise SystemExit(0)
print(d.get('verdict', '?'))
failed = [g['gate'] for g in d.get('gates', []) if not g.get('passed')]
print(f'failed_gates={failed}')
")"

echo "$FAIL_VERDICT"
if [ "$FAIL_RC" -ne 0 ] && echo "$FAIL_VERDICT" | head -n1 | grep -q '^FAIL$'; then
  green "[test] FAIL fixture: OK (exit $FAIL_RC, verdict FAIL)"
else
  red "[test] FAIL fixture: FAILED (exit $FAIL_RC, verdict: $(echo "$FAIL_VERDICT" | head -n1))"
  ANY_FAIL=1
fi

# ---- Entrypoint wrapper end-to-end ------------------------------------------
blue ""
blue "[test] === Entrypoint shell wrapper (ci/validate_changed_variants.sh) ==="
blue "[test]   diff main..HEAD where main lacks the FAIL fixture, HEAD adds it"
set +e
TMPREPO="$(mktemp -d)"
git -C "$TMPREPO" init -q -b main
git -C "$TMPREPO" config user.email "dryrun@example.com"
git -C "$TMPREPO" config user.name "dryrun"
mkdir -p "$TMPREPO/quant-loop/strategies"
cp -r "$REPO_ROOT/quant-loop/validation" "$TMPREPO/quant-loop/validation"
cp -r "$REPO_ROOT/quant-loop/strategies/_dryrun_pass" "$TMPREPO/quant-loop/strategies/_dryrun_pass"
git -C "$TMPREPO" add -A
git -C "$TMPREPO" commit -q -m "baseline: harness + dry-run fixtures (no _dryrun_fail)"
git -C "$TMPREPO" checkout -q -b feature/dryrun
cp -r "$REPO_ROOT/quant-loop/strategies/_dryrun_fail" "$TMPREPO/quant-loop/strategies/_dryrun_fail"
git -C "$TMPREPO" add -A
git -C "$TMPREPO" commit -q -m "feat: add _dryrun_fail strategy variant (CI dry-run fixture)"
WRAPPER_RC=0
# cd into TMPREPO so the wrapper's `git rev-parse --show-toplevel`
# resolves to TMPREPO, not the original checkout. The wrapper script
# accepts BASE_REF as $1; pass `main` (default would be `origin/main`,
# unavailable in a fresh TMPREPO).
(
  cd "$TMPREPO"
  bash "$REPO_ROOT/quant-loop/validation/ci/validate_changed_variants.sh" main HEAD
)
WRAPPER_RC=$?
set -e
rm -rf "$TMPREPO"

if [ "$WRAPPER_RC" -ne 0 ]; then
  green "[test] Entrypoint wrapper: OK (exit $WRAPPER_RC, merge blocked on FAIL)"
else
  red "[test] Entrypoint wrapper: FAILED (exit 0 — wrapper did not block merge on FAIL)"
  ANY_FAIL=1
fi

blue ""
if [ "$ANY_FAIL" -eq 0 ]; then
  green "[test] ALL CI DRY-RUN CHECKS PASSED"
  exit 0
else
  red "[test] ONE OR MORE CI DRY-RUN CHECKS FAILED"
  exit 1
fi