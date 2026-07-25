#!/usr/bin/env python3
"""Build a machine- and human-readable inventory of quant-loop's ``data/`` tree.

This is the W3-T3 infrastructure-sprint task. It scans the repo's ``data/``
directory and emits two deterministic, idempotent artefacts:

1. ``data/manifests/inventory.yaml`` — a machine-readable manifest listing
   every dataset (``perp_<tf>``, ``funding``, ``trades``, ``features``,
   ``tradfi_1d``, ``vpvr``), the parquet files it contains per symbol, row
   counts, and the first/last timestamp for each file. Sorted and free of
   any wall-clock fields so ``--check`` is bit-reproducible.
2. ``data/README.md`` — a human-readable coverage matrix plus a static
   "Known gaps" section, regenerated from the same scan.

Inputs (read-only)
------------------
Everything under ``data/`` (parquet files, hive-partitioned directories,
and the existing manifests in ``data/manifests/``). Reading uses
``pyarrow.ParquetFile.metadata`` for row counts and ``pq.read_table``
with **single-column projection** for first/last timestamps — the
``trades/`` hive partitions are never fully materialised.

Outputs (written)
-----------------
- ``data/manifests/inventory.yaml``
- ``data/README.md``

Acceptance
----------
- ``python3 scripts/build_data_inventory.py`` generates both artefacts
  with no errors.
- ``python3 scripts/build_data_inventory.py --check`` exits 0 with
  "inventory up to date" when the on-disk artefacts match a fresh scan.
- ``inventory.yaml`` exposes these anchors (planning-stage ground truth):
    * ``datasets.funding.symbols.BTCUSDT.rows == 5100`` and
      ``first_ts.startswith('2021-11-20')``
    * ``datasets.perp_15m.symbols.BTCUSDT.rows == 240392``
    * ``datasets.perp_1m.symbols.BTCUSDT.rows == 3605862``
    * ``datasets.perp_30m.symbols`` has exactly 7 entries
    * ``datasets.vpvr.status == 'empty'``
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import yaml

# --- quant-loop root resolution ---------------------------------------------
#
# Make the repo root importable so ``_shared.paths`` (T1) can be located
# both when this script is run as ``python3 scripts/build_data_inventory.py``
# from the repo root AND when it is imported by tests that prepend the
# scripts directory directly. This mirrors the pattern documented at
# scripts/build_perp_resampled_manifest.py and the test-file convention
# in scripts/test_build_perp_resampled_manifest.py:1-16.
#
# Note: for a *file* path ``__file__.resolve().parents[1]`` is two levels
# up — which gives ``scripts/foo.py`` → ``quant-loop/``. That matches the
# existing convention in scripts/build_perp_resampled_manifest.py:57.
_SCRIPTS_DIR = Path(__file__).resolve().parents[0]
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _data_root() -> Path:
    """Locate the ``data/`` directory.

    Prefers ``_shared.paths.data_root`` (T1) and falls back to deriving
    the root from this file's location when T1 has not been landed yet.
    Honours ``QUANT_LOOP_ROOT`` in the fallback branch to match the
    convention adopted by ``_shared/data_loader.py``.
    """
    try:
        from _shared.paths import data_root as _paths_data_root  # type: ignore

        return Path(_paths_data_root())
    except Exception:
        env = os.environ.get("QUANT_LOOP_ROOT")
        if env:
            return Path(env) / "data"
        # scripts/build_data_inventory.py -> parents[1] == quant-loop/
        return _REPO_ROOT / "data"


# Static, deterministic labelling — used in both the YAML and the README so
# neither artefact embeds a wall-clock or run-id field.
GENERATOR = "scripts/build_data_inventory.py"
SCHEMA_VERSION = 1

# Timeframe → label used for ``perp_<tf>`` dataset keys.
PERP_TFS: Tuple[str, ...] = ("1m", "5m", "15m", "30m", "2h")

# Symbol extraction patterns. We *do* hardcode the universe for ``perp_<tf>``
# (filename pattern is ``{SYM}_{tf}.parquet`` and timeframes cover different
# coin sets) because the planning agent already pinned the per-tf coin sets
# in §0.1; deriving the universe from filenames is the source of truth used
# at scan time below, but the layout in the YAML stays stable.

# Columns most likely to hold the time axis; tried in order. Falls back to
# "row count only, no time range" if none match.
_TIME_COL_CANDIDATES: Tuple[str, ...] = (
    "open_time",
    "ts",
    "datetime",
    "date",
    "Date",
    "Datetime",
    "time",
    "timestamp",
)


# --- pyarrow helpers --------------------------------------------------------

def _row_count(parquet_path: Path) -> int:
    """Return ``num_rows`` from the file's metadata (no data read)."""
    import pyarrow.parquet as pq

    return int(pq.ParquetFile(str(parquet_path)).metadata.num_rows)


