"""Declarative feature pipeline with topological ordering and a
no-lookahead assertion checker.

Features are *declared* (name, input columns, transform, lookback) rather
than coded inline, so a config can enumerate them and the pipeline can:

1. **Topologically sort** — a feature may list another feature as an
   input; the pipeline resolves the dependency DAG and computes in an
   order where every input already exists (cycle -> hard error).
2. **Assert no lookahead** — ``assert_no_lookahead`` recomputes every
   feature on truncated data and verifies the value at the truncation
   point is identical to the full-sample value. A feature whose value at
   bar t changes when bars after t are dropped is, by definition, peeking
   (López de Prado 2018, AFML ch. 7 — "backtesting is not a research
   tool" unless leakage is excluded). Declared ``lookback`` is also
   checked: mutating data older than ``t - lookback - 1`` must not change
   the value at t.
3. **Cache to parquet** — computed frames carry a ``feature_version``
   (hash of the feature definitions); a sidecar ``.meta.json`` stores the
   version next to the parquet so ``load_cache`` can refuse stale caches.

Transform contract: ``func(df) -> pd.Series`` where ``df`` contains the
declared input columns (raw columns and/or upstream features). The
transform must be causal — the checker enforces this empirically.

References:
- López de Prado (2018) *Advances in Financial Machine Learning*, ch. 7
  (cross-validation leakage), ch. 3 (label/feature alignment).
- Bailey & López de Prado (2014) "The Deflated Sharpe Ratio" — motivation
  for hard anti-leakage gates before any performance claim.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple

import numpy as np
import pandas as pd


class LookaheadError(AssertionError):
    """A feature's value depends on data after its timestamp."""


class PipelineDefinitionError(ValueError):
    """Bad DAG: unknown input, cycle, or duplicate feature name."""


@dataclass(frozen=True)
class FeatureDef:
    """One declarative feature.

    Attributes:
        name: output column name.
        inputs: raw DataFrame columns and/or names of other FeatureDefs.
        func: ``func(df[inputs]) -> pd.Series`` — must be causal.
        lookback: max bars of history the transform may touch (0 = only
            the current bar). Used by the lookback-window assertion and
            documented for cache warm-up (first ``lookback`` rows are
            considered warm-up).
    """
    name: str
    inputs: Tuple[str, ...]
    func: Callable[[pd.DataFrame], pd.Series]
    lookback: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            raise PipelineDefinitionError("feature name must be non-empty")
        if not self.inputs:
            raise PipelineDefinitionError(
                f"feature '{self.name}': inputs must be non-empty"
            )
        if self.lookback < 0:
            raise PipelineDefinitionError(
                f"feature '{self.name}': lookback must be >= 0"
            )


def _definition_fingerprint(defs: Tuple[FeatureDef, ...]) -> str:
    """Stable hash over feature names/inputs/lookbacks.

    Note: hashes the *declaration*, not the function bytecode — bump
    ``version`` explicitly when a transform's logic changes.
    """
    h = hashlib.sha256()
    for d in defs:
        h.update(d.name.encode())
        h.update(b"|")
        for inp in d.inputs:
            h.update(inp.encode())
            h.update(b",")
        h.update(str(d.lookback).encode())
        h.update(b";")
    return h.hexdigest()[:12]


