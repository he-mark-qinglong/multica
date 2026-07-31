# T12 — HMM regime detector (frontier-SPEC, 4h refit, multi-asset)

## Status
**2026-07-26**: maturing → frontier-SPEC candidate. SPEC draft at
`research/specs/hmm_regime_detector_4h_20260726/SPEC.md` (path
relative to `~/multica/quant-loop/`). Awaiting strat-indicators
implementation pick-up against the pre-registered acceptance gates.

## Question
Does a **Markov-switching regime detector** on a 4h feature vector
(realized vol-of-vol + cross-asset funding-basis spread + return z-score)
deliver a posterior `p_t,k` whose **averaged-regime sizing** of a
representative donor strategy produces **OOS Sharpe > argmax-regime
sizing OOS Sharpe by > 0.3** (Aumann-falsifier)? If yes, the regime
layer is real, not decoration.

## Revival rationale (NOT a re-run of SMA-35002)

### Why SMA-35002 was KILLED (verbatim, 2026-07-19, smark/kimi 代签)
- Pre-SPEC gates 5/7 FAIL, 3 fatal (A prior-only edge / E synthetic
  calibration uninformative / F prior sensitivity > 0.3)
- Aumann-falsifier FAIL — Bayesian averaging did not outperform argmax
- Prior content (VPVR-distance + funding z-score) is the same signal
  already sub-gate on `funding_carry_asym` lineage data; Bayesian
  averaging cannot compensate
- 15m BTC single-asset scope; Gaussian emission on fat-tailed 15m returns
- **Revival criterion (recorded by strategy-worker-2 in the KILL-acknowledgement
  comment `29d855a0-...`)**: "only reopen if paired with a *new* prior
  content — one whose raw signal clears cost on the canonical pipeline
  *before* being folded into a Kim-filter posterior."

### What is different in T12 (proposal, this SPEC)
1. **NEW prior content** (the load-bearing change vs 35002):
   - `rvov_t` — 4h realized vol-of-vol (std of 1h realized vol over rolling 24h
     window, 4h sampled). Pure-derivation; no VPVR/funding prior content.
     Raw signal mechanism: stress is autocorrelated at 4h timescale
     (literature: Dreman 2013, Ch. 9; Andersen-Bollerslev-Diebold 2007).
   - `fbasis_t` — cross-asset funding-basis spread (per-8h funding rate
     `funding[s] − median(funding over symbols)`, z-scored). Per-symbol,
     4h-sampled via forward-fill. Mechanically distinct from the
     `funding_carry_asym` lineage (which used funding z-score as
     single-asset carry signal); this is a *relative* stress indicator
     across the perp complex.
   - `retz_t` — 4h close-to-close return z-score (rolling 30-bar z, winsorized
     at ±3σ to control fat-tail sensitivity). Carry signal for trend
     component. Pure derivation from close prices.
   - All three have NO content overlap with VPVR (T06/T08/T09 killed)
     and NO content overlap with microstructure taker flow (T01/T04
     killed at retail-taker execution).
2. **Timeframe is 4h, not 15m**. Per regime-macro SKILL: 15m refit =
   curve-fitting; 4h is tradeable. Imposes autocorr(p_t,k, 4h) < 0.5
   as refit-cadence-justification gate.
3. **Multi-asset coverage**: BTC + ETH + SOL jointly via **shared
   transition matrix + symbol-specific emissions**. Per-symbol model is
   rejected a priori (single-symbol HMM has too few bars per state to
   identify K=3 cleanly).
4. **Emission is heavy-tailed** (Student-t, ν estimated from EM) —
   directly addresses 35002's gate E (Gaussian emission uninformative
   on fat-tailed 15m returns).
5. **Aumann-falsifier is THE primary acceptance gate**, not a
   sub-criterion. If the regime layer doesn't beat argmax-regime
   sizing by > 0.3 OOS Sharpe, the entire spec is KILL.

## 2026-07-26 prior-content screening (this session, analytical)

For each of the three feature-vector components, the raw signal
(no HMM averaging) must clear cost on the canonical 4h BTC/ETH/SOL
pipeline BEFORE being folded into the Kim-filter posterior. This is
the resurrection criterion 35002 set; it is pre-registered here as
acceptance gate G0.

| Component | Raw mechanism test (pre-registered) | Source |
|-----------|--------------------------------------|--------|
| `rvov_t` | corr(rvov_t, |next-1d return|) > 0 on 4h BTC/ETH/SOL | needs data run |
| `fbasis_t` | corr(fbasis_t[s], realized vol_t[s+1]) > 0 across symbols | needs data run |
| `retz_t`  | corr(|retz_t|, next-bar vol) > 0 (variance ratio) | trivial sanity |

If ANY of the three fails its raw-mechanism test, that component is
dropped from the feature vector and the HMM proceeds with the
remaining. The HMM is NOT a re-run if all three drop; that means
"no suitable prior content exists for an HMM at 4h on this data",
which is itself a KILL (the spec terminates, the framing is shelved).

