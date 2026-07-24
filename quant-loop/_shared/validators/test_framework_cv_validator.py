"""Tests for framework_cv_validator. Run: pytest _shared/validators/test_framework_cv_validator.py"""
import pytest

from _shared.validators.framework_cv_validator import validate_framework_cv


def _ih(sharpe=1.5, ann_return=0.20):
    """A clean in-house flat dict (matches publish_metrics flatten output)."""
    return {"sharpe": sharpe, "ann_return": ann_return}


def _fw(sharpe=1.3, total_return=0.18):
    """A clean framework_cv_freqtrade.json-shaped dict."""
    return {"framework": {"sharpe": sharpe, "total_return": total_return}}


# ---------------------------------------------------------------------------
# pass case
# ---------------------------------------------------------------------------

def test_pass_case_no_divergence():
    # same signs, modest gaps, no wipeout, no 4x → must NOT raise
    validate_framework_cv(_ih(sharpe=1.5, ann_return=0.20),
                          _fw(sharpe=1.3, total_return=0.18),
                          strategy_name="happy")


def test_pass_case_real_funding_aware_profile():
    # mirrors vpvr_funding_aware_v1_20260711: ih sharpe 7.16 / fw 6.64,
    # ih return 0.21 / fw return 1.25 — both positive, no rule trips.
    validate_framework_cv(_ih(sharpe=7.16, ann_return=0.21),
                          _fw(sharpe=6.64, total_return=1.25),
                          strategy_name="funding_aware")


# ---------------------------------------------------------------------------
# Rule 1: sign flip
# ---------------------------------------------------------------------------

def test_sign_flip_on_sharpe_raises():
    # ih +1.5, fw -1.5: opposite signs, gap 3.0 > 0.05
    with pytest.raises(AssertionError, match="sign flip on sharpe"):
        validate_framework_cv(_ih(sharpe=1.5, ann_return=0.20),
                              _fw(sharpe=-1.5, total_return=0.18),
                              strategy_name="flip_sharpe")


def test_sign_flip_on_return_raises():
    # returns opposite signs: ih +0.20, fw -0.20 (gap 0.40 > 0.05)
    with pytest.raises(AssertionError, match="sign flip on ann_return"):
        validate_framework_cv(_ih(sharpe=1.5, ann_return=0.20),
                              _fw(sharpe=1.3, total_return=-0.20),
                              strategy_name="flip_return")


def test_sign_flip_below_gap_floor_does_not_raise():
    # opposite signs but abs gap below floor → noise, do not reject
    # ih sharpe +0.02, fw sharpe -0.02: gap 0.04 < 0.05 floor
    validate_framework_cv(_ih(sharpe=0.02, ann_return=0.01),
                          _fw(sharpe=-0.02, total_return=0.01),
                          strategy_name="noise")


def test_real_xs_pairs_profile_raises_sign_flip():
    # mirrors vpvr_xs_pairs_30m_funding_filter_20260712: ih sharpe +0.46 /
    # fw sharpe -4.86 → sign flip on sharpe (also wipeout, also 4x).
    with pytest.raises(AssertionError, match="sign flip"):
        validate_framework_cv(_ih(sharpe=0.4603, ann_return=50.1287),
                              _fw(sharpe=-4.8649, total_return=-0.9959),
                              strategy_name="xs_pairs")


# ---------------------------------------------------------------------------
# Rule 2: wipeout
# ---------------------------------------------------------------------------

def test_framework_wipeout_raises():
    # framework return -0.60 < -0.50. NB returns are SAME sign (both negative)
    # so the sign-flip rule does not fire — isolates the wipeout rule.
    with pytest.raises(AssertionError, match="framework wipeout"):
        validate_framework_cv(_ih(sharpe=1.5, ann_return=-0.10),
                              _fw(sharpe=1.3, total_return=-0.60),
                              strategy_name="case7")


def test_framework_return_at_wipeout_boundary_does_not_raise():
    # exactly -0.50 is NOT below the strict < threshold; same-sign returns so
    # the sign-flip rule does not fire either.
    validate_framework_cv(_ih(sharpe=1.5, ann_return=-0.10),
                          _fw(sharpe=1.3, total_return=-0.50),
                          strategy_name="case8")


# ---------------------------------------------------------------------------
# Rule 3: 4x divergence
# ---------------------------------------------------------------------------

def test_4x_divergence_raises():
    # ih return +3.0 (> 2.0 = +200%), fw return +0.3 (< 0.5 = +50%); same sign
    with pytest.raises(AssertionError, match="4x return divergence"):
        validate_framework_cv(_ih(sharpe=1.5, ann_return=3.0),
                              _fw(sharpe=1.3, total_return=0.3),
                              strategy_name="4x")


def test_4x_inhouse_huge_but_framework_also_huge_does_not_raise():
    # ih +3.0 but fw +1.5 (>= 0.5) → not a 4x divergence
    validate_framework_cv(_ih(sharpe=1.5, ann_return=3.0),
                          _fw(sharpe=1.3, total_return=1.5),
                          strategy_name="both_huge")


# ---------------------------------------------------------------------------
# Missing fields — must NOT raise (caller maps to UNVALIDATED)
# ---------------------------------------------------------------------------

def test_missing_inhouse_sharpe_does_not_raise():
    ih = {"ann_return": 0.20}  # no sharpe
    validate_framework_cv(ih, _fw(), strategy_name="missing_ih_sharpe")


def test_missing_inhouse_return_does_not_raise():
    ih = {"sharpe": 1.5}  # no ann_return
    validate_framework_cv(ih, _fw(), strategy_name="missing_ih_return")


def test_missing_framework_block_does_not_raise():
    # framework_cv present but no framework/framework_oos/legacy keys
    validate_framework_cv(_ih(), {"engine": "freqtrade"}, strategy_name="no_fw_block")


def test_empty_dicts_do_not_raise():
    validate_framework_cv({}, {}, strategy_name="empty")


def test_non_dict_inputs_do_not_raise():
    validate_framework_cv(None, None, strategy_name="nones")  # type: ignore[arg-type]
    validate_framework_cv([], _fw(), strategy_name="list_ih")  # type: ignore[arg-type]