def _ts_min_max(parquet_path: Path, time_col: str) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(first_ts_iso, last_ts_iso)`` for a parquet's ``time_col``.

    Reads only the time column. Returns ``(None, None)`` if the column is
    missing or the file is empty.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        table = pq.read_table(str(parquet_path), columns=[time_col])
    except Exception:
        return (None, None)
    if table.num_rows == 0 or time_col not in table.column_names:
        return (None, None)
    col = table.column(time_col)
    if col.num_chunks == 0:
        return (None, None)
    # ``pa.compute.min/max`` handle datetime/timestamp/int uniformly.
    tmin = pa.compute.min(col).as_py()
    tmax = pa.compute.max(col).as_py()
    return (_to_iso(tmin), _to_iso(tmax))


def _to_iso(value) -> Optional[str]:
    """Normalise a pyarrow scalar / Python datetime to an ISO-8601 UTC string.

    Returns ``None`` for ``None``. Floats/ints are treated as ms epoch.
    Datetimes are converted to UTC and serialised with their offset.
    """
    if value is None:
        return None
    # ``datetime`` from Python's stdlib
    try:
        import datetime as _dt

        if isinstance(value, _dt.datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=_dt.timezone.utc)
            else:
                value = value.astimezone(_dt.timezone.utc)
            # ``isoformat`` keeps the +00:00 suffix; this matches the format
            # used by the existing perp_resampled manifest.
            return value.isoformat()
    except Exception:
        pass
    # Pandas Timestamp — fallback for cases pyarrow returns a wrapped type.
    try:
        import pandas as _pd

        if isinstance(value, _pd.Timestamp):
            ts = value
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            return ts.isoformat()
    except Exception:
        pass
    # Integer / float epoch (ms).
    try:
        import pandas as _pd

        ts = _pd.to_datetime(value, unit="ms", utc=True)
        return ts.isoformat()
    except Exception:
        # Last-resort: stringify whatever pyarrow gave us.
        return str(value)


def _detect_time_column(parquet_path: Path) -> Optional[str]:
    """Pick the first timestamp-like column from the parquet schema."""
    import pyarrow.parquet as pq

    schema = pq.ParquetFile(str(parquet_path)).schema_arrow
    present = set(schema.names)
    for cand in _TIME_COL_CANDIDATES:
        if cand in present:
            return cand
    # Pick any timestamp-typed column as a last resort.
    for field in schema:
        try:
            import pyarrow as pa

            if pa.types.is_timestamp(field.type) or pa.types.is_temporal(field.type):
                return field.name
        except Exception:
            continue
    return None


# --- Dataset scanners -------------------------------------------------------

def _scan_perp_tf(data_dir: Path, tf: str) -> Dict[str, object]:
    """Scan ``data/perp_<tf>/<SYM>_<tf>.parquet`` for ``tf`` in ``PERP_TFS``."""
    sub = data_dir / f"perp_{tf}"
    if not sub.is_dir():
        return {"path_pattern": f"data/perp_{tf}/{{SYM}}_{tf}.parquet",
                "exists": False,
                "symbols": {}}

    pattern = f"{{SYM}}_{tf}.parquet"
    suffix = f"_{tf}.parquet"
    symbols: Dict[str, Dict[str, object]] = {}
    if sub.is_dir():
        for entry in sorted(sub.iterdir()):
            if not entry.is_file() or not entry.name.endswith(suffix):
                continue
            sym = entry.name[: -len(suffix)]
            rel = str(entry.relative_to(data_dir.parent))
            rows = _row_count(entry)
            first_ts, last_ts = _ts_min_max(entry, "open_time")
            symbols[sym] = {
                "path": rel,
                "rows": rows,
                "first_ts": first_ts,
                "last_ts": last_ts,
            }
    return {
        "path_pattern": f"data/perp_{tf}/{pattern}",
        "exists": True,
        "symbols": symbols,
    }


