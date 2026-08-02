import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.strategy_kit.feature_pipeline import (
    FeatureDef,
    FeaturePipeline,
    LookaheadError,
    PipelineDefinitionError,
)


def _df(n: int = 200, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame(
        {
            "close": close,
            "volume": rng.uniform(1e5, 1e6, n),
        },
        index=pd.date_range("2026-01-01", periods=n, freq="h"),
    )


def _causal_defs() -> list[FeatureDef]:
    return [
        FeatureDef("ret_1", ("close",),
                   lambda d: d["close"].pct_change().fillna(0.0), lookback=1),
        FeatureDef("mom_10", ("close",),
                   lambda d: d["close"].pct_change(10).fillna(0.0), lookback=10),
        # chained: consumes another feature
        FeatureDef("mom_sq", ("mom_10",), lambda d: d["mom_10"] ** 2,
                   lookback=10),
    ]


# ---------------------------------------------------------------------------
# DAG resolution
# ---------------------------------------------------------------------------

def test_topological_order_respects_dependencies():
    p = FeaturePipeline(_causal_defs())
    order = [d.name for d in p.resolve_order(("close", "volume"))]
    assert order.index("mom_10") < order.index("mom_sq")


def test_cycle_detected():
    defs = [
        FeatureDef("a", ("b",), lambda d: d["b"]),
        FeatureDef("b", ("a",), lambda d: d["a"]),
    ]
    p = FeaturePipeline(defs)
    with pytest.raises(PipelineDefinitionError, match="cycle"):
        p.compute(_df())


def test_unknown_input_detected():
    p = FeaturePipeline([FeatureDef("x", ("ghost",), lambda d: d["ghost"])])
    with pytest.raises(PipelineDefinitionError, match="unknown inputs"):
        p.compute(_df())


def test_duplicate_names_detected():
    with pytest.raises(PipelineDefinitionError, match="duplicate"):
        FeaturePipeline([
            FeatureDef("x", ("close",), lambda d: d["close"]),
            FeatureDef("x", ("close",), lambda d: d["close"] * 2),
        ])


# ---------------------------------------------------------------------------
# computation
# ---------------------------------------------------------------------------

def test_compute_values_and_version_attr():
    df = _df()
    p = FeaturePipeline(_causal_defs())
    feats = p.compute(df)
    assert list(feats.columns) == ["ret_1", "mom_10", "mom_sq"]
    assert feats.attrs["feature_version"] == p.feature_version
    assert np.allclose(feats["mom_sq"], feats["mom_10"] ** 2)
    pd.testing.assert_series_equal(
        feats["ret_1"], df["close"].pct_change().fillna(0.0), check_names=False
    )


def test_chained_feature_via_compute():
    df = _df(50)
    p = FeaturePipeline(_causal_defs())
    feats = p.compute(df)
    expected = df["close"].pct_change(10).fillna(0.0) ** 2
    assert np.allclose(feats["mom_sq"], expected)


# ---------------------------------------------------------------------------
# anti-lookahead — the core guarantee
# ---------------------------------------------------------------------------

def test_causal_pipeline_passes_assertion():
    p = FeaturePipeline(_causal_defs())
    p.assert_no_lookahead(_df())  # must not raise


def test_future_peeking_feature_caught():
    # shift(-1) reads tomorrow's close — the canonical leak.
    leaky = FeaturePipeline([
        FeatureDef("tomorrow", ("close",),
                   lambda d: d["close"].shift(-1), lookback=1),
    ])
    with pytest.raises(LookaheadError, match="reads data after t"):
        leaky.assert_no_lookahead(_df())


def test_centered_rolling_caught():
    leaky = FeaturePipeline([
        FeatureDef("centered", ("close",),
                   lambda d: d["close"].rolling(5, center=True).mean(),
                   lookback=5),
    ])
    with pytest.raises(LookaheadError):
        leaky.assert_no_lookahead(_df())


def test_global_normalisation_caught():
    # (x - full-sample mean) / std leaks the whole distribution into t.
    leaky = FeaturePipeline([
        FeatureDef("zscore", ("close",),
                   lambda d: (d["close"] - d["close"].mean()) / d["close"].std(),
                   lookback=100),
    ])
    with pytest.raises(LookaheadError):
        leaky.assert_no_lookahead(_df())


def test_underdeclared_lookback_caught():
    # Causal, but reads 30 bars back while declaring lookback=2.
    under = FeaturePipeline([
        FeatureDef("sneaky", ("close",),
                   lambda d: d["close"].pct_change(30).fillna(0.0),
                   lookback=2),
    ])
    with pytest.raises(LookaheadError, match="declared lookback"):
        under.assert_no_lookahead(_df(120))


def test_lookahead_check_independent_of_column_noise():
    # Feature reading 'close' must not move when unrelated 'volume' changes.
    df = _df()
    p = FeaturePipeline(_causal_defs())
    a = p.compute(df)["mom_10"]
    df2 = df.copy()
    df2["volume"] = df2["volume"] * 3
    b = p.compute(df2)["mom_10"]
    pd.testing.assert_series_equal(a, b, check_names=False)


# ---------------------------------------------------------------------------
# parquet cache
# ---------------------------------------------------------------------------

def test_cache_roundtrip(tmp_path):
    df = _df(60)
    p = FeaturePipeline(_causal_defs())
    feats = p.compute(df)
    path = p.save_cache(feats, tmp_path / "feats.parquet")
    assert path.exists()
    assert (tmp_path / "feats.parquet.meta.json").exists()
    loaded = p.load_cache(path)
    # parquet roundtrip does not preserve DatetimeIndex.freq — values do.
    pd.testing.assert_frame_equal(loaded, feats, check_freq=False)
    assert loaded.attrs["feature_version"] == p.feature_version


def test_stale_cache_rejected_and_recomputed(tmp_path):
    df = _df(60)
    p_v1 = FeaturePipeline(_causal_defs(), version="1.0.0")
    path = p_v1.compute_cached(df, tmp_path / "c.parquet")
    # Same defs, bumped version -> different feature_version -> recompute.
    p_v2 = FeaturePipeline(_causal_defs(), version="2.0.0")
    with pytest.raises(ValueError, match="stale cache"):
        p_v2.load_cache(tmp_path / "c.parquet")
    out = p_v2.compute_cached(df, tmp_path / "c.parquet")  # auto-recompute
    assert out.attrs["feature_version"] == p_v2.feature_version


def test_save_cache_refuses_foreign_frame(tmp_path):
    p = FeaturePipeline(_causal_defs())
    foreign = pd.DataFrame({"ret_1": [0.1]})
    with pytest.raises(ValueError, match="refusing to cache"):
        p.save_cache(foreign, tmp_path / "x.parquet")


def test_version_fingerprint_changes_with_defs():
    p1 = FeaturePipeline(_causal_defs(), version="1.0.0")
    defs2 = _causal_defs() + [
        FeatureDef("extra", ("close",), lambda d: d["close"] * 0, lookback=0)
    ]
    p2 = FeaturePipeline(defs2, version="1.0.0")
    assert p1.feature_version != p2.feature_version
