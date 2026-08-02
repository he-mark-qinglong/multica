import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.strategy_kit.labels import BarrierConfig
from _shared.strategy_kit.meta_labeling import (
    MetaModel,
    ToyLogistic,
    ToyLogisticConfig,
    build_meta_dataset,
    market_state_features,
    uniqueness_weights,
)


# ---------------------------------------------------------------------------
# synthetic market: alternating uptrend (long wins) / chop-down (long loses)
# ---------------------------------------------------------------------------

def _market(n_blocks=8, block=40, seed=7):
    """Uptrend blocks drift +0.4%/bar; chop blocks alternate -0.3%/+0.2%
    (net down). Deterministic -> labels are exact, no flaky randomness."""
    rng = np.random.default_rng(seed)
    rets, in_trend = [], []
    for b in range(n_blocks):
        trend = (b % 2 == 0)
        for _ in range(block):
            if trend:
                rets.append(0.004)
            else:
                rets.append(-0.003 if len(rets) % 2 == 0 else 0.002)
            in_trend.append(trend)
    rets = np.array(rets) + rng.normal(0, 1e-5, len(rets))  # dequantize
    close = 100.0 * np.cumprod(1.0 + rets)
    idx = pd.date_range("2026-01-01", periods=len(close), freq="h")
    data = pd.DataFrame(
        {
            "close": close,
            "high": close * 1.0005,
            "low": close * 0.9995,
            "volume": np.full(len(close), 1000.0),
        },
        index=idx,
    )
    return data, pd.Series(in_trend, index=idx)


@pytest.fixture
def dataset():
    data, in_trend = _market()
    side = pd.Series(1, index=data.index)  # primary model: always long
    cfg = BarrierConfig(tp=0.01, sl=0.01, max_bars=10)
    ds = build_meta_dataset(data, side, config=cfg, weight="return")
    return data, in_trend, ds


# ---------------------------------------------------------------------------
# dataset construction
# ---------------------------------------------------------------------------

def test_labels_follow_regime(dataset):
    """Long signals whose whole barrier horizon sits inside an uptrend
    block are profitable (y=1); inside a chop-down block they are not
    (y=0). Signals near a block boundary look into the next regime, so
    purity is judged over the full horizon, not just the signal bar."""
    _, in_trend, ds = dataset
    horizon = 10  # BarrierConfig.max_bars in the fixture
    pure_trend = in_trend[::-1].rolling(horizon + 1).min()[::-1].astype(bool)
    pure_chop = (~in_trend)[::-1].rolling(horizon + 1).min()[::-1].astype(bool)
    y = ds.y
    assert (y[pure_trend.reindex(y.index)] == 1).all()
    assert (y[pure_chop.reindex(y.index)] == 0).all()


def test_flat_signals_and_censored_rows_excluded():
    data, _ = _market(n_blocks=2)
    side = pd.Series(0, index=data.index)
    side.iloc[10:20] = 1
    side.iloc[-5:] = 1  # signals at data end -> barrier='end' -> censored
    ds = build_meta_dataset(data, side, config=BarrierConfig(max_bars=10),
                            weight="none")
    assert len(ds.y) == 10                       # only the interior 10
    assert ds.events["side"].eq(1).all()
    assert ds.X.index.equals(ds.y.index)


def test_features_are_present_and_causal(dataset):
    _, in_trend, ds = dataset
    assert set(ds.X.columns) == set(market_state_features())
    # warmup rows legitimately yield NaN (rolling windows not full yet);
    # past the longest lookback everything is finite
    assert np.isfinite(ds.X.iloc[70:].to_numpy()).all()
    # trend_strength separates the regimes -> it is a real market-state read
    ts = ds.X["trend_strength"]
    aligned = in_trend.reindex(ds.X.index)
    assert ts[aligned].mean() > ts[~aligned].mean()


