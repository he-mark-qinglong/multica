# SPEC — Distributional Benchmark Comparison (DBC v1)

**Issue**: SMA-35675 — [SMA-35669-005] Research #5 — benchmark comparison
**Parent project**: SMA-35669 (MAP-P8 — Research & Validation Tools; walk-forward / CPCV / attribution / scenario analysis)
**Frontier-SPEC bucket**: SMA-30199 (promotion target after V-gate acceptance)
**Author**: quant-researcher (78069161-efaa-493c-9561-d72a130c5926)
**Date**: 2026-07-28
**Status**: SPEC → handed to multica-strategy (L3) for implementation + V-gate run

## 1. Purpose

Provide a **full-distribution benchmark-comparison primitive** for the
MAP-P8 validation stack. Every sibling tool in
`research/benchmark_comparison/` compares *summary statistics* of
strategy vs benchmark (α / β / IR / Sharpe / capture ratios). None
answers the question that actually kills crypto strategies:

> "Is the strategy's return **distribution** different from the
> benchmark's — and if so, **where**: location, scale, or tails?"

Two return series can have identical mean, variance, Sharpe and beta
while one carries a fat left tail and the other doesn't. #55's
moment-based tests report "no significant excess" for both. For a
stack whose strategy gates include max_drawdown < 25% (G4) and whose
whole edge claims are tail-shaped (mean-reversion = short-vol profile,
momentum = long-vol profile), the distributional layer is the missing
validation instrument.

DBC answers three pre-registered questions per (strategy, benchmark)
pair:

- **Q1 (difference)**: Do the two return distributions differ at all?
  → two-sample KS statistic + energy distance, each with a
  permutation p-value.
- **Q2 (magnitude)**: How large is the typical discrepancy, in return
  units? → Wasserstein-1 distance with a block-bootstrap 95% CI.
- **Q3 (attribution)**: Where does the difference live? → quantile-
  difference decomposition (location / scale / residual-shape) +
  tail-quantile gap table (q01/q05/q95/q99) + CVaR(5%) gap, each with
  block-bootstrap CIs.

This is a **validation tool**, not an alpha signal. It produces no
trades, no positions, no sizing. It consumes two return series and
emits a JSON evidence block.

## 2. Prior content — dedup vs siblings (mandatory, per ESCALATE on SMA-35675)

Sibling Research #N lenses already shipped in this EPIC (all return-
space, moment-based or equity-curve-based):

| # | lens | issue | overlap with DBC |
|---|---|---|---|
| #25 | underwater / drawdown profile (pain, Ulcer, recovery) | SMA-35695 | none — path-space, not distribution-space |
| #35 | regime-conditional α / IR / concentration | SMA-35705 | none — conditioning layer, DBC is unconditional; composable |
| #45 | multi-benchmark composite + block bootstrap + rolling IR | SMA-35715 | shares block-bootstrap *mechanism* only (§4.4), not content |
| #55 | single-benchmark α / β / TE / IR + IID bootstrap + permutation | SMA-35725 | shares permutation *mechanism* only; DBC has no α/β/IR |
| #65 | cross-framework aggregator | SMA-35735 | none — orchestration layer |
| #75 | CAPM α / β / R² / up-down capture + bootstrap CI | SMA-35745 | none — factor-model space |
| #95 | equity-curve leaderboard + pairwise correlation + CI | SMA-35765 | none — cross-strategy, DBC is strategy-vs-benchmark |

**Zero content overlap.** Every sibling compares first/second moments
or equity-curve paths; DBC compares full CDFs and tail functionals.
The KILL ledger (results-ledger.md) contains no tooling line anywhere
near this axis — killed lines are all alpha families (VPVR, funding,
xs-pairs, OFI, iceberg), which DBC does not touch.

## 3. Architecture

Single file, family convention, dependency-light:

```
quant-loop/research/benchmark_comparison/distribution_compare.py   # public API, pure numpy + scipy, no I/O
quant-loop/research/benchmark_comparison/test_distribution_compare.py  # ≥25 unit tests
quant-loop/research/benchmark_comparison/run_distribution_demo.py      # canonical-fixture demo → JSON
quant-loop/research/benchmark_comparison/DISTRIBUTION_COMPARE.md       # API docs + conventions + limitations
```

