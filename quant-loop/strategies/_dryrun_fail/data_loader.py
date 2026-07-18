"""Synthetic data loader for the _dryrun_fail CI dry-run fixture.

Returns a deterministic 60-day 1d OHLCV series with a *negative* drift
of -0.15%/day plus a tiny noise term. The drift is intentionally
opposite the PASS fixture so that the long trades emitted by the
harness_adapter fill at a loss inside the backtrader/freqtrade replays,
producing a FAIL verdict that propagates through the CI hook.

Deterministic via numpy default_rng(seed=349622).
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

_SYMBOL = "BTCUSDT"
_N_BARS = 220  # covers 3 OOS windows (~73 bars each) with buffer
_START = pd.Timestamp("2026-01-01", tz="UTC")
_DAILY_DRIFT = -0.0015
_DAILY_NOISE = 0.008
_SEED = 349622


def load_all(
    symbols: Iterable[str] | None = None,
    *args,
    **kwargs,
) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(_SEED)
    drift = _DAILY_DRIFT
    noise = rng.normal(loc=0.0, scale=_DAILY_NOISE, size=_N_BARS)
    log_rets = drift + noise
    price = 30_000.0 * np.exp(np.cumsum(log_rets))

    idx = pd.date_range(_START, periods=_N_BARS, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "open": price * (1.0 + rng.normal(0, 0.001, _N_BARS)),
            "high": price * (1.0 + np.abs(rng.normal(0.003, 0.001, _N_BARS))),
            "low": price * (1.0 - np.abs(rng.normal(0.003, 0.001, _N_BARS))),
            "close": price,
            "volume": rng.uniform(1_000, 5_000, _N_BARS),
        },
        index=idx,
    )
    df.index.name = "openTime"
    return {_SYMBOL: df}