def test_pluggable_feature_fn():
    data, _ = _market(n_blocks=2)
    side = pd.Series(1, index=data.index)
    ds = build_meta_dataset(
        data, side, config=BarrierConfig(max_bars=5),
        features={"const": lambda d, t: 1.0,
                  "bar": lambda d, t: float(t)},
        weight="none",
    )
    assert list(ds.X.columns) == ["const", "bar"]
    assert (ds.X["const"] == 1.0).all()


# ---------------------------------------------------------------------------
# sample weights (AFML ch. 4)
# ---------------------------------------------------------------------------

def test_return_weights_are_abs_ret_normalised(dataset):
    _, _, ds = dataset
    assert ds.w.mean() == pytest.approx(1.0)
    expected = ds.events["ret"].abs() / ds.events["ret"].abs().mean()
    pd.testing.assert_series_equal(ds.w, expected, check_names=False)


def test_uniqueness_weights_penalise_overlap():
    idx = pd.date_range("2026-01-01", periods=100, freq="h")
    # long event [10, 90]; short event [20, 25] fully inside it; short
    # event [92, 95] entirely outside any other event
    t0 = pd.Series([idx[10], idx[20], idx[92]])
    t1 = pd.Series([idx[90], idx[25], idx[95]])
    w = uniqueness_weights(t0, t1, idx)
    assert w.mean() == pytest.approx(1.0)
    # the fully-overlapped short event is the least unique; the isolated
    # one is the most unique
    assert w.iloc[1] < w.iloc[0] < w.iloc[2]
    assert w.iloc[1] < 1.0 < w.iloc[2]


# ---------------------------------------------------------------------------
# end-to-end meta model
# ---------------------------------------------------------------------------

def test_meta_model_ranks_strong_signals_above_weak(dataset):
    """AFML 3.6 end-to-end: meta model trained on regime features assigns
    higher P(profitable) to strong-trend signals than to chop signals."""
    _, in_trend, ds = dataset
    model = ToyLogistic(ToyLogisticConfig(lr=0.5, n_iter=3000))
    assert isinstance(model, MetaModel)  # protocol conformance
    model.fit(ds.X.to_numpy(), ds.y.to_numpy(), sample_weight=ds.w.to_numpy())
    proba = model.predict_proba(ds.X.to_numpy())
    assert proba.shape == (len(ds.y),)
    assert ((proba > 0) & (proba < 1)).all()

    aligned = in_trend.reindex(ds.y.index)
    p_strong = proba[aligned.to_numpy()]
    p_weak = proba[~aligned.to_numpy()]
    assert p_strong.mean() > p_weak.mean() + 0.3
    assert p_strong.mean() > 0.5
    assert p_weak.mean() < 0.5


def test_meta_model_generalises_to_unseen_blocks():
    """Train on first half of blocks, predict second half: ranking holds."""
    data, in_trend = _market(n_blocks=10)
    side = pd.Series(1, index=data.index)
    ds = build_meta_dataset(data, side, config=BarrierConfig(max_bars=10),
                            weight="uniqueness")
    cut = ds.X.index[len(ds.y) // 2]
    tr, te = ds.X.index <= cut, ds.X.index > cut
    model = ToyLogistic(ToyLogisticConfig(lr=0.5, n_iter=3000))
    model.fit(ds.X.to_numpy()[tr], ds.y.to_numpy()[tr],
              sample_weight=ds.w.to_numpy()[tr])
    proba = model.predict_proba(ds.X.to_numpy()[te])
    y_te = ds.y.to_numpy()[te]
    # precision of "take trades with p > 0.5" beats the base rate
    take = proba > 0.5
    assert take.any()
    assert y_te[take].mean() > y_te.mean()


def test_toy_logistic_guards():
    model = ToyLogistic()
    with pytest.raises(RuntimeError):
        model.predict_proba(np.zeros((3, 2)))
    with pytest.raises(ValueError):
        model.fit(np.zeros((3, 2)), np.zeros(4))


def test_invalid_weight_mode_rejected():
    data, _ = _market(n_blocks=1)
    side = pd.Series(1, index=data.index)
    with pytest.raises(ValueError):
        build_meta_dataset(data, side, weight="bogus")
