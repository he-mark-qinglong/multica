# SPEC — HMM Regime Detector (4h, multi-asset, BTC/ETH/SOL)

**Issue**: SMA-35762 — Research #92 — regime detection HMM
**Parent project**: SMA-35669
**Author**: quant-researcher (78069161-efaa-493c-9561-d72a130c5926)
**Date**: 2026-07-26
**Status**: maturing → frontier-SPEC candidate. Awaiting strat-indicators implementation.

## 1. Purpose

Provide a probabilistic regime detector that produces a posterior
`p_t,k ∈ Δ^K` over K latent market regimes at the 4h bar, on BTC /
ETH / SOL, using a feature vector whose components are **new prior
content** relative to the killed SMA-35002 Bayesian Regime Posterior.

The detector's value is established by the **Aumann-falsifier**
(gate G4, below): a representative donor strategy with **averaged-
regime sizing** must produce OOS Sharpe that exceeds the **argmax-
regime sizing** OOS Sharpe by > 0.3 AND exceeds flat-sizing OOS
Sharpe by > 0.3. Otherwise the regime layer is decoration (per
`regime-macro` SKILL falsification rule) and the SPEC is KILLED.

## 2. Revival rationale vs SMA-35002 (KILLED 2026-07-19)

SMA-35002 was KILLED because:
- Prior content (VPVR-distance + funding z-score) is the same signal
  already sub-gate on `funding_carry_asym` lineage (5/6 gates FAIL);
  Bayesian averaging cannot compensate.
- Pre-SPEC gates 5/7 FAIL (3 fatal: A prior-only edge / E synthetic
  calibration uninformative / F prior sensitivity > 0.3).
- Aumann-falsifier FAIL: Bayesian averaging did not outperform
  argmax.
- 15m BTC single-asset scope; Gaussian emission on fat-tailed
  returns failed gate E.

**Recorded resurrection criterion** (strategy-worker-2, comment
`29d855a0-...`, 2026-07-19T04:32+08): "only reopen if paired with a
*new* prior content — one whose raw signal clears cost on the canonical
pipeline *before* being folded into a Kim-filter posterior."

This SPEC satisfies that criterion on every load-bearing axis:

| Axis | SMA-35002 | T12 (this SPEC) |
|------|-----------|-----------------|
| Prior content | VPVR-distance + funding z-score (sub-gate) | rvov + fbasis + retz (new, see §3) |
| Timeframe | 15m | 4h (per regime-macro SKILL) |
| Coverage | BTC only | BTC + ETH + SOL (joint) |
| Emission | Gaussian (uninformative on fat tails) | Student-t (ν estimated via EM) |
| Aumann-falsifier | FAIL | primary acceptance gate (G4) |
| Min-frequency constraint | absent | ≥ 1% bars per regime (anti-pattern guard) |
| Refit cadence | 15m (curve-fitting) | 4h, with autocorr justification gate (G3) |

## 3. Feature vector (NEW prior content, pre-screened)

```
X_t = [
    rvov_t,         # 4h realized vol-of-vol
                    # = std over rolling 24h window of 1h realized vol,
                    # 4h-sampled. Mechanism: stress autocorrelation
                    # (Andersen-Bollerslev-Diebold 2007).
    fbasis_t[s],    # 4h cross-asset funding-basis spread, per-symbol
                    # = (funding[s] - median(funding across symbols)) / std,
                    # 8h funding forward-filled to 4h. Mechanism: cross-asset
                    # funding dispersion is a perp-complex stress indicator
                    # (Persaud 2023, perp basis literature).
    retz_t[s],      # 4h close-to-close return z-score, per-symbol
                    # = (r_t - μ_30bar) / σ_30bar, winsorized ±3σ.
                    # Trend-component signal.
]
shape = (T, 3) per symbol, stacked per-symbol for joint EM.
```

