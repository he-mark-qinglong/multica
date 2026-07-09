"""Backtest entry point for `pairs_cointegration_1d_20260709`.

B1 scaffold only: validates the config and shows the workflow the B3 owner
needs to wire against the live-data adapter. Once `strategy.run_backtest`
becomes real (B2), this driver simply forwards to it; for now it raises
NotImplementedError with a clear pointer at the B2 hand-off.

This module exists as a *placeholder* so the directory layout matches the
sister strategies (`vpvr_reversion_1d_20260621_dd_sizing/run_backtest.py`)
and B3 can land the real driver without further plumbing.
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config() -> dict:
    """Load the strategy config from disk. B2 reads the same keys."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:  # pragma: no cover
    """B2/B3 owner fills this. Raises so a stale shell run is loud, not silent."""
    cfg = load_config()
    raise NotImplementedError(
        "pairs_cointegration_1d_20260709 backtest is owned by B2/B3. "
        "B1 delivers cointegration.py + strategy/portfolio scaffolds only."
    )


if __name__ == "__main__":  # pragma: no cover
    main()