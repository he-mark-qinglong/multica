"""Tests for decompose.py. Plain asserts, prints N/N passed at end.

Run directly: python3 _shared/attribution/test_decompose.py
Also collectable by pytest.
"""
import json
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from decompose import (
    RATIFIED,
    CostSpec,
    LedgerError,
    alpha_beta,
    attribute,
    normalize_trades,
    write_report,
)

results = []


def check(name, fn):
    try:
        fn()
        results.append((name, True, ""))
    except Exception as e:  # noqa: BLE001
        results.append((name, False, f"{type(e).__name__}: {e}"))


def _single_df():
    # 3 hand-computed single-instrument trades.
    return pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 3,
            "direction": ["long", "short", "long"],
            "entry_ts": ["2026-01-01T00:00", "2026-01-02T00:00", "2026-01-03T00:00"],
            "exit_ts": ["2026-01-01T06:00", "2026-01-02T06:00", "2026-01-03T06:00"],
            "entry_price": [100.0, 100.0, 100.0],
            "exit_price": [102.0, 102.0, 99.0],
            "bars_held": [6, 6, 6],
            "exit_reason": ["tp", "sl", "tp"],
        }
    )


def _pair_df():
    # long_a_short_b: ret_a = +1%, ret_b = -2%  -> gross = 0.01 - (-0.02) = 0.03
    return pd.DataFrame(
        {
            "pair": ["A/B"],
            "direction": ["long_a_short_b"],
            "entry_ts": ["2026-01-01T00:00"],
            "exit_ts": ["2026-01-01T06:00"],
            "entry_price_a": [100.0],
            "entry_price_b": [200.0],
            "exit_price_a": [101.0],
            "exit_price_b": [196.0],
            "bars_held": [6],
            "exit_reason": ["z_mean_revert"],
        }
    )


def t_gross_recompute_single():
    tr = normalize_trades(_single_df())
    got = tr["gross_ret"].tolist()
    exp = [0.02, -0.02, -0.01]  # long 100->102, short 100->102, long 100->99
    assert all(abs(g - e) < 1e-15 for g, e in zip(got, exp)), got


def t_gross_recompute_pair():
    tr = normalize_trades(_pair_df())
    assert abs(tr["gross_ret"].iloc[0] - 0.03) < 1e-15, tr["gross_ret"].iloc[0]


def t_a0_identity():
    tr = normalize_trades(_single_df())
    rep = attribute(tr, [("ratified", RATIFIED), ("cheap", CostSpec(1, 1))])
    for s in rep["scenarios"]:
        assert abs(s["net_sum"] - (s["gross_sum"] - s["cost_sum"])) < 1e-12


def t_a2_hand_computed_scenario():
    tr = normalize_trades(_single_df())
    spec = CostSpec(4, 7, fills_per_round_trip=2)  # 0.0022 per trade
    rep = attribute(tr, [("ratified", spec)])
    s = rep["scenarios"][0]
    # gross_sum = 0.02 - 0.02 - 0.01 = -0.01; cost = 3 * 0.0022
    assert abs(s["gross_sum"] - (-0.01)) < 1e-15
    assert abs(s["cost_sum"] - 0.0066) < 1e-15
    assert abs(s["net_sum"] - (-0.0166)) < 1e-15
    assert s["verdict"] == "MECHANISM_KILL"
    # break-even on a gross-positive ledger:
    tr2 = normalize_trades(_single_df().iloc[[0]])
    rep2 = attribute(tr2, [("ratified", spec)])
    s2 = rep2["scenarios"][0]
    assert abs(s2["break_even_bps_per_side"] - 0.02 * 10_000 / 2) < 1e-9
    assert s2["verdict"] == "VIABLE_AT_COST"


