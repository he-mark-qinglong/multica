"""Built-in factor library (metric A5) — production-grade cross-asset /
crypto-perp factors with paper-backed definitions.

Twelve factors, each with:
  - a pure ``compute(data, **params) -> pd.Series`` implementation
    (DataFrame in, Series out, causal, no I/O);
  - a frozen :class:`FactorSpec` carrying name / direction / reference
    paper / required columns;
  - a registration in the shared indicator registry
    (:mod:`_shared.strategy_kit.registry`) so configs can reference factors
    by name and get schema-validated parameter binding for free.

Conventions
-----------
- ``data`` is a bar-level DataFrame with at least ``required_columns``.
  Funding/basis factors additionally accept a ``funding`` column (per-bar
  funding rate, e.g. 8h rate forward-filled to bars) and a ``basis``
  column ((perp - spot) / spot); both degrade to 0-filled Series when the
  column is absent so a factor never hard-fails on spot-only data — the
  caller decides whether a degenerate factor is tradeable.
- ``direction``: ``+1`` = higher factor value predicts higher forward
  returns (long-high), ``-1`` = long-low.

References
----------
- Jegadeesh & Titman (1993) "Returns to Buying Winners...", JF (momentum_12_1)
- Jegadeesh (1990) / Lehmann (1990) short-term reversal (reversal_5d)
- Andersen et al. (2003) realized vol (vol_realized, vol_of_vol)
- Amihud (2002) "Illiquidity and stock returns", JFM (amihud_illiq)
- Kyle (1985) "Continuous Auctions and Insider Trading", Econometrica (kyle_lambda)
- Easley, López de Prado & O'Hara (2012) "Flow Toxicity and Liquidity...",
  RFS (vpin_proxy)
- Moskowitz, Ooi & Pedersen (2012) "Time Series Momentum", JFE (funding carry
  analogue; funding_level / funding_change)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Mapping, Tuple

import numpy as np
import pandas as pd

from _shared.strategy_kit.registry import ParamSpec, register_indicator

# ---------------------------------------------------------------------------
# Factor metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactorSpec:
    """Metadata for one library factor.

    Attributes:
        name: unique registry key (snake_case).
        compute: pure ``f(data, **params) -> pd.Series``.
        direction: +1 long-high / -1 long-low (expected forward-return sign).
        reference: paper backing the factor definition.
        required_columns: columns ``data`` must contain.
        params: parameter schema (shared registry ``ParamSpec``).
        description: one-line doc.
    """
    name: str
    compute: Callable[..., pd.Series]
    direction: int
    reference: str
    required_columns: Tuple[str, ...]
    params: Mapping[str, ParamSpec] = field(default_factory=dict)
    description: str = ""


_FACTOR_SPECS: Dict[str, FactorSpec] = {}


def _factor(name: str, direction: int, reference: str,
            required_columns: Tuple[str, ...],
            params: Mapping[str, ParamSpec],
            description: str) -> Callable[[Callable[..., pd.Series]],
                                          Callable[..., pd.Series]]:
    """Decorator: build a FactorSpec and register the factor in the shared
    indicator registry under the same name."""
    def decorator(func: Callable[..., pd.Series]) -> Callable[..., pd.Series]:
        spec = FactorSpec(
            name=name, compute=func, direction=direction, reference=reference,
            required_columns=required_columns, params=dict(params),
            description=description,
        )
        _FACTOR_SPECS[name] = spec
        register_indicator(name, params=params, description=description,
                           source="_shared/strategy_kit/factor_library.py")(func)
        return func
    return decorator


def get_factor_spec(name: str) -> FactorSpec:
    """Return the :class:`FactorSpec` for ``name`` (KeyError if unknown)."""
    return _FACTOR_SPECS[name]


def list_factors() -> Dict[str, FactorSpec]:
    """name -> FactorSpec for every library factor (sorted by name)."""
    return dict(sorted(_FACTOR_SPECS.items()))


def compute_factor(name: str, data: pd.DataFrame, **params) -> pd.Series:
    """Compute factor ``name`` after checking required columns are present."""
    spec = _FACTOR_SPECS[name]
    missing = [c for c in spec.required_columns if c not in data.columns]
    if missing:
        raise ValueError(f"factor '{name}': missing columns {missing}")
    return spec.compute(data, **params)


# ---------------------------------------------------------------------------
# Shared column helpers
# ---------------------------------------------------------------------------
def _funding(data: pd.DataFrame) -> pd.Series:
    """Per-bar funding rate; zero-filled when the column is absent."""
    if "funding" in data.columns:
        return data["funding"].astype(float)
    return pd.Series(0.0, index=data.index)


def _basis(data: pd.DataFrame) -> pd.Series:
    """Per-bar (perp - spot)/spot basis; zero-filled when absent."""
    if "basis" in data.columns:
        return data["basis"].astype(float)
    return pd.Series(0.0, index=data.index)


# ---------------------------------------------------------------------------
# 1-2. Momentum & reversal
# ---------------------------------------------------------------------------
@_factor(
    "momentum_12_1", direction=+1,
    reference="Jegadeesh & Titman (1993) JF; Moskowitz, Ooi & Pedersen (2012) JFE",
    required_columns=("close",),
    params={
        "lookback": ParamSpec("int", default=252, min=2),
        "skip": ParamSpec("int", default=21, min=0),
    },
    description="12-1 momentum: close[t-skip]/close[t-lookback] - 1 (skip most recent month)",
)
def momentum_12_1(data: pd.DataFrame, lookback: int = 252,
                  skip: int = 21) -> pd.Series:
    """Classic 12-1 momentum: return from ``t-lookback`` to ``t-skip``,
    skipping the most recent ``skip`` bars to avoid the short-term
    reversal horizon (Jegadeesh & Titman 1993)."""
    close = data["close"].astype(float)
    return close.shift(skip) / close.shift(lookback) - 1.0


@_factor(
    "reversal_5d", direction=-1,
    reference="Jegadeesh (1990) JF; Lehmann (1990) RFS",
    required_columns=("close",),
    params={"window": ParamSpec("int", default=5, min=1)},
    description="short-term reversal: trailing ``window``-bar return (long the losers)",
)
def reversal_5d(data: pd.DataFrame, window: int = 5) -> pd.Series:
    """Trailing ``window``-bar simple return. Direction is -1: recent losers
    outperform (short-term reversal, Jegadeesh 1990)."""
    close = data["close"].astype(float)
    return close / close.shift(window) - 1.0


# ---------------------------------------------------------------------------
# 3-4. Volatility
# ---------------------------------------------------------------------------
@_factor(
    "vol_realized", direction=-1,
    reference="Andersen, Bollerslev, Diebold & Labys (2003) Econometrica; "
              "Ang et al. (2006) JF (idiosyncratic vol anomaly)",
    required_columns=("close",),
    params={
        "window": ParamSpec("int", default=20, min=2),
        "periods_per_year": ParamSpec("int", default=365, min=1),
    },
    description="annualised realised vol of log returns (low-vol anomaly: long low vol)",
)
def vol_realized(data: pd.DataFrame, window: int = 20,
                 periods_per_year: int = 365) -> pd.Series:
    """Annualised realised volatility of log close returns over ``window``."""
    log_ret = np.log(data["close"].astype(float)).diff()
    return log_ret.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(
        periods_per_year)


@_factor(
    "vol_of_vol", direction=-1,
    reference="Baltussen, Van Bekkum & Van Vliet (2021) 'Unknowns on unknowns', "
              "JPM; Andersen et al. (2003)",
    required_columns=("close",),
    params={
        "vol_window": ParamSpec("int", default=20, min=2),
        "vov_window": ParamSpec("int", default=20, min=2),
    },
    description="std of rolling realised vol (uncertainty about uncertainty)",
)
def vol_of_vol(data: pd.DataFrame, vol_window: int = 20,
               vov_window: int = 20) -> pd.Series:
    """Std of the rolling realised-vol series itself."""
    rv = vol_realized(data, window=vol_window)
    return rv.rolling(vov_window, min_periods=vov_window).std(ddof=1)


# ---------------------------------------------------------------------------
# 5. Volume
# ---------------------------------------------------------------------------
@_factor(
    "volume_zscore", direction=-1,
    reference="Llorente, Michaely, Saar & Wang (2002) JF (volume-return dynamics)",
    required_columns=("volume",),
    params={"window": ParamSpec("int", default=20, min=2)},
    description="z-score of volume vs trailing window (abnormal volume)",
)
def volume_zscore(data: pd.DataFrame, window: int = 20) -> pd.Series:
    """(volume - rolling mean) / rolling std of volume."""
    v = data["volume"].astype(float)
    mu = v.rolling(window, min_periods=window).mean()
    sd = v.rolling(window, min_periods=window).std(ddof=1)
    return (v - mu) / sd.replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# 6-8. Perp microstructure: funding & basis
# ---------------------------------------------------------------------------
@_factor(
    "funding_level", direction=-1,
    reference="Moskowitz, Ooi & Pedersen (2012) JFE (carry); "
              "Ang, Chen & Xing (2006) RFS (perp funding as carry)",
    required_columns=("close",),
    params={"window": ParamSpec("int", default=24, min=1)},
    description="mean funding rate over window (long the payers = negative funding)",
)
def funding_level(data: pd.DataFrame, window: int = 24) -> pd.Series:
    """Trailing mean of the funding rate. Direction -1: persistently high
    positive funding signals overcrowded longs — short them, collect
    funding (funding-carry)."""
    return _funding(data).rolling(window, min_periods=1).mean()


@_factor(
    "funding_change", direction=-1,
    reference="Bojraj & Titman (2019) 'Funding rate dynamics in perpetual "
              "futures' (change in crowding predicts returns)",
    required_columns=("close",),
    params={"window": ParamSpec("int", default=8, min=1)},
    description="delta of funding rate vs ``window`` bars ago (crowding shock)",
)
def funding_change(data: pd.DataFrame, window: int = 8) -> pd.Series:
    """Change in funding rate over the last ``window`` bars; a positive
    shock = fresh long crowding -> negative forward returns."""
    return _funding(data).diff(window)


@_factor(
    "basis_perp_spot", direction=-1,
    reference="Fama & French (1987) JPE (basis as carry); "
              "Schmeling, Schrimpf & Todorov (2023) crypto carry",
    required_columns=("close",),
    params={"window": ParamSpec("int", default=24, min=1)},
    description="mean (perp-spot)/spot basis over window (rich basis -> fade)",
)
def basis_perp_spot(data: pd.DataFrame, window: int = 24) -> pd.Series:
    """Trailing mean of the (perp - spot)/spot basis. Direction -1: rich
    positive basis = longs paying up -> fade (cash-and-carry short leg)."""
    return _basis(data).rolling(window, min_periods=1).mean()


# ---------------------------------------------------------------------------
# 9. Open interest proxy
# ---------------------------------------------------------------------------
@_factor(
    "oi_change_proxy", direction=-1,
    reference="Hong & Yogo (2012) RFS (open interest as positioning signal); "
              "bar-level proxy when no OI feed exists",
    required_columns=("close", "volume"),
    params={"window": ParamSpec("int", default=20, min=2)},
    description="signed volume accumulation as OI-change proxy (positioning build-up)",
)
def oi_change_proxy(data: pd.DataFrame, window: int = 20) -> pd.Series:
    """Signed-volume accumulation z-score: sign(ret) * volume, rolled up and
    z-scored — a bar-level proxy for open-interest change (positioning
    build-up in the direction of the move)."""
    sign = np.sign(data["close"].astype(float).diff()).fillna(0.0)
    acc = (sign * data["volume"].astype(float)).rolling(
        window, min_periods=window).sum()
    mu = acc.rolling(window, min_periods=window).mean()
    sd = acc.rolling(window, min_periods=window).std(ddof=1)
    return (acc - mu) / sd.replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# 10-12. Liquidity / toxicity
# ---------------------------------------------------------------------------
@_factor(
    "amihud_illiq", direction=+1,
    reference="Amihud (2002) JFM 'Illiquidity and stock returns'",
    required_columns=("close", "volume"),
    params={"window": ParamSpec("int", default=20, min=1)},
    description="mean |ret|/dollar_volume x1e9 (illiquidity premium)",
)
def amihud_illiq(data: pd.DataFrame, window: int = 20) -> pd.Series:
    """Rolling mean of |return| / dollar volume, scaled x1e9 (Amihud 2002).
    Direction +1: illiquid names earn an illiquidity premium."""
    close = data["close"].astype(float)
    dollar_vol = close * data["volume"].astype(float)
    raw = (close.pct_change().abs() / dollar_vol.replace(0.0, np.nan)).fillna(0.0)
    return raw.rolling(window, min_periods=1).mean() * 1e9


@_factor(
    "kyle_lambda", direction=-1,
    reference="Kyle (1985) Econometrica 'Continuous Auctions and Insider Trading'",
    required_columns=("close", "volume"),
    params={"window": ParamSpec("int", default=20, min=3)},
    description="rolling OLS slope of price change on signed volume (price impact)",
)
def kyle_lambda(data: pd.DataFrame, window: int = 20) -> pd.Series:
    """Rolling Kyle lambda: OLS slope of Δprice on signed sqrt-volume over
    ``window`` bars — the price impact per unit of signed order flow
    (Kyle 1985). High lambda = thin, informed market -> fade the move."""
    close = data["close"].astype(float)
    dp = close.diff()
    sv = np.sign(dp) * np.sqrt(data["volume"].astype(float))

    def _slope(idx: np.ndarray) -> float:
        x = sv.iloc[idx].to_numpy()
        y = dp.iloc[idx].to_numpy()
        x = x - x.mean()
        denom = float(np.dot(x, x))
        if denom == 0.0:
            return np.nan
        return float(np.dot(x, y - y.mean()) / denom)

    out = pd.Series(np.nan, index=data.index, dtype=float)
    for end in range(window, len(data) + 1):
        out.iloc[end - 1] = _slope(np.arange(end - window, end))
    return out * 1e6  # scale for readability


@_factor(
    "vpin_proxy", direction=-1,
    reference="Easley, López de Prado & O'Hara (2012) RFS 'Flow Toxicity and "
              "Liquidity in a High-frequency World'",
    required_columns=("close", "volume"),
    params={
        "window": ParamSpec("int", default=20, min=2),
        "vol_window": ParamSpec("int", default=30, min=2),
    },
    description="CDF-bucket volume imbalance ratio (order-flow toxicity proxy)",
)
def vpin_proxy(data: pd.DataFrame, window: int = 20,
               vol_window: int = 30) -> pd.Series:
    """VPIN approximation (Easley et al. 2012): classify each bar's volume as
    buy/sell via the normal CDF of the volatility-scaled return
    (``Phi(ret / sigma)``), then take the rolling mean of |buy - sell| /
    total volume. High VPIN = toxic flow -> negative forward returns."""
    from scipy.stats import norm  # local: keep module import dependency-free

    close = data["close"].astype(float)
    ret = close.pct_change()
    sigma = ret.rolling(vol_window, min_periods=vol_window).std(ddof=1)
    z = (ret / sigma.replace(0.0, np.nan)).fillna(0.0)
    v = data["volume"].astype(float)
    buy = v * norm.cdf(z.to_numpy())
    sell = v - buy
    imbalance = (buy - sell).abs() / v.replace(0.0, np.nan)
    return imbalance.rolling(window, min_periods=window).mean()