Matches the #55 file layout exactly (`benchmark_compare.py` +
`test_benchmark_compare.py` + `run_btc30m_demo.py` + `README.md`), so
the L3 implementer has a working template in the same directory.

### 3.1 Parameters (defaults, all configurable)

| param | default | meaning |
|---|---|---|
| `n_perm` | 10 000 | permutation resamples for Q1 p-values |
| `n_boot` | 10 000 | block-bootstrap resamples for Q2/Q3 CIs |
| `block_len` | 48 | bootstrap block length in bars (48 × 30m = 24h; must be ≥ dominant autocorrelation horizon of the input) |
| `tail_quantiles` | (0.01, 0.05, 0.95, 0.99) | Q3 tail table |
| `cvar_alpha` | 0.05 | expected-shortfall level for Q3 |
| `seed` | 42 | all RNG seeded (G6 culture: 10 000 resamples, seed=42) |
| `fwer_alpha` | 0.0125 | Bonferroni per-test level across the 4-test family (G7 culture: 0.05/4) |

## 4. Methods — datacontract + sub-gate per method

### 4.1 Q1 — two-sample KS + energy distance (permutation p)

- KS: `scipy.stats.ks_2samp` statistic only; p-value via permutation
  (pool, reshuffle, split), NOT the asymptotic approximation — the
  30m crypto return series has tied/discretized bars and
  autocorrelation; permutation under the null of exchangeability is
  the honest reference. Autocorrelation caveat documented: permutation
  p-values are anti-conservative under strong autocorrelation; the
  companion block-bootstrap CI (Q2) is the primary magnitude claim.
- Energy distance (Székely & Rizzo 2004): E|X−Y′| + E|X′−Y| − 2E|X−Y|
  form on the two samples; permutation p-value same protocol.
- Sub-gate **V2.1**: on N=500 synthetic null pairs (both samples drawn
  from the same Student-t(ν=4) fitted to BTC 30m moments), the
  empirical false-positive rate of each Q1 test at α=0.0125 must lie
  in [0.002, 0.030]. Outside → test is miscalibrated, method FAILs.

### 4.2 Q2 — Wasserstein-1 distance + block-bootstrap CI

- W1 = `scipy.stats.wasserstein_distance` (1-D exact).
- CI: stationary/circular block bootstrap (block_len default 48),
  resample *paired* (strategy, benchmark) blocks jointly to preserve
  cross-correlation, recompute W1 per resample, percentile CI.
- Sub-gate **V2.2**: on a synthetic shifted pair (same shape, location
  shift δ known), the 95% CI must contain the true W1 in ≥ 90% of 500
  simulations. On AR(1) returns (ρ=0.3, realistic for 30m crypto),
  IID-bootstrap CI coverage must be reported AND block-bootstrap
  coverage must land in [0.90, 0.99] — this is the test that forces
  block (not IID) resampling, the #45 lesson.

### 4.3 Q3 — quantile-decomposition + tail table + CVaR gap

- Quantile-difference decomposition on u-grid {0.01..0.99, step 0.01}:
  ΔQ(u) = Q_s(u) − Q_b(u) = **location** (Δ at median) +
  **scale** × (Q_b(u) − Q_b(0.5)) + **residual-shape**(u), where
  scale = IQR_s / IQR_b. Reports the R² of the location+scale fit:
  how much of the distributional difference is *just* a shift+stretch
  vs genuine shape change.
- Tail table: Δ at tail_quantiles with paired block-bootstrap CIs.
- CVaR(α) gap: ES_s(5%) − ES_b(5%), block-bootstrap CI. Direct input
  to the G4 max-drawdown conversation: a strategy can beat buy-hold
  on Sharpe while losing on ES(5%).
