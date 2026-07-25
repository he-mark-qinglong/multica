#!/usr/bin/env python3
"""Merge an incremental fetch into an existing parquet file.

Reads an existing parquet and a freshly-fetched incoming parquet,
concatenates them, deduplicates on `--key` keeping the latest row,
sorts by `--key`, asserts strict monotonicity and uniqueness, then
atomically replaces the existing parquet with the merged result via a
temp-file + os.replace.

Intended for the round-2 W3-T15 incremental refresh flow where the
binance fetch scripts overwrite single windows but never append. We
need to keep the historical depth of perp_1m / perp_30m / funding
intact while extending to "now".

CLI:
    --existing PATH    existing canonical parquet
    --incoming PATH    freshly-fetched incremental parquet
    --key     COL      timestamp column to dedupe/sort on
                       (open_time for klines, ts for funding)
    --out     PATH     output parquet (defaults to --existing, in-place)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--existing", required=True, type=Path,
                   help="Existing canonical parquet file.")
    p.add_argument("--incoming", required=True, type=Path,
                   help="Newly-fetched incremental parquet file.")
    p.add_argument("--key", required=True,
                   help="Column to dedupe/sort on (open_time|ts).")
    p.add_argument("--out", type=Path, default=None,
                   help="Output parquet (default: overwrite --existing).")
    args = p.parse_args(argv)
    if args.out is None:
        args.out = args.existing
    return args


def _ts_repr(val, key: str) -> str:
    """Best-effort ISO string for the printed first/last timestamps."""
    try:
        if hasattr(val, "isoformat"):
            return str(val)
        if isinstance(val, (int, float)) and key == "open_time":
            from datetime import datetime, timezone
            return datetime.fromtimestamp(int(val) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        pass
    return str(val)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    existing: Path = args.existing
    incoming: Path = args.incoming
    out: Path = args.out
    key: str = args.key

    if not existing.exists():
        print(f"ERROR: existing parquet missing: {existing}", file=sys.stderr)
        return 2
    if not incoming.exists():
        print(f"ERROR: incoming parquet missing: {incoming}", file=sys.stderr)
        return 2

    df_existing = pd.read_parquet(existing)
    df_incoming = pd.read_parquet(incoming)
    if key not in df_existing.columns:
        print(f"ERROR: key={key!r} not in existing columns: "
              f"{list(df_existing.columns)}", file=sys.stderr)
        return 3
    if key not in df_incoming.columns:
        print(f"ERROR: key={key!r} not in incoming columns: "
              f"{list(df_incoming.columns)}", file=sys.stderr)
        return 3

    old_rows = len(df_existing)
    new_rows = len(df_incoming)
    if new_rows == 0:
        print(f"WARN: incoming has 0 rows ({incoming}); nothing to merge")
        return 0

    # Normalize schemas (column set + dtypes) before concat to avoid surprises.
    cols_existing = set(df_existing.columns)
    cols_incoming = set(df_incoming.columns)
    if cols_existing != cols_incoming:
        only_existing = cols_existing - cols_incoming
        only_incoming = cols_incoming - cols_existing
        if only_existing or only_incoming:
            print(f"WARN: column-set drift existing-only={sorted(only_existing)} "
                  f"incoming-only={sorted(only_incoming)} — intersecting only",
                  file=sys.stderr)
            keep = sorted(cols_existing & cols_incoming)
            df_existing = df_existing[keep]
            df_incoming = df_incoming[keep]

    # Coerce dtypes on the incoming side to match the canonical frame.
    # Funding's fundingRate is sometimes returned by the binance endpoint
    # as an object (string-encoded decimal), while the existing canonical
    # column is float64. Forcing numeric coercion here keeps pd.concat
    # from widening the column to object and breaking pyarrow write.
    for col in df_existing.columns:
        if col in df_incoming.columns:
            target_dtype = df_existing[col].dtype
            src_dtype = df_incoming[col].dtype
            if target_dtype != src_dtype:
                if target_dtype.kind in ("f", "i") and src_dtype.kind in ("f", "i", "O", "U", "S"):
                    try:
                        df_incoming[col] = pd.to_numeric(df_incoming[col], errors="coerce")
                    except Exception as exc:
                        print(f"WARN: numeric coercion failed for col={col} ({exc!r})",
                              file=sys.stderr)
                elif target_dtype.kind == "M" and src_dtype.kind in ("O", "U", "S"):
                    try:
                        df_incoming[col] = pd.to_datetime(df_incoming[col], utc=True,
                                                        errors="coerce")
                    except Exception as exc:
                        print(f"WARN: datetime coercion failed for col={col} ({exc!r})",
                              file=sys.stderr)

    merged = pd.concat([df_existing, df_incoming], ignore_index=True)
    # Incoming was fetched later => keep="last" wins on overlap rows.
    merged = merged.drop_duplicates(subset=[key], keep="last")
    merged = merged.sort_values(key).reset_index(drop=True)

    # Invariants: strictly increasing, no duplicates on the key.
    if not merged[key].is_monotonic_increasing:
        print(f"ERROR: merged key={key} not monotonic increasing", file=sys.stderr)
        return 4
    if not merged[key].is_unique:
        dup_n = int(merged[key].duplicated().sum())
        print(f"ERROR: merged key={key} still has {dup_n} duplicates", file=sys.stderr)
        return 5

    merged_rows = len(merged)
    first_ts = merged[key].iloc[0]
    last_ts = merged[key].iloc[-1]

    # Atomic write: tmp + os.replace so a half-written file never replaces good data.
    tmp_path = out.with_suffix(out.suffix + ".tmp")
    try:
        merged.to_parquet(tmp_path, engine="pyarrow", index=False)
        os.replace(tmp_path, out)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    print(f"MERGE_OK existing={existing.name} incoming={incoming.name} "
          f"key={key} old_rows={old_rows} new_rows={new_rows} "
          f"merged_rows={merged_rows} "
          f"first_ts={_ts_repr(first_ts, key)} last_ts={_ts_repr(last_ts, key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())