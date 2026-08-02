"""Pytest configuration for the strategies tree.

Strategy directories are self-contained: their test files do
``sys.path.insert(0, <strategy dir>)`` and then import sibling modules by
bare top-level name (``from strategy import ...``, ``import data_loader``,
...).  Dozens of strategy directories therefore define modules with
identical top-level names.  In a single full-tree pytest process the first
import would win and every other strategy's tests would silently (or
loudly) resolve to the wrong module.

``_IsolatedModule`` fixes this without editing any test: before importing a
test module it drops any of those known strategy-local names from
``sys.modules`` when they were previously imported from a *different*
strategy directory.  The test module's own ``sys.path.insert`` then makes
its local copy import afresh.  Modules imported from the same directory are
kept, and nothing outside ``strategies/`` is ever touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Directories/files excluded from collection:
# - _graveyard/: archived dead strategies; several test files are genuinely
#   bitrotted (they import names their own strategy modules no longer
#   define, or modules that were moved when the directory was archived).
#   Fixing them would mean rewriting dead code, not resolving a collision.
# - impl_vpvr_multi_tf_funding/tests/test_build_signals.py: build_signals.py
#   does ``from iceberg_detector import detect_iceberg_bars`` from an
#   external sibling repo that is not present; the in-repo detector
#   (strategies/loid_iceberg_v4_1m_20260720/iceberg_detector.py) exposes a
#   different API (``detect``), so the import can never resolve here.
collect_ignore = [
    "_graveyard",
    "impl_vpvr_multi_tf_funding/tests/test_build_signals.py",
]

# Top-level module names that strategy directories re-define locally and
# that collide across directories when collected in one pytest process.
_LOCAL_MODULE_NAMES = frozenset(
    {
        "strategy",
        "data_loader",
        "build_signals",
        "combine_signals",
        "portfolio",
        "universe",
        "cointegration",
        "walk_forward",
        "vpvr_levels",
        "vpvr_levels_band",
        "tod_calendar",
        "state_machine",
        "macro_calendar",
        "funding_signal",
        "indicators",
        "signals",
        "backtest",
        "reporting",
        "utils",
    }
)


def _strategy_root(test_path: Path) -> Path:
    """Return the strategy directory that owns ``test_path``."""
    root = test_path.parent
    if root.name == "tests":
        root = root.parent
    return root


class _IsolatedModule(pytest.Module):
    """Module collector that isolates strategy-local top-level imports."""

    def _getobj(self):
        root = str(_strategy_root(self.path)) + "/"
        saved = {}
        for name, mod in list(sys.modules.items()):
            top = name.split(".", 1)[0]
            if top not in _LOCAL_MODULE_NAMES:
                continue
            mod_file = getattr(mod, "__file__", None)
            if mod_file is None:
                continue
            # Stash the cached module unless it belongs to *this* strategy
            # directory.  This also covers shadowing by repo-root packages
            # (e.g. ``backtest``) that sit on sys.path because pytest was
            # started via ``python -m pytest`` from the repo root.
            if not str(mod_file).startswith(root):
                saved[name] = sys.modules.pop(name)
        try:
            return super()._getobj()
        finally:
            # Drop the strategy-local copies this import registered and put
            # the stashed modules back: test modules hold direct references
            # to what they imported, so unwinding sys.modules here keeps
            # later imports elsewhere (e.g. ``from backtest import
            # factor_backtester`` in _shared/execution/cost_model.py)
            # resolving to the right module.
            for name, mod in list(sys.modules.items()):
                top = name.split(".", 1)[0]
                if top not in _LOCAL_MODULE_NAMES:
                    continue
                mod_file = getattr(mod, "__file__", None)
                if mod_file is not None and str(mod_file).startswith(root):
                    del sys.modules[name]
            for name, mod in saved.items():
                if name not in sys.modules:
                    sys.modules[name] = mod


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makemodule(module_path: Path, parent):
    """Collect every test module under strategies/ via _IsolatedModule."""
    return _IsolatedModule.from_parent(parent, path=module_path)
