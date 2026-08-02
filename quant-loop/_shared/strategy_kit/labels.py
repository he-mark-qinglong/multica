"""Triple-barrier labels (López de Prado 2018, AFML ch. 3).

For each bar t, three barriers are set from the entry price ``P_t``:

- **TP**  upper barrier at ``P_t * (1 + tp)``   (take-profit)
- **SL**  lower barrier at ``P_t * (1 - sl)``   (stop-loss)
- **T**   vertical barrier ``max_bars`` ahead   (time stop)

The label is +1 if TP is touched first, -1 if SL is touched first, 0 if
neither is touched before the vertical barrier (sign of the time-stop
return is *not* folded in by default — the plain 0 keeps the "no signal"
class clean; pass ``sign_on_timeout=True`` for the AFML variant where the
vertical barrier labels by the sign of the return).

Touch detection uses the high/low range when provided (a bar touching
*both* barriers is resolved conservatively as SL-first — the adverse
outcome — since intra-bar order is unknowable from OHLC). With only a
close series, touches are close-to-close.

Labels are a *research* artifact: the label at t is built from data after
t by design, and must be consumed with purged/embargoed CV (AFML ch. 7)
or via CPCV (``_shared/validation/cpcv.py``).

References:
- López de Prado (2018) *Advances in Financial Machine Learning*, ch. 3
  "Labeling" — the triple-barrier method.
- López de Prado (2018) AFML ch. 7 — purged k-fold / embargo for
  overlapping labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BarrierConfig:
    """Triple-barrier parameters.

    Attributes:
        tp: take-profit distance as a fraction (0.02 = +2%).
        sl: stop-loss distance as a fraction (0.01 = -1%).
        max_bars: vertical barrier in bars (>= 1).
        side: +1 for long labels, -1 flips the barrier directions
            (short-side labeling).
        sign_on_timeout: when True, vertical-barrier hits are labeled by
            the sign of the holding return instead of 0 (AFML variant).
    """
    tp: float = 0.02
    sl: float = 0.01
    max_bars: int = 24
    side: int = 1
    sign_on_timeout: bool = False

    def __post_init__(self) -> None:
        if self.tp <= 0 or self.sl <= 0:
            raise ValueError("tp and sl must be positive fractions")
        if self.max_bars < 1:
            raise ValueError("max_bars must be >= 1")
        if self.side not in (1, -1):
            raise ValueError("side must be +1 (long) or -1 (short)")


def triple_barrier_labels(
    close: pd.Series,
    config: BarrierConfig,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """Compute triple-barrier labels for every bar.

    Args:
        close: close prices (defines entry prices and, absent high/low,
            the touch path).
        config: BarrierConfig.
        high, low: optional bar ranges for intra-bar touch detection.

    Returns:
        DataFrame indexed like ``close`` with columns:
          - ``label``       int in {+1, 0, -1}
          - ``touch_bar``   int positional index of the first barrier touch
                            (vertical barrier = t + max_bars, clamped)
          - ``touch_time``  index value at ``touch_bar`` (NaT/NaN past end)
          - ``ret``         realised return from entry to the touch bar,
                            in side-adjusted terms (long: raw; short: -raw)
          - ``barrier``     which barrier fired: 'tp' | 'sl' | 'time' | 'end'
        The last ``max_bars`` rows are labeled from the truncated path and
        marked ``barrier='end'`` when no price barrier fires before the
        data runs out — callers should treat those as censored.
    """
    c = close.astype(float).to_numpy()
    n = len(c)
    use_range = high is not None and low is not None
    if use_range:
        h = high.reindex(close.index).astype(float).to_numpy()
        l = low.reindex(close.index).astype(float).to_numpy()

    labels = np.zeros(n, dtype=int)
    touch_bar = np.zeros(n, dtype=int)
    rets = np.full(n, np.nan)
    barrier = np.array(["end"] * n, dtype=object)

    for t in range(n):
        entry = c[t]
        if not np.isfinite(entry) or entry <= 0:
            touch_bar[t] = t
            continue
        last = min(t + config.max_bars, n - 1)
        resolved = False
        for u in range(t + 1, last + 1):
            if use_range:
                hi, lo = h[u], l[u]
                if not np.isfinite(hi) or not np.isfinite(lo):
                    continue
                if config.side == 1:
                    hit_tp = hi >= entry * (1 + config.tp)
                    hit_sl = lo <= entry * (1 - config.sl)
                else:
                    hit_tp = lo <= entry * (1 - config.tp)
                    hit_sl = hi >= entry * (1 + config.sl)
                if hit_tp and hit_sl:
                    # Intra-bar order unknowable -> adverse outcome wins.
                    labels[t] = -1
                    barrier[t] = "sl"
                elif hit_tp:
                    labels[t] = 1
                    barrier[t] = "tp"
                elif hit_sl:
                    labels[t] = -1
                    barrier[t] = "sl"
                else:
                    continue
                touch_bar[t] = u
                rets[t] = config.side * (c[u] / entry - 1.0)
                resolved = True
                break
            else:
                move = config.side * (c[u] / entry - 1.0)
                if move >= config.tp:
                    labels[t], barrier[t] = 1, "tp"
                elif move <= -config.sl:
                    labels[t], barrier[t] = -1, "sl"
                else:
                    continue
                touch_bar[t] = u
                rets[t] = move
                resolved = True
                break
        if not resolved:
            touch_bar[t] = last
            move = config.side * (c[last] / entry - 1.0)
            rets[t] = move
            if last < n - 1:
                barrier[t] = "time"
                if config.sign_on_timeout:
                    labels[t] = int(np.sign(move))
            # else: truncated by data end -> barrier stays 'end', label 0

    idx = close.index
    touch_time = pd.Series(
        [idx[tb] if tb < n else pd.NaT for tb in touch_bar], index=idx
    )
    return pd.DataFrame(
        {
            "label": labels,
            "touch_bar": touch_bar,
            "touch_time": touch_time,
            "ret": rets,
            "barrier": barrier,
        },
        index=idx,
    )
