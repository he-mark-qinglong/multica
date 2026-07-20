# Diff Proposal: Wire Top-3 Anchors (2026-07-20)

> Concrete change-set if A1-001, A8-001, A10-001 are wired (per `anchor_proposals.md`).
>
> Author: multica-code (run 07dd8587, 2026-07-20)
> Status: proposed diff — not yet applied

---

## Summary

**3 net-new files**, **3 net-new cron entries**, **0 existing files modified**,
**0 existing autopilot configs changed**, **0 daemon restarts required**.

The diff is purely additive. Existing autopilots, scripts, and configs continue
to run unchanged. New behavior is opt-in (template required for new strategy
issues only; DSR/paper-trade anchors only touch `in_review` issues).

---

## New files (3)

### File 1: `/home/smark/.multica/issue_templates/strategy-variant.md`
```yaml
---
name: strategy-variant
description: Strategy variant issue template (A1-001 enforcement)
labels: [strategy]
---

## Hypothesis (mechanistic)
<1-2 paragraphs. State the causal mechanism. Cite prior work or mark UNCERTAIN.>

## Signal Design
<How is the signal computed?>

## UNCERTAIN
<List any unverified assumption.>

## Out-of-scope
<What this strategy explicitly does NOT do.>
```

### File 2: `/home/smark/multica/issue_templates/strategy-exploration.md`
```yaml
---
name: strategy-exploration
description: Strategy exploration issue template (A1-001 enforcement)
labels: [strategy-exploration]
---

## Hypothesis (mechanistic OR open-question)
<State the question.>

## Cited prior work
<Papers, blog posts.>

## UNCERTAIN
<What we'd need to verify before promoting.>
```

### File 3: `/home/smark/multica/quant-loop/_shared/validation/dsr_gate.py`
~30 LOC, port of Bailey-López de Prado Deflated Sharpe Ratio. Pure function.
Inputs: `sharpe_estimates`, `n_trials`, `n_obs_per_trial`, `skewness`, `kurtosis`.
Output: `float` DSR probability.

