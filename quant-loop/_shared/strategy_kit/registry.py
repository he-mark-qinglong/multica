"""Indicator registry — single source of truth for named, schema-validated
indicators used across strategy research.

Strategies reference indicators by *name* (config-driven), never by import
path, so a config swap can rewire a pipeline without code changes. Every
registered indicator carries a parameter schema; ``get_indicator`` validates
caller-supplied params against that schema before returning a ready-to-call
function, so a typo'd parameter fails loudly at load time instead of
silently changing behaviour.

All registered callables follow the project convention: pure functions,
``pd.Series`` / ``pd.DataFrame`` in, ``pd.Series`` out, no I/O, no globals.

Built-in registrations wrap existing shared modules (no re-implementation):
  - ``vpvr_poc_distance``   -> ``_shared/indicators/vpvr.py``
  - ``vol_target_weight``   -> ``_shared/sizing/vol_target.py``
  - ``amihud_illiquidity``  -> liquidity proxy in the spirit of
    ``_shared/sizing/liquidity.py`` (bar-level, no L2 required)

References:
- Amihud (2002) "Illiquidity and stock returns" JFM (amihud_illiquidity)
- Moreira & Muir (2017) "Volatility-Managed Portfolios" JF (vol_target_weight)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Schema primitives
# ---------------------------------------------------------------------------

# Supported JSON-schema-lite types for parameter validation.
_TYPE_MAP: Dict[str, type] = {
    "int": int,
    "float": (int, float),  # ints are valid floats
    "str": str,
    "bool": bool,
}


@dataclass(frozen=True)
class ParamSpec:
    """Schema for a single indicator parameter.

    Attributes:
        type: one of ``int | float | str | bool``.
        required: if True, caller must supply the value.
        default: used when not required and not supplied.
        min / max: optional inclusive numeric bounds (int/float only).
        choices: optional allowed-value set.
    """
    type: str
    required: bool = False
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    choices: Optional[Tuple[Any, ...]] = None


@dataclass(frozen=True)
class IndicatorSpec:
    """A registered indicator: callable + parameter schema + metadata."""
    name: str
    func: Callable[..., pd.Series]
    params: Mapping[str, ParamSpec] = field(default_factory=dict)
    description: str = ""
    version: str = "1.0.0"
    source: str = ""  # originating module, for traceability


class IndicatorNotFoundError(KeyError):
    """Raised when ``get_indicator`` is asked for an unknown name."""


class ParamValidationError(ValueError):
    """Raised when caller params fail schema validation."""


# Module-level registry. Populated by @register_indicator at import time.
_REGISTRY: Dict[str, IndicatorSpec] = {}


def _validate_params(name: str, schema: Mapping[str, ParamSpec],
                     params: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate ``params`` against ``schema``; return params with defaults filled.

    Pure function — raises ParamValidationError on any violation.
    """
    unknown = set(params) - set(schema)
    if unknown:
        raise ParamValidationError(
            f"indicator '{name}': unknown params {sorted(unknown)}; "
            f"allowed: {sorted(schema)}"
        )
    bound: Dict[str, Any] = {}
    for pname, spec in schema.items():
        if pname in params:
            value = params[pname]
        elif spec.required:
            raise ParamValidationError(
                f"indicator '{name}': missing required param '{pname}'"
            )
        else:
            value = spec.default
        # bool is a subclass of int — reject bools for numeric params and
        # non-bools for bool params explicitly.
        expected = _TYPE_MAP[spec.type]
        if spec.type == "bool":
            ok = isinstance(value, bool)
        elif spec.type in ("int", "float"):
            ok = isinstance(value, expected) and not isinstance(value, bool)
        else:
            ok = isinstance(value, expected)
        if not ok:
            raise ParamValidationError(
                f"indicator '{name}': param '{pname}' must be {spec.type}, "
                f"got {type(value).__name__} ({value!r})"
            )
        if spec.type in ("int", "float"):
            if spec.min is not None and value < spec.min:
                raise ParamValidationError(
                    f"indicator '{name}': param '{pname}'={value} < min {spec.min}"
                )
            if spec.max is not None and value > spec.max:
                raise ParamValidationError(
                    f"indicator '{name}': param '{pname}'={value} > max {spec.max}"
                )
        if spec.choices is not None and value not in spec.choices:
            raise ParamValidationError(
                f"indicator '{name}': param '{pname}'={value!r} not in "
                f"{list(spec.choices)}"
            )
        bound[pname] = value
    return bound


def register_indicator(
    name: str,
    params: Optional[Mapping[str, ParamSpec]] = None,
    description: str = "",
    version: str = "1.0.0",
    source: str = "",
    replace: bool = False,
) -> Callable[[Callable[..., pd.Series]], Callable[..., pd.Series]]:
    """Decorator: register ``func(data, **params) -> pd.Series`` under ``name``.

    Args:
        name: unique registry key.
        params: parameter schema (name -> ParamSpec). Empty means no params.
        description: one-line doc for ``list_indicators``.
        version: semantic version of the indicator definition.
        source: originating module path (traceability).
        replace: allow overwriting an existing registration (default False —
            silent redefinition is almost always a bug).

    Returns:
        The decorator; the wrapped function is returned unchanged.
    """
    def decorator(func: Callable[..., pd.Series]) -> Callable[..., pd.Series]:
        if name in _REGISTRY and not replace:
            raise ValueError(
                f"indicator '{name}' already registered "
                f"(source={_REGISTRY[name].source}); pass replace=True to override"
            )
        _REGISTRY[name] = IndicatorSpec(
            name=name,
            func=func,
            params=dict(params or {}),
            description=description,
            version=version,
            source=source,
        )
        return func

    return decorator


