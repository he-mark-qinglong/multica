"""Meta-labeling (López de Prado 2018, AFML ch. 3.6–3.8).

A *primary* model decides the direction of a trade, ``side ∈ {-1, 0, +1}``.
A *meta* model then answers a different, easier question: **given the
market state at the signal moment, should this trade be taken at all?**
Its label is binary — 1 if the primary signal would have been profitable
(realised side-adjusted return > 0), else 0. The meta model's output
probability is used to size or veto the primary bet (AFML ch. 3.8:
recall can be sacrificed for precision because the primary model already
fixed the trade direction).

Pipeline (all pure functions, pandas in / pandas out):

1. ``build_meta_dataset`` — aligns three things per signal bar t:
     - **label**   y_t = 1[ side_t * ret_t > 0 ], where ``ret_t`` comes
       from ``_shared/strategy_kit/labels.triple_barrier_labels`` run
       with ``side=+1`` (raw forward return); censored ``barrier='end'``
       rows are dropped.
     - **features** market state at t via pluggable feature functions
       ``f(data, t) -> float`` (built-ins: realised vol, volume z-score,
       trend strength, bar range %). Only data <= t is used (causal).
     - **weights** ``none`` | ``return`` (|ret|, AFML ch. 4.5) |
       ``uniqueness`` (sequential-bootstrap average uniqueness over the
       event's barrier span, AFML ch. 4.3).
2. Any ``MetaModel`` (Protocol: ``fit``/``predict_proba``) is trained on
   it. ``ToyLogistic`` is a zero-dependency handwritten weighted logistic
   regression so the flow runs end-to-end with no sklearn.

References:
- López de Prado (2018) *Advances in Financial Machine Learning*:
  ch. 3.6 "Meta-Labeling", ch. 3.8 "A Quantum of Precision",
  ch. 4 "Sample Weights" (return-attributed & average uniqueness).
- Bailey & López de Prado (2012) "The Sharpe Ratio Efficient Frontier"
  (motivation for position sizing by success probability).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Mapping, Optional, Protocol, Tuple, runtime_checkable

import numpy as np
import pandas as pd

from _shared.strategy_kit.labels import BarrierConfig, triple_barrier_labels

# A feature function maps (OHLCV frame, positional bar index) -> scalar.
FeatureFn = Callable[[pd.DataFrame, int], float]

VALID_WEIGHTS: Tuple[str, ...] = ("none", "return", "uniqueness")


# ---------------------------------------------------------------------------
# MetaModel protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class MetaModel(Protocol):
    """Minimal contract for a meta-label classifier.

    ``fit`` consumes a feature matrix, binary labels and sample weights;
    ``predict_proba`` returns P(y=1) per row in [0, 1]. Swap in sklearn /
    xgboost / a gateway model — the dataset builder doesn't care.
    """

    def fit(self, X: np.ndarray, y: np.ndarray,
            sample_weight: Optional[np.ndarray] = None) -> "MetaModel":
        ...

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        ...


# ---------------------------------------------------------------------------
# Built-in causal market-state features
# ---------------------------------------------------------------------------

def market_state_features(vol_lookback: int = 20,
                          trend_lookback: int = 20,
                          vol_of_vol_lookback: int = 60,
                          ) -> Dict[str, FeatureFn]:
    """Default pluggable feature set (all causal, data <= t only).

    Expects ``data`` with at least ``close``; ``high``/``low``/``volume``
    are optional and yield NaN features when missing (``ToyLogistic``
    imputes NaN with the training mean).
    """
    def _win(s: pd.Series, t: int, n: int) -> np.ndarray:
        return s.iloc[max(0, t - n + 1): t + 1].astype(float).to_numpy()

    def realized_vol(data: pd.DataFrame, t: int) -> float:
        rets = pd.Series(_win(data["close"], t, vol_lookback + 1)).pct_change().dropna()
        return float(rets.std(ddof=0)) if len(rets) >= 2 else np.nan

    def vol_of_vol(data: pd.DataFrame, t: int) -> float:
        rets = pd.Series(_win(data["close"], t, vol_of_vol_lookback + 1)).pct_change().dropna()
        if len(rets) < 4:
            return np.nan
        roll = rets.rolling(vol_lookback).std(ddof=0).dropna()
        return float(roll.std(ddof=0)) if len(roll) >= 2 else np.nan

    def volume_zscore(data: pd.DataFrame, t: int) -> float:
        if "volume" not in data:
            return np.nan
        w = _win(data["volume"], t, vol_lookback)
        mu, sd = w.mean(), w.std()
        return float((w[-1] - mu) / sd) if sd > 0 else 0.0

    def trend_strength(data: pd.DataFrame, t: int) -> float:
        """Normalised drift: mean(ret) / std(ret) over the trend window —
        a rolling t-stat-like trend measure (sign = direction)."""
        rets = pd.Series(_win(data["close"], t, trend_lookback + 1)).pct_change().dropna()
        if len(rets) < 2:
            return np.nan
        sd = rets.std(ddof=0)
        return float(rets.mean() / sd * np.sqrt(len(rets))) if sd > 0 else 0.0

    def bar_range_pct(data: pd.DataFrame, t: int) -> float:
        if "high" not in data or "low" not in data:
            return np.nan
        c = float(data["close"].iloc[t])
        if c <= 0:
            return np.nan
        return float((data["high"].iloc[t] - data["low"].iloc[t]) / c)

    def momentum(data: pd.DataFrame, t: int) -> float:
        w = _win(data["close"], t, trend_lookback + 1)
        return float(w[-1] / w[0] - 1.0) if len(w) >= 2 and w[0] > 0 else np.nan

    return {
        "realized_vol": realized_vol,
        "vol_of_vol": vol_of_vol,
        "volume_zscore": volume_zscore,
        "trend_strength": trend_strength,
        "bar_range_pct": bar_range_pct,
        "momentum": momentum,
    }


# ---------------------------------------------------------------------------
# Sample weights (AFML ch. 4)
# ---------------------------------------------------------------------------

def uniqueness_weights(t0: pd.Series, t1: pd.Series,
                       index: pd.Index) -> pd.Series:
    """Average uniqueness per event (sequential-bootstrap spirit).

    Args:
        t0: event start positions (bar index values, as in ``index``).
        t1: event end positions (touch bars), same length.
        index: the bar index the positions refer to.

    Returns:
        Weights u_i = mean over the event's span of 1 / concurrency,
        normalised to mean 1. Pure function.
    """
    pos = pd.Series(np.arange(len(index)), index=index)
    spans = [
        (int(pos.loc[a]), int(pos.loc[b]))
        for a, b in zip(t0, t1)
        if a in pos.index and b in pos.index
    ]
    n = len(index)
    concurrency = np.zeros(n)
    for a, b in spans:
        concurrency[a: b + 1] += 1.0
    out = []
    for a, b in spans:
        c = concurrency[a: b + 1]
        c = np.where(c > 0, c, 1.0)
        out.append(float(np.mean(1.0 / c)))
    w = pd.Series(out, index=t0.index[: len(out)])
    mean = w.mean()
    return w / mean if mean > 0 else w


# ---------------------------------------------------------------------------
# Dataset builder
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetaDataset:
    """Aligned meta-labeling dataset. Immutable container.

    Attributes:
        X: feature frame, one row per non-censored signal bar.
        y: binary labels (1 = primary signal profitable).
        w: sample weights (mean 1).
        events: side / ret / barrier provenance for each row.
    """
    X: pd.DataFrame
    y: pd.Series
    w: pd.Series
    events: pd.DataFrame


def build_meta_dataset(
    data: pd.DataFrame,
    side: pd.Series,
    config: Optional[BarrierConfig] = None,
    features: Optional[Mapping[str, FeatureFn]] = None,
    weight: str = "return",
) -> MetaDataset:
    """Build (X, y, w) for meta-labeling from a primary side series.

    Args:
        data: OHLCV bar frame (``close`` required; ``high``/``low`` used
            by triple-barrier touch detection and features when present).
        side: primary model output per bar, values in {-1, 0, +1}
            (0 = no signal -> excluded from the dataset).
        config: triple-barrier config for the label horizon; ``side`` in
            the config is forced to +1 so ``ret`` is the raw forward
            return and the side-adjustment happens here.
        features: name -> FeatureFn; defaults to
            ``market_state_features()``.
        weight: 'none' | 'return' | 'uniqueness'.

    Returns:
        MetaDataset with rows for bars where side != 0 and the barrier
        event is not censored by the data end.
    """
    if weight not in VALID_WEIGHTS:
        raise ValueError(f"weight must be one of {VALID_WEIGHTS}, got {weight!r}")
    cfg = config or BarrierConfig()
    cfg = BarrierConfig(tp=cfg.tp, sl=cfg.sl, max_bars=cfg.max_bars, side=1,
                        sign_on_timeout=cfg.sign_on_timeout)
    feats = dict(features) if features is not None else market_state_features()

    s = side.reindex(data.index).fillna(0).astype(int)
    tb = triple_barrier_labels(
        data["close"].astype(float), cfg,
        high=data.get("high"), low=data.get("low"),
    )

    sig_mask = (s != 0) & (tb["barrier"] != "end") & tb["ret"].notna()
    evt_idx = tb.index[sig_mask]
    pos_of = pd.Series(np.arange(len(data)), index=data.index)

    rows, ys, starts, ends, sides, rets = [], [], [], [], [], []
    for t in evt_idx:
        i = int(pos_of.loc[t])
        rows.append({name: fn(data, i) for name, fn in feats.items()})
        r = float(tb["ret"].loc[t])
        sd = int(s.loc[t])
        ys.append(1 if sd * r > 0 else 0)
        starts.append(t)
        ends.append(tb["touch_time"].loc[t])
        sides.append(sd)
        rets.append(r)

    X = pd.DataFrame(rows, index=evt_idx)
    y = pd.Series(ys, index=evt_idx, dtype=int)
    events = pd.DataFrame(
        {"side": sides, "ret": rets, "t0": starts, "t1": ends},
        index=evt_idx,
    )

    if weight == "return" and len(evt_idx):
        w = pd.Series(np.abs(rets), index=evt_idx)
        mean = w.mean()
        w = w / mean if mean > 0 else pd.Series(1.0, index=evt_idx)
    elif weight == "uniqueness" and len(evt_idx):
        w = uniqueness_weights(events["t0"], events["t1"], data.index)
    else:
        w = pd.Series(1.0, index=evt_idx)

    return MetaDataset(X=X, y=y, w=w, events=events)


# ---------------------------------------------------------------------------
# Toy meta model — handwritten weighted logistic regression (zero deps)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToyLogisticConfig:
    """Hyperparameters for ``ToyLogistic``.

    Attributes:
        lr: gradient step size.
        n_iter: full-batch gradient descent iterations.
        l2: ridge penalty on the weights (not the intercept).
    """
    lr: float = 0.1
    n_iter: int = 2000
    l2: float = 1e-3


class ToyLogistic:
    """Weighted binary logistic regression via full-batch gradient descent.

    Features are standardised on the training set (NaN -> training mean,
    zero-variance columns -> constant 0), so predict-time NaNs degrade
    gracefully. Demonstration-grade: fine for thousands of rows, not a
    substitute for a production solver.
    """

    def __init__(self, config: Optional[ToyLogisticConfig] = None) -> None:
        self.config = config or ToyLogisticConfig()
        self._coef: Optional[np.ndarray] = None
        self._mean: Optional[np.ndarray] = None
        self._std: Optional[np.ndarray] = None

    def _prepare_fit(self, X: np.ndarray) -> np.ndarray:
        mean = np.nanmean(X, axis=0)
        mean = np.where(np.isfinite(mean), mean, 0.0)
        std = np.nanstd(X, axis=0)
        std = np.where(std > 0, std, 1.0)
        self._mean, self._std = mean, std
        Z = (np.where(np.isfinite(X), X, mean) - mean) / std
        return np.column_stack([np.ones(len(Z)), Z])  # intercept column

    def _prepare_predict(self, X: np.ndarray) -> np.ndarray:
        if self._mean is None or self._std is None:
            raise RuntimeError("ToyLogistic.predict_proba called before fit")
        Z = (np.where(np.isfinite(X), X, self._mean) - self._mean) / self._std
        return np.column_stack([np.ones(len(Z)), Z])

    def fit(self, X: np.ndarray, y: np.ndarray,
            sample_weight: Optional[np.ndarray] = None) -> "ToyLogistic":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        if X.ndim != 2 or len(X) != len(y):
            raise ValueError("X must be (n, k) and y must be (n,)")
        w = (np.ones(len(y)) if sample_weight is None
             else np.asarray(sample_weight, dtype=float))
        Zb = self._prepare_fit(X)
        coef = np.zeros(Zb.shape[1])
        reg = np.ones_like(coef) * self.config.l2
        reg[0] = 0.0  # don't penalise the intercept
        for _ in range(self.config.n_iter):
            z = np.clip(Zb @ coef, -30.0, 30.0)
            p = 1.0 / (1.0 + np.exp(-z))
            grad = Zb.T @ (w * (p - y)) / w.sum() + reg * coef
            coef -= self.config.lr * grad
        self._coef = coef
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """P(y=1) per row, in (0, 1)."""
        if self._coef is None:
            raise RuntimeError("ToyLogistic.predict_proba called before fit")
        Zb = self._prepare_predict(np.asarray(X, dtype=float))
        z = np.clip(Zb @ self._coef, -30.0, 30.0)
        return 1.0 / (1.0 + np.exp(-z))
