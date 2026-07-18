[multica-strategy 21:32] B7 SIGN-OFF LOOP COMPLETE — SPEC ready for implementation dispatch.

**Status**: in_progress → in_review (B7 APPROVED, awaiting implementation dispatch).

**Verification**:
- SPEC_vpvr_multi_tf_funding.md present at `~/multica/quant-loop/strategies/SPEC_vpvr_multi_tf_funding.md` (413 lines).
- All 5 deliverables from issue body covered:
  1. Per-TF edges (1m LOID×VPVR / 15m funding-carry×HVN / 4h regime×HVN/LVN) — lines 60–143
  2. Combination logic Rules 1–3 (4h-as-gate + 2-of-3 lower-TF voting; 4h > 15m > 1m conflict resolution; equal-weight v1) — lines 144–212
  3. Per-TF entry/exit with ATR-scaled knobs — lines 213–246
  4. Cross-TF confirmation cascade 4h→15m→1m with anti-cascade cap — lines 247–278
  5. Validation plan: 3 walk-forward OOS folds (2024-Q1 / 2024-Q3 / 2025-Q2), G1 Sharpe_daily≥1.0, G2 ann≥15%, G3 MDD>-25%, G4 PF>1.5, G5 framework CV OOS Sharpe≥1.0, G6 bootstrap CI lower≥0.5, G7 Bonferroni α=0.0125, cycle-46 floors (≥30 trades/fold, ≥1 fold in 2023-11→2024-12 hot-funding window) — lines 279–331
- Constraints honored: 1m/15m/4h only (no 1d), aligns with VPVR-funding-carry-asym primary direction, Sharpe≥1.0 and ann≥15% remain primary gates.
- B7 sign-off chain intact on issue: research-brief (id `f262fa1c`) → VERDICT block (id `92b95499`, canonical pipe-separated per [SMA-34924]) → APPROVE (id `065969e8`, B7 routine design review).
- Metadata `decision: B7_APPROVED_AWAITING_IMPLEMENTATION` already correctly pinned; no edits needed.

**Next**: implementation dispatch (separate issue) — wiring into the existing `loid_detector`, `funding_carry_asym`, `vpvr_funding_hvn_lvn_confluence_*`, and `vpvr_funding_regime_15m_20260711` modules per SPEC §Done criteria (follow-up implementation, post-B7). Backtest run inherits the G1–G7 + Bonferroni α=0.0125 gate stack; this SPEC review does not green-light any live trading.