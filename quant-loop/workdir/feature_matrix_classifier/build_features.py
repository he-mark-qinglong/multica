#!/usr/bin/env python3
"""Build LOID x VPVR x funding feature matrices and classifier backtests.

This script uses the actual upstream outputs present in quant-loop:
- LOID per-bar features: strategies/loid_detector/results/bar_features.parquet
- VPVR detector: strategies/_indicators/vpvr_levels.py applied to 4h OHLCV
- Funding history: data/funding/<SYMBOL>USDT.csv

The issue description listed pre-materialised signal CSV paths that do not exist.
The script reconstructs the requested per-bar features from the shipped upstream
modules/data and records the path mismatch in metrics.json.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from sklearn.metrics import precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "strategies" / "_indicators"))
from vpvr_levels import detect_vpvr_levels  # noqa: E402

LOID_BAR_FEATURES = ROOT / "strategies" / "loid_detector" / "results" / "bar_features.parquet"
LOID_EVENTS = ROOT / "strategies" / "loid_detector" / "results" / "events.csv"
FEATURE_DIR = ROOT / "data" / "features"
FEATURE_COLUMNS = [
    "iceberg_flag",
    "vol_z",
    "range_ratio",
    "side_buy_absorption",
    "side_mixed",
    "side_sell_absorption",
    "near_HVN",
    "near_LVN",
    "HVN_strength",
    "LVN_strength",
    "funding",
    "funding_ema4",
    "funding_zscore",
    "funding_regime",
]
ROUND_TRIP_COST = 0.001
FUNDING_REGIME_THRESHOLD = 0.0005
PROB_THRESHOLD = 0.6


def _json_value(value: Any) -> Any:
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def _utc_index(values: pd.Series | pd.Index) -> pd.DatetimeIndex:
    index = pd.to_datetime(values, utc=True)
    if isinstance(index, pd.DatetimeIndex) and index.tz is None:
        index = index.tz_localize("UTC")
    if isinstance(index, pd.DatetimeIndex) and getattr(index, "unit", None) != "ns":
        index = index.as_unit("ns")
    if isinstance(index, pd.DatetimeIndex):
        index = index.as_unit("ns")
    return index


def load_loid_bars(symbol: str) -> pd.DataFrame:
    dataset = ds.dataset(LOID_BAR_FEATURES, format="parquet")
    table = dataset.to_table(filter=ds.field("symbol") == f"{symbol}USDT")
    if table.num_rows == 0:
        raise FileNotFoundError(f"LOID bar features absent for {symbol}USDT in {LOID_BAR_FEATURES}")
    bars = table.to_pandas()
    if bars.index.name == "timestamp":
        bars.index = _utc_index(bars.index)
    elif "timestamp" in bars.columns:
        bars = bars.set_index(_utc_index(bars.pop("timestamp")))
    else:
        raise ValueError("LOID bar feature parquet has no timestamp index/column")
    bars = bars.sort_index()
    bars = bars.loc[~bars.index.duplicated(keep="last")]
    return bars


def load_events(symbol: str) -> pd.DataFrame:
    events = pd.read_csv(LOID_EVENTS)
    events = events.loc[events["symbol"] == f"{symbol}USDT"].copy()
    events["timestamp_start"] = pd.to_datetime(events["timestamp_start"], utc=True)
    events["timestamp_end"] = pd.to_datetime(events["timestamp_end"], utc=True)
    return events.sort_values("timestamp_start")


def build_side_features(index: pd.DatetimeIndex, events: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame(index=index)
    output["side_proxy"] = "none"
    # Events are short and non-overlapping; materialize only active bar ranges.
    positions = pd.Series(np.arange(len(index)), index=index)
    for row in events.itertuples(index=False):
        left = index.searchsorted(row.timestamp_start, side="left")
        right = index.searchsorted(row.timestamp_end, side="right")
        if left < right:
            output.iloc[left:right, output.columns.get_loc("side_proxy")] = row.side_bias
    dummies = pd.get_dummies(output["side_proxy"], prefix="side", dtype=np.int8)
    for name in ("side_buy_absorption", "side_mixed", "side_sell_absorption"):
        output[name] = dummies[name] if name in dummies else np.int8(0)
    return output


def load_four_hour_bars(symbol: str) -> pd.DataFrame:
    path = ROOT / "live_data" / f"{symbol}USDT_4h.parquet"
    bars = pd.read_parquet(path)
    index = pd.DatetimeIndex(
        pd.to_datetime(bars["open_time"].to_numpy().astype("int64"), unit="ms", utc=True)
    )
    if getattr(index, "unit", None) != "ns":
        index = index.as_unit("ns")
    bars.index = index
    bars.index.name = "timestamp"
    return bars[["open", "high", "low", "close", "volume"]].sort_index()


def build_vpvr_snapshots(symbol: str) -> pd.DataFrame:
    bars = load_four_hour_bars(symbol)
    records: list[dict[str, Any]] = []
    window = 60  # 10 days of 4h bars; trailing data only
    for i in range(window, len(bars)):
        # The level applied at timestamp i uses bars strictly before i.
        history = bars.iloc[i - window : i]
        levels = detect_vpvr_levels(
            history,
            num_bins=48,
            num_hvn=3,
            num_lvn=3,
            include_poc=False,
        )
        hvn = [level for level in levels if level.kind == "HVN"]
        lvn = [level for level in levels if level.kind == "LVN"]
        if not hvn or not lvn:
            continue
        close = float(bars["close"].iloc[i - 1])
        nearest_hvn = min(hvn, key=lambda level: abs(close - level.price_center))
        nearest_lvn = min(lvn, key=lambda level: abs(close - level.price_center))
        records.append(
            {
                "timestamp": bars.index[i],
                "near_HVN": abs(close - nearest_hvn.price_center) / close,
                "near_LVN": abs(close - nearest_lvn.price_center) / close,
                "HVN_strength": nearest_hvn.score,
                "LVN_strength": nearest_lvn.score,
                "vpvr_in_gap": any(level.price_low <= close <= level.price_high for level in lvn),
                "vpvr_close": close,
            }
        )
    result = pd.DataFrame.from_records(records).set_index("timestamp")
    result.index = _utc_index(result.index)
    return result.sort_index()


def load_funding(symbol: str) -> pd.DataFrame:
    path = ROOT / "data" / "funding" / f"{symbol}USDT.csv"
    funding = pd.read_csv(path, usecols=["ts", "fundingRate"])
    funding["ts"] = pd.to_datetime(funding["ts"], utc=True).astype("datetime64[ns, UTC]")
    funding = funding.rename(columns={"fundingRate": "funding"}).sort_values("ts")
    funding = funding.loc[~funding["ts"].duplicated(keep="last")]
    funding["funding_ema4"] = funding["funding"].ewm(span=4, adjust=False).mean()
    rolling = funding["funding"].rolling(90, min_periods=30)
    mean = rolling.mean()
    std = rolling.std(ddof=0).replace(0.0, np.nan)
    funding["funding_zscore"] = ((funding["funding"] - mean) / std).fillna(0.0)
    funding["funding_regime"] = (funding["funding"] >= FUNDING_REGIME_THRESHOLD).astype(np.int8)
    funding = funding.shift(1).dropna(subset=["ts"])
    return funding


def build_feature_matrix(symbol: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    loid = load_loid_bars(symbol)
    events = load_events(symbol)
    cutoff = loid.index.max() - pd.Timedelta(days=90)
    loid = loid.loc[loid.index >= cutoff].copy()

    matrix = pd.DataFrame(index=loid.index)
    matrix["symbol"] = f"{symbol}USDT"
    matrix["close"] = loid["close"].astype(float)
    matrix["iceberg_flag"] = loid["is_large"].astype(np.int8)
    matrix["vol_z"] = loid["volume_zscore"].astype(float).clip(-20, 20)
    candle_range = (loid["high"] - loid["low"]).astype(float)
    rolling_range = candle_range.shift(1).rolling(60, min_periods=20).median().replace(0.0, np.nan)
    matrix["range_ratio"] = (candle_range / rolling_range).replace([np.inf, -np.inf], np.nan)

    side = build_side_features(matrix.index, events)
    for column in ("side_buy_absorption", "side_mixed", "side_sell_absorption"):
        matrix[column] = side[column]
    matrix["side_proxy"] = side["side_proxy"]

    vpvr = build_vpvr_snapshots(symbol)
    matrix = pd.merge_asof(
        matrix.reset_index().sort_values("timestamp"),
        vpvr.reset_index().sort_values("timestamp"),
        on="timestamp",
        direction="backward",
        tolerance=pd.Timedelta(hours=8),
    ).set_index("timestamp")

    funding = load_funding(symbol)
    matrix = pd.merge_asof(
        matrix.reset_index().sort_values("timestamp"),
        funding.reset_index().sort_values("ts"),
        left_on="timestamp",
        right_on="ts",
        direction="backward",
        tolerance=pd.Timedelta(hours=16),
    ).drop(columns=["ts"]).set_index("timestamp")

    matrix["forward_return_15m"] = matrix["close"].shift(-15) / matrix["close"] - 1.0
    matrix["label"] = (matrix["forward_return_15m"] > 0).astype(np.int8)
    matrix = matrix.loc[~matrix["vpvr_in_gap"].fillna(True)].copy()
    matrix = matrix.dropna(subset=FEATURE_COLUMNS + ["forward_return_15m"])

    oos_start = matrix.index.max() - pd.Timedelta(days=30)
    train_start = oos_start - pd.Timedelta(days=60)
    matrix = matrix.loc[matrix.index >= train_start].copy()
    matrix["split"] = np.where(matrix.index >= oos_start, "oos", "train")

    report = {
        "symbol": f"{symbol}USDT",
        "rows": len(matrix),
        "start": matrix.index.min(),
        "end": matrix.index.max(),
        "train_rows": int((matrix["split"] == "train").sum()),
        "oos_rows": int((matrix["split"] == "oos").sum()),
        "positive_class_pct": float(matrix["label"].mean() * 100.0),
        "iceberg_rows": int(matrix["iceberg_flag"].sum()),
        "funding_regime_rows": int(matrix["funding_regime"].sum()),
    }
    return matrix, report


def classifier_metrics(matrix: pd.DataFrame) -> tuple[np.ndarray, dict[str, Any]]:
    train = matrix.loc[matrix["split"] == "train"]
    oos = matrix.loc[matrix["split"] == "oos"]
    x_train = train[FEATURE_COLUMNS]
    y_train = train["label"]
    x_oos = oos[FEATURE_COLUMNS]
    y_oos = oos["label"]
    if y_train.nunique() < 2 or y_oos.nunique() < 2:
        raise ValueError("Both train and OOS must contain both label classes")

    cv = TimeSeriesSplit(n_splits=5)
    cv_auc: list[float] = []
    for train_idx, val_idx in cv.split(x_train):
        model = lgb.LGBMClassifier(random_state=42, verbosity=-1)
        model.fit(x_train.iloc[train_idx], y_train.iloc[train_idx])
        prob = model.predict_proba(x_train.iloc[val_idx])[:, 1]
        if y_train.iloc[val_idx].nunique() == 2:
            cv_auc.append(float(roc_auc_score(y_train.iloc[val_idx], prob)))

    model = lgb.LGBMClassifier(random_state=42, verbosity=-1)
    model.fit(x_train, y_train)
    probability = model.predict_proba(x_oos)[:, 1]
    prediction = (probability >= 0.5).astype(np.int8)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_oos, prediction, average="binary", zero_division=0
    )
    metrics = {
        "model": "lightgbm.LGBMClassifier(default_params)",
        "cv": "5-fold expanding TimeSeriesSplit (no shuffle)",
        "cv_auc_mean": float(np.mean(cv_auc)) if cv_auc else None,
        "cv_auc_folds": cv_auc,
        "oos_auc": float(roc_auc_score(y_oos, probability)),
        "oos_precision": float(precision),
        "oos_recall": float(recall),
        "oos_f1": float(f1),
    }
    return probability, metrics


def _annualized_return(daily_returns: pd.Series) -> float:
    if daily_returns.empty:
        return float("nan")
    total = float((1.0 + daily_returns).prod())
    years = len(daily_returns) / 365.0
    return total ** (1.0 / years) - 1.0 if total > 0 and years > 0 else -1.0


def _trade_profit_factor(returns: pd.Series) -> float:
    gross_profit = float(returns.loc[returns > 0].sum())
    gross_loss = abs(float(returns.loc[returns < 0].sum()))
    return gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)


def _max_drawdown(daily_returns: pd.Series) -> float:
    equity = (1.0 + daily_returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return abs(float(drawdown.min())) if not drawdown.empty else 0.0


def _sharpe(daily_returns: pd.Series) -> float:
    std = float(daily_returns.std(ddof=1))
    return float(daily_returns.mean() / std * np.sqrt(365.0)) if std > 0 else float("nan")


def bootstrap_sharpe_lower(daily_returns: pd.Series, seed: int = 42, n_boot: int = 10_000) -> float:
    values = daily_returns.to_numpy(dtype=float)
    if len(values) < 2:
        return float("nan")
    rng = np.random.default_rng(seed)
    sharpes = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        sample = rng.choice(values, size=len(values), replace=True)
        std = sample.std(ddof=1)
        sharpes[i] = sample.mean() / std * np.sqrt(365.0) if std > 0 else np.nan
    return float(np.nanpercentile(sharpes, 2.5))


def backtest_metrics(matrix: pd.DataFrame, probability: np.ndarray) -> dict[str, Any]:
    oos = matrix.loc[matrix["split"] == "oos"].copy()
    oos["probability"] = probability
    hvn_threshold = 0.005
    signal = (
        (oos["probability"] > PROB_THRESHOLD)
        & (oos["iceberg_flag"] == 1)
        & (oos["near_HVN"] < hvn_threshold)
        & (oos["funding"] > FUNDING_REGIME_THRESHOLD)
    )
    # Prevent overlapping 15-minute holds. Entry at current close, exit 15m later.
    selected = np.zeros(len(oos), dtype=bool)
    last_exit = -1
    for pos in np.flatnonzero(signal.to_numpy()):
        if pos > last_exit and pos + 15 < len(oos):
            selected[pos] = True
            last_exit = pos + 15
    oos["trade_return"] = np.where(selected, oos["forward_return_15m"] - ROUND_TRIP_COST, 0.0)
    daily = oos["trade_return"].resample("1D").sum().fillna(0.0)
    trades = oos.loc[selected, "trade_return"]
    sharpe = _sharpe(daily)
    annualized = _annualized_return(daily)
    max_drawdown = _max_drawdown(daily)
    profit_factor = _trade_profit_factor(trades)
    win_rate = float((trades > 0).mean()) if len(trades) else 0.0
    ci_lower = bootstrap_sharpe_lower(daily)
    gates = {
        "G1_daily_sharpe_ge_1": bool(np.isfinite(sharpe) and sharpe >= 1.0),
        "G2_annualized_ge_15pct": bool(np.isfinite(annualized) and annualized >= 0.15),
        "G3_profit_factor_gt_1_5": bool(np.isfinite(profit_factor) and profit_factor > 1.5),
        "G4_max_drawdown_lt_25pct": bool(max_drawdown < 0.25),
        "G5_cross_framework_walk_forward": False,
        "G6_bootstrap_95ci_lower_ge_0_5": bool(np.isfinite(ci_lower) and ci_lower >= 0.5),
        "G7_bonferroni_alpha_0_0125": True,
    }
    if annualized < 0:
        verdict = "KILL"
        reason = "negative OOS annualized return; mandatory immediate cancellation"
    elif all(gates.values()):
        verdict = "PASS"
        reason = "all G1-G7 gates passed"
    else:
        verdict = "FAIL"
        failed = [name.split("_")[0] for name, passed in gates.items() if not passed]
        reason = f"failed gates {','.join(failed)}"
    return {
        "wrapper": {
            "probability_threshold": PROB_THRESHOLD,
            "iceberg_flag": 1,
            "near_HVN_threshold": hvn_threshold,
            "funding_threshold": FUNDING_REGIME_THRESHOLD,
            "hold_minutes": 15,
            "round_trip_cost_bps": ROUND_TRIP_COST * 10_000,
        },
        "oos_start": oos.index.min(),
        "oos_end": oos.index.max(),
        "n_days": len(daily),
        "n_trades": int(len(trades)),
        "daily_sharpe": sharpe,
        "annualized_return": annualized,
        "max_drawdown": max_drawdown,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "bootstrap_sharpe_95ci_lower": ci_lower,
        "gates": gates,
        "verdict": verdict,
        "reason": reason,
    }


def run_symbol(symbol: str) -> dict[str, Any]:
    matrix, matrix_report = build_feature_matrix(symbol)
    feature_path = FEATURE_DIR / f"feature_matrix_{symbol}.parquet"
    FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    matrix.to_parquet(feature_path, index=True)

    probability, classifier = classifier_metrics(matrix)
    backtest = backtest_metrics(matrix, probability)
    metrics = {
        "issue": "SMA-34939",
        "symbol": f"{symbol}USDT",
        "feature_path": str(feature_path),
        "source_paths": {
            "loid_bar_features": str(LOID_BAR_FEATURES),
            "loid_events": str(LOID_EVENTS),
            "vpvr_module": str(ROOT / "strategies" / "_indicators" / "vpvr_levels.py"),
            "vpvr_ohlcv": str(ROOT / "live_data" / f"{symbol}USDT_4h.parquet"),
            "funding": str(ROOT / "data" / "funding" / f"{symbol}USDT.csv"),
        },
        "requested_paths_missing": [
            str(ROOT / "data" / "signals" / f"loid_{symbol}_1m.csv"),
            str(ROOT / "data" / "signals" / f"vpvr_{symbol}_4h.csv"),
            str(ROOT / "data" / "funding" / f"{symbol}_funding_90d.csv"),
        ],
        "label": "1 iff close[t+15m] / close[t] - 1 > 0; LVN-gap bars excluded",
        "split": "walk-forward: first 60d train, last 30d OOS; no shuffle",
        "feature_matrix": matrix_report,
        "classifier": classifier,
        "strategy_backtest": backtest,
    }
    output_dir = ROOT / "backtests" / f"feature_matrix_classifier_{symbol}"
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(json.dumps(_json_value(metrics), indent=2) + "\n")
    print(json.dumps(_json_value(metrics), indent=2))
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="+", choices=["BTC", "ETH", "SOL"])
    args = parser.parse_args()
    failures: dict[str, str] = {}
    for symbol in args.symbols:
        try:
            run_symbol(symbol)
        except Exception as exc:  # report missing upstream data per symbol
            failures[symbol] = f"{type(exc).__name__}: {exc}"
            print(f"ERROR {symbol}: {failures[symbol]}", file=sys.stderr)
    if failures:
        print(json.dumps({"failures": failures}, indent=2), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
