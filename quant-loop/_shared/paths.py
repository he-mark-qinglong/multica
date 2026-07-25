"""Path resolution for quant-loop. Single source of truth.

All code that needs repo/data paths MUST import from here instead of
hardcoding absolute paths. Resolution order:
  1. $QUANT_LOOP_ROOT env var (points at the quant-loop/ directory)
  2. Derived from __file__ (this file lives in quant-loop/_shared/)
"""
import os
from pathlib import Path

ENV_VAR = "QUANT_LOOP_ROOT"


def quant_loop_root() -> Path:
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def data_root() -> Path:
    return quant_loop_root() / "data"


def live_data_root() -> Path:
    return quant_loop_root() / "live_data"
