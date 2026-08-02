"""ML gateway — single, version-checked entry point for model inference.

Strategies never touch model objects directly. They call
``MLGateway.predict(features_df)`` and the gateway enforces the contract:

1. **Feature-version binding** — every model is loaded together with the
   ``feature_version`` it was trained on (produced by
   ``_shared/strategy_kit/feature_pipeline.py``). The features DataFrame
   must carry ``df.attrs["feature_version"]``; a mismatch raises
   ``VersionMismatchError`` *before* inference, so a stale pipeline can
   never silently feed a retrained model.
2. **Feature-name / order check** — columns must match the model's
   training-time feature list exactly (order included).

Built-in models:
- ``PassthroughModel`` — no-ML baseline; echoes one column (or the row
  mean) as the "prediction". Lets strategies wire the full gateway path
  before any real model exists (walk before you run).
- ``load_pickle_model`` — any pickled object exposing ``predict``. A
  scikit-learn estimator (e.g. ``RandomForestRegressor``) works when
  sklearn is installed; the gateway itself never imports sklearn, so the
  dependency stays optional.

References:
- López de Prado (2018) *Advances in Financial Machine Learning*, ch. 7
  (feature importance must be tied to a fixed feature set), ch. 10 (bet
  sizing from model probability).
- Sculley et al. (2015) "Hidden Technical Debt in Machine Learning
  Systems" (NeurIPS) — glue-code / pipeline-jungle motivation for a single
  version-checked gateway instead of ad-hoc model calls.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Protocol, Sequence, runtime_checkable

import numpy as np
import pandas as pd


class VersionMismatchError(RuntimeError):
    """Features were produced by a different pipeline version than the model
    was trained on."""


class FeatureSchemaError(ValueError):
    """Feature columns do not match the model's training-time schema."""


@runtime_checkable
class Predictor(Protocol):
    """Anything with a ``predict`` method (sklearn-compatible)."""

    def predict(self, X: Any) -> Any:  # pragma: no cover - protocol decl
        ...


@dataclass(frozen=True)
class ModelBundle:
    """A model plus the schema/version it was trained against.

    Attributes:
        model: object exposing ``predict``.
        model_version: free-form version tag (e.g. git sha, run id).
        feature_version: must equal the ``feature_version`` of the
            pipeline that produced incoming features.
        feature_names: exact training-time column order.
    """
    model: Predictor
    model_version: str
    feature_version: str
    feature_names: tuple[str, ...]


class PassthroughModel:
    """Baseline "model": echoes a column (or row mean) as the prediction.

    Useful for wiring tests and for strategies that want the gateway path
    without ML. ``predict`` returns ``X[column]`` when ``column`` is set,
    else the row-wise mean of all columns.
    """

    def __init__(self, column: Optional[str] = None) -> None:
        self.column = column

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.column is not None:
            return X[self.column].to_numpy(dtype=float)
        return X.mean(axis=1).to_numpy(dtype=float)


def load_pickle_model(
    path: str | Path,
    model_version: str,
    feature_version: str,
    feature_names: Sequence[str],
) -> ModelBundle:
    """Load a pickled model and bind it to its training-time schema.

    The pickle must contain an object with a ``predict`` method (any
    sklearn estimator qualifies). ``feature_version`` / ``feature_names``
    are supplied *by the caller* — they are metadata about how the model
    was trained and cannot be trusted to live inside an arbitrary pickle.

    Raises:
        FileNotFoundError: bad path.
        TypeError: unpickled object has no ``predict``.
    """
    path = Path(path)
    with path.open("rb") as fh:
        obj = pickle.load(fh)
    if not isinstance(obj, Predictor):
        raise TypeError(
            f"pickled object at {path} has no predict method "
            f"(got {type(obj).__name__})"
        )
    return ModelBundle(
        model=obj,
        model_version=model_version,
        feature_version=feature_version,
        feature_names=tuple(feature_names),
    )


def make_passthrough_bundle(
    feature_names: Sequence[str],
    feature_version: str,
    column: Optional[str] = None,
    model_version: str = "passthrough-1.0",
) -> ModelBundle:
    """Convenience: a ModelBundle wrapping ``PassthroughModel``."""
    names = tuple(feature_names)
    if column is not None and column not in names:
        raise ValueError(
            f"passthrough column {column!r} not in feature_names {list(names)}"
        )
    return ModelBundle(
        model=PassthroughModel(column=column),
        model_version=model_version,
        feature_version=feature_version,
        feature_names=names,
    )


class MLGateway:
    """Version-checked inference entry point for one or more models.

    Multiple bundles can be registered by name; ``predict`` picks one.
    All checks are pure — no mutation, no I/O at inference time.
    """

    def __init__(self, bundles: Optional[dict[str, ModelBundle]] = None) -> None:
        self._bundles: dict[str, ModelBundle] = dict(bundles or {})

    def register(self, name: str, bundle: ModelBundle, replace: bool = False) -> None:
        if name in self._bundles and not replace:
            raise ValueError(f"model '{name}' already registered")
        self._bundles[name] = bundle

    def bundle(self, name: str) -> ModelBundle:
        try:
            return self._bundles[name]
        except KeyError:
            raise KeyError(
                f"unknown model '{name}'; registered: {sorted(self._bundles)}"
            ) from None

    @staticmethod
    def _check_features(bundle: ModelBundle, features: pd.DataFrame) -> None:
        got_version = features.attrs.get("feature_version")
        if got_version != bundle.feature_version:
            raise VersionMismatchError(
                f"feature_version mismatch: model '{bundle.model_version}' "
                f"expects '{bundle.feature_version}', features carry "
                f"{got_version!r}"
            )
        got_cols = tuple(features.columns)
        if got_cols != bundle.feature_names:
            raise FeatureSchemaError(
                f"feature columns {list(got_cols)} do not match model schema "
                f"{list(bundle.feature_names)} (order matters)"
            )

    def predict(self, features: pd.DataFrame, model: Optional[str] = None) -> pd.Series:
        """Run inference after version + schema checks.

        Args:
            features: DataFrame carrying ``attrs["feature_version"]`` and
                exactly the model's training columns (same order).
            model: registered model name; required when more than one
                bundle is registered, defaults to the only bundle.

        Returns:
            Prediction Series aligned to ``features.index``.
        """
        if model is None:
            if len(self._bundles) != 1:
                raise ValueError(
                    f"model name required; registered: {sorted(self._bundles)}"
                )
            model = next(iter(self._bundles))
        b = self.bundle(model)
        self._check_features(b, features)
        raw = np.asarray(b.model.predict(features[list(b.feature_names)]), dtype=float)
        if raw.shape[0] != len(features):
            raise RuntimeError(
                f"model returned {raw.shape[0]} predictions for "
                f"{len(features)} rows"
            )
        return pd.Series(raw, index=features.index, name=f"pred:{model}")
