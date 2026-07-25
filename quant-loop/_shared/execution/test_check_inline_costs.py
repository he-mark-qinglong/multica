"""Tests for check_inline_costs.py — regex coverage, exemption set, seeded
end-to-end via both in-process and subprocess CLI, and real-tree smoke.

Run from ``quant-loop/``::

    python3 -m pytest _shared/execution/test_check_inline_costs.py -q

sys.path is configured so ``from _shared.execution.check_inline_costs import
...`` works whether the test is invoked from the repo root or via
``pytest _shared/execution/``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

HERE = Path(__file__).resolve().parent
# Quant-loop root (one above _shared/execution/).
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _shared.execution.check_inline_costs import (  # noqa: E402
    ASSIGN_RE,
    COST_LITERAL_RE,
    Violation,
    is_exempt,
    scan,
    scan_file,
)


# -----------------------------------------------------------------------------
# COST_LITERAL_RE coverage
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "fee = 0.0004",                  # 4 bps
        "slippage = 0.0008",             # 8 bps
        "plug_in = 0.0011",              # 11 bps
        "alt = 0.0016",                  # 16 bps
        "rt = 0.0022",                   # 22 bps — the canonical perp round trip
        "x = 0.0024",                    # 24 bps
        "setcommission(commission=0.0004)",  # backtrader convention
        "TAKER_FEE = 0.0004",            # noqa: E501
        "0.0022 round_trip",             # at start of line
        "   0.0004   # inline",          # padded
    ],
)
def test_cost_literal_re_matches(text: str):
    assert COST_LITERAL_RE.search(text), f"expected match in: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "notional * 1.0004",        # extra leading digit -> not a bps fraction
        "0.00225",                  # 22.5 bps -> trailing digit must not match
        "0.00229",                  # 22.9 bps -> trailing digit must not match
        "x = 2.0",                  # arbitrary double, not in the 4/8/11/16/22/24 set
        "10_000.0",                 # large denominator, no bps pattern
        "0.0001",                   # 1 bps -> not in canonical set (not a flag target)
        "0.0005",                   # 5 bps -> not a flag target
        "x = 0.001",                # 10 bps -> not a flag target
        "fee=0.00225",              # negative + trailing digit guard
        "value = 0.00040001",       # trailing digits after 0.0004
    ],
)
def test_cost_literal_re_does_not_match(text: str):
    assert not COST_LITERAL_RE.search(text), f"unexpected match in: {text!r}"


# -----------------------------------------------------------------------------
# ASSIGN_RE coverage
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "fee_bps = 5.0",
        "fee_bps = 5",
        "fees_bps = 7.0",
        "fees_bps_per_side = 4.0",
        "fees_bps_per_fill = 4.0",
        "slippage_bps = 7",
        "slippage_bps_per_side = 7.0",
        "cost_bps_rt = 22.0",
        "FEE_RT_BPS = 22.0",
        "FEE_BPS_PER_FILL = 4.0",
        "FEE_BPS_PER_SIDE = 4.0",
        "MY_FEE_BPS = 22",
        "fee_bps = float(5.0)",                          # float() wrapper allowed
        "fee_bps = float(cfg.get('foo', 5.0))",          # inline-call RHS where a literal appears later on the line
        "    fee_bps = 5.0",                              # indented
        "        fee_bps = 5.0",                          # deeper indent
        "fee_bps = 5 + 0",                               # non-greedy regex takes the first digit
    ],
)
def test_assign_re_matches(text: str):
    m = ASSIGN_RE.search(text)
    assert m, f"expected assign match in: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "coffee_bps = 5",                       # word boundary prevents internal `fee_bps` hit
        "coffee_FEE_BPS = 5",                   # underscore-bound; no boundary before FEE_BPS
        "from _shared.execution.cost_model import apply_cost",  # import lines never flagged
        "x = 5.0",                              # no cost-naming LHS
        "fee = 5.0",                            # short form, not fee_bps
        "notional_bps = 5",                     # not_fee_bps pattern
        "totally_unrelated_var = 22.0",         # safe
        "fee_bps_string = 'five bps'",          # string literal, not a numeric match
        "fee_bps = \"five\"",                   # string literal on RHS
        "fee_bps_per_fill = SMA34900_FEE_BPS_PER_SIDE",  # not matched by the alt pattern (no anchor for per_fill)
    ],
)
def test_assign_re_does_not_match(text: str):
    m = ASSIGN_RE.search(text)
    assert not m, f"unexpected assign match in: {text!r}"


# -----------------------------------------------------------------------------
# Self-guard: lines referencing the canonical source are never flagged
# -----------------------------------------------------------------------------


def test_self_guard_skips_cost_model_import_line(tmp_path: Path):
    p = tmp_path / "strategy.py"
    p.write_text(
        "from _shared.execution.cost_model import apply_cost, BINANCE_FUTURES\n"
        "FEE_BPS = 5.0\n"  # bare assignment, no canonical reference -> would flag
    )
    violations = scan_file(p)
    # First line is self-guarded (mentions cost_model); second line is
    # not, so we expect one violation.
    assert len(violations) == 1
    assert violations[0].lineno == 2


def test_self_guard_skips_sma34900_reference_line(tmp_path: Path):
    p = tmp_path / "strategy.py"
    p.write_text(
        "FEE_BPS = SMA34900_FEE_BPS_PER_SIDE\n"  # self-guarded
        "fee_bps = 5.0\n"  # not self-guarded -> flagged
    )
    violations = scan_file(p)
    assert len(violations) == 1
    assert violations[0].lineno == 2


# -----------------------------------------------------------------------------
# is_exempt
# -----------------------------------------------------------------------------


@pytest.fixture
def repo_root():
    return Path(REPO_ROOT).resolve()


@pytest.mark.parametrize(
    "relpath",
    [
        "backtest/factor_backtester.py",
        "_shared/execution/cost_model.py",
        "validation/generic_harness.py",
        "validation/oos_harness.py",
    ],
)
def test_is_exempt_canonical_sources(repo_root: Path, relpath: str):
    assert is_exempt(repo_root / relpath)


def test_is_exempt_strategy_path_is_not_exempt(repo_root: Path):
    p = repo_root / "strategies" / "foo" / "strategy.py"
    assert not is_exempt(p)


@pytest.mark.parametrize(
    "relpath",
    [
        "strategies/_graveyard/x/y.py",
        "strategies/foo/_graveyard/y.py",
        "strategies/some_strategy/tests/test_x.py",
        "strategies/some_strategy/framework_adapter_x.py",
        "strategies/some_strategy/test_runner.py",
        "strategies/some_strategy/strategy_test.py",
    ],
)
def test_is_exempt_whitelist_categories(repo_root: Path, relpath: str):
    assert is_exempt(repo_root / relpath)


def test_is_exempt_tmp_path_is_resolved(tmp_path: Path):
    # The scanner must accept absolute paths and resolve symlinks; feed it
    # a tmp_path with nested dirs and confirm.
    nested = tmp_path / "_graveyard" / "inner.py"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("# nothing\n")
    assert is_exempt(nested)
    clean = tmp_path / "strategies" / "foo" / "strategy.py"
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_text("# nothing\n")
    assert not is_exempt(clean)


# -----------------------------------------------------------------------------
# scan_file: structural assertions
# -----------------------------------------------------------------------------


def test_scan_file_returns_named_tuple(tmp_path: Path):
    p = tmp_path / "strategy.py"
    p.write_text("fee_bps = 5.0\n")
    v = scan_file(p)
    assert len(v) == 1
    assert isinstance(v[0], Violation)
    assert v[0].pattern == "assign"
    assert "fee_bps" in v[0].matched


def test_scan_file_clean_returns_empty(tmp_path: Path):
    p = tmp_path / "strategy.py"
    p.write_text(
        "from _shared.execution.cost_model import apply_cost, BINANCE_FUTURES\n"
        "def run(notional, adv):\n"
        "    return apply_cost(notional, adv, venue=BINANCE_FUTURES)\n"
    )
    assert scan_file(p) == []


def test_scan_file_combines_literal_and_assign(tmp_path: Path):
    p = tmp_path / "strategy.py"
    p.write_text(
        "cost = equity * 0.0022\n"
        "fee_bps = 4.0\n"
    )
    violations = scan_file(p)
    assert len(violations) == 2
    patterns = sorted(v.pattern for v in violations)
    assert patterns == ["assign", "literal"]


# -----------------------------------------------------------------------------
# End-to-end via in-process scan() and subprocess CLI
# -----------------------------------------------------------------------------


def _write_violating_tree(root: Path) -> Path:
    """Write a tiny tree under `root` containing two flagged violations."""
    pkg = root / "strategies" / "seed_x"
    pkg.mkdir(parents=True, exist_ok=True)
    pkg.joinpath("strategy.py").write_text(
        "cost = equity * 0.0022\n"
        "fee_bps = 4.0\n"
    )
    return pkg


def test_scan_seeded_violation_in_process(tmp_path: Path):
    _write_violating_tree(tmp_path)
    violations = scan(root=tmp_path)
    assert len(violations) == 2
    assert all(v.pattern in ("literal", "assign") for v in violations)


def test_scan_seeded_violation_subprocess_enforce(tmp_path: Path):
    _write_violating_tree(tmp_path)
    cmd = [
        sys.executable,
        str(HERE / "check_inline_costs.py"),
        "--root", str(tmp_path),
        "--enforce",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 1, (
        f"--enforce on a tree with 2 violations must exit 1; "
        f"got stdout={res.stdout!r}, stderr={res.stderr!r}"
    )
    # Summary line ends with "N violations in M files".
    last_line = res.stdout.strip().splitlines()[-1]
    assert last_line.startswith("2 violations")
    assert "1 files" in last_line


def test_scan_seeded_violation_subprocess_report_exits_zero(tmp_path: Path):
    _write_violating_tree(tmp_path)
    cmd = [
        sys.executable,
        str(HERE / "check_inline_costs.py"),
        "--root", str(tmp_path),
        "--report",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    last_line = res.stdout.strip().splitlines()[-1]
    assert last_line.startswith("2 violations")


def test_enforce_clean_tree_passes(tmp_path: Path):
    pkg = tmp_path / "strategies" / "seed_x"
    pkg.mkdir(parents=True, exist_ok=True)
    pkg.joinpath("strategy.py").write_text(
        "from _shared.execution.cost_model import apply_cost, BINANCE_FUTURES\n"
        "def run(notional, adv):\n"
        "    return apply_cost(notional, adv, venue=BINANCE_FUTURES)\n"
    )
    cmd = [
        sys.executable,
        str(HERE / "check_inline_costs.py"),
        "--root", str(tmp_path),
        "--enforce",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, (
        f"--enforce on a clean tree must exit 0; "
        f"got stdout={res.stdout!r}, stderr={res.stderr!r}"
    )


def test_enforce_exits_two_on_missing_root(tmp_path: Path):
    cmd = [
        sys.executable,
        str(HERE / "check_inline_costs.py"),
        "--root", str(tmp_path / "does_not_exist"),
        "--report",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 2


def test_default_flag_is_report(tmp_path: Path):
    """No flag -> equivalent to --report -> always exits 0 even with violations."""
    _write_violating_tree(tmp_path)
    cmd = [
        sys.executable,
        str(HERE / "check_inline_costs.py"),
        "--root", str(tmp_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0


# -----------------------------------------------------------------------------
# Real-tree smoke + structural assertion
# -----------------------------------------------------------------------------


def test_real_tree_report_nonzero():
    """Real strategies/ must contain at least one violation (drift snapshot).

    Note: this is a regression baseline. After the parallel migration
    tasks (T7/T8/T14) complete, this count should drop toward zero; at
    that point flip the assertion to == 0 to keep the suite green.
    """
    violations = scan(root=REPO_ROOT / "strategies")
    assert len(violations) > 0, "real strategies/ tree must contain drift"


def test_real_tree_framework_adapter_files_never_flagged():
    """framework_adapter_*.py files must never appear in the report."""
    violations = scan(root=REPO_ROOT / "strategies")
    adapter_violations = [v for v in violations if "framework_adapter_" in v.path.name]
    assert adapter_violations == [], (
        f"framework_adapter_ files leaked into the report: "
        f"{[str(v.path) for v in adapter_violations]}"
    )


def test_real_tree_includes_canonical_drift_files():
    """The historical drift baseline includes `mtf_vpvr_edge_zscore_...`."""
    violations = scan(root=REPO_ROOT / "strategies")
    paths = {str(v.path) for v in violations}
    assert any(
        "mtf_vpvr_edge_zscore_1m_15m_2h_20260718" in p for p in paths
    ), "expected canonical drift in mtf_vpvr_edge_zscore_1m_15m_2h_20260718"


def test_real_tree_test_files_never_flagged():
    """Tests/ files in the strategies tree must be exempt."""
    violations = scan(root=REPO_ROOT / "strategies")
    bad = [
        v for v in violations
        if v.path.name.startswith("test_")
        or v.path.name.endswith("_test.py")
        or "tests" in v.path.parts
        or "_graveyard" in v.path.parts
    ]
    assert bad == [], (
        f"exempt-path file leaked into the report: {[str(v.path) for v in bad]}"
    )


def test_scan_determinism(tmp_path: Path):
    """Two consecutive scans of the same tree must produce identical lists."""
    _write_violating_tree(tmp_path)
    a = scan(root=tmp_path)
    b = scan(root=tmp_path)
    assert [(v.path, v.lineno, v.pattern, v.matched) for v in a] == [
        (v.path, v.lineno, v.pattern, v.matched) for v in b
    ]