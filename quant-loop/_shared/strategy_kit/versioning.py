"""Strategy version management (metric A12).

A strategy *version* is an immutable snapshot record of a strategy
directory: its path, a config hash (``config.json``), a code hash (all
``.py`` files), a parent version, and a creation timestamp. Versions are
append-only records in a small JSON store; ``checkout`` moves a logical
``current`` pointer between recorded versions (it never rewrites strategy
files — re-materialising old code is a git job, not this module's).

Public API
----------
- :func:`register_version` — snapshot a strategy directory into the store.
- :func:`list_versions` / :func:`current` / :func:`checkout` — query and
  move the current pointer.
- :func:`lineage` — walk the parent chain from a version to its root.
- :func:`diff_versions` — config diff + code file-list diff between two
  versions.

The store is plain JSON (default ``.strategy_versions.json`` next to the
strategies root) so it diffs and greps cleanly.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyVersion:
    """One immutable strategy-directory snapshot.

    Attributes:
        strategy_name: directory name of the strategy.
        version_id: content-addressed id (first 12 hex chars of the record
            hash).
        path: absolute path of the snapshotted directory.
        config_hash: sha256 of the canonicalised ``config.json`` ("" when
            absent).
        code_hash: combined sha256 over all ``.py`` files.
        code_files: relative path -> sha256 for every hashed code file.
        config: parsed ``config.json`` content ({} when absent) — kept so
            diffs do not depend on files that may have moved.
        parent: ``version_id`` of the parent version, None for a root.
        created_at: ISO-8601 UTC timestamp.
    """
    strategy_name: str
    version_id: str
    path: str
    config_hash: str
    code_hash: str
    code_files: Mapping[str, str] = field(default_factory=dict)
    config: Mapping = field(default_factory=dict)
    parent: Optional[str] = None
    created_at: str = ""


@dataclass(frozen=True)
class VersionDiff:
    """Difference between two versions.

    Attributes:
        config_changed: key -> (value_in_a, value_in_b) for keys present in
            both with different values.
        config_added: keys only in b (with b's value).
        config_removed: keys only in a (with a's value).
        code_added: files only in b.
        code_removed: files only in a.
        code_changed: files in both whose content hash changed.
    """
    config_changed: Mapping[str, Tuple] = field(default_factory=dict)
    config_added: Mapping[str, object] = field(default_factory=dict)
    config_removed: Mapping[str, object] = field(default_factory=dict)
    code_added: Tuple[str, ...] = ()
    code_removed: Tuple[str, ...] = ()
    code_changed: Tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        """True when the two versions are identical."""
        return not (self.config_changed or self.config_added
                    or self.config_removed or self.code_added
                    or self.code_removed or self.code_changed)


class VersioningError(Exception):
    """Unknown version / strategy, or malformed store."""


# ---------------------------------------------------------------------------
# Hashing helpers (pure)
# ---------------------------------------------------------------------------
def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_config(config_path: Path) -> Tuple[str, Mapping]:
    """Canonical sha256 of a config.json + its parsed content.

    Canonicalisation (sorted keys, compact separators) makes the hash
    robust to key-order/whitespace edits. Returns ("", {}) when the file
    does not exist.
    """
    if not config_path.is_file():
        return "", {}
    parsed = json.loads(config_path.read_text())
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return _sha256_text(canonical), parsed


def hash_code(strategy_dir: Path) -> Tuple[str, Dict[str, str]]:
    """Hash every ``*.py`` file under ``strategy_dir`` (non-recursive into
    ``__pycache__`` / results / data). Returns (combined_hash, per-file
    hashes keyed by path relative to the directory)."""
    per_file: Dict[str, str] = {}
    skip_dirs = {"__pycache__", ".git", "results", "data"}
    for path in sorted(strategy_dir.rglob("*.py")):
        rel = path.relative_to(strategy_dir)
        if any(part in skip_dirs for part in rel.parts):
            continue
        per_file[str(rel)] = _sha256_text(path.read_text())
    combined = _sha256_text(
        "\n".join(f"{rel}:{digest}" for rel, digest in sorted(per_file.items())))
    return combined, per_file


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------
def _load_store(store_path: Path) -> Dict:
    if not store_path.is_file():
        return {"versions": [], "current": {}}
    return json.loads(store_path.read_text())


def _save_store(store_path: Path, store: Dict) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, indent=2, sort_keys=True))


def _record_to_version(record: Mapping) -> StrategyVersion:
    return StrategyVersion(
        strategy_name=record["strategy_name"],
        version_id=record["version_id"],
        path=record["path"],
        config_hash=record["config_hash"],
        code_hash=record["code_hash"],
        code_files=dict(record.get("code_files", {})),
        config=record.get("config", {}),
        parent=record.get("parent"),
        created_at=record.get("created_at", ""),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def register_version(strategy_dir: Path | str,
                     store_path: Path | str,
                     parent: Optional[str] = None,
                     created_at: Optional[str] = None) -> StrategyVersion:
    """Snapshot ``strategy_dir`` into the version store.

    Args:
        strategy_dir: directory containing at least a ``strategy.py``
            (a ``config.json`` is optional but recommended).
        store_path: JSON store file (created on first use).
        parent: ``version_id`` this version descends from; when omitted and
            the strategy already has a current version, that current
            version becomes the parent (natural iteration flow).
        created_at: ISO timestamp override (tests); defaults to now (UTC).

    Returns the recorded :class:`StrategyVersion` and moves the strategy's
    current pointer to it.
    """
    strategy_dir = Path(strategy_dir).resolve()
    if not (strategy_dir / "strategy.py").is_file():
        raise VersioningError(
            f"{strategy_dir} is not a strategy directory (no strategy.py)")
    store_path = Path(store_path)
    store = _load_store(store_path)

    config_hash, config = hash_config(strategy_dir / "config.json")
    code_hash, code_files = hash_code(strategy_dir)
    created = created_at or datetime.now(timezone.utc).isoformat()
    name = strategy_dir.name

    if parent is None:
        parent = store["current"].get(name)
    version_id = _sha256_text(
        f"{name}|{config_hash}|{code_hash}|{parent}|{created}")[:12]

    record = {
        "strategy_name": name,
        "version_id": version_id,
        "path": str(strategy_dir),
        "config_hash": config_hash,
        "code_hash": code_hash,
        "code_files": code_files,
        "config": config,
        "parent": parent,
        "created_at": created,
    }
    store["versions"] = [v for v in store["versions"]
                         if v["version_id"] != version_id]
    store["versions"].append(record)
    store["current"][name] = version_id
    _save_store(store_path, store)
    return _record_to_version(record)


def list_versions(store_path: Path | str,
                  strategy_name: Optional[str] = None) -> List[StrategyVersion]:
    """All recorded versions (oldest first), optionally for one strategy."""
    store = _load_store(Path(store_path))
    out = [_record_to_version(r) for r in store["versions"]]
    if strategy_name is not None:
        out = [v for v in out if v.strategy_name == strategy_name]
    return out


def _find(store: Dict, strategy_name: str,
          version_id: str) -> StrategyVersion:
    for record in store["versions"]:
        if (record["strategy_name"] == strategy_name
                and record["version_id"] == version_id):
            return _record_to_version(record)
    raise VersioningError(
        f"unknown version '{version_id}' for strategy '{strategy_name}'")


def current(store_path: Path | str, strategy_name: str) -> StrategyVersion:
    """The strategy's checked-out (most recently registered) version."""
    store = _load_store(Path(store_path))
    vid = store["current"].get(strategy_name)
    if vid is None:
        raise VersioningError(
            f"no current version for strategy '{strategy_name}'")
    return _find(store, strategy_name, vid)


def checkout(store_path: Path | str, strategy_name: str,
             version_id: str) -> StrategyVersion:
    """Move the current pointer to ``version_id`` (logical checkout — no
    files are rewritten). Returns the now-current version."""
    store_path = Path(store_path)
    store = _load_store(store_path)
    version = _find(store, strategy_name, version_id)
    store["current"][strategy_name] = version_id
    _save_store(store_path, store)
    return version


def lineage(store_path: Path | str, strategy_name: str,
            version_id: Optional[str] = None) -> List[StrategyVersion]:
    """Parent chain from ``version_id`` (default: current) back to the root,
    newest first. Cycle-safe."""
    store = _load_store(Path(store_path))
    vid = version_id or store["current"].get(strategy_name)
    if vid is None:
        raise VersioningError(
            f"no current version for strategy '{strategy_name}'")
    chain: List[StrategyVersion] = []
    seen = set()
    while vid is not None and vid not in seen:
        seen.add(vid)
        version = _find(store, strategy_name, vid)
        chain.append(version)
        vid = version.parent
    return chain


def diff_versions(store_path: Path | str, strategy_name: str,
                  version_a: str, version_b: str) -> VersionDiff:
    """Config + code file-list diff between two recorded versions."""
    store = _load_store(Path(store_path))
    a = _find(store, strategy_name, version_a)
    b = _find(store, strategy_name, version_b)

    keys_a, keys_b = set(a.config), set(b.config)
    changed = {
        k: (a.config[k], b.config[k])
        for k in sorted(keys_a & keys_b) if a.config[k] != b.config[k]
    }
    files_a, files_b = set(a.code_files), set(b.code_files)
    return VersionDiff(
        config_changed=changed,
        config_added={k: b.config[k] for k in sorted(keys_b - keys_a)},
        config_removed={k: a.config[k] for k in sorted(keys_a - keys_b)},
        code_added=tuple(sorted(files_b - files_a)),
        code_removed=tuple(sorted(files_a - files_b)),
        code_changed=tuple(sorted(
            f for f in files_a & files_b
            if a.code_files[f] != b.code_files[f])),
    )


__all__ = [
    "StrategyVersion",
    "VersionDiff",
    "VersioningError",
    "register_version",
    "list_versions",
    "current",
    "checkout",
    "lineage",
    "diff_versions",
    "hash_config",
    "hash_code",
]