(File `paper_tracker.py` from A10-001 is sketched in `anchor_proposals.md`; not
duplicated here — that's a 4th new file if A10-001 is wired.)

**Paper state path** (consumed by `paper_tracker.py` when A10-001 wires):
`/home/smark/multica/quant-loop/state/paper/<strategy_id>.json` — confirmed
aligned between Python `PAPER_STATE_DIR` (line ~227) and YAML criteria
(line ~274) in `anchor_proposals.md`. **Precondition**: verify
`binance_usdm_paper` connector has shipped at least one state file before
wiring A10-001, otherwise the gate will block all live transitions with
`paper_days=0`.

---

## New cron entries (3)

| Anchor | Cron | Agent | Trigger spec |
|--------|------|-------|--------------|
| A1-001 | on issue create | null (template-level) | labels IN (strategy, strategy-exploration) |
| A8-001 | `37 * * * *` Asia/Shanghai | strategy-validator (07cc9e07) | hourly @ :37, slot with framework-validate |
| A10-001 | `37 8 * * *` Asia/Shanghai | strategy-validator (07cc9e07) | daily 08:37 |

No collision with existing crons. The `37 *` slot is shared with
framework-validate (07cc9e07 owner); both can run on same owner without conflict
because they're stateful idempotent checks against different blast_radius sets.

---

## Wiring commands (3, sequential)

```bash
# 1. Register A1-001 templates (one-time)
multica issue template create strategy-variant \
  --file /home/smark/.multica/issue_templates/strategy-variant.md

multica issue template create strategy-exploration \
  --file /home/smark/.multica/issue_templates/strategy-exploration.md

# 2. Create A8-001 autopilot
multica autopilot create \
  --title "DSR gate (A8-001)" \
  --assignee-id 07cc9e07-3832-4c38-8df4-565cea79cbf2 \
  --execution-mode run_only \
  --description-file /home/smark/.multica/autopilot-backups/A8-001.yaml

# 3. Create A10-001 autopilot
multica autopilot create \
  --title "Paper trade gate (A10-001)" \
  --assignee-id 07cc9e07-3832-4c38-8df4-565cea79cbf2 \
  --execution-mode run_only \
  --description-file /home/smark/.multica/autopilot-backups/A10-001.yaml
```

After each `multica autopilot create`, attach a cron trigger (separate
`trigger-add` call). Autopilot configs are stored under
`/home/smark/.multica/autopilot-backups/` so rollback is `multica autopilot delete`
on the returned ID.

---

## Behavioral changes (what's different after wiring)

### Before
- A `[STRATEGY-EXPLORATION]` issue can be created with body `let's try funding
  arb again` — no mechanism, no UNCERTAIN. Ships through dispatch.
- A backtest-passed strategy can flip to `done` after Evidence Review Gate sees
  a passing PR. DSR is never computed. Paper-trade duration is never checked.

### After
- `[STRATEGY-*]` issues missing `## Hypothesis (mechanistic)` are blocked at
  creation with `[GATE-FAIL] Strategy issue missing ## Hypothesis section`. The
  author is forced to articulate a causal story.
- `in_review` strategy backtests are checked hourly: DSR computed from
  min(in_house, freqtrade, backtrader) Sharpe (note: framework-validate rotates
  ONE framework per hourly run — freqtrade → backtrader → vectorbt → jesse →
  nautilus_trader → zipline-reloaded — and converges to full OOS CV over ~6h of
  cron slots, not simultaneous); if DSR ≤ 0.5 OR n_trades < 30, status flips to
  `blocked` with the failure reason in the comment.
- `in_review` strategy live-transition issues are checked daily at 08:37: paper
  duration must be ≥ 14 days with no errors in last 7 days, else `blocked`.

### Failure-mode coverage (before → after)

| Failure mode | Caught before? | Caught after? | By which anchor |
|---|---|---|---|
| V5-style curve-fit narrative | No (passed review, ship-gates 3/5 FAIL) | Yes (blocked at issue create) | A1-001 |
| Multiple-testing inflation | No (DSR never computed) | Yes (DSR gate fails) | A8-001 |
| Live-skip (no paper trade) | No (manual review only) | Yes (paper_days < 14 → blocked) | A10-001 |
| V10 catastrophic single-window | Partial (OOS gate catches mean, not window) | Same (deferred — needs A7-001) | — |

---

## What does NOT change

- **No existing autopilot is modified.** framework-validate, publish-gate,
  REGRESSION-TEST, Evidence Review Gate continue unchanged.
- **No daemon restart.** All wiring is `multica autopilot create` + `trigger-add`,
  which write to the existing autopilot daemon's state.
- **No source code in `quant-loop/strategies/*` is touched.** The new files live
  in `_shared/validation/` and `~/.multica/issue_templates/`.
- **No cron collision.** The :37 hourly slot is shared with framework-validate,
  but they're independent idempotent checks with disjoint blast_radius.
- **No backfill.** Existing in-flight strategy issues are grandfathered (template
  applies to NEW issues only; DSR/paper gates apply to NEW in_review transitions).

---

## Risk + rollback

### Risk: False-positive blocking
- **A1-001**: a perfectly good strategy issue fails the template because the
  author forgot the heading. Mitigation: the failure comment tells them what to
  fix; adding `## Hypothesis (mechanistic)` is a 1-line edit.
- **A8-001**: DSR ≤ 0.5 blocks a strategy that actually has edge but DSR is
  noisy at low n. Mitigation: criteria includes `n_oos_trades_total >= 30`; below
  30 trades the gate auto-passes (DSR undefined → conservative). For 30+ trades,
  false-positive rate at DSR > 0.5 is < 5% per Bailey-López de Prado.
- **A10-001**: paper duration < 14 blocks a strategy that's been running paper
  off-connector. Mitigation: criteria is 14 days, but the `binance_usdm_paper`
  connector is the official path; if a strategy bypasses the connector, the
  architecture itself is the issue.

### Rollback (per anchor)
```bash
# A1-001: disable both templates
multica issue template delete strategy-variant
multica issue template delete strategy-exploration

# A8-001: disable autopilot
multica autopilot delete <A8-001-id>

# A10-001: disable autopilot
multica autopilot delete <A10-001-id>
```

Each rollback is one command. No state rollback needed because the new files
don't mutate existing data structures.

---

## Open questions for smark

1. **DSR threshold**: spec says `> 0.5`. Some research (Bailey-López de Prado
   2014) suggests `> 0.95` for high-confidence edge. Confirm threshold before
   wiring A8-001.
2. **Paper duration**: spec implies "N days", 14 is my proposal. Some strategies
   need longer (e.g. regime-conditional strategies need full regime cycle).
   Confirm `min_days` parameter or move to per-strategy config.
3. **Template enforcement strictness**: should missing template sections HARD-block
   (status=blocked) or SOFT-block (comment + priority nudge)? Hard-block is what
   A1-001 currently does; soft-block is friendlier but slower to take effect.
4. **1d-TF ban + family-exhaustion counter**: gates 12/13 in the audit are
   UNCERTAIN until canonical SPEC sync. Confirm before proposing A12-001 / A13-001.

---

## Sign-off

**Author**: multica-code agent (run 07dd8587, 2026-07-20)
**Status**: proposed — wiring requires smark confirmation of DSR threshold + paper
duration + template strictness questions above.
**Next useful action**: smark answers the 4 open questions → autopilot-administrator
executes the 3 wiring commands (or smark decides to defer).