## Feature vector (final)
```
X_t = [
    rvov_t,          # 4h realized vol-of-vol (24h roll of 1h std, 4h sampled)
    fbasis_t[s],     # 4h cross-asset funding-basis spread (z-scored, per-symbol)
    retz_t[s],       # 4h close-to-close return z-score (30-bar roll, winsorized ±3σ)
]
shape = (T, 3) per symbol; stacked per-symbol for joint estimation
```

## Model specification
- **Markov-switching HMM** (Hamilton 1989), K ∈ {3, 4} (K=2 rejected a
  priori as redundant with `_shared/regime/btc_gate.py` trend/vol
  hard-assignment; K=5+ rejected for over-parameterization on the
  2y window).
- **Emission**: multivariate Student-t (ν estimated via EM, initialized
  via method-of-moments), full covariance per regime.
- **Initialization**: 25 random restarts with k-means seeds on a
  6-month initial training window; select by BIC.
- **K-selection rule**: BIC over the initial 6-month training window,
  constrained by min-frequency ≥ 1% of bars per regime (per
  regime-macro SKILL anti-pattern: don't let < 1% "crisis" bars collapse
  K).
- **Refit cadence**: every 4h bar (no rolling refit window — refit on
  the **expanding** training set anchored at first bar). Justification
  gate: `autocorr(p_t,k, 4h) < 0.5` for all k. If fails, halve cadence
  to 8h (next cadence per skill); if fails at 8h, KILL (regime is
  noise).