**Raw-mechanism test (acceptance gate G0)**: each of `rvov`, `fbasis`,
`retz` must pass its raw mechanism correlation test on the 4h
BTC/ETH/SOL pipeline BEFORE being folded into the Kim-filter posterior.
If any component fails G0, drop it; if all three drop, KILL the SPEC
(the framing "no suitable prior content exists for an HMM at 4h on
this data" is itself a verdict).

## 4. Model specification

- **Markov-switching HMM** (Hamilton 1989), K ∈ {3, 4}.
- K=2 rejected a priori (redundant with `_shared/regime/btc_gate.py`
  trend/vol hard-assignment).
- K=5+ rejected for over-parameterization on the 2y window.
- **Emission**: multivariate Student-t, ν estimated via EM
  (initialized via method-of-moments). Full covariance per regime.
- **Initialization**: 25 random restarts with k-means seeds on the
  initial 6-month training window. Select by BIC.
- **K-selection rule**: BIC over the initial 6m training window,
  constrained by min-frequency ≥ 1% bars per regime.
- **Refit cadence**: every 4h bar, on the **expanding** training set
  anchored at first bar (no rolling refit window).
- **Justification gate** (G3): `autocorr(p_t,k, 4h) < 0.5` for all k
  on validation window. If fails → halve cadence to 8h. If fails at
  8h → KILL (regime is noise).
- **Outlier handling**: winsorize ±3σ before EM.

## 5. Public API contract

```python
# File: _shared/regime/hmm_4h.py
from dataclasses import dataclass
import numpy as np
import pandas as pd

class RegimeHMM4H:
    """4h Markov-switching regime detector (Student-t emissions)."""

    def fit(self, features_4h: pd.DataFrame, *, K: int = 3,
            nu_init: float = 5.0,
            n_restarts: int = 25) -> "RegimeHMM4H":
        """Fit on expanding training set anchored at first bar."""

    def predict_proba(self, features_4h: pd.DataFrame) -> pd.DataFrame:
        """Returns DataFrame indexed by timestamp, columns p_t,k for k in 0..K-1.
        Rows sum to 1."""

    def regime_label(self, p_t: np.ndarray) -> int:
        """argmax — for diagnostics only; trading uses averaged sizing."""

    @property
    def bic(self) -> float: ...
    @property
    def nu(self) -> float: ...
    @property
    def regime_mean(self) -> np.ndarray: ...  # shape (K, 3)
    @property
    def regime_cov(self) -> np.ndarray: ...   # shape (K, 3, 3)
    @property
    def transition_matrix(self) -> np.ndarray: ...  # shape (K, K)


def regime_sizing(p_t: np.ndarray, base_size: float = 1.0) -> float:
    """Per regime-macro SKILL §Core methods.5: full size when
    max_k p > 0.6, scale linearly when 0.6 ≥ max_k p ≥ H*(p)/log K
    threshold, zero when entropy H(p) > 1.2.

    Returns position-size multiplier in [0, base_size].
    """
```

## 6. Pre-registered acceptance gates

Set BEFORE any backtest result.

| Gate | Definition | Failure → |
|------|------------|-----------|
| **G0** | All three raw-feature components clear their mechanism test | drop component; if 0 left → KILL spec |
| **G1** | BIC selects K ∈ {3, 4} on initial 6m training | reject K=2; refit; if K=2 wins → reject as redundant |
| **G2** | Min regime frequency ≥ 1% bars per regime in K | merge smallest; re-eval |
| **G3** | `autocorr(p_t,k, 4h) < 0.5` for all k on validation | halve cadence to 8h; if fails → KILL |
| **G4** | **Aumann-falsifier**: avg-regime sizing OOS Sharpe > argmax-regime sizing OOS Sharpe by > 0.3 AND > flat-sizing by > 0.3 | KILL (regime layer decoration per regime-macro SKILL) |
| **G5** | Walk-forward DSR > 0 across 4+ expanding windows on BTC+ETH+SOL | KILL (over-fit / under-powered) |
| **G6** | median OOS Sharpe > 0 across all windows AND no single-window Sharpe < -1.0 | KILL (cycle-46 negative-fold pattern) |
| **G7** | Cross-framework CV: hmmlearn vs custom-em both produce same K, same regime-mean sign on validation window within ε=0.2 | KILL (implementation artefact) |

## 7. Walk-forward OOS protocol

7 expanding windows, anchored at 2024-01:

| Window | Train (anchored at first bar) | Test | Embargo |
|--------|-------------------------------|------|---------|
| WF1 | 2024-01 → 2024-09 (9m)  | 2024-10 → 2024-12 (3m) | 24h around regime switches |
| WF2 | 2024-01 → 2025-03 (15m) | 2025-01 → 2025-03 (3m) | 24h |
| WF3 | 2024-01 → 2025-06 (18m) | 2025-04 → 2025-06 (3m) | 24h |
| WF4 | 2024-01 → 2025-09 (21m) | 2025-07 → 2025-09 (3m) | 24h |
| WF5 | 2024-01 → 2025-12 (24m) | 2025-10 → 2025-12 (3m) | 24h |
| WF6 | 2024-01 → 2026-03 (27m) | 2026-01 → 2026-03 (3m) | 24h |
| WF7 | 2024-01 → 2026-06 (30m) | 2026-04 → 2026-06 (3m) | 24h |

**Donor strategy** for the Aumann-falsifier (G4): a non-regime-gated
baseline — BTC/ETH/SOL 4h trend-following with 30-bar EMA cross.
Isolates the regime layer's contribution, not the donor signal's edge.

**Cost assumption**: VIP0 pair-RT 9bp (per T10 / SMA-36598). Stress
corner: 15bp.

## 8. Implementation ownership

- **strat-indicators** (L3): `_shared/regime/hmm_4h.py` + unit tests +
  README per §5.
- **strat-validation** (L3): runs the walk-forward OOS protocol;
  reports G0-G7 with concrete numbers in the EVIDENCE comment.
- **quant-analyst** (L4): cross-framework CV (G7) + sanity audit
  (do regime labels match observable market structure on test
  windows?).

## 9. Cycle-46 dedup

| Killed/archived line | Mechanism | T12 overlap |
|----------------------|-----------|-------------|
| T01 OFI (killed cost-cap) | 1m taker-flow imbalance | NONE: T12 uses 4h aggregated features |
| T04 iceberg (killed cost-cap) | sub-second absorption | NONE: 4h aggregated |
| T06 funding-carry-asym (killed sub-gate) | single-asset funding z as carry | NONE: T12 uses *cross-asset* relative spread |
| T08 VPVR-confluence (archived) | VPVR + funding>0.03% trigger | NONE: no VPVR feature |
| T09 vpvr_xs_pairs_4h (killed cycle-46) | VPVR pair-stat-arb | NONE: no VPVR, no pair signal |
| T11 vpvr_edge_reversion (SPEC candidate) | LVN/HVN + 1d TTL | NONE: T12 is state detector, T11 is directional signal |
| SMA-35002 Bayesian (killed sub-gate) | VPVR + funding-z, 15m | DISTINCT on all load-bearing axes (§2 table) |

## 10. References

- Hamilton 1989 (https://doi.org/10.2307/1912559) — Markov-switching original
- Kim 1994 — Kim filter for Markov-switching state-space
- Kim & Nelson 1999, *State-Space Models with Regime Switching*, MIT Press, ISBN 9780262112383
- Andersen, Bollerslev, Diebold 2007 — realized vol-of-vol literature
- Persaud 2023 — perp basis dispersion as stress indicator (industry)
- Albers et al. 2025 (arXiv:2502.18625) — execution microstructure (referenced for cost-cap, NOT prior content)
- `regime-macro` SKILL — 4h refit, Aumann-falsifier, regime-conditional sizing
- `paper-replication` SKILL — 35-cell sweep + cross-framework CV
- `_shared/regime/btc_gate.py` — existing hard-assignment classifier
  (OPT-IN LIBRARY, NOT REPLACED by this SPEC)