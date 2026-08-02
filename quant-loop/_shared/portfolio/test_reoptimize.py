"""Tests for portfolio/reoptimize.py (I18)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.dynamic_erc import DynamicERCParams
from _shared.portfolio.reoptimize import (
    ReoptRecord, ReoptimizeConfig, Reoptimizer,
)

RNG = np.random.default_rng(7)


def _returns(n=80, vols=(0.01, 0.03), seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    data = {
        "A": rng.normal(0, vols[0], n),
        "B": rng.normal(0, vols[1], n),
    }
    return pd.DataFrame(data, index=idx)


def _cfg(**kw):
    kw.setdefault("erc_params",
                  DynamicERCParams(lookback=60, min_lookback=20,
                                   rebalance_freq=1))
    return ReoptimizeConfig(**kw)


def test_every_n_bars_schedule():
    ro = Reoptimizer(_cfg(every_n_bars=10))
    df = _returns()
    fired = []
    for i, ts in enumerate(df.index):
        rec = ro.on_bar(i, ts, df.loc[:ts])
        if rec is not None:
            fired.append((i, rec))
    assert [i for i, _ in fired] == [0, 10, 20, 30, 40, 50, 60, 70]
    # Bar 0 has < min_lookback rows → skipped; later ones apply.
    assert fired[0][1].skip_reason == "insufficient data for ERC"
    assert any(rec.applied for _, rec in fired[1:])


def test_daily_schedule_fires_once_per_date():
    ro = Reoptimizer(_cfg(daily=True))
    df = _returns()
    dates = []
    for i, ts in enumerate(df.index):
        rec = ro.on_bar(i, ts, df.loc[:ts])
        if rec is not None:
            dates.append(ts.date())
    assert len(dates) == len(set(dates))  # at most one fire per UTC date
    assert len(dates) > 1


def test_cron_schedule_fires_at_slot_once():
    ro = Reoptimizer(_cfg(cron_times=("09:30",)))
    df = _returns()
    fired = []
    for i, ts in enumerate(df.index):
        rec = ro.on_bar(i, ts, df.loc[:ts])
        if rec is not None:
            fired.append(ts)
    assert fired, "cron slot never fired"
    for ts in fired:
        assert (ts.hour, ts.minute) >= (9, 30)
    # One fire per day at most, and first eligible bar of the day.
    by_day = {}
    for ts in fired:
        by_day.setdefault(ts.date(), []).append(ts)
    assert all(len(v) == 1 for v in by_day.values())
    first_bar_after_slot = {
        d: min(t for t in df.index
               if t.date() == d and (t.hour, t.minute) >= (9, 30))
        for d in by_day
    }
    for d, v in by_day.items():
        assert v[0] == first_bar_after_slot[d]


def test_debounce_skips_small_weight_change():
    """Near-identical data → tiny weight delta → not applied."""
    ro = Reoptimizer(_cfg(weight_change_threshold=0.05))
    df = _returns()
    rec1 = ro.trigger_manual(df.index[-1], df)
    assert rec1.applied
    applied_w = dict(ro.weights)
    # Same statistical regime, one extra bar from the same generator.
    extra = pd.DataFrame(
        {"A": [0.001], "B": [-0.002]},
        index=[df.index[-1] + pd.Timedelta(hours=1)],
    )
    df2 = pd.concat([df, extra])
    rec2 = ro.trigger_manual(df2.index[-1], df2)
    assert not rec2.applied
    assert "debounced" in rec2.skip_reason
    assert ro.weights == applied_w  # unchanged


def test_large_regime_shift_applies():
    ro = Reoptimizer(_cfg(weight_change_threshold=0.01))
    df = _returns(vols=(0.01, 0.03))
    rec1 = ro.trigger_manual(df.index[-1], df)
    assert rec1.applied
    w1 = dict(ro.weights)
    # Flip the vol regime: B becomes the low-vol asset. The frame grows
    # by one bar, as on any real re-fire (DynamicERC caches by length).
    df2 = _returns(n=81, vols=(0.03, 0.01), seed=8)
    rec2 = ro.trigger_manual(df2.index[-1], df2)
    assert rec2.applied
    assert ro.weights != w1
    assert rec2.weight_diff
    assert max(abs(d) for d in rec2.weight_diff.values()) > 0.01


def test_first_compute_always_applies():
    """From empty weights any ERC output is applied (nothing to debounce)."""
    ro = Reoptimizer(_cfg(weight_change_threshold=0.99))
    df = _returns()
    rec = ro.trigger_manual(df.index[-1], df)
    assert rec.applied


def test_cov_summary_recorded():
    ro = Reoptimizer(_cfg())
    df = _returns()
    rec = ro.trigger_manual(df.index[-1], df)
    cs = rec.cov_summary
    assert cs["n_assets"] == 2.0
    assert cs["n_observations"] == 60.0  # lookback window
    assert -1.0 <= cs["mean_pairwise_corr"] <= 1.0
    assert cs["mean_asset_vol"] > 0


def test_audit_log_written(tmp_path):
    path = tmp_path / "reopt.jsonl"
    ro = Reoptimizer(_cfg(every_n_bars=25), audit_path=path)
    df = _returns()
    for i, ts in enumerate(df.index):
        ro.on_bar(i, ts, df.loc[:ts])
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(lines) == len(ro.records) == 4  # bars 0, 25, 50, 75
    assert {l["trigger"] for l in lines} == {"every_n_bars"}
    assert any(l["applied"] for l in lines)
    assert any(not l["applied"] for l in lines)  # bar 0: insufficient data


def test_records_are_frozen():
    ro = Reoptimizer(_cfg())
    df = _returns()
    rec = ro.trigger_manual(df.index[-1], df)
    assert isinstance(rec, ReoptRecord)
    with pytest.raises(AttributeError):
        rec.applied = False  # type: ignore[misc]


def test_config_validation():
    with pytest.raises(ValueError):
        ReoptimizeConfig(every_n_bars=0)
    with pytest.raises(ValueError):
        ReoptimizeConfig(cron_times=("25:00",))
    with pytest.raises(ValueError):
        ReoptimizeConfig(weight_change_threshold=-0.1)
