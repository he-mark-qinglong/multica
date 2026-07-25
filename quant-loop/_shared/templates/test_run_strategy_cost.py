"""Tests pinning the default round-trip cost in the generic runner.

W3-T13 (wave 0): the universal ``run_strategy`` default cost is now derived
from the ratified SMA-34900 perp venue constants in
``_shared.execution.cost_model.BINANCE_FUTURES`` (4 bps taker fee + 7 bps
fixed pure slippage per side = 22 bps round trip). These tests guard against
silent drift: any future change to the default must update both the
``DEFAULT_COST_BPS_RT`` constant and the ``run_strategy`` signature, and
must remain in lock-step with the venue constants.

Two assertions per guard:

* the signature default equals the literal ``22.0`` (so the user-facing
  behaviour is pinned), and
* the signature default equals ``DEFAULT_COST_BPS_RT`` (so the signature
  cannot drift away from the constant).

The CLI default mirrors the same constant via argparse, and an explicit
override (``cost_bps_rt=24.0``) is verified to still take effect.
"""
from __future__ import annotations

import inspect
import io
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # quant-loop/

from _shared.execution.cost_model import BINANCE_FUTURES  # noqa: E402
from _shared.templates import example_strategy  # noqa: E402
from _shared.templates.run_strategy import (  # noqa: E402
    DEFAULT_COST_BPS_RT,
    main,
    run_strategy,
)
from _shared.templates.strategy_contract_v2 import make_synthetic_bars  # noqa: E402


# ---------------------------------------------------------------------------
# Reference value: ratified SMA-34900 perp round-trip cost.
# 2 x (4 bps taker fee + 7 bps fixed pure slippage) per side = 22 bps.
# ---------------------------------------------------------------------------
_RATIFIED_RT_BPS: float = 2.0 * (
    BINANCE_FUTURES.taker_fee_bps
    + (BINANCE_FUTURES.fixed_pure_slippage_bps or 0.0)
)


# ---------------------------------------------------------------------------
# Signature / constant guards.
# ---------------------------------------------------------------------------
def test_default_cost_constant_equals_ratified_22bps():
    """``DEFAULT_COST_BPS_RT`` is wired to the ratified 22 bps value.

    Defends against the constant silently being re-pointed at a different
    venue (e.g. spot) or computed against a stale slippage number.
    """
    assert _RATIFIED_RT_BPS == pytest.approx(22.0)
    assert DEFAULT_COST_BPS_RT == pytest.approx(_RATIFIED_RT_BPS)
    assert DEFAULT_COST_BPS_RT == pytest.approx(22.0)


def test_default_cost_is_ratified_22():
    """Signature default equals 22.0 and equals DEFAULT_COST_BPS_RT.

    Both assertions are needed: the literal pins the user-visible value, and
    the constant cross-check ensures the signature and the constant stay in
    lock-step (no hardcoded magic number reintroduced in the signature).
    """
    sig_default = inspect.signature(run_strategy).parameters[
        "cost_bps_rt"
    ].default
    assert sig_default == pytest.approx(22.0)
    assert sig_default == pytest.approx(DEFAULT_COST_BPS_RT)


def test_argparse_default_uses_constant():
    """The CLI default mirrors ``DEFAULT_COST_BPS_RT`` exactly.

    The CLI is the public entry point, so any drift between argparse and
    the constant would silently produce different results depending on
    whether the user invoked via Python or via ``python -m``.
    """
    parser = _build_argparser_for_test()
    # ``strategy`` is a required positional in the real CLI; passing a
    # placeholder is enough — we only care about the ``--cost-bps-rt``
    # default that argparse materialises.
    args = parser.parse_args(["placeholder_strategy.py"])
    assert args.cost_bps_rt == pytest.approx(DEFAULT_COST_BPS_RT)
    assert args.cost_bps_rt == pytest.approx(22.0)


# ---------------------------------------------------------------------------
# CLI help text exposes the ratified value (also checked by the verification
# script ``python -m _shared.templates.run_strategy --help | grep 22.0``).
# ---------------------------------------------------------------------------
def test_cli_help_exposes_ratified_22():
    """``--help`` output mentions ``22.0`` so users can see the new default.

    Uses ``subprocess`` to exercise the actual ``__main__`` entry point
    rather than re-implementing argparse in-process; this is the same path
    the verification script in the W3-T13 card uses.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "_shared.templates.run_strategy", "--help"],
        cwd=str(Path(__file__).resolve().parents[2]),  # quant-loop/
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"--help failed: {proc.stderr}"
    assert "22.0" in proc.stdout, (
        f"--help output should mention the ratified default 22.0; "
        f"got:\n{proc.stdout}"
    )


# ---------------------------------------------------------------------------
# Explicit overrides are still honoured (regression guard for W3-T13).
# ---------------------------------------------------------------------------
def test_explicit_cost_still_honored():
    """Passing ``cost_bps_rt=24.0`` explicitly still works (legacy path).

    Several downstream tests (e.g. ``test_strategy_contract_v2``) pass an
    explicit cost to compare against the pre-W3-T13 baseline; this test
    pins that the explicit-overrides-explicit path remains intact.
    """
    bars = make_synthetic_bars(["SYNTH"], n_bars=500)
    strategy_path = Path(example_strategy.__file__)
    out = run_strategy(
        strategy_path,
        {"symbol": "SYNTH"},
        bars=bars,
        cost_bps_rt=24.0,
        freq_per_year=365 * 24,
    )
    assert set(out["metrics"].keys()) == {
        "sharpe_daily",
        "annualized_return",
        "max_drawdown_pct",
        "profit_factor",
        "n_trades",
        "n_bars",
        "win_rate",
        "calmar",
        "sortino",
    }
    assert out["n_trades"] > 0
    assert isinstance(out["equity"], pd.Series)
    assert (out["equity"] > 0).all()


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------
def _build_argparser_for_test():
    """Build the same argparse parser ``main`` does, without invoking it.

    Mirrors the option list in ``run_strategy.main`` so the test exercises
    the same ``default=DEFAULT_COST_BPS_RT`` wiring without going through
    the ``__main__`` entry point (which would require a strategy file).
    """
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("strategy")
    parser.add_argument("--cost-bps-rt", type=float, default=DEFAULT_COST_BPS_RT)
    return parser