- Sub-gate **V2.3 (power)**: on synthetic pairs with known injected
  differences — (a) pure location shift 2bp/bar, (b) pure scale ×1.3,
  (c) pure tail change (Student-t ν=8 vs ν=3, same mean/var) — at
  n=79 000 (canonical fixture size) the correct decomposition
  component must be flagged dominant in ≥ 90% of 200 simulations per
  case. This is the gate that proves the attribution is not noise.

### 4.4 Shared resampling layer

Block-bootstrap and permutation helpers are module-private
(`_block_bootstrap_pairs`, `_permute_pvalue`) so #55/#45 keep their
own implementations untouched — no refactor of sibling code, no
shared-mutable-state risk. Duplication of ~40 lines of resampling
code is accepted deliberately (family convention: each tool file is
self-contained and revertible).

## 5. Public API contract

```python
# File: research/benchmark_comparison/distribution_compare.py
# Pure numpy + scipy. No I/O, no pandas dependency in core (demo script may use pandas).

def ks_test_perm(r_s, r_b, n_perm=10000, seed=42) -> dict
    # {"stat": float, "p_value": float, "n_perm": int}

def energy_distance_perm(r_s, r_b, n_perm=10000, seed=42) -> dict
    # {"stat": float, "p_value": float, "n_perm": int}

def wasserstein_gap(r_s, r_b, n_boot=10000, block_len=48, seed=42) -> dict
    # {"w1": float, "ci_lo": float, "ci_hi": float, "unit": "return_per_bar"}

def quantile_decomposition(r_s, r_b, u_grid=None) -> dict
    # {"location": float, "scale": float, "r2_location_scale": float,
    #  "residual_max_abs": float, "u_grid": [...], "delta_q": [...]}

def tail_gap_table(r_s, r_b, quantiles=(0.01,0.05,0.95,0.99),
                   n_boot=10000, block_len=48, seed=42) -> dict
    # {"quantiles": [...], "delta": [...], "ci_lo": [...], "ci_hi": [...],
    #  "cvar_alpha": 0.05, "cvar_gap": float, "cvar_ci_lo": float, "cvar_ci_hi": float}

def compare_distributions(r_s, r_b, **kw) -> dict
    # Orchestrator: runs Q1+Q2+Q3, applies Bonferroni FWER across the 4-test family,
    # returns the full JSON evidence block + "verdict" field:
    #   "DIFFERENT_SHAPE"  — Q1 significant after FWER AND r2_location_scale < 0.9
    #   "SHIFT_OR_SCALE"   — Q1 significant AND r2_location_scale >= 0.9
    #   "INDISTINGUISHABLE"— no Q1 test significant after FWER
    #   "TAIL_RISK_WARNING"— any of the above AND cvar_gap CI excludes 0 on the adverse side
```

All functions accept array-like, return JSON-serializable dicts, never
print, never read files, raise `ValueError` on n < 200 per series
(below which tail claims are meaningless at this bar frequency).

## 6. Pre-registered verification gates (V0-V4)

| gate | criterion | owner |
|---|---|---|
| **V0** | dependency-light: core module imports numpy + scipy only; no pandas, no I/O, no network. Sibling layout mirrored. | strat impl |
| **V1** | statistical correctness: KS matches `scipy.stats.ks_2samp` stat to 1e-12 on 10 analytic cases; W1 matches `scipy.stats.wasserstein_distance` to 1e-12; energy distance matches the closed-form E-distance on Gaussian pairs (analytic reference) to 1e-6 relative. | strat impl |
| **V2** | calibration + power: V2.1 (null calibration), V2.2 (CI coverage, forces block bootstrap), V2.3 (attribution power ≥ 90% per case) all PASS. | strat-validation |
| **V3** | ≥ 25 unit tests, all PASS; demo on canonical fixture runs end-to-end and emits `/tmp/research_05_distribution_demo.json`. | strat-validation |
| **V4 (Aumann-falsifier for tools)** | On the canonical fixture demo, DBC must produce ≥ 1 conclusion that #55's moment-based toolkit could NOT have produced — i.e., at least one (strategy, benchmark) pair where #55-style tests say "not significant" but DBC flags DIFFERENT_SHAPE or TAIL_RISK_WARNING (or vice versa: DBC shows a scary-looking Sharpe gap is pure location shift). If every DBC verdict is informationally identical to what #55 already reports on the same pairs, the tool is decoration → **KILL the SPEC, record in results-ledger, do not retry without a new data axis**. | quant-analyst (re-implementation audit) |

