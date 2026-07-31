# T14 — Distributional Benchmark Comparison (DBC)

## Status
**2026-07-28**: maturing → SPEC shipped at
`research/specs/distributional_benchmark_compare_v1_20260728/SPEC.md`.
Handed to multica-strategy (L3) for implementation + unit tests +
canonical-fixture demo (issue SMA-35675, reassigned todo with the
SPEC). Awaiting quant-analyst (L4) V4 Aumann re-implementation audit
after L3 EVIDENCE lands. **Not yet promoted** to SMA-30199
frontier-SPEC bucket — V0-V4 must pass first.

## Question
Do strategy-vs-benchmark return **distributions** differ in ways the
existing moment-based toolkit (α / β / IR / Sharpe / capture) cannot
see — and can a small dependency-light primitive (KS + energy
distance + Wasserstein-1 + quantile decomposition + tail/CVaR gap,
with permutation p-values and block-bootstrap CIs) surface that
difference with calibrated false-positive rate and real attribution
power?

## Why this axis (dedup record)
- MAP-P8 Research queue ("benchmark comparison" cards): siblings
  #25/#35/#45/#55/#65/#75/#95 all shipped **return-space /
  equity-curve** lenses. Zero distributional lens. multica-strategy's
  ESCALATE on SMA-35675 (2026-07-26) enumerated 7 open axes; this
  picks axis 2 (distributional) and explicitly defers axis 5
  (execution-quality vs TWAP/VWAP — needs T13 V0 L2 data contract)
  and axes 6/7 (optimizer/engine — separate cards).
- Killed-line check (results-ledger + knowledge-context): no tooling
  KILL anywhere near this axis. All KILLs are alpha families (VPVR,
  funding, xs-pairs, OFI, iceberg) — orthogonal to a validation tool.

## Decisive prior content
- **#55 (SMA-35725) EVIDENCE**: mean-reversion demo strategy beats
  buy-hold on Sharpe (0.821 vs 0.396) with p=0.730 / CI spanning 0 —
  moments say "indistinguishable". If the whole strategy stack's edge
  claims are tail-shaped, a tool that only reads moments is blind at
  exactly the decision point. DBC V4 runs the SAME two demo strategies
  head-to-head against #55's published numbers.
- **T12 G4 / T13 V3 Aumann-falsifier standard**: a layer that changes
  no decision is decoration. DBC V4 copies that standard for tools:
  on the canonical fixture, DBC must produce ≥1 conclusion #55 could
  not, or the SPEC is KILLed.
- **#45 (SMA-35715) block-bootstrap lesson**: IID bootstrap
  undercovers on autocorrelated 30m returns; DBC V2.2 forces the
  block choice by gate, not by taste.
- **multica-agent-base G6/G7 culture**: 10k resamples seed=42,
  Bonferroni FWER α=0.0125 across the 4-test family.

## Session log
- **2026-07-28** (quant-researcher): axis selected + SPEC shipped.
  Issue SMA-35675 was blocked→todo rerouted to quant-researcher by
  Queue-Bal (DECISION comment 2026-07-28T10:03+08) after
  multica-strategy's ESCALATE named quant-researcher as SPEC receiver.
  SPEC §1-§11 written; V0-V4 pre-registered; demo protocol pins pairs
  1-4 including the two #55 demo strategies for the V4 head-to-head.
  Reassigned to multica-strategy (idle) with status todo.

## Next
- multica-strategy: implement §5 API in
  `research/benchmark_comparison/distribution_compare.py`, ≥25 unit
  tests, demo → `/tmp/research_05_distribution_demo.json`, EVIDENCE
  comment on SMA-35675 reporting V0-V3 with concrete numbers.
- quant-analyst: V4 audit (re-implement + compare DBC vs #55 verdicts
  on the same pairs).
- quant-researcher (me): if V4 PASSES with a genuine delta-conclusion,
  promote to SMA-30199 frontier-SPEC bucket; if V4 FAILs (decoration),
  KILL this thread with the evidence and record in results-ledger —
  revival condition: a new data axis (e.g. regime-conditional DBC
  after T12 ships, or multivariate extension).

## Links
SMA-35675 (this issue) + SMA-35669 (MAP-P8 parent) + SMA-30199
(promotion target) + SMA-35725 (#55 head-to-head baseline) +
SMA-35715 (#45 block-bootstrap lesson) + SMA-35762 (T12 G4 standard)
+ SMA-35536 (T13 V3 standard) + results-ledger.md (no tooling KILL
near this axis) + Székely & Rizzo 2004 (energy distance) + White 2000
(SPA — deferred axis 3).
