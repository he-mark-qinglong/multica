"""End-to-end tests for the generic three-framework CV harness (Phase D).

Uses a dummy signal-layer strategy (frequent fixed-hold longs) over synthetic
1h bars. Framework replay legs (backtrader/freqtrade/vectorbt) are exercised
when the engines are installed and recorded as skips otherwise — the harness
must never crash on a missing framework.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from _shared.run_backtest import Trade

from validation import generic_harness as gh
from validation import oos_harness
from validation.adapters.vectorbt_replay import VectorbtReplayError, run_vectorbt_replay


# --------------------------------------------------------------------------
# fixtures / dummy strategy
# --------------------------------------------------------------------------

def _synthetic_data(n_days: int = 60, seed: int = 7) -> dict[str, pd.DataFrame]:
    idx = pd.date_range("2026-01-01", periods=n_days * 24, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    out = {}
    for k, sym in enumerate(("BTCUSDT", "ETHUSDT")):
        ret = 0.0002 + 0.002 * rng.standard_normal(len(idx))
        close = (100.0 + 50.0 * k) * np.cumprod(1.0 + ret)
        out[sym] = pd.DataFrame(
            {"open": close, "high": close * 1.001, "low": close * 0.999,
             "close": close, "volume": 1.0},
            index=idx,
        )
    return out


def dummy_signals(df: pd.DataFrame, cfg: dict) -> list[dict]:
    """Dummy contract-v2 strategy: long every 48 bars, hold 12 bars."""
    idx = df.index
    trades = []
    i = 10
    while i + 12 < len(idx):
        trades.append({"entry_ts": idx[i], "exit_ts": idx[i + 12], "direction": "long"})
        i += 48
    return trades


CONFIG = {
    "timeframe": "1h",
    "instruments": ["BTCUSDT", "ETHUSDT"],
    "fees_bps_per_side": 1.0,
    "slippage_bps_per_side": 1.0,
    "starting_capital_usd": 100_000.0,
    "sizing": {"per_signal_weight_pct": 0.01, "max_gross_exposure_pct": 0.05},
}


# --------------------------------------------------------------------------
# unit: trade normalisation / helpers
# --------------------------------------------------------------------------

def test_normalize_trades_accepts_dicts_and_trade_objects():
    ts0 = pd.Timestamp("2026-01-01", tz="UTC")
    ts1 = pd.Timestamp("2026-01-02", tz="UTC")
    out = gh.normalize_trades(
        [
            {"entry_ts": ts0, "exit_ts": ts1, "direction": "short"},
            Trade(entry_ts=ts0, exit_ts=ts1, direction="long", size_fraction=0.5),
        ],
        default_size=0.01,
    )
    assert len(out) == 2
    assert out[0].direction == "short" and out[0].size_fraction == pytest.approx(0.01)
    assert out[1].size_fraction == pytest.approx(0.5)  # explicit size preserved


def test_normalize_trades_rejects_bad_direction():
    with pytest.raises(ValueError, match="direction"):
        gh.normalize_trades(
            [{"entry_ts": pd.Timestamp("2026-01-01", tz="UTC"),
              "exit_ts": pd.Timestamp("2026-01-02", tz="UTC"),
              "direction": "sideways"}], default_size=0.01)


def test_trade_dicts_shape_for_replay_adapters():
    t = Trade(entry_ts=pd.Timestamp("2026-01-01", tz="UTC"),
              exit_ts=pd.Timestamp("2026-01-02", tz="UTC"), direction="long")
    d = gh.trade_dicts([t])[0]
    assert d["direction"] == "long"
    assert d["entry_date"] == t.entry_ts and d["exit_date"] == t.exit_ts


def test_freq_per_year():
    assert gh.freq_per_year("1h") == 365 * 24
    assert gh.freq_per_year("1m") == 365 * 24 * 60
    with pytest.raises(ValueError):
        gh.freq_per_year("7m")


def test_unknown_framework_rejected():
    with pytest.raises(ValueError, match="unknown frameworks"):
        gh.run_generic_validation(dummy_signals, CONFIG, _synthetic_data(),
                                  frameworks=["native", "metatrader"])


# --------------------------------------------------------------------------
# end-to-end: programmatic API
# --------------------------------------------------------------------------

def test_end_to_end_native_only_writes_verdict(tmp_path):
    passed, report = gh.run_generic_validation(
        dummy_signals, CONFIG, _synthetic_data(),
        n_windows=3, frameworks=["native"],
        output_dir=tmp_path, variant_name="dummy_hf")

    verdict_path = tmp_path / "verdict.json"
    assert verdict_path.exists()
    assert (tmp_path / "verdict.md").exists()
    on_disk = json.loads(verdict_path.read_text())

    assert on_disk["pipeline"] == "generic"
    assert on_disk["variant"] == "dummy_hf"
    assert on_disk["verdict"] in ("PASS", "FAIL")
    assert len(on_disk["windows"]) == 3
    assert set(on_disk["symbols"]) == {"BTCUSDT", "ETHUSDT"}
    gate_ids = {g["gate"] for g in on_disk["gates"]}
    assert {"G1", "G2", "G3", "G4", "G5", "G6", "G7", "T1"} <= gate_ids
    # every window has native metrics with trades from the dummy schedule
    for sym in on_disk["symbols"].values():
        assert len(sym) == 3
        for win in sym.values():
            assert "native" in win
            assert win["native"]["n_trades"] > 0
    assert isinstance(passed, bool)


def test_missing_frameworks_are_skipped_not_fatal(tmp_path):
    """Replay legs whose engines are absent land in framework_skips."""
    passed, report = gh.run_generic_validation(
        dummy_signals, CONFIG, _synthetic_data(n_days=30),
        n_windows=2, frameworks=["native", "backtrader", "freqtrade", "vectorbt"],
        output_dir=tmp_path, variant_name="dummy_skips")

    def _available(mod_or_cli: str) -> bool:
        import shutil
        import importlib.util
        if importlib.util.find_spec(mod_or_cli) is not None:
            return True
        return shutil.which(mod_or_cli) is not None

    skips = report["framework_skips"]
    for fw in ("backtrader", "freqtrade", "vectorbt"):
        if not _available(fw):
            assert fw in skips, f"{fw} unavailable but not recorded as skipped"
            for sym in report["symbols"].values():
                for win in sym.values():
                    assert fw not in win
    assert (tmp_path / "verdict.json").exists()


def test_backtrader_leg_runs_when_installed(tmp_path):
    pytest.importorskip("backtrader")
    _, report = gh.run_generic_validation(
        dummy_signals, CONFIG, _synthetic_data(n_days=30),
        n_windows=2, frameworks=["native", "backtrader"],
        output_dir=tmp_path, variant_name="dummy_bt")
    for sym in report["symbols"].values():
        for win in sym.values():
            assert "backtrader" in win
            assert win["backtrader"]["n_trades"] > 0


# --------------------------------------------------------------------------
# vectorbt adapter
# --------------------------------------------------------------------------

def test_vectorbt_replay_raises_clean_error_when_missing(monkeypatch):
    if types.ModuleType("vectorbt") in sys.modules.values() or \
            any(name == "vectorbt" for name in sys.modules):
        pytest.skip("vectorbt installed; missing-engine path not applicable")
    df = _synthetic_data(n_days=2)["BTCUSDT"]
    with pytest.raises(VectorbtReplayError, match="not installed"):
        run_vectorbt_replay(df, [], symbol="BTCUSDT")


class _StubTrades:
    def __init__(self, returns):
        self.records_readable = pd.DataFrame({"Return": returns})


class _StubPortfolio:
    def __init__(self, close, init_cash, n_returns):
        self._close = close
        self._init_cash = init_cash
        self.trades = _StubTrades([0.001] * n_returns)

    def value(self):
        return np.full(len(self._close), self._init_cash, dtype=float)


def test_vectorbt_replay_with_stubbed_engine(monkeypatch):
    """Plumbing test: signal construction, tz handling, result parsing."""
    captured = {}

    class _StubVBT(types.ModuleType):
        class Portfolio:
            @staticmethod
            def from_signals(**kwargs):
                captured.update(kwargs)
                n = int(kwargs["entries"].sum())  # one long entry per trade
                return _StubPortfolio(kwargs["close"], kwargs["init_cash"], n)

    monkeypatch.setitem(sys.modules, "vectorbt", _StubVBT("vectorbt"))

    df = _synthetic_data(n_days=3)["BTCUSDT"]
    idx = df.index
    trades = [
        {"direction": "long", "entry_date": idx[10], "exit_date": idx[20]},
        {"direction": "short", "entry_date": idx[30], "exit_date": idx[40]},
    ]
    run = run_vectorbt_replay(df, trades, symbol="BTCUSDT",
                              starting_cash=50_000.0, fees=0.0002, size=0.02)

    assert run.framework == "vectorbt"
    assert len(run.equity) == len(df)
    # tz-aware input must be localised to naive for vectorbt
    assert captured["entries"].index.tz is None
    assert int(captured["entries"].sum()) == 1
    assert int(captured["short_entries"].sum()) == 1
    assert int(captured["exits"].sum()) == 1
    assert int(captured["short_exits"].sum()) == 1
    assert captured["init_cash"] == 50_000.0
    assert captured["fees"] == pytest.approx(0.0002)
    assert captured["size"] == pytest.approx(0.02)
    assert run.trade_pnls == [0.001]


# --------------------------------------------------------------------------
# end-to-end: variant-directory contract + CLI routing
# --------------------------------------------------------------------------

_DATA_LOADER = '''
import numpy as np
import pandas as pd


def load_all(symbols, timeframe):
    idx = pd.date_range("2026-01-01", periods=30 * 24, freq="1h", tz="UTC")
    rng = np.random.default_rng(11)
    out = {}
    for k, sym in enumerate(symbols):
        ret = 0.0002 + 0.002 * rng.standard_normal(len(idx))
        close = (100.0 + 10.0 * k) * np.cumprod(1.0 + ret)
        out[sym] = pd.DataFrame(
            {"open": close, "high": close * 1.001, "low": close * 0.999,
             "close": close, "volume": 1.0}, index=idx)
    return out
'''

_SIGNALS = '''
def generate_signals(df, cfg):
    idx = df.index
    trades = []
    i = 10
    while i + 12 < len(idx):
        trades.append({"entry_ts": idx[i], "exit_ts": idx[i + 12], "direction": "long"})
        i += 48
    return trades
'''


def _make_variant(tmp_path: Path) -> Path:
    vdir = tmp_path / "dummy_hf_1h_20260724"
    vdir.mkdir()
    (vdir / "config.json").write_text(json.dumps(CONFIG))
    (vdir / "data_loader.py").write_text(_DATA_LOADER)
    (vdir / "signals.py").write_text(_SIGNALS)
    return vdir


def test_run_generic_from_variant(tmp_path):
    vdir = _make_variant(tmp_path)
    assert gh.is_generic_variant(vdir)
    passed, report = gh.run_generic_from_variant(
        vdir, n_windows=2, frameworks=["native"])
    assert report["variant"] == "dummy_hf_1h_20260724"
    assert report["pipeline"] == "generic"
    assert (vdir / "results" / "validation" / "verdict.json").exists()


def test_cli_routes_signals_variant_to_generic_pipeline(tmp_path, monkeypatch):
    vdir = _make_variant(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "oos_harness", "--variant", str(vdir),
        "--windows", "2", "--frameworks", "native",
    ])
    rc = oos_harness.main()
    assert rc in (0, 1)  # gate outcome is data-dependent; 2 would mean harness error
    verdict = json.loads((vdir / "results" / "validation" / "verdict.json").read_text())
    assert verdict["pipeline"] == "generic"


def test_cli_rejects_unknown_framework(tmp_path, monkeypatch):
    vdir = _make_variant(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "oos_harness", "--variant", str(vdir), "--frameworks", "native,nope",
    ])
    assert oos_harness.main() == 2


# --------------------------------------------------------------------------
# W1-T8: fee-shock sweep — default key shape and aggregation contract
# --------------------------------------------------------------------------

FEE_SHOCK_LOCKED_KEYS = {
    "extra_round_trip_bps",
    "sharpe_daily_resampled",
    "annualized_return",
    "total_return",
    "max_drawdown_pct",
}


def test_fee_shock_default_key_shape_in_verdict(tmp_path):
    """Default fee_shock_bps=(60.0,) lands at report['fee_shock']['60.0']
    with the full locked key set, and gates G1-G7 are unaffected."""
    _, report = gh.run_generic_validation(
        dummy_signals, CONFIG, _synthetic_data(n_days=60),
        n_windows=3, frameworks=["native"],
        output_dir=tmp_path, variant_name="dummy_fee_default")

    assert "fee_shock" in report
    fs = report["fee_shock"]
    assert "60.0" in fs, f"default key missing: keys={sorted(fs)}"
    per_sym_60 = fs["per_symbol"]["BTCUSDT"]["60.0"]
    assert set(per_sym_60) == FEE_SHOCK_LOCKED_KEYS
    for k in FEE_SHOCK_LOCKED_KEYS:
        assert isinstance(per_sym_60[k], float)
    # aggregate key (multi-symbol mean) carries the same locked schema
    assert set(fs["60.0"]) == FEE_SHOCK_LOCKED_KEYS
    # gates G1-G7/T1 must still be present and unchanged in count
    gate_ids = {g["gate"] for g in report["gates"]}
    assert {"G1", "G2", "G3", "G4", "G5", "G6", "G7", "T1"} <= gate_ids


def test_fee_shock_sweep_keys_monotone_and_mean_aggregated(tmp_path):
    """Custom bps schedule; per-symbol keys present; aggregate is the mean
    of per-symbol values; higher bps must not raise Sharpe on any per-symbol
    series (adding drag only subtracts from a non-negative basis)."""
    _, report = gh.run_generic_validation(
        dummy_signals, CONFIG, _synthetic_data(n_days=90),
        n_windows=3, frameworks=["native"],
        output_dir=tmp_path, variant_name="dummy_fee_custom",
        fee_shock_bps=(10.0, 30.0, 60.0, 120.0))

    fs = report["fee_shock"]
    expected_bps = ["10.0", "30.0", "60.0", "120.0"]
    assert sorted(fs["per_symbol"]) == ["BTCUSDT", "ETHUSDT"]
    for sym in ("BTCUSDT", "ETHUSDT"):
        per_sym = fs["per_symbol"][sym]
        assert sorted(per_sym, key=float) == expected_bps
        for k in expected_bps:
            assert set(per_sym[k]) == FEE_SHOCK_LOCKED_KEYS
        # monotone: as fee drag increases, per-symbol Sharpe must not rise.
        # The implementation subtracts daily drag; the underlying daily_ret is
        # bar-level. We only require non-increasing on the *per-symbol* mean
        # across the two symbols here, which is the natural contract.
        sharpes = [per_sym[k]["sharpe_daily_resampled"] for k in expected_bps]
        for a, b in zip(sharpes, sharpes[1:]):
            assert a >= b - 1e-12, (
                f"Sharpe rose with higher bps on per-symbol set: {sharpes}")

    # aggregate equals mean of per-symbol values at every bps level
    for k in expected_bps:
        agg = fs[k]
        sym_vals = [fs["per_symbol"][s][k] for s in ("BTCUSDT", "ETHUSDT")]
        for metric in FEE_SHOCK_LOCKED_KEYS:
            mean = sum(sv[metric] for sv in sym_vals) / len(sym_vals)
            assert agg[metric] == pytest.approx(mean, rel=1e-9), (
                f"aggregate[{k}][{metric}] != mean of per-symbol: "
                f"{agg[metric]} vs {mean}")
        # aggregate must round-trip back via _aggregate_fee_shock helper too
        rebuilt = gh._aggregate_fee_shock(fs["per_symbol"])
        assert rebuilt[k] == pytest.approx(agg, rel=1e-9)

