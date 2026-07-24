#!/usr/bin/env python3
"""
Data prep for the volatility_edge strategy family.

Gate 2 plumbing task (no edge claim). Re-resamples OHLCV from the shared
1m perp pool into a uniform-schema 15m/1h/4h set with integrity hash,
row-count audit, and a last-bar look-ahead identity check.

Why this exists
---------------
SMA-35067 (xs_pairs_30m_funding_filter) KILL traced partly to data
convention drift across shared-pool parquets (e.g. live_data/*_15m.parquet
has 10 cols while live_data/*_4h.parquet has 12 cols including the always-0
`ignore` column). Framework-agnostic loading — backtrader / freqtrade /
vectorbt — needs ONE schema. This script pins that schema.

Inputs (read-only)
------------------
data/perp_1m/{BTC,ETH,SOL}USDT_1m.parquet   (shared 1m base, Binance schema, 12 cols)

Outputs (local-only, no git push)
---------------------------------
data/manifests/volatility_edge_<YYYY-MM-DD>/{SYM}_{15m,1h,4h}.parquet
data/manifests/volatility_edge_<YYYY-MM-DD>.yaml

Schema (uniform 10 cols, UTC ms timestamps)
-------------------------------------------
open_time, open, high, low, close, volume,
quote_volume, trades, taker_buy_base, taker_buy_quote

Acceptance
----------
- sha256 recorded per parquet
- row counts match resample math per UTC day
- last-bar close identity check passes (no look-ahead)
- manifest YAML written locally
- framework smoke-load (see data_prep_volatility_edge_smoke.py)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# --- Configuration -----------------------------------------------------------

# Source pool (read-only). Anchor at quant-loop/ as canonical root.
QUANT_LOOP_ROOT = Path(__file__).resolve().parents[1]
SRC_1M_DIR = QUANT_LOOP_ROOT / "data" / "perp_1m"

# Universe. Expandable per issue UNCERTAIN note.
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Timeframes to resample (TF → pandas offset rule).
TIMEFRAMES = {
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
}

# Uniform output schema. Matches live_data/...15m.parquet / 1h.parquet 10-col
# format used by freqtrade/backtrader/vectorbt without per-engine reformat.
OUT_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
]

# Expected rows per UTC day per TF (acceptance math).
ROWS_PER_UTC_DAY = {"1m": 1440, "15m": 96, "1h": 24, "4h": 6}


# --- Helpers -----------------------------------------------------------------

def sha256_of_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def normalize_1m(df: pd.DataFrame) -> pd.DataFrame:
    """Project Binance 12-col raw klines to the uniform 10-col schema."""
    # Source columns: open_time, open, high, low, close, volume, close_time,
    #                 quote_volume, trades, taker_buy_base, taker_buy_quote, ignore
    keep = [c for c in OUT_COLUMNS if c in df.columns]
    out = df[keep].copy()
    # Ensure int64 for time, float64 for OHLCV.
    out["open_time"] = out["open_time"].astype("int64")
    for c in ("open", "high", "low", "close", "volume", "quote_volume",
              "trades", "taker_buy_base", "taker_buy_quote"):
        out[c] = out[c].astype("float64")
    return out


def resample_ohlcv(df_1m: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    Resample 1m to `rule`. UTC-aligned via bin label = left edge.
    open_time = first 1m open_time inside the bin (UTC-aligned to bin start).
    """
    g = df_1m.set_index(pd.to_datetime(df_1m["open_time"], unit="ms", utc=True))
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "quote_volume": "sum",
        "trades": "sum",
        "taker_buy_base": "sum",
        "taker_buy_quote": "sum",
    }
    out = g.resample(rule, label="left", closed="left").agg(agg).reset_index()
    out = out.dropna(subset=["open"])  # drop empty leading bin
    # Normalize to nanoseconds-since-epoch, then convert to milliseconds.
    # pandas stores datetime64[ms, UTC] as 1 ns-resolution timestamp with TZ;
    # .view('int64') gives the raw int64 representation in the underlying unit
    # (ms here). We just want ms since epoch as int64.
    out["open_time"] = out["open_time"].dt.tz_convert("UTC").dt.tz_localize(None).astype("int64")
    # Reorder: open_time first, then OUT_COLUMNS minus open_time.
    ordered = ["open_time"] + [c for c in OUT_COLUMNS if c != "open_time"]
    return out[ordered].reset_index(drop=True)


