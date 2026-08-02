"""Tests for _shared/strategy_kit/doc_links.py (A20)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json
from pathlib import Path

import pytest

from _shared.strategy_kit import doc_links as dl
from _shared.strategy_kit.doc_links import StrategyManifest


def _strategy(root: Path, name: str, with_spec: bool = True,
              manifest: dict | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "strategy.py").write_text("def generate_signals(bars, config):\n"
                                   "    return []\n")
    if with_spec:
        (d / "SPEC.md").write_text(f"# {name} spec\n")
    if manifest is not None:
        (d / "strategy.manifest.json").write_text(json.dumps(manifest))
    return d


# ---------------------------------------------------------------------------
# Manifest dataclass & I/O
# ---------------------------------------------------------------------------
def test_manifest_rejects_bad_status():
    with pytest.raises(ValueError, match="status"):
        StrategyManifest(strategy="x", status="running")


def test_manifest_is_frozen():
    m = StrategyManifest(strategy="x")
    with pytest.raises(Exception):
        m.status = "active"  # noqa: B015 - frozen check


def test_write_then_read_roundtrip(tmp_path):
    d = _strategy(tmp_path, "s1")
    m = StrategyManifest(strategy="s1", status="active",
                         parent_research="s0",
                         research_docs=("SPEC.md", "docs/edge.md"),
                         notes="hello")
    path = dl.write_manifest(d, m)
    assert path.name == "strategy.manifest.json"
    back = dl.read_manifest(d)
    assert back == m


def test_read_missing_manifest_returns_none(tmp_path):
    d = _strategy(tmp_path, "s1")
    assert dl.read_manifest(d) is None


def test_ensure_manifest_autodiscovers_and_persists(tmp_path):
    d = _strategy(tmp_path, "s1")
    (d / "docs").mkdir()
    (d / "docs" / "edge.md").write_text("# edge\n")
    m = dl.ensure_manifest(d, status="active", parent_research="s0")
    assert m.research_docs == ("SPEC.md", "docs/edge.md")
    assert m.status == "active" and m.parent_research == "s0"
    assert (d / "strategy.manifest.json").is_file()
    # second call returns the persisted manifest unchanged
    again = dl.ensure_manifest(d, status="deprecated")
    assert again == m


def test_discover_docs_sorted_and_relative(tmp_path):
    d = _strategy(tmp_path, "s1")
    (d / "AAA.md").write_text("x")
    assert dl.discover_docs(d) == ("AAA.md", "SPEC.md")


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
def test_scan_strategies_mixes_manifest_and_autodiscovery(tmp_path):
    _strategy(tmp_path, "a_with_manifest", manifest={
        "status": "deprecated", "parent_research": "root_line",
        "research_docs": ["SPEC.md"],
    })
    _strategy(tmp_path, "b_autodiscovered")
    (tmp_path / "_graveyard").mkdir()      # skipped: private
    (tmp_path / "reports").mkdir()         # skipped: no strategy.py
    rows = dl.scan_strategies(tmp_path)
    by_name = {r.strategy: r for r in rows}
    assert set(by_name) == {"a_with_manifest", "b_autodiscovered"}
    assert by_name["a_with_manifest"].status == "deprecated"
    assert by_name["a_with_manifest"].parent_research == "root_line"
    assert by_name["b_autodiscovered"].status == "research"
    assert by_name["b_autodiscovered"].research_docs == ("SPEC.md",)
    # scanning never writes manifests
    assert not (tmp_path / "b_autodiscovered"
                / "strategy.manifest.json").exists()


def test_scan_real_project_strategies_root():
    """The real strategies/ tree must scan cleanly and non-trivially."""
    root = Path("/Users/mark/multica/quant-loop/strategies")
    rows = dl.scan_strategies(root)
    assert len(rows) >= 20  # real strategy dirs with a top-level strategy.py
    assert all(r.strategy for r in rows)
    assert sum(1 for r in rows if r.research_docs) >= 1  # SPEC.md files exist


def test_render_markdown_table(tmp_path):
    _strategy(tmp_path, "b_strat")
    _strategy(tmp_path, "a_strat", manifest={
        "status": "active", "parent_research": "b_strat",
        "research_docs": ["SPEC.md", "docs/edge.md"],
    })
    md = dl.scan_markdown(tmp_path)
    assert md.startswith("# Strategy ↔ Research Document Map")
    lines = md.splitlines()
    assert "| strategy | status | parent_research | research_docs |" in lines
    # sorted by strategy name: a_strat row precedes b_strat row
    a_idx = next(i for i, l in enumerate(lines) if l.startswith("| a_strat"))
    b_idx = next(i for i, l in enumerate(lines) if l.startswith("| b_strat"))
    assert a_idx < b_idx
    assert "| a_strat | active | b_strat | SPEC.md<br>docs/edge.md |" in lines
    assert "| b_strat | research | — | SPEC.md |" in lines
    assert "2 strategies." in md


def test_render_empty_scan(tmp_path):
    md = dl.render_markdown([])
    assert "0 strategies." in md
