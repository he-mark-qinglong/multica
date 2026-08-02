"""Tests for _shared/strategy_kit/versioning.py (A12)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json
from pathlib import Path

import pytest

from _shared.strategy_kit import versioning as vs
from _shared.strategy_kit.versioning import VersioningError


def _make_strategy(tmp_path: Path, name: str = "demo_strat",
                   fast: int = 12, extra_file: bool = False) -> Path:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "strategy.py").write_text(
        f"FAST = {fast}\n"
        "def generate_signals(bars, config):\n    return []\n"
    )
    (d / "config.json").write_text(json.dumps({"fast": fast, "slow": 48}))
    if extra_file:
        (d / "helper.py").write_text("def helper():\n    return 1\n")
    return d


@pytest.fixture()
def store(tmp_path):
    return tmp_path / ".strategy_versions.json"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def test_register_captures_hashes_and_moves_current(tmp_path, store):
    d = _make_strategy(tmp_path)
    v = vs.register_version(d, store, created_at="2026-08-01T00:00:00+00:00")
    assert v.strategy_name == "demo_strat"
    assert v.parent is None
    assert len(v.config_hash) == 64 and len(v.code_hash) == 64
    assert v.code_files == {"strategy.py": v.code_files["strategy.py"]}
    assert v.config == {"fast": 12, "slow": 48}
    assert vs.current(store, "demo_strat").version_id == v.version_id


def test_register_requires_strategy_py(tmp_path, store):
    d = tmp_path / "not_a_strategy"
    d.mkdir()
    with pytest.raises(VersioningError):
        vs.register_version(d, store)


def test_second_registration_parents_to_current(tmp_path, store):
    d = _make_strategy(tmp_path)
    v1 = vs.register_version(d, store, created_at="2026-08-01T00:00:00+00:00")
    _make_strategy(tmp_path, fast=20)  # mutate config + code
    v2 = vs.register_version(d, store, created_at="2026-08-01T01:00:00+00:00")
    assert v2.parent == v1.version_id
    assert v2.version_id != v1.version_id
    assert vs.current(store, "demo_strat").version_id == v2.version_id


def test_identical_resnapshot_same_content_different_time(tmp_path, store):
    d = _make_strategy(tmp_path)
    v1 = vs.register_version(d, store, created_at="2026-08-01T00:00:00+00:00")
    v2 = vs.register_version(d, store, created_at="2026-08-02T00:00:00+00:00")
    # content identical -> same config/code hashes, but a new version id
    assert v1.config_hash == v2.config_hash
    assert v1.code_hash == v2.code_hash
    assert v1.version_id != v2.version_id


def test_config_hash_robust_to_key_order(tmp_path, store):
    d = _make_strategy(tmp_path)
    (d / "config.json").write_text('{"slow": 48, "fast": 12}')
    h1, _ = vs.hash_config(d / "config.json")
    (d / "config.json").write_text('{\n  "fast": 12,\n  "slow": 48\n}\n')
    h2, _ = vs.hash_config(d / "config.json")
    assert h1 == h2


def test_missing_config_yields_empty_hash(tmp_path, store):
    d = _make_strategy(tmp_path)
    (d / "config.json").unlink()
    v = vs.register_version(d, store, created_at="2026-08-01T00:00:00+00:00")
    assert v.config_hash == "" and v.config == {}


# ---------------------------------------------------------------------------
# current / checkout / lineage
# ---------------------------------------------------------------------------
def _three_versions(tmp_path, store):
    d = _make_strategy(tmp_path)
    v1 = vs.register_version(d, store, created_at="2026-08-01T00:00:00+00:00")
    _make_strategy(tmp_path, fast=20)
    v2 = vs.register_version(d, store, created_at="2026-08-01T01:00:00+00:00")
    _make_strategy(tmp_path, fast=33, extra_file=True)
    v3 = vs.register_version(d, store, created_at="2026-08-01T02:00:00+00:00")
    return v1, v2, v3


def test_checkout_moves_pointer(tmp_path, store):
    v1, v2, v3 = _three_versions(tmp_path, store)
    assert vs.current(store, "demo_strat").version_id == v3.version_id
    vs.checkout(store, "demo_strat", v1.version_id)
    assert vs.current(store, "demo_strat").version_id == v1.version_id
    # checkout does not rewrite strategy files (logical pointer only)
    assert json.loads((tmp_path / "demo_strat" / "config.json")
                      .read_text())["fast"] == 33


def test_checkout_unknown_version_raises(tmp_path, store):
    _three_versions(tmp_path, store)
    with pytest.raises(VersioningError):
        vs.checkout(store, "demo_strat", "deadbeef0000")


def test_lineage_newest_first_to_root(tmp_path, store):
    v1, v2, v3 = _three_versions(tmp_path, store)
    chain = vs.lineage(store, "demo_strat")
    assert [v.version_id for v in chain] == [v3.version_id, v2.version_id,
                                             v1.version_id]
    mid = vs.lineage(store, "demo_strat", version_id=v2.version_id)
    assert [v.version_id for v in mid] == [v2.version_id, v1.version_id]


def test_lineage_cycle_safe(tmp_path, store):
    v1, v2, _ = _three_versions(tmp_path, store)
    # hand-corrupt the store into a parent cycle
    data = json.loads(store.read_text())
    for rec in data["versions"]:
        if rec["version_id"] == v1.version_id:
            rec["parent"] = v2.version_id
    store.write_text(json.dumps(data))
    chain = vs.lineage(store, "demo_strat", version_id=v2.version_id)
    assert [v.version_id for v in chain] == [v2.version_id, v1.version_id]


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------
def test_diff_config_and_code(tmp_path, store):
    v1, v2, v3 = _three_versions(tmp_path, store)
    d = vs.diff_versions(store, "demo_strat", v1.version_id, v3.version_id)
    assert d.config_changed == {"fast": (12, 33)}
    assert d.code_added == ("helper.py",)
    assert d.code_changed == ("strategy.py",)
    assert d.code_removed == () and not d.empty


def test_diff_same_version_is_empty(tmp_path, store):
    v1, _, _ = _three_versions(tmp_path, store)
    d = vs.diff_versions(store, "demo_strat", v1.version_id, v1.version_id)
    assert d.empty


def test_diff_config_added_removed_keys(tmp_path, store):
    d = _make_strategy(tmp_path)
    v1 = vs.register_version(d, store, created_at="2026-08-01T00:00:00+00:00")
    (d / "config.json").write_text(json.dumps({"fast": 12, "new_key": 1}))
    v2 = vs.register_version(d, store, created_at="2026-08-01T01:00:00+00:00")
    diff = vs.diff_versions(store, "demo_strat", v1.version_id, v2.version_id)
    assert diff.config_added == {"new_key": 1}
    assert diff.config_removed == {"slow": 48}


def test_list_versions_filter(tmp_path, store):
    _three_versions(tmp_path, store)
    other = _make_strategy(tmp_path, name="other_strat")
    vs.register_version(other, store, created_at="2026-08-01T03:00:00+00:00")
    assert len(vs.list_versions(store)) == 4
    assert len(vs.list_versions(store, "demo_strat")) == 3


def test_current_unknown_strategy_raises(tmp_path, store):
    with pytest.raises(VersioningError):
        vs.current(store, "ghost")
