#!/usr/bin/env python3
"""
Build resampled perp klines (5m/15m) from the shared 1m pool + manifest.

Phase B (infrastructure unification) data-contract task, modelled on
scripts/data_prep_volatility_edge.py (template:
data/manifests/volatility_edge_2026-07-20.yaml). Re-resamples OHLCV from the
shared 1m perp pool into a uniform-schema 5m/15m set under data/perp_5m/ and
data/perp_15m/ — the canonical paths new strategies MUST use (old strategies
keep their own local copies untouched; this script is additive only).

Inputs (read-only)
------------------
data/perp_1m/{BTC,ETH,SOL}USDT_1m.parquet   (shared 1m base, Binance schema, 12 cols)

Outputs
-------
data/perp_5m/{SYM}_5m.parquet
data/perp_15m/{SYM}_15m.parquet
data/manifests/perp_resampled_<YYYY-MM-DD>.yaml

Schema (uniform 10 cols, UTC ms timestamps; close_time/ignore dropped)
----------------------------------------------------------------------
open_time, open, high, low, close, volume,
quote_volume, trades, taker_buy_base, taker_buy_quote

Manifest contract
-----------------
market: usdm_perp, source: perp_1m, per-file sha256 / rows / time range,
last-bar look-ahead identity check, per-day row-count audit, and a timestamp
continuity audit (gaps in open_time per output file).

Acceptance
----------
- sha256 recorded per parquet
- pandas can read every output parquet back
- open_time strictly increasing and continuous (diff == bin width), any gap
  is reported in the manifest continuity section
- last-bar close identity check passes (no look-ahead)
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

# Universe. Matches the 1m pool contents.
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Timeframes to resample (TF → pandas offset rule).
TIMEFRAMES = {
    "5m": "5min",
    "15m": "15min",
}

# Uniform output schema. Identical to data_prep_volatility_edge.py so one
# loader works for every perp kline parquet in the repo.
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
ROWS_PER_UTC_DAY = {"1m": 1440, "5m": 288, "15m": 96}

MARKET = "usdm_perp"
SOURCE = "perp_1m"


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
    open_time = left edge of the bin in ms since epoch (int64).
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
    # Convert bin label to ms since epoch as int64. Normalize to ns first so
    # the conversion is correct regardless of the datetime64 unit pandas
    # picked (pandas>=2 keeps the `unit` passed to to_datetime).
    ts = out["open_time"].dt.tz_convert("UTC").dt.tz_localize(None)
    ts = ts.astype("datetime64[ns]")
    out["open_time"] = (ts.astype("int64") // 1_000_000).astype("int64")
    # Reorder: open_time first, then OUT_COLUMNS minus open_time.
    ordered = ["open_time"] + [c for c in OUT_COLUMNS if c != "open_time"]
    return out[ordered].reset_index(drop=True)


def bin_ms_for_rule(rule: str) -> int:
    """Bin width in ms for a pandas offset rule like '5min'/'15min'."""
    return int(pd.Timedelta(rule).total_seconds() * 1000)


def identity_check(df_1m: pd.DataFrame, df_resampled: pd.DataFrame,
                   tf_label: str, rule: str) -> dict:
    """
    Look-ahead identity check.
    For the LAST resampled bar, verify OHLC equals the aggregation of the 1m
    bars inside the same bin. Proves no look-ahead across the resample boundary.
    """
    last = df_resampled.iloc[-1]
    t_open = int(last["open_time"])
    bin_ms = bin_ms_for_rule(rule)
    t_close = t_open + bin_ms  # exclusive upper bound
    in_bin = df_1m[(df_1m["open_time"] >= t_open) & (df_1m["open_time"] < t_close)]
    if len(in_bin) == 0:
        return {
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
        "passed": ok,
        "last_bin_open_ms": t_open,
        "last_bin_close_ms_excl": t_close,
        "n_1m_in_bin": int(len(in_bin)),
        "checks": {k: {"actual": v[0], "expected": v[1], "diff": v[0] - v[1]} for k, v in checks.items()},
    }


def continuity_audit(df: pd.DataFrame, rule: str) -> dict:
    """
    Timestamp continuity audit: open_time must be strictly increasing with
    diff == bin width. Any deviation is a gap (inherited from the 1m source).
    """
    bin_ms = bin_ms_for_rule(rule)
    if df.empty:
        return {"bin_ms": bin_ms, "strictly_increasing": False, "n_gaps": 0, "gaps": []}
    diffs = df["open_time"].diff().dropna()
    gap_idx = diffs[diffs != bin_ms].index
    gaps = []
    for i in gap_idx:
        gaps.append({
            "prev_open_time_ms": int(df["open_time"].iloc[i - 1]),
            "next_open_time_ms": int(df["open_time"].iloc[i]),
            "diff_ms": int(diffs.loc[i]),
        })
    return {
        "bin_ms": bin_ms,
        "strictly_increasing": bool((diffs > 0).all()),
        "n_gaps": len(gaps),
        "gaps": gaps[:20],  # cap detail; count stays exact
    }


def count_rows_per_day(df: pd.DataFrame) -> dict:
    """Per-UTC-day row counts. Accept: 1440 (1m), 288 (5m), 96 (15m)."""
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

def build_manifest(date_str: str, root: Path = QUANT_LOOP_ROOT,
                   symbols: list[str] | None = None,
                   timeframes: dict[str, str] | None = None,
                   dry_run: bool = False) -> dict:
    symbols = symbols or SYMBOLS
    timeframes = timeframes or TIMEFRAMES
    src_1m_dir = root / "data" / "perp_1m"
    yaml_path = root / "data" / "manifests" / f"perp_resampled_{date_str}.yaml"

    manifest = {
        "family": "perp_resampled",
        "date": date_str,
        "market": MARKET,
        "source": SOURCE,
        "schema": OUT_COLUMNS,
        "tf_rule": dict(timeframes),
        "row_count_expectation": ROWS_PER_UTC_DAY,
        "universe": symbols,
        "source_pool_1m": "data/perp_1m/{SYM}_1m.parquet",
        "base_tf": "1m",
        "base_paths": {},
        "resampled": {},
        "identity_checks": {},
        "row_count_audit": {},
        "continuity": {},
    }

    t0 = time.time()
    for sym in symbols:
        src = src_1m_dir / f"{sym}_1m.parquet"
        if not src.exists():
            raise FileNotFoundError(f"missing 1m source: {src}")
        # sha256 of the 1m base file (record, don't move it).
        src_sha = sha256_of_file(src)
        src_df = normalize_1m(pd.read_parquet(src))
        manifest["base_paths"][sym] = {
            "path": str(src.relative_to(root)),
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

        for tf_label, rule in timeframes.items():
            t_resample = time.time()
            df_resampled = resample_ohlcv(src_df, rule)
            resample_seconds = time.time() - t_resample

            # Identity check vs 1m base.
            ic = identity_check(src_df, df_resampled, tf_label, rule)
            manifest["identity_checks"].setdefault(sym, {})[tf_label] = ic

            # Row-count audit on resampled.
            per_day = count_rows_per_day(df_resampled)
            manifest["row_count_audit"].setdefault(sym, {})[tf_label] = audit_row_counts(per_day, tf_label)

            # Continuity audit on resampled timestamps.
            manifest["continuity"].setdefault(sym, {})[tf_label] = continuity_audit(df_resampled, rule)

            # Write parquet.
            out_path = root / "data" / f"perp_{tf_label}" / f"{sym}_{tf_label}.parquet"
            if not dry_run:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                df_resampled.to_parquet(out_path, index=False)
            sha = sha256_of_file(out_path) if out_path.exists() else None

            manifest["resampled"].setdefault(sym, {})[tf_label] = {
                "path": str(out_path.relative_to(root)),
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
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(_emit_yaml(manifest))
    manifest["yaml_path"] = str(yaml_path.relative_to(root))

    return manifest


def _emit_yaml(m: dict) -> str:
    """Tiny YAML emitter, schema-stable. Avoids pyyaml dependency."""
    lines = []
    lines.append(f"family: {m['family']}")
    lines.append(f"date: {m['date']}")
    lines.append(f"market: {m['market']}")
    lines.append(f"source: {m['source']}")
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
                if k in ("passed", "checks"):
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
    lines.append("continuity:")
    for sym, by_tf in m["continuity"].items():
        lines.append(f"  {sym}:")
        for tf, info in by_tf.items():
            lines.append(f"    {tf}:")
            for k, v in info.items():
                if isinstance(v, list):
                    lines.append(f"      {k}:")
                    for gap in v:
                        lines.append(f"        - {{prev_open_time_ms: {gap['prev_open_time_ms']}, "
                                     f"next_open_time_ms: {gap['next_open_time_ms']}, diff_ms: {gap['diff_ms']}}}")
                else:
                    lines.append(f"      {k}: {str(v).lower() if isinstance(v, bool) else v}")
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

    print(f"[build_perp_resampled_manifest] date={args.date} dry_run={args.dry_run}", file=sys.stderr)
    m = build_manifest(args.date, dry_run=args.dry_run)
    # Stdout: short summary (machine-readable).
    summary = {
        "date": m["date"],
        "yaml_path": m.get("yaml_path"),
        "resampled_paths": {sym: {tf: info["path"] for tf, info in by_tf.items()}
                            for sym, by_tf in m["resampled"].items()},
        "identity_pass": {sym: {tf: ic["passed"] for tf, ic in by_tf.items()}
                          for sym, by_tf in m["identity_checks"].items()},
        "row_counts": {sym: {tf: info["rows"] for tf, info in by_tf.items()}
                       for sym, by_tf in m["resampled"].items()},
        "continuity_gaps": {sym: {tf: info["n_gaps"] for tf, info in by_tf.items()}
                            for sym, by_tf in m["continuity"].items()},
    }
    print(json.dumps(summary, indent=2))
    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(m, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
