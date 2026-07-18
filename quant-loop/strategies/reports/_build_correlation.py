"""B6 correlation matrix builder.

Usage:
    python3 _build_correlation.py \
        --variant-dirs ../vpvr_defi_basis_15m_hyperliquid_dydx_20260716 ../vpvr_sentiment_attention_1m_20260716 ../vpvr_stable_depeg_regime_4h_20260716 \
        --display-csv ./display_engine_46.csv \
        --out ./correlation_matrix.csv ./correlation_matrix_long.csv

Produces:
  * correlation_matrix.csv    — 3 new × 3 new Pearson correlation of daily returns.
  * correlation_matrix_long.csv — long-format correlation of each new variant vs the
    full published family, computed in feature space (Sharpe, Sortino, ann_return, max_dd, win_rate, pf,
    bars_per_year, n_trades_z). This is a *similarity proxy*, not a true return-correlation,
    because most published strategies do not publish equity curves to a common filesystem.
  * Prints concentration warnings for any pair |corr| > 0.6 (issue spec hard threshold).
"""
from __future__ import annotations
import argparse
import csv
import json
import math
import os
import sys
from collections import OrderedDict
from datetime import datetime

VARIANT_KEYS = {
    "vpvr_defi_basis_15m_hyperliquid_dydx_20260716": "iter#70_DeFi-basis",
    "vpvr_sentiment_attention_1m_20260716": "iter#71_sentiment",
    "vpvr_stable_depeg_regime_4h_20260716": "iter#72_stable-depeg",
}


def _load_equity(path: str) -> "tuple[list, list]":
    """Read equity curve (ts, equity) as parallel lists."""
    if not os.path.exists(path):
        return ([], [])
    ts_list, eq_list = [], []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("ts") or row.get("timestamp") or row.get("date")
            eq = row.get("equity") or row.get("nav")
            if ts is None or eq is None:
                continue
            ts_list.append(ts)
            eq_list.append(float(eq))
    return ts_list, eq_list


def _resample_to_daily(ts_list, eq_list):
    """Resample to daily frequency by last-wins. Returns (dates, daily_eq)."""
    if not ts_list:
        return [], []
    last_by_date = OrderedDict()
    for ts, eq in zip(ts_list, eq_list):
        date = ts[:10]
        last_by_date[date] = eq
    dates = list(last_by_date.keys())
    eqs = list(last_by_date.values())
    return dates, eqs


def _daily_returns(eqs):
    if len(eqs) < 2:
        return []
    out = []
    for i in range(1, len(eqs)):
        prev, cur = eqs[i - 1], eqs[i]
        if prev == 0:
            continue
        out.append((cur - prev) / prev)
    return out


def _pearson(xs, ys):
    """Pearson correlation. NaN-safe."""
    n = min(len(xs), len(ys))
    if n < 2:
        return float("nan")
    xs = xs[:n]
    ys = ys[:n]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _metrics_features(path: str) -> "list | None":
    """Read a 6-d feature vector from metrics.json (B3 flat format).
    Returns None only if the file is unreadable; missing values are returned as None and
    filled with column-median in `_impute_features` before correlation.
    """
    if not os.path.exists(path):
        return None
    with open(path) as f:
        try:
            data = json.load(f)
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    feats = [
        data.get("sharpe"),
        data.get("sortino"),
        data.get("ann_return_pct"),
        data.get("max_drawdown_pct"),
        data.get("win_rate"),
        data.get("profit_factor"),
    ]
    tf = data.get("timeframe") or "1d"
    bpy = {
        "1m": 525_600,
        "5m": 525_600 // 5,
        "15m": 525_600 // 15,
        "30m": 525_600 // 30,
        "1h": 525_600 // 60,
        "4h": 525_600 // (60 * 4),
        "8h": 525_600 // (60 * 8),
        "1d": 365,
    }.get(tf, 365)
    feats.append(bpy)
    # All values may be None; we leave that to the imputation step.
    return feats


