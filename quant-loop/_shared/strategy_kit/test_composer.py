import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.strategy_kit.composer import (
    ComposerConfig,
    compose_signals,
    decorrelate_weights,
    fixed_weights,
    ic_weights,
)


def _signals(n: int = 400, seed: int = 1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    base = rng.normal(0, 1, n)
    df = pd.DataFrame(
        {
            "mom": np.sign(pd.Series(base).rolling(5).mean().fillna(0)),
            "rev": -np.sign(pd.Series(base).rolling(3).mean().fillna(0)),
            "carry": np.tanh(rng.normal(0, 1, n)),
        },
        index=idx,
    )
    fwd = pd.Series(rng.normal(0, 0.01, n), index=idx)
    return df, fwd


# ---------------------------------------------------------------------------
# fixed weights
# ---------------------------------------------------------------------------

def test_fixed_weights_normalised():
    df, _ = _signals()
    w = fixed_weights(df, {"mom": 2.0, "rev": 1.0, "carry": 1.0})
    assert w.abs().sum() == pytest.approx(1.0)
    assert w["mom"] == pytest.approx(0.5)


def test_fixed_all_zero_rejected():
    df, _ = _signals()
    with pytest.raises(ValueError):
        fixed_weights(df, {})


def test_compose_fixed_output_bounds():
    df, _ = _signals()
    out = compose_signals(df, ComposerConfig(
        method="fixed",
        weights={"mom": 0.5, "rev": 0.3, "carry": 0.2},
        decorrelate=False,
    ))
    assert out.between(-1, 1).all()
    assert len(out) == len(df)


def test_compose_fixed_recovers_single_signal():
    df, _ = _signals()
    out = compose_signals(df[["mom"]], ComposerConfig(
        method="fixed", weights={"mom": 1.0}, decorrelate=False,
    ))
    pd.testing.assert_series_equal(out, df["mom"].fillna(0).clip(-1, 1),
                                   check_names=False)


# ---------------------------------------------------------------------------
# vote
# ---------------------------------------------------------------------------

def test_vote_majority():
    df = pd.DataFrame({
        "a": [1, 1, -1],
        "b": [1, -1, -1],
        "c": [-1, 1, -1],
    }, dtype=float)
    out = compose_signals(df, ComposerConfig(method="vote", decorrelate=False))
    # row0: +1,+1,-1 -> +1/3; row1: +1,-1,+1 -> +1/3; row2: all -1 -> -1
    assert out.iloc[0] == pytest.approx(1 / 3)
    assert out.iloc[1] == pytest.approx(1 / 3)
    assert out.iloc[2] == pytest.approx(-1.0)


def test_vote_tie_gives_zero():
    df = pd.DataFrame({"a": [1.0], "b": [-1.0]})
    out = compose_signals(df, ComposerConfig(method="vote", decorrelate=False))
    assert out.iloc[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# IC weights
# ---------------------------------------------------------------------------

def test_ic_weights_favour_predictive_signal():
    n = 600
    rng = np.random.default_rng(3)
    idx = pd.RangeIndex(n)
    fwd = pd.Series(rng.normal(0, 1, n), index=idx)
    good = fwd.shift(1).fillna(0) + rng.normal(0, 0.1, n)  # yesterday's fwd ~ today's signal
    noise = pd.Series(rng.normal(0, 1, n), index=idx)
    # Make 'good' genuinely predictive: signal_t ~ fwd_{t}
    good = fwd + rng.normal(0, 0.5, n)
    df = pd.DataFrame({"good": good, "noise": noise}, index=idx)
    w = ic_weights(df, fwd, lookback=500)
    assert w["good"] > 0.9
    assert w.abs().sum() == pytest.approx(1.0)


def test_ic_negative_signal_gets_negative_weight():
    n = 400
    rng = np.random.default_rng(5)
    fwd = pd.Series(rng.normal(0, 1, n))
    anti = -fwd + rng.normal(0, 0.3, n)
    df = pd.DataFrame({"anti": anti})
    w = ic_weights(df, fwd, lookback=350)
    assert w["anti"] == pytest.approx(-1.0)


def test_ic_requires_forward_returns():
    df, _ = _signals()
    with pytest.raises(ValueError, match="forward_returns"):
        compose_signals(df, ComposerConfig(method="ic", decorrelate=False))


# ---------------------------------------------------------------------------
# decorrelation
# ---------------------------------------------------------------------------

def test_decorrelate_shrinks_duplicate_signal():
    n = 500
    rng = np.random.default_rng(9)
    s1 = pd.Series(rng.normal(0, 1, n))
    s2 = s1 * 0.98 + rng.normal(0, 0.02, n)  # near-duplicate of s1
    s3 = pd.Series(rng.normal(0, 1, n))      # independent
    df = pd.DataFrame({"s1": s1, "s2": s2, "s3": s3})
    # Distinct initial weights make the greedy accept order deterministic:
    # s1 (largest) is accepted first, its near-duplicate s2 is shrunk hard,
    # independent s3 keeps its share.
    w = pd.Series({"s1": 0.4, "s2": 0.3, "s3": 0.3})
    out = decorrelate_weights(w, df, threshold=0.7, lookback=500)
    assert out["s2"] < 0.05
    assert out["s3"] > 0.4
    assert out.abs().sum() == pytest.approx(1.0)


def test_decorrelate_off_leaves_fixed_composite():
    df, _ = _signals()
    w_off = compose_signals(df, ComposerConfig(
        method="fixed", weights={"mom": 1, "rev": 1, "carry": 1},
        decorrelate=False,
    ))
    w_on = compose_signals(df, ComposerConfig(
        method="fixed", weights={"mom": 1, "rev": 1, "carry": 1},
        decorrelate=True, corr_threshold=0.9999,  # effectively off
    ))
    pd.testing.assert_series_equal(w_off, w_on, check_names=False, atol=1e-6)


def test_invalid_method_rejected():
    with pytest.raises(ValueError, match="method"):
        ComposerConfig(method="magic")


def test_nan_signals_treated_as_flat():
    df = pd.DataFrame({"a": [np.nan, 1.0], "b": [1.0, np.nan]})
    out = compose_signals(df, ComposerConfig(
        method="fixed", weights={"a": 0.5, "b": 0.5}, decorrelate=False,
    ))
    assert out.iloc[0] == pytest.approx(0.5)
    assert out.iloc[1] == pytest.approx(0.5)
