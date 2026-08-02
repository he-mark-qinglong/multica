"""Tests for research/long_momentum/long_momentum.py (pure core)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from research.long_momentum.long_momentum import (
    efficiency_ratio,
    momentum_trades,
    stats_by_year,
    strategy_bar_returns,
    trade_stats,
)


def make_closes(spec: dict[str, list[float]], start="2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(next(iter(spec.values()))),
                        freq="1h", tz="UTC")
    return pd.DataFrame({k: pd.Series(v, index=idx) for k, v in spec.items()})


# ---------------------------------------------------------------------------
# efficiency_ratio
# ---------------------------------------------------------------------------

def test_er_monotonic_is_one():
    c = pd.Series(np.arange(1.0, 50.0))  # perfectly efficient uptrend
    er = efficiency_ratio(c, 10)
    assert er.iloc[-1] == pytest.approx(1.0)


def test_er_alternating_near_zero():
    c = pd.Series([100.0, 101.0] * 25)  # net move 0, path long
    er = efficiency_ratio(c, 10)
    assert er.iloc[-1] == pytest.approx(0.0, abs=1e-12)


def test_er_warmup_is_nan():
    c = pd.Series(np.arange(1.0, 30.0))
    er = efficiency_ratio(c, 10)
    assert er.iloc[:10].isna().all()


# ---------------------------------------------------------------------------
# momentum_trades
# ---------------------------------------------------------------------------

def test_picks_strongest_symbol():
    n = 200
    a = np.linspace(100, 200, n)          # strong steady uptrend
    b = np.linspace(100, 90, n)           # downtrend
    c = np.full(n, 100.0)                 # flat
    closes = make_closes({"A": a, "B": b, "C": c})
    trades = momentum_trades(closes, lookback=24, hold=8, fee_bp=10.0)
    assert not trades.empty
    assert (trades["symbols"] == "A").all()
    assert (trades["gross_ret"] > 0).all()


def test_trades_are_non_overlapping():
    n = 500
    rng = np.random.default_rng(0)
    closes = make_closes({
        "A": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)),
        "B": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)),
        "C": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)),
    })
    hold = 24
    trades = momentum_trades(closes, lookback=48, hold=hold)
    gaps = trades["entry_time"].diff().dropna().dt.total_seconds() / 3600
    assert (gaps == hold).all()


def test_fees_subtracted():
    n = 200
    closes = make_closes({
        "A": np.linspace(100, 200, n),
        "B": np.linspace(100, 90, n),
        "C": np.full(n, 100.0),
    })
    fee_bp = 10.0
    trades = momentum_trades(closes, lookback=24, hold=8, fee_bp=fee_bp)
    assert np.allclose(trades["net_ret"],
                       trades["gross_ret"] - 2 * fee_bp / 1e4)


def test_short_history_returns_empty():
    closes = make_closes({"A": [1.0, 2.0, 3.0], "B": [1.0, 2.0, 3.0]})
    trades = momentum_trades(closes, lookback=24, hold=8)
    assert trades.empty


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def test_trade_stats_known_values():
    trades = pd.DataFrame({"net_ret": [0.01, -0.005, 0.02, 0.005]})
    s = trade_stats(trades)
    assert s["n"] == 4
    assert s["mean_bp"] == pytest.approx(75.0)
    assert s["winrate"] == pytest.approx(0.75)
    assert s["pf"] == pytest.approx(0.035 / 0.005)
    r = trades["net_ret"]
    assert s["t"] == pytest.approx(r.mean() / r.std(ddof=1) * 2.0)


def test_stats_by_year_groups():
    trades = pd.DataFrame({
        "entry_time": pd.to_datetime(
            ["2024-01-01", "2024-06-01", "2025-01-01"], utc=True),
        "year": [2024, 2024, 2025],
        "net_ret": [0.01, 0.01, -0.02],
    })
    by = stats_by_year(trades)
    assert by.loc[2024, "n"] == 2
    assert by.loc[2025, "n"] == 1
    assert by.loc[2025, "mean_bp"] == pytest.approx(-200.0)


# ---------------------------------------------------------------------------
# strategy_bar_returns
# ---------------------------------------------------------------------------

def test_bar_returns_match_trade_returns():
    n = 300
    rng = np.random.default_rng(1)
    closes = make_closes({
        "A": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)),
        "B": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)),
        "C": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)),
    })
    lookback, hold, fee_bp = 24, 12, 10.0
    trades = momentum_trades(closes, lookback, hold, fee_bp=fee_bp)
    px = closes.dropna()

    # Zero-fee stream: per-trade segment must compound to the basket's
    # mean bar-return path over exactly the hold window.
    strat0 = strategy_bar_returns(closes, trades, hold, fee_bp=0.0)
    bar_ret = px.pct_change().fillna(0.0)
    for tr in trades.itertuples():
        syms = tr.symbols.split(",")
        i0 = px.index.get_loc(tr.entry_time)
        seg = strat0.iloc[i0 + 1: i0 + hold + 1]
        expected = (1 + bar_ret[syms].iloc[i0 + 1: i0 + hold + 1]
                    .mean(axis=1)).prod() - 1
        assert float((1 + seg).prod() - 1) == pytest.approx(
            float(expected), abs=1e-12)

    # Fee stream: each trade is charged exactly 2 fees, so the compounded
    # segment is the zero-fee segment dragged by ~2 * fee_bp.
    strat_f = strategy_bar_returns(closes, trades, hold, fee_bp=fee_bp)
    for tr in trades.itertuples():
        i0 = px.index.get_loc(tr.entry_time)
        seg_f = strat_f.iloc[i0 + 1: i0 + hold + 1]
        seg_0 = strat0.iloc[i0 + 1: i0 + hold + 1]
        drag = float((1 + seg_f).prod() / (1 + seg_0).prod() - 1)
        assert drag == pytest.approx(-2 * fee_bp / 1e4, abs=1e-4)


def test_correlation_with_grid_handles_return_level_series():
    """Regression: grid_equity is a cumulative-return series (crosses zero);
    pct_change must be taken on the equity *level* (1 + cumret)."""
    from research.long_momentum.long_momentum import correlation_with_grid

    n = 24 * 400
    rng = np.random.default_rng(2)
    closes = make_closes({
        "A": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)),
        "B": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)),
        "C": 100 * np.cumprod(1 + rng.normal(0, 0.01, n)),
    })
    # Synthetic grid: steady small gains -> cumulative return drifts up
    # through zero, exactly the case that broke pct_change before.
    hourly = pd.Series(0.0002, index=closes.index)
    grid_cumret = (1 + hourly).cumprod() - 1.0

    res = correlation_with_grid(closes, lookback=24, hold=24,
                                grid_equity=grid_cumret)
    assert res["n_days"] > 300
    assert -1.0 <= res["corr"] <= 1.0
    for key in ("mom", "grid", "combo_50_50"):
        m = res[key]
        assert np.isfinite(m["ann_ret"])
        assert -1.0 <= m["max_dd"] <= 0.0
    # Grid with constant positive bar returns: no drawdown.
    assert res["grid"]["max_dd"] == pytest.approx(0.0, abs=1e-12)
