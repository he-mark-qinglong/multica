"""Signal decay monitoring (G20).

Tracks whether a live signal's predictive power is intact, decaying, or
dead, from three rolling diagnostics:

  * **Rolling IC** — Spearman rank correlation between the signal and the
    forward return over a sliding window (the standard information
    coefficient of Grinold & Kahn).
  * **Rolling Sharpe** — annualised Sharpe of the signal-following
    returns over the same window, as an investability cross-check.
  * **Decay slope** — OLS slope of the rolling IC against time,
    expressed per year; plus a log-linear (exponential-decay) fit on the
    positive part of the rolling IC giving a **half-life estimate**:
    ``t½ = ln(2) / λ`` for ``IC(t) ≈ IC₀ · exp(-λt)``.

Classification (see :class:`DecayReport`):

  * ``dead``     — recent IC at/below ``ic_dead`` (default 0): the signal
    no longer predicts forward returns.
  * ``decaying`` — recent IC still positive but the yearly IC slope is
    negative beyond ``slope_eps`` AND the recent IC has fallen below
    ``decay_fraction`` of the early-sample IC.
  * ``alive``    — otherwise.

Core logic is pure functions on :class:`pandas.Series`;
:func:`monitor_decay` is the single entry point returning a frozen
:class:`DecayReport`.

References:
  - Grinold & Kahn (2000), "Active Portfolio Management", Ch. 6 —
    information coefficient as the measure of signal quality.
  - Qian, Hua & Sorensen (2007), "Quantitative Equity Portfolio
    Management", Ch. 3 — IC decay / alpha horizon and half-life of
    signals.
  - Harvey & Liu (2015), "Backtesting", JPM 42(1) — monitoring live
    performance against the backtested distribution to detect decay.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "DecayReport",
    "monitor_decay",
    "rolling_ic",
    "rolling_sharpe",
    "ic_slope_per_year",
    "half_life_years",
]

_STATUS = ("alive", "decaying", "dead")


@dataclass(frozen=True)
class DecayReport:
    """Frozen summary of a signal-decay diagnostic run."""

    status: str                      # "alive" | "decaying" | "dead"
    half_life_years: float | None    # ln2/λ from exp fit; None if not decaying exponentially
    ic_slope_per_year: float         # OLS slope of rolling IC vs time (per year)
    recent_ic: float                 # mean rolling IC over the last `recent` windows
    early_ic: float                  # mean rolling IC over the first `recent` windows
    recent_sharpe: float             # mean rolling Sharpe over the last `recent` windows
    rolling_ic: pd.Series = field(compare=False)
    rolling_sharpe: pd.Series = field(compare=False)
    diagnostics: dict[str, float] = field(default_factory=dict)


def _years(index: pd.DatetimeIndex) -> np.ndarray:
    """Index → years since first timestamp (float64)."""
    t0 = index[0]
    return (index - t0).total_seconds().to_numpy() / (365.25 * 86400.0)


def rolling_ic(
    signal: pd.Series,
    forward_returns: pd.Series,
    window: int,
) -> pd.Series:
    """Rolling Spearman rank IC between signal and forward returns. Pure.

    Ranks are computed *within each window* (true rolling Spearman, not
    Pearson on globally-ranked data). Constant windows yield NaN.
    """
    aligned = pd.DataFrame({"s": signal, "r": forward_returns}).dropna()
    s = aligned["s"].to_numpy(dtype=float)
    r = aligned["r"].to_numpy(dtype=float)
    out = np.full(len(aligned), np.nan)
    for i in range(window - 1, len(aligned)):
        sw = pd.Series(s[i - window + 1 : i + 1]).rank().to_numpy()
        rw = pd.Series(r[i - window + 1 : i + 1]).rank().to_numpy()
        if sw.std() < 1e-12 or rw.std() < 1e-12:
            continue
        out[i] = np.corrcoef(sw, rw)[0, 1]
    return pd.Series(out, index=aligned.index, name="rolling_ic")


def rolling_sharpe(
    returns: pd.Series,
    window: int,
    periods_per_year: float = 365.0,
) -> pd.Series:
    """Rolling annualised Sharpe of a return stream. Pure."""
    mu = returns.rolling(window).mean()
    sigma = returns.rolling(window).std(ddof=1)
    return (mu / sigma.replace(0.0, np.nan)) * math.sqrt(periods_per_year)


def ic_slope_per_year(rolling: pd.Series) -> float:
    """OLS slope of a rolling-IC series against time, per year. Pure."""
    s = rolling.dropna()
    if len(s) < 3:
        return 0.0
    t = _years(s.index)
    t = t - t.mean()
    y = s.to_numpy() - s.mean()
    denom = float((t * t).sum())
    return float((t * y).sum() / denom) if denom > 0 else 0.0


def half_life_years(rolling: pd.Series) -> float | None:
    """Half-life (years) from a log-linear fit on positive rolling IC.

    Fits ``ln IC(t) = a - λt`` on windows with IC > 0; returns
    ``ln(2)/λ`` when λ > 0 (genuine exponential decay), else ``None``
    (IC flat or rising — half-life undefined/infinite).
    """
    s = rolling.dropna()
    s = s[s > 0]
    if len(s) < 3:
        return None
    t = _years(s.index)
    slope, _intercept = np.polyfit(t, np.log(s.to_numpy()), 1)
    lam = -float(slope)
    if lam <= 0:
        return None
    return float(math.log(2.0) / lam)


def monitor_decay(
    signal: pd.Series,
    forward_returns: pd.Series,
    strategy_returns: pd.Series | None = None,
    window: int = 60,
    recent: int = 10,
    ic_dead: float = 0.0,
    slope_eps: float = 1e-4,
    decay_fraction: float = 0.5,
    periods_per_year: float = 365.0,
) -> DecayReport:
    """Run the full decay diagnostic and classify the signal.

    Args:
        signal: signal values (tz-aware or naive datetime index).
        forward_returns: forward returns aligned to ``signal``'s index
            (caller controls horizon and look-ahead hygiene).
        strategy_returns: optional realised returns of the traded
            strategy for the rolling-Sharpe cross-check; defaults to
            ``sign(signal) * forward_returns`` (a naive follower).
        window: rolling window length (bars) for IC and Sharpe.
        recent: how many trailing windows define "recent" IC/Sharpe
            (and how many leading windows define "early" IC).
        ic_dead: recent IC at/below this ⇒ status ``dead``.
        slope_eps: |slope| below this counts as flat (per year).
        decay_fraction: recent IC below this fraction of early IC (with a
            negative slope) ⇒ status ``decaying``.
        periods_per_year: annualisation for the rolling Sharpe.
    """
    ic = rolling_ic(signal, forward_returns, window)
    if strategy_returns is None:
        aligned = pd.DataFrame({"s": signal, "r": forward_returns}).dropna()
        strategy_returns = np.sign(aligned["s"]) * aligned["r"]
    sharpe = rolling_sharpe(strategy_returns, window, periods_per_year)

    ic_valid = ic.dropna()
    recent_ic = float(ic_valid.iloc[-recent:].mean()) if len(ic_valid) else 0.0
    early_ic = float(ic_valid.iloc[:recent].mean()) if len(ic_valid) else 0.0
    sharpe_valid = sharpe.dropna()
    recent_sharpe = float(sharpe_valid.iloc[-recent:].mean()) if len(sharpe_valid) else 0.0
    slope = ic_slope_per_year(ic)
    hl = half_life_years(ic)

    if recent_ic <= ic_dead:
        status = "dead"
    elif slope < -slope_eps and early_ic > 0 and recent_ic < decay_fraction * early_ic:
        status = "decaying"
    else:
        status = "alive"

    return DecayReport(
        status=status,
        half_life_years=hl,
        ic_slope_per_year=slope,
        recent_ic=recent_ic,
        early_ic=early_ic,
        recent_sharpe=recent_sharpe,
        rolling_ic=ic,
        rolling_sharpe=sharpe,
        diagnostics={
            "window": float(window),
            "recent": float(recent),
            "n_valid_ic": float(len(ic_valid)),
            "ic_dead": ic_dead,
            "decay_fraction": decay_fraction,
        },
    )
