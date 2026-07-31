# T14 — Performance Attribution (strategy-level PnL decomposition)

- Status: **shipped** (2026-07-28) — tool + SPEC + donor EVIDENCE. Not a strategy thread; a research-infrastructure thread.
- Issue: SMA-35757 (Research #87), parent SMA-35669 (MAP-P8 Research & Validation Tools)
- SPEC: `research/specs/performance_attribution_v1_20260728/SPEC.md`
- Code: `_shared/attribution/` (decompose.py, test_decompose.py, README.md — opt-in library convention)
- Evidence: `analysis/attribution/` (run_donor_attribution.py + mtf_h3_btcsol_attribution.json + trend_multi_btc_attribution.json)

## Question

Can "was this strategy killed by cost or by mechanism?" be made mechanical —
one function from closed-trade ledger to gross-vs-cost decomposition +
classification — instead of re-derived ad-hoc per issue (as happened in
cycle-46 for T01/T04, T11, and the mtf_xs_pairs fee-shock seal)?

## Method (chosen over alternatives)

- NOT Brinson (no sectors/benchmark weights in crypto perp single/pair strategies).
- Cost attribution: gross recomputed from entry/exit prices (ledger `pnl_pct` never trusted — cost conventions differ per strategy), cost parameterized by CostSpec, swept over scenarios, break-even solved analytically.
- Conditional cuts: exit_reason / direction / year / holding-time quartile.
- Classification: MECHANISM_KILL (gross≤0) / COST_CAP_KILL (gross>0, net≤0) / VIABLE_AT_COST.
- Sentinels (T11 lesson): reject exit<entry, price≤0, unknown direction, missing column.
- Optional alpha_beta() OLS split (T07-adjacent).

## Gates (A0-A6, pre-registered in SPEC §5)

A0-A5 PASS (12/12 unit tests; A3 exact reproduction of ledger Σpnl_pct,
abs_err 7.8e-13 on 44,845 trades; A4 byte-deterministic across runs).
**A6 FAIL — pre-registered premise false, finding recorded (not redefined).**

## Key findings

1. **H3 (mtf_xs_pairs BTC/SOL, 44,845 trades, 2022-01→2026-07)**: gross edge
   +0.5 bp/trade (gross_sum +2.257); net NEGATIVE at every cost ≥ its own
   config (8bp RT): −33.62 @ 8bp, −195.06 @ 44bp ratified, −213.00 @ 24bp,
   −535.88 @ 60bp. Break-even 0.126 bps/side — zero cost headroom. Verdict:
   COST_CAP_KILL at all realistic costs. Confirms the 2026-07-26 family seal
   mechanically.
2. **A6 finding**: summary.json "PROFITABLE" tag + Sharpe 2.32 came from a
   cost-FREE per-bar equity path (`mtf_xs_pairs_base_20260718.py:560`,
   pnl_per_bar has no cost term; cost only in the trade log, line 576).
   SMA-36566 fee-shock bug class. The ledger's own cost-inclusive trade
   stats (win_rate 0.326, PF 0.604) were already negative — the summary
   mixed gross Sharpe with net PF.
3. **H3 cuts (ratified cost)**: z_mean_revert exits carry ALL the gross
   edge (+52.6 gross over 12,899 trades, 36.9% net win rate, −3.2bp mean
   net); regime_break exits are a bleed (−46.5 gross over 31,674 trades).
   Edge lives in 25–240-bar holds; <3-bar holds are churn. Direction and
   year cuts are symmetric — no seasonality, no directional bias. The
   signal geometry works when allowed to mean-revert; regime_break
   force-exits destroy it.
4. **trend_multi BTC (245 trades)**: MECHANISM_KILL — gross_sum −0.865 even
   at zero cost. Cost is irrelevant; matches ledger Sharpe −3.63.
5. **口径 divergence**: tool break-even (0.126 bps/side, trade-log units) vs
   curator's equity-path estimate (20bps RT) — convention-driven
   (full-notional per-trade vs half-notional × sizing_scale × compounded
   per-bar). Open audit item for quant-analyst: unify the conventions so
   per-trade and equity-path attribution agree by construction.

## Consequences / next

- results-ledger verdicts can now be auto-annotated with
  cost_drag_ratio + kill-class from each strategy's trades CSV (batch job —
  strategy-worker territory, not this thread).
- quant-analyst audit item: do other strategy summaries share the
  cost-free bar_return pattern? (Check `_indicators/*base*.py` for
  pnl_per_bar without a cost term.)
- T07 portfolio-correlation can use alpha_beta() + per-strategy net daily
  series from this tool as inputs.
- Tool is opt-in (`_shared/attribution/README.md`); no strategy rewiring.

## Links

SMA-35757 (this thread's issue) + SMA-35669 (parent Research queue) +
SMA-34875 (H3 campaign — donor 1) + SMA-36566 (fee-shock bug class) +
SMA-35037/SMA-35021 (T01/T04 cost-cap kills — the pattern this tool
classifies) + SMA-36615/SMA-36661 (T11 mechanism kill — the other class) +
SMA-34900/SMA-34913 (ratified cost constants) + `_shared/execution/COST_CONVENTION.md` +
`_shared/execution/cost_model.py` (constants source) +
`execution/slippage_attribution_p7exec_043` (dedup: per-fill live layer) +
multica-agent-base §strategy-layer (cycle-46) + research-journal skill
(kill-with-reason; A6 gate-failure discipline per T11 lesson).
