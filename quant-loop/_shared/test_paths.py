"""Tests for ``_shared/paths.py``."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import paths  # noqa: E402


def test_root_derived_from_file(monkeypatch):
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    root = paths.quant_loop_root()
    assert root == Path(paths.__file__).resolve().parents[1]
    assert (root / "_shared").is_dir()


def test_data_and_live_data_roots(monkeypatch):
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    assert paths.data_root() == paths.quant_loop_root() / "data"
    assert paths.live_data_root() == paths.quant_loop_root() / "live_data"


def test_env_var_takes_precedence(monkeypatch, tmp_path):
    fake = str(tmp_path / "ql_fake")
    monkeypatch.setenv(paths.ENV_VAR, fake)
    assert paths.quant_loop_root() == Path(fake)
    assert paths.data_root() == Path(fake) / "data"
    assert paths.live_data_root() == Path(fake) / "live_data"


def test_env_var_unset_falls_back(monkeypatch):
    monkeypatch.setenv(paths.ENV_VAR, "/tmp/ql_fake")
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    assert paths.quant_loop_root() == Path(paths.__file__).resolve().parents[1]