def get_indicator(name: str, **params: Any) -> Callable[[Any], pd.Series]:
    """Fetch indicator ``name`` with params validated against its schema.

    Returns a one-argument callable ``f(data) -> pd.Series`` with the
    validated params bound. Raises IndicatorNotFoundError for unknown names
    and ParamValidationError for bad params.
    """
    if name not in _REGISTRY:
        raise IndicatorNotFoundError(
            f"unknown indicator '{name}'; registered: {sorted(_REGISTRY)}"
        )
    spec = _REGISTRY[name]
    bound = _validate_params(name, spec.params, params)

    def call(data: Any) -> pd.Series:
        return spec.func(data, **bound)

    call.__name__ = f"indicator:{name}"
    return call


def get_spec(name: str) -> IndicatorSpec:
    """Return the full IndicatorSpec (schema + metadata) for ``name``."""
    if name not in _REGISTRY:
        raise IndicatorNotFoundError(
            f"unknown indicator '{name}'; registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def list_indicators() -> Dict[str, str]:
    """name -> description for every registered indicator."""
    return {n: s.description for n, s in sorted(_REGISTRY.items())}


def clear_registry() -> None:
    """Drop all registrations. Test isolation only — never call in prod."""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# Built-in registrations — thin adapters over existing shared modules.
# Registered lazily on first access of `register_builtins` so that importing
# this module never hard-fails if an optional upstream module moves.
# ---------------------------------------------------------------------------

_BUILTINS_REGISTERED = False


def _vpvr_poc_distance(data: pd.DataFrame, lookback: int = 200,
                       num_bins: int = 50) -> pd.Series:
    """Signed distance of close from the rolling VPVR POC, in ATR-like units.

    Positive = close above the Point of Control (crowded longs above value).
    Wraps ``_shared/indicators/vpvr.build_volume_profile`` on a trailing
    window; result is a causal rolling indicator (uses only data <= t).
    """
    from _shared.indicators.vpvr import build_volume_profile, find_poc

    close = data["close"].astype(float)
    high = data["high"].astype(float)
    low = data["low"].astype(float)
    volume = data["volume"].astype(float)
    tr = pd.concat(
        [(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(14, min_periods=1).mean()

    out = pd.Series(np.nan, index=data.index, dtype=float)
    n = len(data)
    for t in range(lookback, n):
        lo, hi = t - lookback, t
        centers, profile, _bin_width = build_volume_profile(
            high.iloc[lo:hi],
            low.iloc[lo:hi],
            volume.iloc[lo:hi],
            num_bins=num_bins,
        )
        poc = find_poc(centers, profile)
        scale = atr.iloc[t] if atr.iloc[t] > 0 else np.nan
        out.iloc[t] = (close.iloc[t] - poc) / scale if np.isfinite(scale) else np.nan
    return out


def _vol_target_weight(data: pd.DataFrame, target_vol: float = 0.15,
                       lookback: int = 20, floor: float = 0.1,
                       cap: float = 3.0,
                       periods_per_year: int = 365) -> pd.Series:
    """Vol-target size multiplier from close returns.

    Wraps ``_shared/sizing/vol_target.vol_target_weights``.
    """
    from _shared.sizing.vol_target import vol_target_weights

    returns = data["close"].astype(float).pct_change().fillna(0.0)
    return vol_target_weights(
        returns,
        target_vol=target_vol,
        lookback=lookback,
        floor=floor,
        cap=cap,
        periods_per_year=periods_per_year,
    )


def _amihud_illiquidity(data: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Rolling Amihud illiquidity: mean(|ret| / dollar_volume).

    Bar-level liquidity proxy in the spirit of
    ``_shared/sizing/liquidity.py`` (MCLS) for when no L2 snapshot exists.
    Higher = less liquid = smaller allowable size. Causal (uses <= t only).
    """
    close = data["close"].astype(float)
    dollar_vol = close * data["volume"].astype(float)
    raw = (close.pct_change().abs() / dollar_vol.replace(0.0, np.nan)).fillna(0.0)
    return raw.rolling(lookback, min_periods=1).mean() * 1e9  # scaled for readability


def register_builtins() -> None:
    """Idempotently register the built-in example indicators."""
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    register_indicator(
        "vpvr_poc_distance",
        params={
            "lookback": ParamSpec("int", default=200, min=10),
            "num_bins": ParamSpec("int", default=50, min=5),
        },
        description="(close - VPVR POC) / ATR over trailing window",
        source="_shared/indicators/vpvr.py",
    )(_vpvr_poc_distance)
    register_indicator(
        "vol_target_weight",
        params={
            "target_vol": ParamSpec("float", default=0.15, min=0.0, max=2.0),
            "lookback": ParamSpec("int", default=20, min=2),
            "floor": ParamSpec("float", default=0.1, min=0.0),
            "cap": ParamSpec("float", default=3.0, min=0.0),
            "periods_per_year": ParamSpec("int", default=365, min=1),
        },
        description="inverse-vol position multiplier (Moreira & Muir 2017)",
        source="_shared/sizing/vol_target.py",
    )(_vol_target_weight)
    register_indicator(
        "amihud_illiquidity",
        params={"lookback": ParamSpec("int", default=20, min=1)},
        description="rolling Amihud (2002) illiquidity, scaled x1e9",
        source="_shared/sizing/liquidity.py (bar-level proxy)",
    )(_amihud_illiquidity)
    _BUILTINS_REGISTERED = True


register_builtins()