- **Outlier handling**: winsorize at ±3σ before EM (covers the
  tail-sensitivity problem that killed 35002's Gaussian emission).

## Outputs (public API contract)
```python
# File: quant-loop/_shared/regime/hmm_4h.py
class RegimeHMM4H:
    def fit(self, features_4h: pd.DataFrame, *, K: int = 3) -> "RegimeHMM4H": ...
    def predict_proba(self, features_4h: pd.DataFrame) -> pd.DataFrame:
        """Returns DataFrame indexed by timestamp, columns = p_t,k for k in 0..K-1."""
    def regime_label(self, p_t: np.ndarray) -> int:
        """argmax — for diagnostics only; trading uses averaged sizing."""
    @property
    def bic(self) -> float: ...
    @property
    def nu(self) -> float: ...  # Student-t degrees of freedom (post-EM)
    @property
    def regime_mean(self) -> np.ndarray: ...  # shape (K, 3)
    @property
    def regime_cov(self) -> np.ndarray: ...  # shape (K, 3, 3)
    @property
    def transition_matrix(self) -> np.ndarray: ...  # shape (K, K)
```

A regime-decision helper for strategies:
```python
def regime_sizing(p_t: np.ndarray, base_size: float) -> float:
    """Per regime-macro SKILL: full size when max_k p > 0.6, reduce when diffuse,
    zero when entropy H(p) > 1.2. Returns position-size multiplier in [0, 1]."""
```

## Pre-registered acceptance gates (a priori, BEFORE backtest)

| Gate | Definition | Failure → |
|------|------------|-----------|
| **G0** | All three raw-feature components clear their mechanism test (table above) | drop component; if 0 left → KILL spec |
| **G1** | BIC selects K ∈ {3, 4} on initial 6m training window | reject K, refit; if K=2 wins → reject as redundant |
| **G2** | Min regime frequency ≥ 1% of bars in each regime (K=3 or K=4) | merge smallest regime into nearest neighbor; re-eval |
| **G3** | `autocorr(p_t,k, 4h) < 0.5` for all k on validation window | halve cadence to 8h; if fails → KILL |
| **G4** | Walk-forward OOS Aumann-falsifier: **avg-regime sizing OOS Sharpe > argmax-regime sizing OOS Sharpe by > 0.3** AND > flat-sizing OOS Sharpe by > 0.3 | KILL (regime layer is decoration per regime-macro SKILL) |
| **G5** | Walk-forward OOS DSR (Deflated Sharpe Ratio) > 0 across 4+ expanding windows on BTC+ETH+SOL | KILL (over-fit / under-powered) |
| **G6** | median OOS Sharpe > 0 across all windows AND no single-window Sharpe < -1.0 | KILL (structural negative-fold pattern) |
| **G7** | Cross-framework CV: hmmlearn vs custom-em both produce same K, same regime-mean sign on the validation window within tolerance ε=0.2 | KILL (implementation artefact) |

## Walk-forward OOS protocol

| Window | Length | Refit anchor | Test |
|--------|--------|--------------|------|
| WF1 | 2024-01 → 2024-12 (12m) | 2024-01 expanding | 2024-10 → 2024-12 (3m) |
| WF2 | 2024-04 → 2025-03 (12m expanding) | 2024-04 expanding | 2025-01 → 2025-03 (3m) |
| WF3 | 2024-07 → 2025-06 (12m expanding) | 2024-07 expanding | 2025-04 → 2025-06 (3m) |
| WF4 | 2024-10 → 2025-09 (12m expanding) | 2024-10 expanding | 2025-07 → 2025-09 (3m) |
| WF5 | 2025-01 → 2025-12 (12m expanding) | 2025-01 expanding | 2025-10 → 2025-12 (3m) |
| WF6 | 2025-04 → 2026-03 (12m expanding) | 2025-04 expanding | 2026-01 → 2026-03 (3m) |
| WF7 | 2025-07 → 2026-06 (12m expanding) | 2025-07 expanding | 2026-04 → 2026-06 (3m) |

Embargo: 24h around each regime-switch event (skip first 6 bars of
each new regime to avoid embedding lookahead in transition estimates).
Donor strategy for sizing test: a **non-regime-gated baseline** — e.g.
BTC/ETH/SOL 4h trend-following with 30-bar EMA cross — so the test
isolates the regime layer's contribution, not the donor signal's edge.

Cost assumption: VIP0 pair-RT 9bp per T10 cost decomposition
(SMA-36598). Stress corner: 15bp (cycle-46 cost stress).

## Cycle-46 dedup (no prior-content overlap with killed families)
- T01 OFI KILL: 1m microstructure taker flow. → T12 uses 4h macro features. NO overlap.
- T04 iceberg KILL: sub-second absorption. → T12 uses 4h aggregated features. NO overlap.
- T06 funding-carry-asym KILL: single-asset funding z-score as carry. → T12 uses
  *cross-asset* funding-basis spread (relative), not single-asset funding z. NO overlap.
- T08 VPVR-confluence archived: VPVR + funding>0.03% trigger. → T12 has NO VPVR feature. NO overlap.
- T09 vpvr_xs_pairs_4h KILL: VPVR pair-stat-arb. → T12 has NO VPVR feature, NO pair signal. NO overlap.
- T11 vpvr_edge_reversion SPEC candidate: LVN/HVN geometry + 1d TTL. → T12 is regime detector,
  not directional signal. Different layer (state vs signal). NO overlap.
- SMA-35002 Bayesian KILL: VPVR + funding-z prior, 15m. → T12 has NEW prior content
  (rvov, fbasis, retz), 4h TF, multi-asset, heavy-tailed emission. Distinct on
  load-bearing axes.

## Implementation ownership (downstream of SPEC)
- **strat-indicators** (L3): implements `_shared/regime/hmm_4h.py` +
  tests + README per the public API contract above.
- **strat-validation** (L3): runs the walk-forward OOS protocol, reports
  G0-G7 with concrete numbers in the EVIDENCE comment.
- **quant-analyst** (L4): cross-framework CV (G7) and framework sanity
  audit (does the regime label match observable market structure on
  the test windows?).

## Blockers before implementation start
1. None on the SPEC itself. Implementation can start against the
   acceptance gates as soon as strat-indicators picks up.
2. Data availability: 4h klines 2024-01-01 → 2026-07-26 BTC/ETH/SOL
   must exist in `~/multica/quant-loop/data/`. Funding-basis series
   must be queryable. (smark to confirm availability, but
   quant-researcher-side expectation: 4h klines are standard; funding
   is the same 8h perp-feed used elsewhere.)

## Artifacts (to be produced by L3 implementation)
- `_shared/regime/hmm_4h.py` — public API
- `_shared/regime/test_hmm_4h.py` — unit tests + golden-value smoke
- `_shared/regime/README.md` — usage doc with worked example
- `results/hmm_regime_4h_20260726/walk_forward_{1..7}.json` — per-WF
  metrics (G4 Aumann-falsifier numbers)
- `results/hmm_regime_4h_20260726/summary.json` — G0-G7 verdict line
- `results/hmm_regime_4h_20260726/SPEC.md` — copy of this SPEC's
  acceptance gates for traceability

## Links
- SMA-35762 (this issue, parent)
- SMA-35669 (parent project — Research queue #92)
- SMA-35002 (Bayesian Regime Posterior KILL — load-bearing revival criterion)
- SMA-35002 metadata `decision` field (KILLED 2026-07-19, smark/kimi 代签)
- SMA-35002 comment `29d855a0-...` (strategy-worker-2 KILL-acknowledgement
  with resurrection criterion)
- SMA-34990 (T06 funding-carry-asym KILL — distinct prior content)
- SMA-34875 (mtf_xs_pairs H3 — multi-TF confirmation template)
- SMA-30199 (frontier-SPEC bucket — promotion target)
- SMA-36598 (T10 cost-cap decomposition — 9bp VIP0 floor reference)
- `_shared/regime/btc_gate.py` (existing hard-assignment classifier — opt-in,
  not replaced by this SPEC)
- `multica-agent-base` §strategy-layer (cycle-46 family-exhaustion rule)
- `regime-macro` SKILL (4h refit, Aumann-falsifier, regime-conditional sizing)
- `paper-replication` SKILL (cross-framework CV requirement)
- Hamilton 1989 (Markov-switching original); Kim 1994 (filter);
  Kim-Nelson 1999 (textbook reference)