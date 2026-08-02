"""Research-document linkage for strategy directories (metric A20).

Each strategy directory may carry a ``strategy.manifest.json`` declaring
which research documents back the strategy, which research line it
descends from, and its lifecycle status::

    {
      "strategy": "vpvr_edge_reversion_1d_20260726",
      "status": "active",                      // research | active | deprecated
      "parent_research": "vpvr_reversion_4h_stablecoin_netflow_20260713",
      "research_docs": ["SPEC.md", "docs/decisions/vpvr_edge.md"],
      "notes": "free text"
    }

Public API
----------
- :class:`StrategyManifest` — frozen manifest record.
- :func:`read_manifest` / :func:`write_manifest` / :func:`ensure_manifest`
  — per-directory manifest I/O. ``ensure_manifest`` auto-discovers
  ``*.md`` / ``SPEC*`` files in the directory as ``research_docs``.
- :func:`scan_strategies` — walk a strategies root, one row per strategy
  directory (manifest when present, auto-discovered docs otherwise —
  scanning never writes files).
- :func:`render_markdown` — the strategy ↔ document association table as
  markdown.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

MANIFEST_NAME = "strategy.manifest.json"

VALID_STATUSES = ("research", "active", "deprecated")


@dataclass(frozen=True)
class StrategyManifest:
    """Research linkage of one strategy directory.

    Attributes:
        strategy: strategy directory name.
        status: lifecycle state — ``research`` | ``active`` | ``deprecated``.
        parent_research: name of the strategy/research line this one
            descends from (None for a root line).
        research_docs: doc links (repo-relative paths or URLs).
        notes: free text.
    """
    strategy: str
    status: str = "research"
    parent_research: Optional[str] = None
    research_docs: Tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"status must be one of {VALID_STATUSES}, got {self.status!r}")


# ---------------------------------------------------------------------------
# Per-directory manifest I/O
# ---------------------------------------------------------------------------
def discover_docs(strategy_dir: Path | str) -> Tuple[str, ...]:
    """Auto-discover research docs inside a strategy directory: every
    top-level ``*.md`` file plus anything under a local ``docs/`` dir,
    as paths relative to the strategy directory (sorted)."""
    strategy_dir = Path(strategy_dir)
    docs: List[str] = []
    for path in sorted(strategy_dir.glob("*.md")):
        docs.append(path.name)
    local_docs = strategy_dir / "docs"
    if local_docs.is_dir():
        for path in sorted(local_docs.rglob("*.md")):
            docs.append(str(path.relative_to(strategy_dir)))
    return tuple(docs)


def read_manifest(strategy_dir: Path | str) -> Optional[StrategyManifest]:
    """Read ``strategy.manifest.json``; None when absent. Raises ValueError
    on a malformed manifest."""
    strategy_dir = Path(strategy_dir)
    path = strategy_dir / MANIFEST_NAME
    if not path.is_file():
        return None
    raw = json.loads(path.read_text())
    return StrategyManifest(
        strategy=raw.get("strategy", strategy_dir.name),
        status=raw.get("status", "research"),
        parent_research=raw.get("parent_research"),
        research_docs=tuple(raw.get("research_docs", ())),
        notes=raw.get("notes", ""),
    )


def write_manifest(strategy_dir: Path | str,
                   manifest: StrategyManifest) -> Path:
    """Write ``strategy.manifest.json`` (overwrites). Returns the path."""
    strategy_dir = Path(strategy_dir)
    payload = {
        "strategy": manifest.strategy,
        "status": manifest.status,
        "parent_research": manifest.parent_research,
        "research_docs": list(manifest.research_docs),
        "notes": manifest.notes,
    }
    path = strategy_dir / MANIFEST_NAME
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def ensure_manifest(strategy_dir: Path | str,
                    status: str = "research",
                    parent_research: Optional[str] = None,
                    notes: str = "") -> StrategyManifest:
    """Return the existing manifest, or create one with auto-discovered
    ``research_docs`` and write it. The only write function a scan-adjacent
    workflow should need."""
    strategy_dir = Path(strategy_dir)
    existing = read_manifest(strategy_dir)
    if existing is not None:
        return existing
    manifest = StrategyManifest(
        strategy=strategy_dir.name,
        status=status,
        parent_research=parent_research,
        research_docs=discover_docs(strategy_dir),
        notes=notes,
    )
    write_manifest(strategy_dir, manifest)
    return manifest


# ---------------------------------------------------------------------------
# Project-wide scan
# ---------------------------------------------------------------------------
def _is_strategy_dir(path: Path) -> bool:
    return path.is_dir() and not path.name.startswith(("_", ".")) \
        and (path / "strategy.py").is_file()


def scan_strategies(root: Path | str) -> List[StrategyManifest]:
    """One :class:`StrategyManifest` row per strategy directory under
    ``root`` (non-recursive beyond one level). Directories without a
    manifest get an in-memory default with auto-discovered docs — nothing
    is written. Skips private dirs (``_graveyard``, ``.x``) and
    non-strategy dirs (``reports/``)."""
    root = Path(root)
    rows: List[StrategyManifest] = []
    for child in sorted(root.iterdir()):
        if not _is_strategy_dir(child):
            continue
        manifest = read_manifest(child)
        if manifest is None:
            manifest = StrategyManifest(
                strategy=child.name,
                research_docs=discover_docs(child),
            )
        rows.append(manifest)
    return rows


def render_markdown(rows: List[StrategyManifest]) -> str:
    """Render the strategy ↔ research-document association table."""
    lines = [
        "# Strategy ↔ Research Document Map",
        "",
        f"{len(rows)} strategies.",
        "",
        "| strategy | status | parent_research | research_docs |",
        "|---|---|---|---|",
    ]
    for m in sorted(rows, key=lambda r: r.strategy):
        docs = "<br>".join(m.research_docs) if m.research_docs else "—"
        parent = m.parent_research or "—"
        lines.append(f"| {m.strategy} | {m.status} | {parent} | {docs} |")
    lines.append("")
    return "\n".join(lines)


def scan_markdown(root: Path | str) -> str:
    """Scan a strategies root and return the markdown association table."""
    return render_markdown(scan_strategies(root))


__all__ = [
    "MANIFEST_NAME",
    "VALID_STATUSES",
    "StrategyManifest",
    "discover_docs",
    "read_manifest",
    "write_manifest",
    "ensure_manifest",
    "scan_strategies",
    "render_markdown",
    "scan_markdown",
]
