"""Tests for scripts/new_variant.py.

Run::

    cd /Users/mark/multica/quant-loop
    /Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest scripts/test_new_variant.py -q

Three pytest cases cover the scaffold contract end-to-end:

* the three required files land on disk with the expected config keys;
* the freshly generated ``signals.py`` passes the contract-v2 smoke check
  (signature + synthetic smoke run);
* a second ``build_variant`` call with the same name on the same UTC day
  raises ``FileExistsError`` instead of silently overwriting.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# scripts/ -> new_variant.py is a sibling; the contract checker lives under
# _shared/ at the quant-loop root. Prepend both so the test can run from any
# cwd (the production acceptance command uses /Users/mark/multica/quant-loop).
_SCRIPTS = Path(__file__).resolve().parent
_QUANT_LOOP = _SCRIPTS.parent
for p in (str(_SCRIPTS), str(_QUANT_LOOP)):
    if p not in sys.path:
        sys.path.insert(0, p)

import new_variant as nv  # noqa: E402  (sys.path mutation above)

from _shared.templates.strategy_contract_v2 import check_contract  # noqa: E402


def test_build_variant_writes_three_files_and_config(tmp_path):
    vdir = nv.build_variant(
        name="smoke_x",
        timeframe="1h",
        symbols=["BTCUSDT", "SOLUSDT"],
        strategies_root=tmp_path,
    )
    assert vdir.is_dir()
    assert (vdir / "config.json").is_file()
    assert (vdir / "data_loader.py").is_file()
    assert (vdir / "signals.py").is_file()

    cfg = json.loads((vdir / "config.json").read_text())
    assert cfg["timeframe"] == "1h"
    assert cfg["instruments"] == ["BTCUSDT", "SOLUSDT"]
    assert vdir.name.startswith("smoke_x_")


def test_build_variant_signals_passes_contract_check(tmp_path):
    vdir = nv.build_variant(
        name="smoke_y",
        timeframe="1h",
        symbols=["BTCUSDT"],
        strategies_root=tmp_path,
    )
    spec = importlib.util.spec_from_file_location(
        f"signals_{vdir.name}", vdir / "signals.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    report = check_contract(module)
    assert report["ok"] is True
    assert isinstance(report["n_trades"], int)
    assert report["n_trades"] >= 0


def test_build_variant_raises_on_existing_dir(tmp_path):
    nv.build_variant(
        name="smoke_z",
        timeframe="1h",
        symbols=["BTCUSDT"],
        strategies_root=tmp_path,
    )
    with pytest.raises(FileExistsError):
        nv.build_variant(
            name="smoke_z",
            timeframe="1h",
            symbols=["BTCUSDT"],
            strategies_root=tmp_path,
        )