def _scan_funding(data_dir: Path) -> Dict[str, object]:
    """Scan ``data/funding/*.parquet`` (ignore csv/json/fetch_funding.py/README)."""
    sub = data_dir / "funding"
    symbols: Dict[str, Dict[str, object]] = {}
    if sub.is_dir():
        for entry in sorted(sub.iterdir()):
            if not entry.is_file() or entry.suffix != ".parquet":
                continue
            sym = entry.stem
            rel = str(entry.relative_to(data_dir.parent))
            rows = _row_count(entry)
            first_ts, last_ts = _ts_min_max(entry, "ts")
            symbols[sym] = {
                "path": rel,
                "rows": rows,
                "first_ts": first_ts,
                "last_ts": last_ts,
            }
    return {
        "path_pattern": "data/funding/{SYM}.parquet",
        "symbols": symbols,
    }


def _scan_trades(data_dir: Path) -> Dict[str, object]:
    """Scan ``data/trades/{SYM}_aggtrades.parquet`` (hive-partitioned).

    Row counts come from summing each ``ParquetFragment.metadata.num_rows``
    inside every ``year=YYYY/month=M/data.parquet`` shard — **no data is
    ever read**. First/last timestamps are obtained by reading only the
    ``ts`` column from the lexicographically earliest shard (for
    ``first_ts``) and the lexicographically latest shard (for ``last_ts``).
    """
    import pyarrow.dataset as pds

    sub = data_dir / "trades"
    symbols: Dict[str, Dict[str, object]] = {}
    if not sub.is_dir():
        return {"path_pattern": "data/trades/{SYM}_aggtrades.parquet", "symbols": {}}

    for entry in sorted(sub.iterdir()):
        if not entry.is_dir() or not entry.name.endswith("_aggtrades.parquet"):
            continue
        sym = entry.name[: -len("_aggtrades.parquet")]
        rel = entry.relative_to(data_dir.parent)

        # Sum row counts across all fragments (zero bytes read).
        try:
            ds = pds.dataset(str(entry), format="parquet", partitioning="hive")
        except Exception:
            symbols[sym] = {"path": str(rel), "status": "unreadable",
                            "rows": 0, "first_ts": None, "last_ts": None}
            continue

        # Sum row counts from fragment metadata (no IO beyond schema probe).
        try:
            fragments = list(ds.get_fragments())
        except Exception:
            fragments = []
        rows = sum(int(fr.metadata.num_rows) for fr in fragments if fr.metadata is not None)

        # Discover shards by walking the directory; hive layout is
        # ``year=YYYY/month=M/data.parquet`` (with an optional nested
        # ``day=.../`` level). We sort lexicographically on the full path.
        shards = sorted(str(p) for p in entry.rglob("*.parquet"))
        first_ts: Optional[str] = None
        last_ts: Optional[str] = None
        if shards:
            first_ts, _ = _ts_min_max(Path(shards[0]), "ts")
            _, last_ts = _ts_min_max(Path(shards[-1]), "ts")

        symbols[sym] = {
            "path": str(rel),
            "rows": rows,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "shard_count": len(shards),
        }
    return {
        "path_pattern": "data/trades/{SYM}_aggtrades.parquet (hive-partitioned)",
        "symbols": symbols,
    }


