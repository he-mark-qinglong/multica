import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pickle

import numpy as np
import pandas as pd
import pytest

from _shared.strategy_kit.ml_gateway import (
    FeatureSchemaError,
    MLGateway,
    ModelBundle,
    PassthroughModel,
    VersionMismatchError,
    load_pickle_model,
    make_passthrough_bundle,
)

FEATURES = ("mom", "vol")
FVERSION = "1.0.0+abc123"


def _features(n: int = 50, version: str = FVERSION) -> pd.DataFrame:
    rng = np.random.default_rng(2)
    df = pd.DataFrame(
        {"mom": rng.normal(0, 1, n), "vol": rng.uniform(0, 1, n)},
        index=pd.date_range("2026-01-01", periods=n, freq="h"),
    )
    df.attrs["feature_version"] = version
    return df


# ---------------------------------------------------------------------------
# passthrough path
# ---------------------------------------------------------------------------

def test_passthrough_column_echo():
    gw = MLGateway({"base": make_passthrough_bundle(FEATURES, FVERSION, column="mom")})
    feats = _features()
    pred = gw.predict(feats)
    pd.testing.assert_series_equal(pred, feats["mom"].rename("pred:base"))


def test_passthrough_row_mean():
    gw = MLGateway({"base": make_passthrough_bundle(FEATURES, FVERSION)})
    feats = _features()
    pred = gw.predict(feats)
    assert np.allclose(pred, feats.mean(axis=1))


def test_passthrough_bad_column_rejected():
    with pytest.raises(ValueError, match="not in feature_names"):
        make_passthrough_bundle(FEATURES, FVERSION, column="nope")


# ---------------------------------------------------------------------------
# version & schema binding
# ---------------------------------------------------------------------------

def test_version_mismatch_raises():
    gw = MLGateway({"base": make_passthrough_bundle(FEATURES, FVERSION)})
    with pytest.raises(VersionMismatchError, match="feature_version mismatch"):
        gw.predict(_features(version="9.9.9+deadbeef"))


def test_missing_version_attr_raises():
    gw = MLGateway({"base": make_passthrough_bundle(FEATURES, FVERSION)})
    feats = _features()
    feats.attrs.clear()
    with pytest.raises(VersionMismatchError):
        gw.predict(feats)


def test_column_order_mismatch_raises():
    gw = MLGateway({"base": make_passthrough_bundle(FEATURES, FVERSION)})
    feats = _features()[["vol", "mom"]]  # same columns, wrong order
    with pytest.raises(FeatureSchemaError, match="order matters"):
        gw.predict(feats)


def test_missing_column_raises():
    gw = MLGateway({"base": make_passthrough_bundle(FEATURES, FVERSION)})
    with pytest.raises(FeatureSchemaError):
        gw.predict(_features()[["mom"]])


# ---------------------------------------------------------------------------
# pickle loader (sklearn-compatible protocol, no sklearn needed)
# ---------------------------------------------------------------------------

class _LinearToy:
    """Minimal pickle-able model with an sklearn-like predict."""

    def __init__(self, w):
        self.w = np.asarray(w, dtype=float)

    def predict(self, X):
        return X.to_numpy(dtype=float) @ self.w


def test_load_pickle_model_roundtrip(tmp_path):
    p = tmp_path / "model.pkl"
    with p.open("wb") as fh:
        pickle.dump(_LinearToy([2.0, -1.0]), fh)
    bundle = load_pickle_model(
        p, model_version="toy-0.1", feature_version=FVERSION,
        feature_names=FEATURES,
    )
    assert isinstance(bundle, ModelBundle)
    gw = MLGateway({"toy": bundle})
    feats = _features()
    pred = gw.predict(feats, model="toy")
    expected = 2.0 * feats["mom"] - 1.0 * feats["vol"]
    assert np.allclose(pred, expected)


def test_load_pickle_rejects_non_model(tmp_path):
    p = tmp_path / "bad.pkl"
    with p.open("wb") as fh:
        pickle.dump({"not": "a model"}, fh)
    with pytest.raises(TypeError, match="no predict"):
        load_pickle_model(p, "v1", FVERSION, FEATURES)


def test_load_pickle_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_pickle_model(tmp_path / "nope.pkl", "v1", FVERSION, FEATURES)


# ---------------------------------------------------------------------------
# multi-model gateway
# ---------------------------------------------------------------------------

def test_multiple_models_require_name():
    gw = MLGateway({
        "a": make_passthrough_bundle(FEATURES, FVERSION, column="mom"),
        "b": make_passthrough_bundle(FEATURES, FVERSION, column="vol"),
    })
    with pytest.raises(ValueError, match="model name required"):
        gw.predict(_features())
    feats = _features()
    assert np.allclose(gw.predict(feats, model="b"), feats["vol"])


def test_unknown_model_raises():
    gw = MLGateway({"a": make_passthrough_bundle(FEATURES, FVERSION)})
    with pytest.raises(KeyError, match="unknown model"):
        gw.predict(_features(), model="ghost")


def test_prediction_length_guard():
    class _BadLen:
        def predict(self, X):
            return np.zeros(len(X) + 1)

    bundle = ModelBundle(model=_BadLen(), model_version="bad",
                         feature_version=FVERSION, feature_names=FEATURES)
    gw = MLGateway({"bad": bundle})
    with pytest.raises(RuntimeError, match="predictions"):
        gw.predict(_features())
