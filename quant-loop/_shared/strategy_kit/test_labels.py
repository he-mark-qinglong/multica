import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.strategy_kit.labels import BarrierConfig, triple_barrier_labels


def _close(values, start="2026-01-01"):
    return pd.Series(
        values, index=pd.date_range(start, periods=len(values), freq="h"),
        dtype=float,
    )


# ---------------------------------------------------------------------------
# basic barrier resolution (close-only)
# ---------------------------------------------------------------------------

def test_tp_hit_first():
    close = _close([100, 101, 103, 102, 101])
    cfg = BarrierConfig(tp=0.02, sl=0.01, max_bars=4)
    out = triple_barrier_labels(close, cfg)
    # bar0: 103 >= 100*1.02 at bar2 -> +1, tp
    assert out["label"].iloc[0] == 1
    assert out["barrier"].iloc[0] == "tp"
    assert out["touch_bar"].iloc[0] == 2
    assert out["ret"].iloc[0] == pytest.approx(0.03)


def test_sl_hit_first():
    close = _close([100, 99, 98.5, 99])
    cfg = BarrierConfig(tp=0.05, sl=0.01, max_bars=3)
    out = triple_barrier_labels(close, cfg)
    # bar0: 99 <= 100*0.99 at bar1 -> -1, sl
    assert out["label"].iloc[0] == -1
    assert out["barrier"].iloc[0] == "sl"
    assert out["touch_bar"].iloc[0] == 1


def test_vertical_barrier_gives_zero():
    close = _close([100, 100.2, 99.8, 100.1, 100.0, 100.3])
    cfg = BarrierConfig(tp=0.05, sl=0.05, max_bars=3)
    out = triple_barrier_labels(close, cfg)
    assert out["label"].iloc[0] == 0
    assert out["barrier"].iloc[0] == "time"
    assert out["touch_bar"].iloc[0] == 3  # t + max_bars
    assert out["ret"].iloc[0] == pytest.approx(0.001)


def test_sign_on_timeout_variant():
    close = _close([100, 100.5, 100.4, 100.6, 100.5, 100.4])
    cfg = BarrierConfig(tp=0.10, sl=0.10, max_bars=3, sign_on_timeout=True)
    out = triple_barrier_labels(close, cfg)
    assert out["label"].iloc[0] == 1  # timed out in profit -> sign(+ret)
    assert out["barrier"].iloc[0] == "time"


def test_sl_priority_within_same_bar_close_only():
    # close-only path checks TP before SL, but a single close cannot be both;
    # use a big move that crosses both: first crossing wins by move size.
    close = _close([100, 106, 95])
    cfg = BarrierConfig(tp=0.05, sl=0.04, max_bars=2)
    out = triple_barrier_labels(close, cfg)
    assert out["label"].iloc[0] == 1  # +6% crosses TP first
    assert out["label"].iloc[1] == -1  # 95 <= 106*0.96 -> SL


# ---------------------------------------------------------------------------
# high/low touch detection
# ---------------------------------------------------------------------------

def test_high_low_intrabar_touch():
    close = _close([100, 100.5, 100.6])
    high = _close([100.2, 103.5, 100.7])  # bar1 high crosses TP
    low = _close([99.5, 100.1, 100.2])
    cfg = BarrierConfig(tp=0.03, sl=0.02, max_bars=2)
    out = triple_barrier_labels(close, cfg, high=high, low=low)
    assert out["label"].iloc[0] == 1
    assert out["touch_bar"].iloc[0] == 1


def test_both_barriers_same_bar_resolves_to_sl():
    # Wide bar spanning TP and SL: conservative adverse outcome.
    close = _close([100, 100.5])
    high = _close([100.1, 104.0])
    low = _close([99.9, 97.0])
    cfg = BarrierConfig(tp=0.03, sl=0.02, max_bars=1)
    out = triple_barrier_labels(close, cfg, high=high, low=low)
    assert out["label"].iloc[0] == -1
    assert out["barrier"].iloc[0] == "sl"


# ---------------------------------------------------------------------------
# short side
# ---------------------------------------------------------------------------

def test_short_side_flips_barriers():
    close = _close([100, 98, 97])  # price falls: short profits
    cfg = BarrierConfig(tp=0.02, sl=0.01, max_bars=2, side=-1)
    out = triple_barrier_labels(close, cfg)
    assert out["label"].iloc[0] == 1   # TP for a short = price down 2%
    assert out["barrier"].iloc[0] == "tp"
    assert out["ret"].iloc[0] == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# censoring & edge cases
# ---------------------------------------------------------------------------

def test_end_of_data_marked_censored():
    close = _close([100, 100.1, 100.2])
    cfg = BarrierConfig(tp=0.50, sl=0.50, max_bars=10)
    out = triple_barrier_labels(close, cfg)
    # Nothing fires before data runs out -> barrier='end', label 0.
    assert (out["barrier"] == "end").all()
    assert (out["label"] == 0).all()


def test_touch_time_alignment():
    close = _close([100, 99, 98.5, 99])
    cfg = BarrierConfig(tp=0.05, sl=0.01, max_bars=3)
    out = triple_barrier_labels(close, cfg)
    assert out["touch_time"].iloc[0] == close.index[1]


def test_config_validation():
    with pytest.raises(ValueError):
        BarrierConfig(tp=-0.01)
    with pytest.raises(ValueError):
        BarrierConfig(max_bars=0)
    with pytest.raises(ValueError):
        BarrierConfig(side=0)


def test_labels_on_random_walk_distribution():
    rng = np.random.default_rng(4)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1000))))
    cfg = BarrierConfig(tp=0.02, sl=0.02, max_bars=20)
    out = triple_barrier_labels(close, cfg)
    assert set(out["label"].unique()) <= {-1, 0, 1}
    # Symmetric barriers on a driftless walk: TP/SL roughly balanced.
    n_tp = (out["barrier"] == "tp").sum()
    n_sl = (out["barrier"] == "sl").sum()
    assert n_tp > 0 and n_sl > 0
    assert 0.25 < n_tp / (n_tp + n_sl) < 0.75
