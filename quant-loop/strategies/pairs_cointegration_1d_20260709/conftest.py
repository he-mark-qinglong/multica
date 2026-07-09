"""Pytest rootdir hook for `pairs_cointegration_1d_20260709`.

Lets the test suite run from the quant-loop repo root, e.g.::

    cd ~/multica/quant-loop
    python3 -m pytest strategies/pairs_cointegration_1d_20260709/tests/ -v

Without this hook the `tests/test_cointegration.py` does `from cointegration
import ...` and fails to import because `cointegration.py` is a sibling of
the tests dir, not on pytest's default sys.path.

This is a 6-line boilerplate: insert the strategy directory at the head of
sys.path before collection starts. We use `try/except` so running pytest
from inside the strategy dir (where the local import already works) is a
no-op.
"""
import sys
from pathlib import Path

_STRATEGY_DIR = Path(__file__).resolve().parent
if str(_STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(_STRATEGY_DIR))