def t_cost_cap_classification():
    tr = normalize_trades(_single_df().iloc[[0]])  # gross +0.02
    rep = attribute(
        tr,
        [("cheap", CostSpec(1, 1, 2)), ("absurd", CostSpec(500, 500, 2))],
    )
    cheap, absurd = rep["scenarios"]
    assert cheap["verdict"] == "VIABLE_AT_COST"
    assert absurd["verdict"] == "COST_CAP_KILL"
    assert abs(absurd["cost_drag_ratio"] - (0.20 / 0.02)) < 1e-9  # 0.2 cost / 0.02 gross


def t_a1_sentinel_exit_before_entry():
    df = _single_df()
    df.loc[0, "exit_ts"] = "2025-12-31T00:00"
    try:
        normalize_trades(df)
    except LedgerError:
        return
    raise AssertionError("expected LedgerError for exit_ts < entry_ts")


def t_a1_sentinel_bad_price_and_direction():
    df = _single_df()
    df.loc[0, "entry_price"] = 0.0
    try:
        normalize_trades(df)
    except LedgerError:
        pass
    else:
        raise AssertionError("expected LedgerError for zero price")
    df = _single_df()
    df.loc[0, "direction"] = "sideways"
    try:
        normalize_trades(df)
    except LedgerError:
        return
    raise AssertionError("expected LedgerError for unknown direction")


def t_date_aliases():
    df = _single_df().rename(columns={"entry_ts": "entry_date", "exit_ts": "exit_date"})
    tr = normalize_trades(df)
    assert len(tr) == 3 and tr["exit_ts"].notna().all()


def t_a4_determinism(tmp_path=None):
    import tempfile

    tr = normalize_trades(_single_df())
    rep = attribute(tr, [("ratified", RATIFIED), ("cheap", CostSpec(1, 1))])
    with tempfile.TemporaryDirectory() as d:
        p1 = write_report(rep, os.path.join(d, "a.json"))
        p2 = write_report(rep, os.path.join(d, "b.json"))
        with open(p1) as f1, open(p2) as f2:
            assert f1.read() == f2.read()
    # and the dict itself serializes identically twice
    assert json.dumps(rep, sort_keys=True) == json.dumps(rep, sort_keys=True)


def t_cuts_present():
    tr = normalize_trades(_single_df())
    rep = attribute(tr, [("ratified", RATIFIED)])
    assert set(rep["cuts"]["by_exit_reason"]) == {"tp", "sl"}
    assert set(rep["cuts"]["by_direction"]) == {"long", "short"}
    assert "2026" in rep["cuts"]["by_year"]


def t_alpha_beta():
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    mkt = pd.Series([0.001 * ((i % 7) - 3) for i in range(40)], index=idx)
    strat = 0.0005 + 1.5 * mkt  # known alpha_d = 0.0005, beta = 1.5
    out = alpha_beta(strat, mkt)
    assert abs(out["beta"] - 1.5) < 1e-9, out
    assert abs(out["alpha_annualized"] - 0.0005 * 365) < 1e-6, out
    assert out["r2"] > 0.999


def t_empty_and_minimal():
    tr = normalize_trades(_single_df().iloc[[0]])
    rep = attribute(tr, [("ratified", RATIFIED)])
    assert rep["meta"]["n_trades"] == 1
    assert rep["meta"]["kind"] == "single"


def test_all_checks():
    """pytest entry point — runs every t_* check and fails on the first error."""
    for name, fn in sorted(
        [(k, v) for k, v in list(globals().items()) if k.startswith("t_") and callable(v)]
    ):
        fn()


if __name__ == "__main__":
    for _name, _fn in sorted(
        [(k, v) for k, v in list(globals().items()) if k.startswith("t_") and callable(v)]
    ):
        check(_name, _fn)

    _passed = sum(1 for _, ok, _ in results if ok)
    for _name, _ok, _err in results:
        if not _ok:
            print(f"FAIL {_name}: {_err}")
    print(f"{_passed}/{len(results)} passed")
    if _passed != len(results):
        sys.exit(1)
