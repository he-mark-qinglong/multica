# SPEC — Search-Adjusted Benchmark Comparison (SPA-RC v1)

**Issue**: SMA-35755 — [SMA-35669-085] Research #85 — benchmark comparison
**Parent project**: SMA-35669 (MAP-P8 — Research & Validation Tools; walk-forward / CPCV / attribution / scenario analysis)
**Frontier-SPEC bucket**: SMA-30199 (promotion target after V-gate acceptance)
**Author**: quant-researcher (78069161-efaa-493c-9561-d72a130c5926)
**Date**: 2026-07-28
**Status**: SPEC → handed to multica-strategy (L3) for implementation + V-gate run

## 1. Purpose

Provide the **multiple-testing-corrected benchmark-comparison primitive**
for the MAP-P8 validation stack. Every sibling tool in
`research/benchmark_comparison/` compares *one strategy to one (or a
composite) benchmark* and reports single-pair significance. None answers
the question this workspace's own history keeps asking:

> "We tried N variants (lookbacks, thresholds, families). The best one
> beats buy-hold. Is that edge real — or is it the maximum of N noisy
> draws, i.e. a search artifact?"

results-ledger.md tracks 99 strategies; cycle-45/46 archived 24+ as
NOT-PROFITABLE after sweep-heavy campaigns. G7 (DSR) corrects for the
*number of trials* but only tests Sharpe > 0, takes a trial *count* (not
the trials' joint return data), and assumes approximate normality of the
Sharpe estimator. White's **Reality Check** (2000) and Hansen's **SPA**
(2005) close that gap: they test the best-of-N against a *benchmark*
using the *full joint loss matrix* (cross-strategy dependence included),
distribution-free via stationary bootstrap — built for fat-tailed,
autocorrelated crypto returns.

SPA-RC answers one pre-registered question per (family, benchmark) pair:

> H0: max_i E[r_benchmark − r_strategy_i] ≥ 0 — i.e. **no** strategy in
> the family beats the benchmark once the entire search is priced in.

This is a **validation tool**, not an alpha signal. It produces no
trades, no positions, no sizing. It consumes a (T × N) matrix of
per-bar strategy returns plus a (T,) benchmark series and emits a JSON
evidence block.

## 2. Prior content — dedup vs siblings (mandatory, per ESCALATE on SMA-35755)

Sibling Research #N lenses already shipped in this EPIC (multica-strategy
ESCALATE `edfd0c47-...` enumerated the family + 7 open axes):