def identity_check(df_1m: pd.DataFrame, df_resampled: pd.DataFrame, tf_label: str) -> dict:
    """
    Look-ahead identity check.
    For the LAST resampled bar, verify:
      - resampled_close == last 1m close in the same bin
      - resampled_open  == first 1m open in the same bin
      - resampled_high  == max 1m high in the same bin
      - resampled_low   == min 1m low in the same bin
    This proves no look-ahead across the resample boundary.
    """
    last = df_resampled.iloc[-1]
    t_open = int(last["open_time"])
    # Bin width in ms.
    bin_ms = {"15m": 15 * 60 * 1000, "1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000}[tf_label]
    t_close = t_open + bin_ms  # exclusive upper bound
    in_bin = df_1m[(df_1m["open_time"] >= t_open) & (df_1m["open_time"] < t_close)]
    if len(in_bin) == 0:
        return {
            "tf": tf_label,
            "passed": False,
            "reason": "empty bin (no 1m bars fall in last resampled window)",
            "last_bin_open_ms": t_open,
            "last_bin_close_ms_excl": t_close,
        }
    expected_open = float(in_bin.iloc[0]["open"])
    expected_high = float(in_bin["high"].max())
    expected_low = float(in_bin["low"].min())
    expected_close = float(in_bin.iloc[-1]["close"])
    checks = {
        "open": (float(last["open"]), expected_open),
        "high": (float(last["high"]), expected_high),
        "low": (float(last["low"]), expected_low),
        "close": (float(last["close"]), expected_close),
    }
    ok = all(abs(actual - expected) < 1e-9 for actual, expected in checks.values())
    return {
        "tf": tf_label,
        "passed": ok,
        "last_bin_open_ms": t_open,
        "last_bin_close_ms_excl": t_close,
        "n_1m_in_bin": int(len(in_bin)),
        "checks": {k: {"actual": v[0], "expected": v[1], "diff": v[0] - v[1]} for k, v in checks.items()},
    }


def count_rows_per_day(df: pd.DataFrame) -> dict:
    """Per-UTC-day row counts. Accept: 1440 (1m), 96 (15m), 24 (1h), 6 (4h)."""
    if df.empty:
        return {}
    times = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    days = times.dt.strftime("%Y-%m-%d")
    counts = days.value_counts().sort_index()
    return {str(k): int(v) for k, v in counts.items()}


def audit_row_counts(counts: dict, tf_label: str) -> dict:
    """Compare per-day counts against expected. Flag partial/incomplete days."""
    expected = ROWS_PER_UTC_DAY[tf_label]
    full_days = sum(1 for v in counts.values() if v == expected)
    partial = {d: v for d, v in counts.items() if v != expected}
    return {
        "tf": tf_label,
        "expected_per_day": expected,
        "full_days": full_days,
        "partial_days": len(partial),
        "partial_breakdown": partial,
        "first_day": next(iter(counts), None),
        "last_day": next(reversed(counts), None),
    }


# --- Main --------------------------------------------------------------------

def build_manifest(date_str: str, dry_run: bool = False) -> dict:
    out_root = QUANT_LOOP_ROOT / "data" / "manifests" / f"volatility_edge_{date_str}"
    yaml_path = QUANT_LOOP_ROOT / "data" / "manifests" / f"volatility_edge_{date_str}.yaml"

    manifest = {
        "family": "volatility_edge",
        "date": date_str,
        "schema": OUT_COLUMNS,
        "tf_rule": {"15m": "15min", "1h": "1h", "4h": "4h"},
        "row_count_expectation": ROWS_PER_UTC_DAY,
        "universe": SYMBOLS,
        "source_pool_1m": "data/perp_1m/{SYM}_1m.parquet",
        "base_tf": "1m",
        "base_paths": {},
        "resampled": {},
        "identity_checks": {},
        "row_count_audit": {},
    }

    if not dry_run:
        out_root.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    for sym in SYMBOLS:
        src = SRC_1M_DIR / f"{sym}_1m.parquet"
        if not src.exists():
            raise FileNotFoundError(f"missing 1m source: {src}")
        # sha256 of the 1m base file (record, don't move it).
        src_sha = sha256_of_file(src)
        src_df_raw = pd.read_parquet(src)
        src_df = normalize_1m(src_df_raw)
        manifest["base_paths"][sym] = {
            "path": str(src.relative_to(QUANT_LOOP_ROOT)),
            "sha256": src_sha,
            "rows": int(len(src_df)),
            "first_open_time_ms": int(src_df["open_time"].iloc[0]),
            "last_open_time_ms": int(src_df["open_time"].iloc[-1]),
            "first_open_time_iso": utc_iso(int(src_df["open_time"].iloc[0])),
            "last_open_time_iso": utc_iso(int(src_df["open_time"].iloc[-1])),
        }

        # Row-count audit on 1m.
        per_day_1m = count_rows_per_day(src_df)
        manifest["row_count_audit"].setdefault(sym, {})["1m"] = audit_row_counts(per_day_1m, "1m")

        for tf_label, rule in TIMEFRAMES.items():
            t_resample = time.time()
            df_resampled = resample_ohlcv(src_df, rule)
            resample_seconds = time.time() - t_resample

            # Identity check vs 1m base.
            ic = identity_check(src_df, df_resampled, tf_label)
            manifest["identity_checks"].setdefault(sym, {})[tf_label] = ic

            # Row-count audit on resampled.
            per_day = count_rows_per_day(df_resampled)
            manifest["row_count_audit"].setdefault(sym, {})[tf_label] = audit_row_counts(per_day, tf_label)

            # Write parquet.
            out_path = out_root / f"{sym}_{tf_label}.parquet"
            if not dry_run:
                df_resampled.to_parquet(out_path, index=False)
            sha = sha256_of_file(out_path) if out_path.exists() else None

            manifest["resampled"].setdefault(sym, {})[tf_label] = {
                "path": str(out_path.relative_to(QUANT_LOOP_ROOT)),
                "sha256": sha,
                "rows": int(len(df_resampled)),
                "first_open_time_ms": int(df_resampled["open_time"].iloc[0]),
                "last_open_time_ms": int(df_resampled["open_time"].iloc[-1]),
                "first_open_time_iso": utc_iso(int(df_resampled["open_time"].iloc[0])),
                "last_open_time_iso": utc_iso(int(df_resampled["open_time"].iloc[-1])),
                "resample_seconds": round(resample_seconds, 4),
            }

    manifest["wall_seconds"] = round(time.time() - t0, 4)

    # Write YAML (manual emitter — no PyYAML dependency required).
    if not dry_run:
        yaml_path.write_text(_emit_yaml(manifest))
    manifest["yaml_path"] = str(yaml_path.relative_to(QUANT_LOOP_ROOT))

    return manifest


def _emit_yaml(m: dict) -> str:
    """Tiny YAML emitter, schema-stable. Avoids pyyaml dependency."""
    lines = []
    lines.append(f"family: {m['family']}")
    lines.append(f"date: {m['date']}")
    lines.append("schema:")
    for c in m["schema"]:
        lines.append(f"  - {c}")
    lines.append("tf_rule:")
    for k, v in m["tf_rule"].items():
        lines.append(f"  {k}: {v}")
    lines.append("row_count_expectation:")
    for k, v in m["row_count_expectation"].items():
        lines.append(f"  {k}: {v}")
    lines.append(f"universe: [{', '.join(m['universe'])}]")
    lines.append(f"source_pool_1m: \"{m['source_pool_1m']}\"")
    lines.append(f"base_tf: {m['base_tf']}")
    lines.append("base_paths:")
    for sym, info in m["base_paths"].items():
        lines.append(f"  {sym}:")
        for k, v in info.items():
            lines.append(f"    {k}: {v}")
    lines.append("resampled:")
    for sym, by_tf in m["resampled"].items():
        lines.append(f"  {sym}:")
        for tf, info in by_tf.items():
            lines.append(f"    {tf}:")
            for k, v in info.items():
                lines.append(f"      {k}: {v}")
    lines.append("identity_checks:")
    for sym, by_tf in m["identity_checks"].items():
        lines.append(f"  {sym}:")
        for tf, ic in by_tf.items():
            lines.append(f"    {tf}:")
            lines.append(f"      passed: {str(ic['passed']).lower()}")
            for k, v in ic.items():
                if k in ("passed", "tf", "checks"):
                    continue
                lines.append(f"      {k}: {v}")
            if "checks" in ic:
                lines.append(f"      checks:")
                for k, v in ic["checks"].items():
                    lines.append(f"        {k}: {{actual: {v['actual']}, expected: {v['expected']}, diff: {v['diff']}}}")
    lines.append("row_count_audit:")
    for sym, by_tf in m["row_count_audit"].items():
        lines.append(f"  {sym}:")
        for tf, info in by_tf.items():
            lines.append(f"    {tf}:")
            for k, v in info.items():
                if isinstance(v, dict):
                    lines.append(f"      {k}:")
                    for kk, vv in v.items():
                        lines.append(f"        \"{kk}\": {vv}")
                else:
                    lines.append(f"      {k}: {v}")
    lines.append(f"wall_seconds: {m['wall_seconds']}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                   help="manifest date suffix (default: today UTC)")
    p.add_argument("--dry-run", action="store_true", help="do not write parquets or YAML")
    p.add_argument("--summary-json", default=None,
                   help="optional path to also dump the manifest dict as JSON")
    args = p.parse_args(argv)

    print(f"[data_prep_volatility_edge] date={args.date} dry_run={args.dry_run}", file=sys.stderr)
    m = build_manifest(args.date, dry_run=args.dry_run)
    # Stdout: short summary (machine-readable line).
    summary = {
        "date": m["date"],
        "yaml_path": m.get("yaml_path"),
        "resampled_paths": {sym: {tf: info["path"] for tf, info in by_tf.items()}
                            for sym, by_tf in m["resampled"].items()},
        "identity_pass": {sym: {tf: ic["passed"] for tf, ic in by_tf.items()}
                          for sym, by_tf in m["identity_checks"].items()},
        "row_counts": {sym: {tf: info["rows"] for tf, info in by_tf.items()}
                       for sym, by_tf in m["resampled"].items()},
    }
    print(json.dumps(summary, indent=2))
    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(m, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())