class FeaturePipeline:
    """Topologically-ordered, version-tagged feature computation."""

    def __init__(self, defs: Tuple[FeatureDef, ...] | list[FeatureDef],
                 version: str = "1.0.0") -> None:
        self._defs: Tuple[FeatureDef, ...] = tuple(defs)
        names = [d.name for d in self._defs]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise PipelineDefinitionError(f"duplicate feature names: {dupes}")
        self._by_name: Dict[str, FeatureDef] = {d.name: d for d in self._defs}
        self.version = version
        self.fingerprint = _definition_fingerprint(self._defs)
        # feature_version is what ml_gateway binds models against.
        self.feature_version = f"{version}+{self.fingerprint}"

    # ----- DAG resolution -------------------------------------------------

    @property
    def feature_names(self) -> Tuple[str, ...]:
        return tuple(d.name for d in self._defs)

    def resolve_order(self, available_columns: Tuple[str, ...] | list[str]) -> Tuple[FeatureDef, ...]:
        """Kahn topological sort over feature dependencies.

        Inputs that are not feature names must be present in
        ``available_columns`` (the raw data). Raises on unknown inputs and
        on dependency cycles.
        """
        feature_names = set(self._by_name)
        available = set(available_columns)
        for d in self._defs:
            unknown = [i for i in d.inputs
                       if i not in feature_names and i not in available]
            if unknown:
                raise PipelineDefinitionError(
                    f"feature '{d.name}': unknown inputs {unknown}; not a "
                    f"feature and not in data columns {sorted(available)}"
                )
        # Edges only between features.
        indegree: Dict[str, int] = {d.name: 0 for d in self._defs}
        dependents: Dict[str, list[str]] = {d.name: [] for d in self._defs}
        for d in self._defs:
            for inp in d.inputs:
                if inp in feature_names:
                    indegree[d.name] += 1
                    dependents[inp].append(d.name)
        queue = [d.name for d in self._defs if indegree[d.name] == 0]
        ordered: list[str] = []
        while queue:
            name = queue.pop(0)
            ordered.append(name)
            for dep in dependents[name]:
                indegree[dep] -= 1
                if indegree[dep] == 0:
                    queue.append(dep)
        if len(ordered) != len(self._defs):
            cyclic = sorted(set(self._by_name) - set(ordered))
            raise PipelineDefinitionError(
                f"dependency cycle among features: {cyclic}"
            )
        return tuple(self._by_name[n] for n in ordered)

    # ----- computation ------------------------------------------------------

    def compute(self, df: pd.DataFrame,
                include_inputs: bool = False) -> pd.DataFrame:
        """Compute all features; result carries ``attrs["feature_version"]``.

        Args:
            df: raw data with all declared raw input columns.
            include_inputs: also keep the raw input columns in the output.
        """
        out = df.copy() if include_inputs else pd.DataFrame(index=df.index)
        work = df.copy()
        for d in self.resolve_order(tuple(df.columns)):
            cols = list(d.inputs)
            result = d.func(work[cols])
            if not isinstance(result, pd.Series):
                result = pd.Series(result, index=df.index)
            result = result.reindex(df.index)
            out[d.name] = result
            work[d.name] = result  # downstream features may consume it
        out.attrs["feature_version"] = self.feature_version
        return out

    # ----- anti-lookahead checks -------------------------------------------

    def assert_no_lookahead(self, df: pd.DataFrame,
                            sample_points: int = 5,
                            rtol: float = 1e-9) -> None:
        """Empirically verify every feature is causal on this data sample.

        For each sampled bar t: recompute the pipeline on ``df.iloc[:t+1]``
        and compare the value at t against the full-sample value. Any
        difference proves the transform read data after t.

        Also verifies the declared ``lookback``: values at t must be
        invariant to *blanking* rows older than ``t - lookback`` (NaN-fill
        before the window). A transform reading beyond its declared window
        is a bug even if it is technically causal — the warm-up contract
        and any cached incremental computation rely on the bound.

        Raises:
            LookaheadError: on the first violation found (feature, bar).
        """
        full = self.compute(df)
        n = len(df)
        if n < 4:
            raise ValueError("need at least 4 rows to assert no lookahead")
        # Evenly spaced interior sample points (skip the very first rows so
        # truncation leaves the transform something to work on).
        ts = sorted({min(n - 1, max(2, int(round(x))))
                     for x in np.linspace(2, n - 1, sample_points)})
        for t in ts:
            trunc = self.compute(df.iloc[: t + 1])
            for name in self.feature_names:
                a = full[name].iloc[t]
                b = trunc[name].iloc[t]
                if not _close_or_both_nan(a, b, rtol):
                    raise LookaheadError(
                        f"feature '{name}' at row {t}: full-sample value "
                        f"{a!r} != truncated value {b!r} — transform reads "
                        f"data after t"
                    )
        # Declared-lookback check: blank everything before the window and
        # confirm values at t do not move.
        max_lb = max((d.lookback for d in self._defs), default=0)
        if max_lb > 0:
            t = n - 1
            masked = df.copy()
            cutoff = t - max_lb
            if cutoff > 0:
                masked.iloc[:cutoff, :] = np.nan
                masked_res = self.compute(masked)
                for name in self.feature_names:
                    a = full[name].iloc[t]
                    b = masked_res[name].iloc[t]
                    if not _close_or_both_nan(a, b, rtol):
                        raise LookaheadError(
                            f"feature '{name}' at row {t}: value {a!r} "
                            f"changed to {b!r} when rows older than its "
                            f"declared lookback window were blanked — "
                            f"declared lookback is too small"
                        )

    # ----- parquet cache --------------------------------------------------

    def save_cache(self, features: pd.DataFrame, path: str | Path) -> Path:
        """Write features to parquet + version sidecar.

        The DataFrame must carry ``attrs["feature_version"]`` matching this
        pipeline (i.e. come from ``self.compute``).
        """
        path = Path(path)
        fv = features.attrs.get("feature_version")
        if fv != self.feature_version:
            raise ValueError(
                f"refusing to cache: features carry version {fv!r}, "
                f"pipeline is {self.feature_version!r}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        features.to_parquet(path)
        meta = {
            "feature_version": self.feature_version,
            "version": self.version,
            "fingerprint": self.fingerprint,
            "feature_names": list(self.feature_names),
        }
        path.with_suffix(path.suffix + ".meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        return path

    def load_cache(self, path: str | Path) -> pd.DataFrame:
        """Load a cached frame, refusing stale versions.

        Raises ValueError when the sidecar version != this pipeline's
        ``feature_version`` — a stale cache must be recomputed, never
        silently consumed (ml_gateway applies the same binding at
        inference time via ``df.attrs``).
        """
        path = Path(path)
        meta_path = path.with_suffix(path.suffix + ".meta.json")
        if not meta_path.exists():
            raise ValueError(f"cache sidecar missing: {meta_path}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("feature_version") != self.feature_version:
            raise ValueError(
                f"stale cache: sidecar version {meta.get('feature_version')!r} "
                f"!= pipeline {self.feature_version!r} — recompute"
            )
        df = pd.read_parquet(path)
        df.attrs["feature_version"] = meta["feature_version"]
        return df

    def compute_cached(self, df: pd.DataFrame, path: str | Path,
                       force: bool = False) -> pd.DataFrame:
        """Load from cache when version matches, else compute + save."""
        path = Path(path)
        if not force and path.exists():
            try:
                return self.load_cache(path)
            except ValueError:
                pass  # stale — fall through to recompute
        feats = self.compute(df)
        self.save_cache(feats, path)
        return feats


def _close_or_both_nan(a: float, b: float, rtol: float) -> bool:
    """NaN-aware closeness used by the lookahead checker."""
    try:
        if pd.isna(a) and pd.isna(b):
            return True
        if pd.isna(a) != pd.isna(b):
            return False
        return bool(np.isclose(float(a), float(b), rtol=rtol, atol=1e-12))
    except (TypeError, ValueError):
        return bool(a == b)
