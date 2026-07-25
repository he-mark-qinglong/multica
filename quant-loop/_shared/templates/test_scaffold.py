"""Tests for the strategy scaffold generator (W3-T10, wave 2).

Validates:

* ``scaffold()`` produces the expected six entries (SPEC.md, config.json,
  strategy.py, tests/test_contract.py, results/.gitkeep, README.md).
* Re-running into a non-empty target directory refuses (SystemExit(2)).
* Invalid name regex is enforced.
* SPEC.md carries the four mandatory ``## `` section headings the G1-G7
  gate workflow greps for.
* End-to-end: the generated ``tests/test_contract.py`` passes under
  ``pytest`` when invoked with ``cwd=quant-loop/`` (the contract
  sub-test takes ~0.3s on synthetic bars).
* The generated ``strategy.py`` carries no inline cost-literal drift
  (matches ``_shared.execution.check_inline_costs`` regex set).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Match the rest of the templates test convention: this file lives in
# ``_shared/templates/`` so parents[2] = quant-loop/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # quant-loop/

from _shared.templates.scaffold import (  # noqa: E402
    _NAME_RE,
    scaffold,
)


HERE = Path(__file__).resolve().parent
QUANT_LOOP = HERE.parents[1]  # quant-loop/


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

EXPECTED_ENTRIES = (
    "SPEC.md",
    "config.json",
    "strategy.py",
    "tests",
    "results",
    "README.md",
)

SPEC_MANDATORY_HEADINGS = (
    "## Hypothesis",
    "## Falsification",
    "## Data requirements & cost-cap precheck",
    "## Cost constraints",
)


# ---------------------------------------------------------------------------
# scaffold() core contract.
# ---------------------------------------------------------------------------

def test_scaffold_creates_expected_files(tmp_path):
    target = scaffold(
        name="dummy_x",
        symbols=["BTCUSDT"],
        tf="15m",
        out_root=tmp_path,
    )
    assert target == (tmp_path / "dummy_x").resolve()
    assert target.is_dir()
    for entry in EXPECTED_ENTRIES:
        assert (target / entry).exists(), f"missing {entry}"
    assert (target / "tests" / "test_contract.py").is_file()
    assert (target / "results" / ".gitkeep").is_file()
    cfg = json.loads((target / "config.json").read_text(encoding="utf-8"))
    assert cfg == {
        "symbols": ["BTCUSDT"],
        "primary_symbol": "BTCUSDT",
        "timeframe": "15m",
        "size_fraction": 0.95,
    }


def test_scaffold_refuses_nonempty_dir(tmp_path):
    scaffold("dummy_y", ["BTCUSDT"], "15m", out_root=tmp_path)
    # second call into the same non-empty directory must exit with code 2
    with pytest.raises(SystemExit) as ei:
        scaffold("dummy_y", ["BTCUSDT"], "15m", out_root=tmp_path)
    assert ei.value.code == 2


def test_scaffold_allows_repeat_into_empty_dir(tmp_path):
    """An empty target directory (e.g. created externally) is fine.

    The contract is "exists AND non-empty → refuse". A pre-existing
    empty dir should not block scaffolding.
    """
    target = tmp_path / "dummy_z"
    target.mkdir()
    out = scaffold("dummy_z", ["BTCUSDT"], "15m", out_root=tmp_path)
    assert (out / "strategy.py").is_file()


def test_scaffold_invalid_name(tmp_path):
    bad = "Bad-Name"
    assert not _NAME_RE.match(bad), "test fixture must actually be rejected"
    with pytest.raises(SystemExit) as ei:
        scaffold(bad, ["BTCUSDT"], "15m", out_root=tmp_path)
    assert ei.value.code == 2


def test_scaffold_invalid_name_uppercase(tmp_path):
    with pytest.raises(SystemExit) as ei:
        scaffold("BadName", ["BTCUSDT"], "15m", out_root=tmp_path)
    assert ei.value.code == 2


def test_scaffold_invalid_name_leading_digit(tmp_path):
    with pytest.raises(SystemExit) as ei:
        scaffold("1abc", ["BTCUSDT"], "15m", out_root=tmp_path)
    assert ei.value.code == 2


def test_scaffold_empty_symbols_rejected(tmp_path):
    with pytest.raises(SystemExit) as ei:
        scaffold("dummy_w", [], "15m", out_root=tmp_path)
    assert ei.value.code == 2


# ---------------------------------------------------------------------------
# SPEC.md content checks (the four mandatory section headings).
# ---------------------------------------------------------------------------

def test_spec_has_mandatory_sections(tmp_path):
    target = scaffold(
        name="dummy_spec",
        symbols=["BTCUSDT", "ETHUSDT"],
        tf="15m",
        out_root=tmp_path,
        today="2026-07-25",
    )
    spec_text = (target / "SPEC.md").read_text(encoding="utf-8")
    for heading in SPEC_MANDATORY_HEADINGS:
        assert heading in spec_text, f"SPEC.md missing mandatory heading {heading!r}"
    assert "2026-07-25" in spec_text, "SPEC.md must record the generation date"
    assert "dummy_spec" in spec_text, "SPEC.md must carry the strategy name"


def test_spec_template_exists():
    """Single source of truth — the template must live next to scaffold.py."""
    template = HERE / "SPEC_TEMPLATE.md"
    assert template.is_file(), f"SPEC_TEMPLATE.md missing at {template}"
    template_text = template.read_text(encoding="utf-8")
    for heading in SPEC_MANDATORY_HEADINGS:
        assert heading in template_text, (
            f"SPEC_TEMPLATE.md missing heading {heading!r}"
        )


# ---------------------------------------------------------------------------
# Generated strategy.py is contract-clean and inline-cost-clean.
# ---------------------------------------------------------------------------

def test_generated_strategy_emits_no_cost_literals(tmp_path):
    """strategy.py must not carry any inline cost-literal drift.

    These literals are exactly what
    ``_shared.execution.check_inline_costs.COST_LITERAL_RE`` flags. The
    scaffold must not produce a strategy that the cost-drift scanner
    immediately rejects on the first read.
    """
    target = scaffold("dummy_c", ["BTCUSDT"], "15m", out_root=tmp_path)
    text = (target / "strategy.py").read_text(encoding="utf-8")
    for forbidden in ("0.0004", "0.0008", "0.0011", "0.0022", "0.0024"):
        assert forbidden not in text, (
            f"generated strategy.py carries forbidden cost literal {forbidden!r}"
        )


def test_generated_strategy_has_contract_signature(tmp_path):
    target = scaffold("dummy_sig", ["BTCUSDT"], "15m", out_root=tmp_path)
    text = (target / "strategy.py").read_text(encoding="utf-8")
    # contract: generate_signals(bars, config) -> list[Trade]
    assert "def generate_signals(" in text
    assert "bars" in text and "config" in text
    assert "DEFAULT_CONFIG" in text
    assert "_shared.run_backtest" in text


# ---------------------------------------------------------------------------
# End-to-end: generated tests/test_contract.py passes under pytest.
# ---------------------------------------------------------------------------

def test_generated_contract_test_passes(tmp_path):
    """Subprocess pytest on the generated ``tests/`` directory.

    cwd is forced to ``quant-loop/`` so pytest prepends the
    quant-loop rootdir onto ``sys.path`` — that's the contract the
    acceptance command in the task card relies on
    (``python3 -m pytest /tmp/scaffold_accept/<name>/tests -q``).
    """
    target = scaffold("dummy_e2e", ["BTCUSDT", "ETHUSDT"], "15m", out_root=tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(target / "tests"), "-q",
         "--no-header", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        cwd=str(QUANT_LOOP),
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"generated contract test failed:\n"
        f"--- STDOUT ---\n{proc.stdout}\n--- STDERR ---\n{proc.stderr}"
    )
    assert "1 passed" in proc.stdout, (
        f"expected exactly 1 passed in generated test run, got:\n{proc.stdout}"
    )


# ---------------------------------------------------------------------------
# Module-level: scaffold.py must be invokable as ``python3 -m``.
# ---------------------------------------------------------------------------

def test_module_invocation_prints_target_path(tmp_path):
    name = "dummy_mod"
    proc = subprocess.run(
        [sys.executable, "-m", "_shared.templates.scaffold", name,
         "--symbols", "BTCUSDT,ETHUSDT", "--tf", "15m",
         "--out-root", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=str(QUANT_LOOP),
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"scaffold module invocation failed:\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}"
    )
    out = (tmp_path / name).resolve()
    assert out.is_dir(), f"scaffold did not create {out}"
    assert str(out) in proc.stdout, (
        f"scaffold should print the target path; got {proc.stdout!r}"
    )