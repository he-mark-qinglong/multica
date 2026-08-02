"""Signal composer — combine multiple strategy signals into one composite.

Three weighting schemes, all pure functions over ``pd.DataFrame`` inputs:

- ``fixed``   — user-supplied static weights.
- ``ic``      — weights proportional to each signal's information
                coefficient (Spearman rank IC vs forward returns), in the
                spirit of Grinold & Kahn (2000), *Active Portfolio
                Management*, ch. 6 — "the fundamental law".
- ``vote``    — majority vote over signal signs (each signal one vote).

Optional **decorrelation** (López de Prado 2018, *Advances in Financial
Machine Learning*, ch. 8 — bet sizing / feature overlap): when two signals
are highly correlated they are nearly the same bet counted twice, so the
lower-priority signal's weight is shrunk by ``1 - |corr|`` for every
already-accepted partner above ``corr_threshold``.

Signals may be discrete (-1/0/+1) or continuous; the composite is always a
continuous Series in [-1, 1] after the final clip.

References:
- Grinold & Kahn (2000) — IC-weighted signal combination
- López de Prado (2018) — AFML ch. 8 (overlap / decorrelation)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

VALID_METHODS: Tuple[str, ...] = ("fixed", "ic", "vote")


@dataclass(frozen=True)
class ComposerConfig:
    """Configuration for ``compose_signals``.

    Attributes:
        method: 'fixed' | 'ic' | 'vote'.
        weights: static weights for method='fixed' (signal name -> weight).
            Need not sum to 1 — they are normalised internally.
        ic_lookback: trailing window (bars) for IC estimation when
            method='ic'. IC at bar t uses only data <= t (causal).
        ic_min_abs: signals with |IC| below this get zero weight (dead
            signals don't dilute the composite).
        decorrelate: shrink weights of mutually-correlated signals.
        corr_threshold: |corr| above this triggers decorrelation shrinkage.
        corr_lookback: trailing window for the correlation estimate.
    """
    method: str = "fixed"
    weights: Mapping[str, float] = field(default_factory=dict)
    ic_lookback: int = 250
    ic_min_abs: float = 0.0
    decorrelate: bool = True
    corr_threshold: float = 0.7
    corr_lookback: int = 250

    def __post_init__(self) -> None:
        if self.method not in VALID_METHODS:
            raise ValueError(
                f"method must be one of {VALID_METHODS}, got {self.method!r}"
            )
        if not (0.0 <= self.corr_threshold <= 1.0):
            raise ValueError("corr_threshold must be in [0, 1]")


# ---------------------------------------------------------------------------
# Weight estimators (pure)
# ---------------------------------------------------------------------------

def fixed_weights(signals: pd.DataFrame,
                  weights: Mapping[str, float]) -> pd.Series:
    """Normalise user weights onto the signal columns (missing -> 0)."""
    w = pd.Series({c: float(weights.get(c, 0.0)) for c in signals.columns})
    total = w.abs().sum()
    if total <= 0:
        raise ValueError("fixed weights are all zero / missing for these signals")
    return w / total


def ic_weights(signals: pd.DataFrame, forward_returns: pd.Series,
               lookback: int = 250, min_abs_ic: float = 0.0) -> pd.Series:
    """Full-sample causal IC weights.

    For each signal column, IC = Spearman rank correlation between the
    signal and ``forward_returns`` over the trailing ``lookback`` bars.
    Weight = IC (signed — a negatively-predictive signal is *flipped*,
    not dropped). Signals with |IC| < ``min_abs_ic`` get weight 0.

    The trailing window ends at the last row of ``signals``, so the
    estimate uses only information available at that point.
    """
    fwd = forward_returns.reindex(signals.index)
    ic: Dict[str, float] = {}
    for col in signals.columns:
        pair = pd.concat([signals[col], fwd], axis=1).dropna().tail(lookback)
        if len(pair) < 20:
            ic[col] = 0.0
            continue
        ic[col] = float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="spearman"))
    w = pd.Series(ic)
    w = w.where(w.abs() >= min_abs_ic, 0.0)
    total = w.abs().sum()
    if total <= 0:
        # No signal has measurable IC — fall back to equal weights so the
        # composer still returns something interpretable.
        return pd.Series(1.0 / len(signals.columns), index=signals.columns)
    return w / total


def vote_weights(signals: pd.DataFrame) -> pd.Series:
    """One vote per signal."""
    return pd.Series(1.0 / len(signals.columns), index=signals.columns)


# ---------------------------------------------------------------------------
# Decorrelation (pure)
# ---------------------------------------------------------------------------

def decorrelate_weights(weights: pd.Series, signals: pd.DataFrame,
                        threshold: float = 0.7,
                        lookback: int = 250) -> pd.Series:
    """Shrink weights of mutually-correlated signals, greedy by |weight|.

    Signals are accepted in descending |weight| order. Each candidate's
    weight is multiplied by ``(1 - |corr|)`` for every already-accepted
    signal whose trailing |corr| with it exceeds ``threshold``. The result
    is re-normalised so absolute weights still sum to 1 (a zero vector is
    returned unchanged — degenerate input).
    """
    cols = list(weights.index)
    tail = signals[cols].tail(lookback)
    corr = tail.corr().abs().fillna(0.0)
    order = weights.abs().sort_values(ascending=False).index.tolist()
    accepted: list[str] = []
    adjusted = weights.copy().astype(float)
    for col in order:
        for acc in accepted:
            c = float(corr.loc[col, acc])
            if c > threshold:
                adjusted[col] *= (1.0 - c)
        accepted.append(col)
    total = adjusted.abs().sum()
    if total <= 0:
        return adjusted
    return adjusted / total


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

def compose_signals(signals: pd.DataFrame, config: ComposerConfig,
                    forward_returns: Optional[pd.Series] = None) -> pd.Series:
    """Combine signal columns of ``signals`` into one composite in [-1, 1].

    Args:
        signals: DataFrame with one column per signal (index-aligned).
            Values may be discrete (-1/0/+1) or continuous.
        config: ComposerConfig.
        forward_returns: required when method='ic' — forward bar returns
            aligned to ``signals.index`` (e.g. ``close.pct_change().shift(-1)``
            computed *by the caller*, who owns the no-lookahead boundary).

    Returns:
        Composite signal Series, same index, clipped to [-1, 1].
    """
    if signals.shape[1] == 0:
        raise ValueError("signals must have at least one column")

    if config.method == "fixed":
        w = fixed_weights(signals, config.weights)
    elif config.method == "ic":
        if forward_returns is None:
            raise ValueError("forward_returns required for method='ic'")
        w = ic_weights(signals, forward_returns,
                       lookback=config.ic_lookback,
                       min_abs_ic=config.ic_min_abs)
    else:  # vote
        w = vote_weights(signals)

    if config.decorrelate and signals.shape[1] > 1:
        w = decorrelate_weights(w, signals,
                                threshold=config.corr_threshold,
                                lookback=config.corr_lookback)

    if config.method == "vote":
        # Majority of sign votes; magnitude = fraction of agreeing votes.
        signed = np.sign(signals.fillna(0.0))
        composite = signed.mul(w, axis=1).sum(axis=1)
    else:
        composite = signals.fillna(0.0).mul(w, axis=1).sum(axis=1)
    return composite.clip(-1.0, 1.0)