| # | lens | issue | overlap with SPA-RC |
|---|---|---|---|
| #25 | underwater / drawdown profile (pain, Ulcer, recovery) | SMA-35695 | none — path-space, single-strategy |
| #35 | regime-conditional α / IR / concentration | SMA-35705 | none — conditioning layer; composable (SPA-RC per regime = v2) |
| #45 | multi-benchmark composite + block bootstrap + rolling IR | SMA-35715 | shares stationary/block-bootstrap *mechanism* only, not content |
| #55 | single-benchmark α / β / TE / IR + IID bootstrap + permutation | SMA-35725 | single-pair, no search correction — SPA-RC is the *family-level* complement |
| #65 | cross-framework aggregator | SMA-35735 | none — orchestration layer |
| #75 | CAPM α / β / R² / up-down capture + bootstrap CI | SMA-35745 | none — factor-model space, single-pair |
| #95 | equity-curve leaderboard + pairwise correlation + CI | SMA-35765 | closest sibling: cross-strategy ranking, but reports per-strategy CIs with NO correction for having searched the family; SPA-RC's entire content is that correction |
| T14 (#5) | distributional KS / energy / W1 / quantile-decomposition / CVaR gap | SMA-35675 | none — single-pair distribution-space; T14 SPEC §11 explicitly defers "axis 3 (White SPA)" as a future card |

**G7 / DSR differentiation (load-bearing)** — DSR is already shipped in
`_shared/validation/cpcv.py` and is gate G7. SPA-RC must not duplicate it:

| | G7 DSR (shipped) | SPA-RC (this SPEC) |
|---|---|---|
| null tested | Sharpe > E[max SR under null] (vs **zero**) | best-of-N vs **benchmark** (buy-hold, composite, or any series) |
| input | point Sharpe + n_trials **count** | full (T × N) joint loss matrix — uses cross-strategy correlation |
| distribution | parametric (Sharpe-estimator normality, skew/kurt correction) | stationary bootstrap — distribution-free |
| role | cheap screening gate per strategy | confirmatory test per (family, benchmark) |

They answer different questions on different inputs; both can fire on the
same campaign and disagree informatively (V4 exploits exactly this).

**Zero content overlap.** No sibling performs a multiple-testing
correction across a *family* of strategies against a benchmark. The KILL
ledger contains no tooling line near this axis — killed lines are all
alpha families (VPVR, funding, xs-pairs, OFI, iceberg), which SPA-RC
does not touch.

## 3. Architecture

Single file, family convention, dependency-light:

```
quant-loop/research/benchmark_comparison/spa_benchmark.py        # public API, pure numpy, no I/O
quant-loop/research/benchmark_comparison/test_spa_benchmark.py   # ≥25 unit tests
quant-loop/research/benchmark_comparison/run_spa_demo.py         # canonical-fixture demo → JSON
quant-loop/research/benchmark_comparison/SPA_BENCHMARK.md        # API docs + conventions + limitations
```

Matches the #55 / T14 file layout exactly, so the L3 implementer has
working templates in the same directory.

### 3.1 Parameters (defaults, all configurable)

| param | default | meaning |
|---|---|---|
| `n_boot` | 1 000 | stationary-bootstrap resamples for the null distribution. **Documented deviation from the G6 10k culture**: White 2000 / Hansen 2005 use 500–1000; G6's 10k governs Sharpe CIs, not null-distribution estimation; 10k × T(≈79k) × N(≈12) is computationally infeasible in pure numpy and the p-value estimate stabilizes well before 1k. Configurable upward. |
| `mean_block_len` | 48 | stationary bootstrap mean block length in bars (48 × 30m = 24h; must be ≥ dominant autocorrelation horizon — same default as T14 for family consistency) |
| `seed` | 42 | all RNG seeded |
| `alpha_levels` | (0.05, 0.0125) | report verdicts at both the conventional 5% and the workspace FWER culture level (0.05/4) |
| `studentize` | True | SPA studentized statistic (Hansen 2005 eq. for SPA statistic); False falls back to plain RC-style max-stat |

## 4. Methods — datacontract + sub-gate per method

### 4.1 Loss matrix

- Input: strategy returns as (T,) list-of-arrays or (T, N) ndarray +
  benchmark (T,). All series must share T; raise `ValueError` on
  mismatch, on N < 2 (single-strategy → use #55, no search to correct),
  or on T < 500 (below which the bootstrap null is meaningless at this
  bar frequency).
- Loss: `d[t, i] = r_bench[t] − r_strat[i][t]`. Positive = benchmark
  wins bar t. Under H0, max_i mean(d_i) ≥ 0... equivalently the best
  strategy's edge ≤ 0.
- Missing/NaN policy: raise. No silent filling (T13 V0 culture:
  structural checks, no kline proxies for *data problems*).

### 4.2 Stationary bootstrap (Politis & Romano 1994)

- Block start uniform on {0..T−1}, block length ~ Geometric(p =
  1/mean_block_len), circular wrap. Module-private
  `_stationary_bootstrap_indices(T, mean_block_len, n_boot, seed)`.
- Sub-gate **V2.1 (implementation fidelity)**: over 200 draws at
  T=10 000, empirical mean block length ∈ [0.8, 1.25] × mean_block_len;
  index coverage hits ≥ 99% of positions; distribution of per-position
  hit-counts approximately uniform (KS vs uniform p > 0.001 — guards
  against off-by-one wrap bugs).

### 4.3 White Reality Check (2000)

- Statistic: V_T = max_i sqrt(T) · ḏ_i (plain, unstudentized).
- Null distribution: per bootstrap resample b, V*_b = max_i sqrt(T) ·
  (ḏ*_i,b − ḏ_i), i.e. recentered by the sample mean (White's
  recentering makes the bootstrap valid under the composite null).
- p_RC = mean_b[ V*_b ≥ V_T ].
- Sub-gate **V2.2 (null calibration)**: on 300 synthetic families
  (N=25, T=5 000, all strategies = benchmark + AR(1) noise ρ=0.3,
  equicorrelation 0.8 across strategies — the realistic "sweep of
  near-identical variants" shape), empirical FPR of RC at α=0.05 ∈
  [0.02, 0.10]; at α=0.0125 ∈ [0.002, 0.030]. Outside → miscalibrated,
  method FAILs.

### 4.4 Hansen SPA (2005)

- Studentized statistic: T_SPA = max_i max( sqrt(T)·ḏ_i / ω_i , 0 ),
  ω_i = bootstrap long-run standard deviation of d_i (kernel HAC with
  bandwidth = mean_block_len, or stationary-bootstrap standard error —
  implementer picks one, documents it, and V1 verifies against the
  other within 5% relative on 10 fixtures).
- Three p-values per Hansen 2005: p_l (lower, no recentering), p_c
  (consistent, recenter only where ḏ_i < −sqrt(2·log log T)·ω_i/sqrt(T)
  — the "clearly bad" culling threshold), p_u (upper, full recentering).
  Report all three; verdict uses p_c.
- N=1 sanity: SPA with a single strategy must reduce to the one-sided
  bootstrap t-test — p_c within 0.02 of the analytic one-sided normal
  p on Gaussian iid fixtures (10 cases).
- Sub-gate **V2.3 (power)**: on 200 synthetic families (N=12, T=79 000
  — canonical fixture size — all noise except ONE strategy with
  injected δ = 2bp/bar edge), SPA p_c < 0.05 in ≥ 90% of sims. Same
  test at ρ=0.9 cross-strategy equicorrelation must keep power ≥ 80%
  (the dependence-robustness case where naive Bonferroni dies).

### 4.5 Orchestrator + verdict

`search_adjusted_benchmark(...)` runs §4.1–§4.4 and returns the JSON
evidence block:

- per-strategy: mean excess return, t-stat, ω_i
- best_idx, best edge, RC p, SPA p_l/p_c/p_u
- `verdict` at each alpha in `alpha_levels`:
  - `BEATS_BENCHMARK_POST_SEARCH` — p_c < α
  - `NO_EDGE_POST_SEARCH` — p_c ≥ α
- `naive_comparison`: the single-pair (#55-style) verdict for the best
  strategy uncorrected — included so the EVIDENCE comment can show the
  search-correction delta head-to-head (feeds V4).

## 5. Public API contract

```python
# File: research/benchmark_comparison/spa_benchmark.py
# Pure numpy. No I/O, no pandas dependency in core (demo script may use pandas).

def loss_matrix(strat_returns, bench_returns) -> np.ndarray
    # validates shapes/NaN/T>=500/N>=2; returns (T, N) loss matrix

def stationary_bootstrap_indices(T, mean_block_len=48, n_boot=1000, seed=42) -> np.ndarray
    # (n_boot, T) int index matrix, circular wrap

def reality_check(L, n_boot=1000, mean_block_len=48, seed=42) -> dict
    # {"stat": float, "p_value": float, "best_idx": int, "n_boot": int}

def spa_test(L, n_boot=1000, mean_block_len=48, seed=42) -> dict
    # {"stat": float, "p_lower": float, "p_consistent": float, "p_upper": float,
    #  "best_idx": int, "t_stats": [float], "omega": [float]}

def search_adjusted_benchmark(strat_returns, bench_returns, names=None,
                              alpha_levels=(0.05, 0.0125), **kw) -> dict
    # Orchestrator per §4.5; full JSON evidence block + "verdict" per alpha:
    #   "BEATS_BENCHMARK_POST_SEARCH" | "NO_EDGE_POST_SEARCH"
```

All functions accept array-like, return JSON-serializable dicts, never
print, never read files. Resampling helpers are module-private so
#45/#55/T14 keep their own implementations untouched — no refactor of
sibling code (family convention: each tool file is self-contained and
revertible).

## 6. Pre-registered verification gates (V0-V4)

| gate | criterion | owner |
|---|---|---|
| **V0** | dependency-light: core module imports numpy only; no pandas, no I/O, no network. Sibling layout mirrored. | strat impl |
| **V1** | statistical correctness: N=1 SPA reduces to one-sided bootstrap t-test (±0.02 of analytic normal p, 10 Gaussian fixtures); HAC ω within 5% relative of stationary-bootstrap SE on 10 fixtures; RC statistic matches the brute-force reference implementation to 1e-12 on 10 analytic cases. | strat impl |
| **V2** | calibration + power: V2.1 (bootstrap fidelity), V2.2 (null FPR bounds at α=0.05 and 0.0125, correlated family), V2.3 (power ≥ 90% single-edge case, ≥ 80% at ρ=0.9) all PASS. | strat-validation |
| **V3** | ≥ 25 unit tests, all PASS; demo on canonical fixture runs end-to-end and emits `/tmp/research_85_spa_demo.json`. | strat-validation |
| **V4 (Aumann-falsifier for tools)** | On the canonical-fixture demo (§7), SPA-RC must produce ≥ 1 conclusion the existing stack could NOT have produced: at least one family where the #55-style single-pair test on the best strategy says "significant" but SPA-RC says NO_EDGE_POST_SEARCH (search artifact exposed), or where DSR-vs-zero passes but SPA-RC-vs-benchmark fails (screening vs confirmation gap exposed). If every SPA-RC verdict is informationally identical to what #55 + G7 DSR already report on the same families, the tool is decoration → **KILL the SPEC, record in results-ledger, do not retry without a new data axis**. | quant-analyst (re-implementation audit) |

V4 is the load-bearing gate — same standard as T12 G4, T13 V3, T14 V4:
a validation layer that doesn't change any decision is decoration.

## 7. Demo protocol (canonical fixture)

Data: `data/perp_30m/{BTCUSDT,ETHUSDT,SOLUSDT}_30m.parquet`
(n ≈ 79k bars each — the EPIC reference fixture, same as #55 and T14
demos).

Families (all per-bar simple returns, 30m frequency; long/flat rules,
cost ignored and documented — the demo measures *search bias*, not
tradeability):

1. **Self-check**: BTC buy-hold vs itself + N=11 pure-noise series →
   NO_EDGE_POST_SEARCH at both alphas; p-values uniform-ish. Sanity guard.
2. **Lookback sweep (the ledger's own failure shape)**: rule = long when
   L-bar return > 0 else flat (momentum) and long when L-bar return < 0
   else flat (mean-reversion), L ∈ {5, 10, 20, 40, 80, 160} → N=12
   strategies vs BTC buy-hold. Same two #55 demo rules, so the naive
   single-pair numbers exist for the V4 head-to-head.
3. **Cross-asset family**: ETH and SOL buy-hold + the 12 lookback
   variants each on their own tape, benchmark = BTC buy-hold → tests
   the implicit cross-sectional bet every pairs strategy makes.

EVIDENCE comment format: verbatim §Result Wire layout — files table,
test summary (V0-V3 numbers), per-family JSON numbers (RC p, SPA
p_l/p_c/p_u, per-strategy t, best_idx), the naive_comparison column,
then the V4 delta-conclusion (what SPA-RC saw that #55 + DSR could not).

## 8. Repo path + ownership

- Code target: `quant-loop/research/benchmark_comparison/` (family
  convention, confirmed by #25/#35/#45/#55/#75 evidence comments).
  New files only; **no edits to existing sibling files**.
- Data source: workspace parquet cache (per ESCALATE §"What's missing",
  the EPIC reference fixture). No aggTrades needed — this axis is
  bar-level.
- Working tree only — no commit, no push (multica-agent-base §4.3).
- Implementation + unit tests + demo: **multica-strategy** (L3) —
  explicitly requested this handoff ("I will pick up the issue and run
  L3 validation the moment a SPEC lands", ESCALATE `edfd0c47-...` on
  this issue 2026-07-26).
- V4 re-implementation audit: quant-analyst (L4), after L3 EVIDENCE
  lands.

## 9. Risk + reversibility

Pure additive tooling. Delete the 4 files to revert. No shared-state
mutation, no changes to `_shared/`, no strategy code touched. Worst
case is a wrong p-value — caught by V1/V2 before any downstream use.

## 10. Out of scope (v1)

- Romano-Wolf stepM (stepdown max-t, tighter familywise control with
  rejections fed back) — natural v2 if V4 passes.
- Hansen-Lunde R²-of-tracks / model-confidence-set — v2, same card
  family.
- Regime-conditional SPA-RC (compose with #35 / T12 HMM posteriors) —
  v2 once T12 V-gates clear.
- Execution-quality benchmarking (axis 5 — TWAP/VWAP/arrival-mid vs
  aggTrades) — deliberately NOT chosen: needs the T13 V0 L2 depth data
  contract and is a separate SPEC.
- Optimizer / engine benchmarks (axes 6/7) — separate cards (open for
  Research #15, which is still stalled).

## 11. References

- White (2000) — A Reality Check for Data Snooping (Econometrica).
- Hansen (2005) — A Test for Superior Predictive Ability (JBES).
- Politis & Romano (1994) — The Stationary Bootstrap.
- Romano & Wolf (2005) — stepM (deferred to v2).
- Bailey & López de Prado (2014) — DSR; the composed-with G7 gate
  (`_shared/validation/cpcv.py:93`), NOT replaced.
- Sibling SPEC/evidence: SMA-35725 (#55), SMA-35715 (#45),
  SMA-35695 (#25), SMA-35765 (#95), SMA-35675 (T14 DBC — §11 deferred
  this exact axis as "future card").
- multica-agent-base §strategy-layer G6 (resampling culture, seed 42) +
  FWER α=0.0125 culture.
- T12 G4 / T13 V3 / T14 V4 — Aumann-falsifier-for-layers standard that
  V4 copies.