def _scan_features(data_dir: Path) -> Dict[str, object]:
    """Scan ``data/features/feature_matrix_{SYM}.parquet``."""
    sub = data_dir / "features"
    symbols: Dict[str, Dict[str, object]] = {}
    if sub.is_dir():
        for entry in sorted(sub.iterdir()):
            if not entry.is_file() or not entry.name.startswith("feature_matrix_") \
                    or not entry.name.endswith(".parquet"):
                continue
            sym = entry.name[len("feature_matrix_"): -len(".parquet")]
            rel = str(entry.relative_to(data_dir.parent))
            time_col = _detect_time_column(entry)
            if time_col:
                first_ts, last_ts = _ts_min_max(entry, time_col)
            else:
                first_ts, last_ts = None, None
            row: Dict[str, object] = {
                "path": rel,
                "rows": _row_count(entry),
                "time_col": time_col,
                "first_ts": first_ts,
                "last_ts": last_ts,
            }
            symbols[sym] = row
    return {
        "path_pattern": "data/features/feature_matrix_{SYM}.parquet",
        "symbols": symbols,
    }


def _scan_tradfi_1d(data_dir: Path) -> Dict[str, object]:
    """Scan ``data/tradfi_1d/*.parquet``.

    Tries common time columns to discover the first/last bar. Marked as a
    separate dataset from ``perp_*`` because the schema is upstream (Yahoo
    etc.) and has different column names.
    """
    sub = data_dir / "tradfi_1d"
    symbols: Dict[str, Dict[str, object]] = {}
    if sub.is_dir():
        for entry in sorted(sub.iterdir()):
            if not entry.is_file() or entry.suffix != ".parquet":
                continue
            sym = entry.stem
            rel = str(entry.relative_to(data_dir.parent))
            time_col = _detect_time_column(entry)
            if time_col:
                first_ts, last_ts = _ts_min_max(entry, time_col)
            else:
                first_ts, last_ts = None, None
            symbols[sym] = {
                "path": rel,
                "rows": _row_count(entry),
                "time_col": time_col,
                "first_ts": first_ts,
                "last_ts": last_ts,
            }
    return {
        "path_pattern": "data/tradfi_1d/{SYM}_1d.parquet",
        "symbols": symbols,
    }


def _scan_vpvr(data_dir: Path) -> Dict[str, object]:
    """``data/vpvr/`` is a known-empty bucket; record it explicitly."""
    sub = data_dir / "vpvr"
    if not sub.is_dir():
        return {"status": "absent"}
    # Empty iff no entries (including hidden files).
    entries = [p for p in sub.iterdir() if p.name not in (".gitkeep", ".DS_Store")]
    if not entries:
        return {"status": "empty"}
    return {"status": "present",
            "entries": sorted(p.name for p in entries)}


# --- Build & write ----------------------------------------------------------

def _build_datasets(data_dir: Path) -> Dict[str, object]:
    """Return the in-memory datasets dict ready for ``yaml.safe_dump``."""
    datasets: Dict[str, object] = {}
    for tf in PERP_TFS:
        datasets[f"perp_{tf}"] = _scan_perp_tf(data_dir, tf)
    datasets["funding"] = _scan_funding(data_dir)
    datasets["trades"] = _scan_trades(data_dir)
    datasets["features"] = _scan_features(data_dir)
    datasets["tradfi_1d"] = _scan_tradfi_1d(data_dir)
    datasets["vpvr"] = _scan_vpvr(data_dir)
    return datasets


