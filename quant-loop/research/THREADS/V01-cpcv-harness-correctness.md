# V01 — CPCV harness correctness (validation infrastructure)

- **Status**: fixed 2026-08-02 (SMA-36935, depth-review P0-1). Standing re-validation rule below.
- **Scope**: `_shared/validation/cpcv.py` — the harness behind every CPCV OOS number in the results-ledger.

## The bugs (depth-review, 2026-08-02)

1. **Purge leakage (fatal)** — `_purge_boundaries` purged only around the merged test set's global min/max. With k_test≥2 and non-contiguous test groups, interior train/test boundaries were never purged → train label windows overlapped the test period → OOS Sharpe systematically biased **up**.
2. **Embargo semantics (high)** — `_embargo` truncated the head of the TEST set. AFML Ch.7 embargo cuts TRAIN rows immediately after each test window (they are serially correlated with the test period). Old code blocked nothing and destroyed OOS samples.

## The fix

- `_test_segments()`: split test positions into contiguous segments.
- `_purge_boundaries()`: drop train bars within `purge_bars` of EVERY segment, both sides (superset of the t1-horizon rule).
- `_embargo(train, test, embargo_bars)`: drop train bars in (seg_end, seg_end + embargo_bars]; test set never touched.

## Evidence

- Leak-oracle regression (`test_purge_eliminates_label_overlap_leakage`): a strategy earning +|r| only on bars covered by some train forward-h label window shows fake OOS Sharpe > 1 with purge_bars=0 and exactly 0.0 with purge_bars≥h (purgedcv-style synthetic leak proof).
- Index-level interior-boundary purge test + embargo-does-not-cut-test test.
- 16/16 `test_cpcv.py` green; 94 passed / 2 skipped across `_shared/validation` (py3.12, pytest 9.1.1).

## Impact on historical verdicts

- Bias direction was **upward** → all KILLs only get stronger; no revival triggered by this fix.
- Any PASS / WITHIN_TOLERANCE / near-gate verdict (within ~±0.3 Sharpe of a gate) produced before this fix inherited unknown positive bias → must be re-run under the fixed harness before use as ship evidence.
- Batch re-validation of the whole ledger NOT auto-recommended: kills rest on family-level evidence (cycle-46), not boundary effects. smark call required for any re-validation campaign.

## Revival / re-open condition

Re-open only if a future audit shows the per-segment purge is still insufficient (e.g. label horizons >> purge_bars in some strategy's config — then migrate to sample-level prediction_times/evaluation_times purge per purgedcv).

## Links

- SMA-36935 (this fix) + branch `agent/quant-researcher/sma-36661`
- JOURNAL.md 2026-08-02 entry
- T09 (SMA-35167, largest CPCV-sweep consumer) + T01 (SMA-35037, CPCV OOS −34±5 — kill reinforced)
- AFML Ch.7 (López de Prado) + purgedcv (leak-proof pattern)