V4 is the load-bearing gate — same standard as T12 G4 and T13 V3: a
validation layer that doesn't change any decision is decoration.

## 7. Demo protocol (canonical fixture)

Data: `data/perp_30m/{BTCUSDT,ETHUSDT,SOLUSDT}_30m.parquet`
(n ≈ 79k bars each — the EPIC reference fixture, same as #55 demo).

Pairs (all per-bar simple returns, 30m frequency):

1. **Self-check**: BTC buy-hold vs BTC buy-hold → all stats ≈ 0,
   p-values uniform-ish, verdict INDISTINGUISHABLE. Sanity guard.
2. **Momentum rule** (long when 10-bar return > 0, else flat) vs BTC
   buy-hold — the exact #55 demo strategy, so V4 can compare verdicts
   head-to-head against the #55 EVIDENCE numbers (Sharpe −0.299,
   permutation p = 0.302, CI includes 0).
3. **Mean-reversion rule** (long when 10-bar return < 0, else flat) vs
   BTC buy-hold — same reason (#55: Sharpe 0.821, p = 0.730,
   P(outperform) = 0.734 — the case where moments say "fine" and the
   short-vol tail shape is the real story).
4. **ETH buy-hold vs BTC buy-hold** and **SOL buy-hold vs BTC
   buy-hold** — cross-asset distributional benchmark, the pair every
   cross-sectional strategy implicitly bets on.

EVIDENCE comment format: verbatim copy of #55's §Result Wire layout —
files table, test summary, per-pair JSON numbers, then the V4
delta-conclusion (what DBC saw that #55 could not).

## 8. Repo path + ownership

- Code target: `quant-loop/research/benchmark_comparison/` (family
  convention, confirmed by #25/#35/#45/#55/#75 evidence comments).
  New files only; **no edits to existing sibling files**.
- Working tree only — no commit, no push (multica-agent-base §4.3).
- Implementation + unit tests + demo: **multica-strategy** (L3) —
  explicitly requested this handoff ("I will pick up the issue and
  run L3 validation the moment a SPEC lands", ESCALATE 2026-07-26).
- V4 re-implementation audit: quant-analyst (L4), after L3 EVIDENCE
  lands.

## 9. Risk + reversibility

Pure additive tooling. Delete the 4 files to revert. No shared-state
mutation, no changes to `_shared/`, no strategy code touched. Worst
case is a wrong p-value — caught by V1/V2 before any downstream use.

## 10. Out of scope (v1)

- Multivariate (joint BTC/ETH/SOL) distribution comparison — v2, needs
  energy-distance multivariate extension + different calibration.
- Regime-conditional DBC (compose with #35 / T12 HMM posteriors) —
  natural v2 once T12 V-gates clear.
- Execution-quality benchmarking (axis 5 from the ESCALATE menu —
  TWAP/VWAP/arrival-mid vs aggTrades) — deliberately NOT chosen: needs
  L2 depth data contract (T13 V0 territory) and is a separate SPEC.
- Optimizer / engine benchmarks (axes 6/7) — separate cards.

## 11. References

- Székely & Rizzo (2004) — testing for equal distributions (energy distance).
- Romano & Wolf / block bootstrap coverage literature — §4.2 design.
- White (2000) SPA test — motivation for honest resampling reference distributions (not implemented here; axis 3 remains open for a future card).
- Sibling SPEC/evidence: SMA-35725 (#55), SMA-35715 (#45), SMA-35695 (#25), SMA-35765 (#95).
- multica-agent-base §strategy-layer G6 (10k bootstrap, seed 42) + G7 (FWER Bonferroni α=0.0125) culture.
- T12 G4 / T13 V3 — Aumann-falsifier-for-layers standard that V4 copies.
