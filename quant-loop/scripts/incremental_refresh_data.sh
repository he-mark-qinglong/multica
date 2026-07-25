#!/usr/bin/env bash
# Incremental data refresh orchestrator — W3-T15A.
#
# Fetches incremental klines (1m, 30m) + funding from fapi.binance.com,
# starting from each dataset's last timestamp and going up to "today"
# (UTC), then merges each fetched window into the canonical parquet via
# scripts/merge_incremental_parquet.py.
#
# Safety:
#   * Staging dir is mktemp (discarded after the run).
#   * Backup dir is mktemp (kept — printed as BACKUP_DIR for rollback).
#   * Per-file merge is atomic (tmp + os.replace in the python script);
#     a per-symbol fetch failure is logged and skipped, not aborting
#     the rest of the run.
#
# Pre-conditions (enforced by W4 hard freeze, see parent issue C9):
#   * W4-T09~T14 all exit 0 (caller must not invoke this before the
#     wave-4 unfreeze signal).
#
# Usage:
#   bash scripts/incremental_refresh_data.sh
#
set -euo pipefail

PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
QL="$(cd "$(dirname "$0")/.." && pwd)"
MERGE="$QL/scripts/merge_incremental_parquet.py"

TODAY="$(date -u +%F)"
STAGE="$(mktemp -d /tmp/ql_refresh.XXXXXX)"
BAK="$(mktemp -d /tmp/ql_backup.XXXXXX)"
trap 'rc=$?; if [ $rc -ne 0 ]; then echo "REFRESH_FAILED rc=$rc backup=$BAK stage=$STAGE (preserved for debug)" >&2; else [ -d "$STAGE" ] && rm -rf "$STAGE"; fi' EXIT

mkdir -p "$STAGE/perp_1m" "$STAGE/perp_30m" "$STAGE/funding"

echo "[refresh] QL=$QL"
echo "[refresh] TODAY=$TODAY stage=$STAGE backup=$BAK"

# 1) Snapshot existing parquet files for rollback.
for src in "$QL"/data/perp_1m/*.parquet \
           "$QL"/data/perp_30m/*.parquet \
           "$QL"/data/funding/*.parquet; do
    [ -f "$src" ] || continue
    mkdir -p "$BAK/$(dirname "${src#$QL/}")"
    cp -p "$src" "$BAK/${src#$QL/}"
done

# 2) Compute last timestamps per dataset (UTC dates) using the same
#    merge script's key conventions. Perp uses open_time (ms epoch);
#    funding uses ts (datetime64[ms, UTC]).
last_iso() {
    # last_iso <glob> <key>
    local glob="$1" key="$2"
    "$PY" - "$glob" "$key" <<'PYEOF'
import glob, sys
import pandas as pd
from datetime import datetime, timezone
g, key = sys.argv[1], sys.argv[2]
files = sorted(glob.glob(g))
if not files:
    print("1970-01-01")
    sys.exit(0)
last = None
for f in files:
    df = pd.read_parquet(f, columns=[key])
    if key == "open_time":
        v = df[key].max()
    else:
        # ts is datetime64[ns, UTC]; pandas Timestamp max() returns Timestamp
        v = df[key].max().to_pydatetime()
    vlast = v
    if last is None or v > last:
        last = v
if last is None:
    print("1970-01-01")
elif isinstance(last, datetime):
    print(last.strftime("%Y-%m-%d"))
else:
    print(datetime.fromtimestamp(int(last) / 1000, tz=timezone.utc).strftime("%Y-%m-%d"))
PYEOF
}

LAST_1M_ISO="$(last_iso "$QL/data/perp_1m/*_1m.parquet" open_time)"
LAST_30M_ISO="$(last_iso "$QL/data/perp_30m/*_30m.parquet" open_time)"
LAST_FUND_ISO="$(last_iso "$QL/data/funding/*.parquet" ts)"

# Always fetch from the day BEFORE the last known bar — catches late
# corrections that Binance may have published within the gap. This is
# bounded to +1 day on top of the actual last, which is well within
# the script's drop_duplicates(keep="last") safety net.
#
# macOS date(1) is BSD and does not support GNU -d; route the day
# arithmetic through python for cross-platform parity.
shift_back_date() {
    "$PY" - "$1" <<'PYEOF'
import sys
from datetime import date, timedelta
d = date.fromisoformat(sys.argv[1])
print((d - timedelta(days=1)).isoformat())
PYEOF
}
START_1M="$(shift_back_date "$LAST_1M_ISO")"
START_30M="$(shift_back_date "$LAST_30M_ISO")"
START_FUND="$(shift_back_date "$LAST_FUND_ISO")"

echo "[refresh] starts  1m=$START_1M  30m=$START_30M  funding=$START_FUND"

# 3) Fetch to staging. The fetch scripts gate exit-0 on a full-history
#    acceptance (`rows >= 100_000`) intended for SMA-34864 baseline
#    runs; an incremental fetch returns far fewer rows, so we
#    intentionally tolerate the non-zero exit code and verify per-file
#    delivery before the merge step instead.
"$PY" "$QL/scripts/fetch_binance_usdm_1m.py" \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT \
    --start "$START_1M" --end "$TODAY" \
    --out-dir "$STAGE/perp_1m" \
    --format parquet || echo "[refresh] WARN: fetch perp_1m exited non-zero (expected for incremental)"

"$PY" "$QL/scripts/fetch_binance_usdm_30m.py" \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT \
    --start "$START_30M" --end "$TODAY" \
    --out-dir "$STAGE/perp_30m" || echo "[refresh] WARN: fetch perp_30m exited non-zero (expected for incremental)"

"$PY" "$QL/scripts/fetch_binance_funding.py" \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT \
    --start "$START_FUND" --end "$TODAY" \
    --out-dir "$STAGE/funding" || echo "[refresh] WARN: fetch funding exited non-zero (expected for incremental)"

# 4) Merge each staged file into its canonical counterpart. A missing
#    incoming parquet is treated as a per-symbol fetch failure —
#    logged + skipped, never falls back to staging-only overwrite.
merge_pair() {
    local existing="$1" incoming="$2" key="$3" label="$4"
    if [ ! -f "$incoming" ]; then
        echo "WARN[$label]: no incoming parquet, skipping $(basename "$existing")" >&2
        return 0
    fi
    "$PY" "$MERGE" \
        --existing "$existing" \
        --incoming "$incoming" \
        --key "$key" \
        --out "$existing"
}

echo "[refresh] merging perp_1m"
for f in "$QL"/data/perp_1m/*_1m.parquet; do
    [ -f "$f" ] || continue
    base="$(basename "$f")"
    merge_pair "$f" "$STAGE/perp_1m/$base" open_time "perp_1m/$base"
done

echo "[refresh] merging perp_30m"
for f in "$QL"/data/perp_30m/*_30m.parquet; do
    [ -f "$f" ] || continue
    base="$(basename "$f")"
    merge_pair "$f" "$STAGE/perp_30m/$base" open_time "perp_30m/$base"
done

echo "[refresh] merging funding"
for f in "$QL"/data/funding/*.parquet; do
    [ -f "$f" ] || continue
    base="$(basename "$f")"
    merge_pair "$f" "$STAGE/funding/$base" ts "funding/$base"
done

echo "REFRESH_OK backup=$BAK"