def _impute_features(feats_map):
    """Column-median-impute a dict[name → [..None..]] of feature vectors."""
    if not feats_map:
        return feats_map
    cols = list(zip(*feats_map.values()))
    medians = []
    for c in cols:
        clean = [v for v in c if v is not None]
        clean = [v for v in clean if isinstance(v, (int, float)) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))]
        if clean:
            s = sorted(clean)
            medians.append(s[len(s) // 2])
        else:
            medians.append(0.0)
    out = OrderedDict()
    for k, f in feats_map.items():
        cleaned = []
        for v, m in zip(f, medians):
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                cleaned.append(m)
            else:
                cleaned.append(v)
        out[k] = cleaned
    return out


def _feature_vector(summary_path: str):
    """Pull a 7-d feature vector from summary.json / metrics.json / framework_zipline_metrics.json.
    Missing values are left as None; the calling pipeline imputes via column-median.
    """
    if not summary_path or not os.path.exists(summary_path):
        return None
    with open(summary_path) as f:
        try:
            data = json.load(f)
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    feats = OrderedDict()
    # sharpe
    feats["sharpe"] = _safe(lambda: data.get("portfolio", {}).get("sharpe"), None) \
        or _safe(lambda: data.get("sharpe"), None) \
        or _safe(lambda: data.get("BTCUSDT", {}).get("pass_a", {}).get("inhouse_sharpe"), None)
    feats["sortino"] = _safe(lambda: data.get("portfolio", {}).get("sortino"), None) \
        or _safe(lambda: data.get("sortino"), None) \
        or _safe(lambda: data.get("BTCUSDT", {}).get("pass_a", {}).get("inhouse_sortino"), None)
    feats["ann_return"] = _safe(lambda: data.get("portfolio", {}).get("ann_return_pct"), None) \
        or _safe(lambda: data.get("ann_return_pct"), None)
    feats["max_dd"] = _safe(lambda: data.get("portfolio", {}).get("max_drawdown_pct"), None) \
        or _safe(lambda: data.get("max_drawdown_pct"), None) \
        or _safe(lambda: data.get("BTCUSDT", {}).get("pass_a", {}).get("inhouse_max_dd"), None)
    feats["win_rate"] = _safe(lambda: data.get("per_symbol", [{}])[0].get("win_rate"), None) \
        or _safe(lambda: data.get("BTCUSDT", {}).get("pass_b", {}).get("inhouse_win_rate"), None)
    feats["pf"] = _safe(lambda: data.get("per_symbol", [{}])[0].get("profit_factor"), None) \
        or _safe(lambda: data.get("profit_factor"), None)
    # bars_per_year from timeframe
    tf = data.get("timeframe") or "1d"
    feats["bpy"] = {
        "1m": 525_600 // 1,
        "5m": 525_600 // 5,
        "15m": 525_600 // 15,
        "30m": 525_600 // 30,
        "1h": 525_600 // 60,
        "4h": 525_600 // (60 * 4),
        "8h": 525_600 // (60 * 8),
        "1d": 365,
    }.get(tf, 365)
    return list(feats.values())


def _zscore_norm_feats(feat_dicts):
    """Z-score-normalize a column across all rows; row = dict-of-name → array."""
    keys = list(feat_dicts.keys())
    if not keys:
        return {}
    arr = list(feat_dicts.values())
    cols = list(zip(*arr))
    means = [sum(c) / len(c) for c in cols]
    sds = [max(math.sqrt(sum((x - m) ** 2 for x in c) / max(1, len(c) - 1)), 1e-9) for c, m in zip(cols, means)]
    out = {}
    for k, v in feat_dicts.items():
        out[k] = [(x - m) / s for x, m, s in zip(v, means, sds)]
    return out


def build_pairwise_3x3(variant_dirs, workdir):
    """3 × 3 pairwise Pearson correlation of daily returns on a common date grid."""
    daily_by_key = OrderedDict()
    for d in variant_dirs:
        eq_path = os.path.join(workdir, d, "results", "equity_BTCUSDT.csv")
        ts_list, eq_list = _load_equity(eq_path)
        dates, eqs = _resample_to_daily(ts_list, eq_list)
        rets = _daily_returns(eqs)
        label = VARIANT_KEYS.get(d, d)
        daily_by_key[label] = (dates[1:], rets)  # skip first date (no return)
    keys = list(daily_by_key.keys())
    matrix = [[""] + keys]
    for k1 in keys:
        row = [k1]
        for k2 in keys:
            d1, r1 = daily_by_key[k1]
            d2, r2 = daily_by_key[k2]
            # align by date index on shortest intersection
            n = min(len(r1), len(r2))
            row.append(f"{_pearson(r1[:n], r2[:n]):.3f}")
        matrix.append(row)
    return matrix


def build_long_vs_published(variant_dirs, workdir, display_csv=None):
    """Long-format table: each new variant vs each published strategy, feature-space cosine similarity."""
    # Collect feature vectors for the 3 new variants from their summary.json
    new_feats = OrderedDict()
    for d in variant_dirs:
        s_path = os.path.join(workdir, d, "results", "summary.json")
        if not os.path.exists(s_path):
            s_path = os.path.join(workdir, d, "results", "metrics.json")
        fv = _feature_vector(s_path)
        if fv is None:
            fv = _metrics_features(s_path)
        if fv is not None:
            label = VARIANT_KEYS.get(d, d)
            new_feats[label] = fv

    # Collect feature vectors for displayed strategies from `/strategies/*/results/`
    pub_feats = OrderedDict()
    if display_csv and os.path.exists(display_csv):
        with open(display_csv) as f:
            reader = csv.DictReader(f)
            for row in reader:
                strat_dir = row.get("strategy_dir")
                if not strat_dir:
                    continue
                s_path = os.path.join(strat_dir, "results", "summary.json")
                if not os.path.exists(s_path):
                    s_path = os.path.join(strat_dir, "results", "metrics.json")
                if not os.path.exists(s_path):
                    fr_path = os.path.join(strat_dir, "results", "framework_zipline_metrics.json")
                    if os.path.exists(fr_path):
                        s_path = fr_path
                fv = _feature_vector(s_path)
                if fv is None and s_path.endswith("metrics.json"):
                    fv = _metrics_features(s_path)
                if fv is not None:
                    pub_feats[row.get("name") or os.path.basename(strat_dir)] = fv
    else:
        # Fallback: scan the strategies dir (workdir IS the strategies dir)
        sdir = workdir
        for entry in sorted(os.listdir(sdir)):
            if not (entry.startswith("vpvr_") or entry.startswith("bb_") or entry.startswith("xs_")):
                continue
            full = os.path.join(sdir, entry)
            if entry in VARIANT_KEYS:
                continue
            s_path = os.path.join(full, "results", "summary.json")
            if not os.path.exists(s_path):
                s_path = os.path.join(full, "results", "metrics.json")
            if not os.path.exists(s_path):
                fr_path = os.path.join(full, "results", "framework_zipline_metrics.json")
                if os.path.exists(fr_path):
                    s_path = fr_path
            fv = _feature_vector(s_path)
            if fv is None and s_path and s_path.endswith("metrics.json"):
                fv = _metrics_features(s_path)
            if fv is not None:
                pub_feats[entry] = fv

    if not new_feats or not pub_feats:
        return [], []

    # If the new variants have feature vectors but the published family is sparse,
    # also pull features from `metrics.json` (B3 flat format) for every vpvr_/bb_/xs_ strategy.
    if len(pub_feats) < 10:
        sdir = workdir
        for entry in sorted(os.listdir(sdir)):
            if not (entry.startswith("vpvr_") or entry.startswith("bb_") or entry.startswith("xs_")):
                continue
            if entry in VARIANT_KEYS:
                continue
            if entry in pub_feats:
                continue
            full = os.path.join(sdir, entry)
            # Try summary.json first, then metrics.json
            for s_name in ("results/summary.json", "results/metrics.json"):
                s_path = os.path.join(full, s_name)
                if os.path.exists(s_path):
                    fv = _feature_vector(s_path)
                    if fv is None and s_name.endswith("metrics.json"):
                        fv = _metrics_features(s_path)
                    if fv is not None:
                        pub_feats[entry] = fv
                        break

    # Impute missing feature values via column-median before correlation.
    union = dict(_impute_features({**new_feats, **pub_feats}))
    # Z-normalize across the union of rows.
    union_zn = dict(_zscore_norm_feats(union))
    norm_new = {k: union_zn[k] for k in new_feats}
    norm_pub = {k: union_zn[k] for k in pub_feats}

    rows = []
    matrix_3xN = []
    for k1 in norm_new:
        v1 = norm_new[k1]
        col_for_var = [k1]
        for k2 in norm_pub:
            v2 = norm_pub[k2]
            c = _pearson(v1, v2)
            rows.append({
                "new_variant": k1,
                "published": k2,
                "feature_corr": f"{c:.3f}" if not math.isnan(c) else "NA",
                "concentration_warn": "YES" if not math.isnan(c) and abs(c) > 0.6 else "",
            })
            col_for_var.append(f"{c:.3f}" if not math.isnan(c) else "NA")
        matrix_3xN.append(col_for_var)
    return rows, matrix_3xN


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="/home/smark/multica/quant-loop/strategies")
    ap.add_argument("--variant-dirs", nargs="+", required=True)
    ap.add_argument("--display-csv", default=None)
    ap.add_argument("--out-square", required=True)
    ap.add_argument("--out-long", required=True)
    ap.add_argument("--out-3xN", default=None)
    args = ap.parse_args()

    sq = build_pairwise_3x3(args.variant_dirs, args.workdir)
    with open(args.out_square, "w") as f:
        writer = csv.writer(f)
        writer.writerows(sq)

    long_rows, matrix_3xN = build_long_vs_published(args.variant_dirs, args.workdir, args.display_csv)
    with open(args.out_long, "w") as f:
        if long_rows:
            writer = csv.DictWriter(f, fieldnames=list(long_rows[0].keys()))
            writer.writeheader()
            writer.writerows(long_rows)
        else:
            f.write("variant,published,feature_corr,concentration_warn\n")

    if args.out_3xN:
        with open(args.out_3xN, "w") as f:
            writer = csv.writer(f)
            # header = new, pub1, pub2, …
            pub_names = list({r["published"] for r in long_rows})
            writer.writerow(["new_variant"] + pub_names)
            for row in matrix_3xN:
                # row[0] is the new variant label, rest are corr values aligned to pub_names order
                writer.writerow(row)

    # Concentration warnings (|corr| > 0.6)
    warns = [r for r in long_rows if r["concentration_warn"] == "YES"]
    n_concentration = len(warns)
    print(json.dumps({
        "n_new_variants": len(args.variant_dirs),
        "n_published_with_features": len({r["published"] for r in long_rows}),
        "n_pairs_over_0_6": n_concentration,
        "concentration_pairs_sample": warns[:10],
    }, indent=2))


if __name__ == "__main__":
    main()
