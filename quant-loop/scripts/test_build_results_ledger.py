"""pytest tests for scripts/build_results_ledger.py verdict semantics.

Verdict splitting (gate-ledger-fix, 2026-07-25): framework consistency and
in-house profitability are independent recorded fields; a strategy is a LIVE
candidate (PASS) only when BOTH hold.

Run: pytest scripts/test_build_results_ledger.py
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import build_results_ledger as brl


# --- Row fixtures ------------------------------------------------------------

def _row(**overrides):
    row = {
        "strategy_key": "synthetic_1h_20260725",
        "status": "ACTIVE",
        "sharpe_inhouse": 1.5,
        "pf_inhouse": 2.0,
        "maxdd_inhouse": -0.10,
        "n_trades": 100,
        "frameworks": {"backtrader": {"sharpe": 1.2, "verdict": "W5_PASS"}},
        "framework_consistent": True,
        "profitable": True,
    }
    row.update(overrides)
    return row


# --- _framework_consistent ----------------------------------------------------

def test_framework_consistent_true_on_pass_verdict():
    assert brl._framework_consistent(_row()) is True


def test_framework_consistent_true_on_within_tolerance():
    row = _row(frameworks={"backtrader": {"sharpe": 1.2, "verdict": "WITHIN_TOLERANCE"}})
    assert brl._framework_consistent(row) is True


def test_framework_consistent_false_without_frameworks():
    assert brl._framework_consistent(_row(frameworks={})) is False


def test_framework_consistent_false_on_kill_verdict():
    row = _row(frameworks={"backtrader": {"sharpe": 0.4, "verdict": "NOT-PROFITABLE"}})
    assert brl._framework_consistent(row) is False


# --- _profitable ---------------------------------------------------------------

def test_profitable_true_when_all_fields_pass():
    assert brl._profitable(_row()) is True


def test_profitable_false_on_missing_field():
    for field in ("sharpe_inhouse", "pf_inhouse", "maxdd_inhouse", "n_trades"):
        assert brl._profitable(_row(**{field: None})) is False, field


def test_profitable_false_below_bar():
    assert brl._profitable(_row(sharpe_inhouse=0.9)) is False
    assert brl._profitable(_row(pf_inhouse=1.5)) is False
    assert brl._profitable(_row(maxdd_inhouse=-0.30)) is False
    assert brl._profitable(_row(n_trades=29)) is False


def test_profitable_accepts_positive_maxdd_magnitude():
    # maxDD may be stored as a positive magnitude; abs() normalizes.
    assert brl._profitable(_row(maxdd_inhouse=0.10)) is True
    assert brl._profitable(_row(maxdd_inhouse=0.30)) is False


# --- _status verdict splitting --------------------------------------------------

def test_status_pass_requires_both_fields():
    row = _row()
    assert brl._status(row) == "PASS"
    assert row["framework_consistent"] and row["profitable"]


def test_status_cv_pass_when_consistent_but_not_profitable():
    # Framework agreement but in-house metrics below bar -> CV_PASS, not PASS.
    # This is the ledger conflation bug: previously any W5 PASS meant PASS.
    row = _row(sharpe_inhouse=0.5, framework_consistent=True, profitable=False)
    assert brl._status(row) == "CV_PASS"


def test_status_cv_pass_when_profitable_fields_missing():
    row = _row(pf_inhouse=None, maxdd_inhouse=None, n_trades=None,
               framework_consistent=True, profitable=False)
    assert brl._status(row) == "CV_PASS"


def test_status_hold_when_profitable_but_no_framework_agreement():
    row = _row(frameworks={}, framework_consistent=False, profitable=True)
    assert brl._status(row) == "HOLD"


def test_status_kill_on_explicit_not_profitable():
    row = _row(frameworks={"backtrader": {"sharpe": 0.4, "verdict": "NOT-PROFITABLE"}},
               framework_consistent=False, profitable=True)
    assert brl._status(row) == "KILL"


def test_status_untested_only_when_no_data_at_all():
    row = _row(sharpe_inhouse=None, pf_inhouse=None, maxdd_inhouse=None,
               n_trades=None, frameworks={},
               framework_consistent=False, profitable=False)
    assert brl._status(row) == "UNTESTED"


def test_status_graveyard_is_kill():
    assert brl._status(_row(status="GRAVEYARD")) == "KILL"


# --- scan records both fields ---------------------------------------------------

def test_scan_records_framework_consistent_and_profitable(tmp_path, monkeypatch):
    monkeypatch.setattr(brl, "REPO", tmp_path)
    strat = tmp_path / "synthetic_1h_20260725"
    (strat / "results").mkdir(parents=True)
    (strat / "results" / "metrics.json").write_text(
        '{"sharpe": 1.5, "profit_factor": 2.0, "max_drawdown_pct": -0.10, "n_trades": 100}'
    )
    (strat / "results" / "framework_cv_backtrader.json").write_text(
        '{"sharpe": 1.2, "w5_verdict": "W5_PASS"}'
    )
    row = brl.scan_strategy_dir(strat)
    assert row["framework_consistent"] is True
    assert row["profitable"] is True
    assert brl._status(row) == "PASS"


def test_scan_records_split_when_framework_ok_but_metrics_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(brl, "REPO", tmp_path)
    strat = tmp_path / "synthetic_1h_20260725"
    (strat / "results").mkdir(parents=True)
    (strat / "results" / "metrics.json").write_text('{"sharpe": 1.5}')
    (strat / "results" / "framework_cv_backtrader.json").write_text(
        '{"sharpe": 1.2, "w5_verdict": "W5_PASS"}'
    )
    row = brl.scan_strategy_dir(strat)
    assert row["framework_consistent"] is True
    assert row["profitable"] is False
    assert brl._status(row) == "CV_PASS"