def _render_yaml(data_dir: Path) -> str:
    """Build the yaml body deterministically (sorted keys, no wall-clock)."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "datasets": _build_datasets(data_dir),
    }
    # ``sort_keys=True`` + ``default_flow_style=False`` keeps the output
    # bit-stable regardless of dict insertion order.
    return yaml.safe_dump(payload, sort_keys=True, default_flow_style=False,
                          allow_unicode=True)


def _render_readme(data_dir: Path, datasets: Dict[str, object]) -> str:
    """Build the human-readable coverage matrix and Known-gaps section."""
    lines: List[str] = []
    lines.append("# quant-loop `data/` coverage")
    lines.append("")
    lines.append(
        "This page is regenerated by `scripts/build_data_inventory.py` "
        "from the parquet (and hive-partitioned) files under `data/`. "
        "Do not edit by hand — re-run the generator after any data fetch."
    )
    lines.append("")
    lines.append("Generated by: `" + GENERATOR + "`")
    lines.append("")

    # --- Perp klines coverage matrix (datasets in alphabetical order) ---
    lines.append("## Perp klines (USDT-M)")
    lines.append("")
    lines.append("| Timeframe | Path pattern | Symbols | First row count (BTCUSDT) | Time range (BTCUSDT) |")
    lines.append("|---|---|---|---|---|")
    for tf in PERP_TFS:
        key = f"perp_{tf}"
        ds = datasets.get(key, {})
        btc = ds.get("symbols", {}).get("BTCUSDT", {})  # type: ignore[union-attr]
        rows = btc.get("rows", "—") if isinstance(btc, dict) else "—"
        first_ts = btc.get("first_ts", "—") if isinstance(btc, dict) else "—"
        last_ts = btc.get("last_ts", "—") if isinstance(btc, dict) else "—"
        syms = ", ".join(sorted(ds.get("symbols", {}).keys())) if isinstance(ds.get("symbols"), dict) else "—"  # type: ignore[arg-type]
        rng = f"{first_ts} → {last_ts}" if first_ts and last_ts else "—"
        lines.append(f"| `{tf}` | `data/perp_{tf}/{{SYM}}_{tf}.parquet` | {syms} | {rows} | {rng} |")
    lines.append("")

    # --- Funding ---
    fun = datasets.get("funding", {})
    fun_syms = fun.get("symbols", {}) if isinstance(fun, dict) else {}  # type: ignore[union-attr]
    lines.append("## Funding (USDT-M, 8h)")
    lines.append("")
    lines.append("Path: `data/funding/{SYM}.parquet`")
    lines.append("")
    if fun_syms:
        lines.append("| Symbol | Rows | First ts | Last ts |")
        lines.append("|---|---|---|---|")
        for sym in sorted(fun_syms.keys()):
            row = fun_syms[sym]
            lines.append(f"| `{sym}` | {row.get('rows', '—')} | {row.get('first_ts', '—')} | {row.get('last_ts', '—')} |")
        lines.append("")

    # --- Trades ---
    tr = datasets.get("trades", {})
    tr_syms = tr.get("symbols", {}) if isinstance(tr, dict) else {}  # type: ignore[union-attr]
    lines.append("## AggTrades (hive-partitioned)")
    lines.append("")
    lines.append("Path: `data/trades/{SYM}_aggtrades.parquet/{year=YYYY,...}/`")
    lines.append("")
    if tr_syms:
        lines.append("| Symbol | Rows | Shards | First ts | Last ts |")
        lines.append("|---|---|---|---|---|")
        for sym in sorted(tr_syms.keys()):
            row = tr_syms[sym]
            lines.append(
                f"| `{sym}` | {row.get('rows', '—')} | {row.get('shard_count', '—')} "
                f"| {row.get('first_ts', '—')} | {row.get('last_ts', '—')} |"
            )
        lines.append("")

    # --- Features ---
    feat = datasets.get("features", {})
    feat_syms = feat.get("symbols", {}) if isinstance(feat, dict) else {}  # type: ignore[union-attr]
    lines.append("## Feature matrices")
    lines.append("")
    lines.append("Path: `data/features/feature_matrix_{SYM}.parquet`")
    lines.append("")
    if feat_syms:
        lines.append("| Symbol | Rows | Time col | First ts | Last ts |")
        lines.append("|---|---|---|---|---|")
        for sym in sorted(feat_syms.keys()):
            row = feat_syms[sym]
            lines.append(
                f"| `{sym}` | {row.get('rows', '—')} | `{row.get('time_col', '—')}` "
                f"| {row.get('first_ts', '—')} | {row.get('last_ts', '—')} |"
            )
        lines.append("")

    # --- TradFi ---
    tf1 = datasets.get("tradfi_1d", {})
    tf1_syms = tf1.get("symbols", {}) if isinstance(tf1, dict) else {}  # type: ignore[union-attr]
    lines.append("## TradFi daily bars")
    lines.append("")
    lines.append("Path: `data/tradfi_1d/{SYM}_1d.parquet`")
    lines.append("")
    if tf1_syms:
        lines.append("| Symbol | Rows | Time col | First ts | Last ts |")
        lines.append("|---|---|---|---|---|")
        for sym in sorted(tf1_syms.keys()):
            row = tf1_syms[sym]
            lines.append(
                f"| `{sym}` | {row.get('rows', '—')} | `{row.get('time_col', '—')}` "
                f"| {row.get('first_ts', '—')} | {row.get('last_ts', '—')} |"
            )
        lines.append("")

    # --- VPVR ---
    vpvr = datasets.get("vpvr", {})
    lines.append("## VPVR bucket")
    lines.append("")
    if isinstance(vpvr, dict) and vpvr.get("status") == "empty":
        lines.append("- `data/vpvr/` is currently **empty** (no datasets yet).")
    elif isinstance(vpvr, dict) and vpvr.get("status") == "absent":
        lines.append("- `data/vpvr/` does not exist on this checkout.")
    elif isinstance(vpvr, dict) and vpvr.get("status") == "present":
        lines.append("- `data/vpvr/` contains entries: "
                     + ", ".join(f"`{e}`" for e in vpvr.get("entries", [])))
    lines.append("")

    # --- Known gaps (static, copy-paste from the planning card §0.1) ---
    lines.append("## Known gaps")
    lines.append("")
    lines.append(
        "- `funding/` covers **2021-11-20 → 2026-07-17** only; pre-2021 history is missing.\n"
        "- `trades/` (`aggtrades`) covers **2026-01 → 2026-07** only; older shards are absent.\n"
        "- Among klines, **`perp_30m` is the only timeframe covering all 7 symbols** "
        "(BTC/ETH/SOL/BNB/DOGE/AVAX/LINK). Other timeframes cover BTC/ETH/SOL only.\n"
        "- `features/` has matrices for **BTC and ETH only**; the other 5 perp symbols are absent.\n"
        "- `vpvr/` is empty by design at this point in the sprint."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _materialise(data_dir: Path) -> Tuple[str, str, str]:
    """Build both artefacts in memory and return ``(yaml_str, md_str, yaml_path)``.

    ``yaml_path`` is included so callers can report it for diagnostics.
    """
    yaml_str = _render_yaml(data_dir)
    datasets = _build_datasets(data_dir)
    md_str = _render_readme(data_dir, datasets)
    yaml_path = str(data_dir / "manifests" / "inventory.yaml")
    return yaml_str, md_str, yaml_path


def _write_artefacts(data_dir: Path) -> Tuple[Path, Path]:
    """Generate and write both artefacts. Returns their ``Path``s."""
    yaml_str, md_str, _ = _materialise(data_dir)
    inv_path = data_dir / "manifests" / "inventory.yaml"
    readme_path = data_dir / "README.md"
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(yaml_str, encoding="utf-8")
    readme_path.write_text(md_str, encoding="utf-8")
    return inv_path, readme_path


def _check(data_dir: Path) -> int:
    """Idempotency check. Returns process exit code (0 up-to-date, 1 stale)."""
    inv_path = data_dir / "manifests" / "inventory.yaml"
    readme_path = data_dir / "README.md"
    if not inv_path.exists() or not readme_path.exists():
        print(f"stale: missing artefact(s): "
              f"inventory.yaml={'present' if inv_path.exists() else 'absent'}, "
              f"README.md={'present' if readme_path.exists() else 'absent'}",
              file=sys.stderr)
        return 1
    yaml_str, md_str, _ = _materialise(data_dir)
    on_disk_yaml = inv_path.read_text(encoding="utf-8")
    on_disk_md = readme_path.read_text(encoding="utf-8")
    stale: List[str] = []
    if on_disk_yaml != yaml_str:
        stale.append("inventory.yaml")
    if on_disk_md != md_str:
        stale.append("README.md")
    if stale:
        for f in stale:
            print(f"stale: {f} differs from a fresh scan", file=sys.stderr)
        return 1
    print("inventory up to date")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan quant-loop's data/ tree and emit inventory.yaml + README.md.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in memory and compare byte-for-byte with the on-disk "
             "artefacts; exit 0 if up to date, 1 if any file is stale.",
    )
    args = parser.parse_args(argv)

    data_dir = _data_root()
    if args.check:
        return _check(data_dir)

    inv_path, readme_path = _write_artefacts(data_dir)
    print(f"wrote {inv_path}")
    print(f"wrote {readme_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
