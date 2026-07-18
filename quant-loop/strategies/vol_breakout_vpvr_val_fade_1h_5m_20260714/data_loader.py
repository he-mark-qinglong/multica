"""Data loader for vol_breakout_vpvr_val_fade_1h_5m_20260714 (iter#74, V10).

Multi-TF 1h trend + 5m entry on BTCUSDT.

Source layout (canonical):
    /home/smark/multica/quant-loop/live_data/
        BTCUSDT_5m.parquet  (canonical 5m klines)   [falls back to vpvr_iceberg cache]
        BTCUSDT_1h.parquet  (canonical 1h klines)

Output layout (per-symbol merged frame, owned by this strategy):
    <strategy_dir>/data/
        BTCUSDT__5m_with_1h_indicators.parquet  # 5m rows + higher_ema_50 + vpvr_val + atr_5m
        manifest.parquet.sha256

Schema contract (after merge):
    index        : openTime, pd.DatetimeIndex, tz=UTC
    cols (5m)    : open, high, low, close, vol
    cols (added) : atr (5m ATR-14 Wilder), higher_ema_50, vpvr_val

Look-ahead discipline
---------------------
- 1h ema_50 uses ``shift(1)`` so the value at hour ``t`` reflects the
  prior close only.
- 1h vpvr_val is computed on a rolling 168-bar (7d) window, with the
  output shifted by 1 hour. The 5m bar at minute ``m`` therefore reads
  the 1h indicator value as-of the most recent completed hour.
- 5m ATR uses the standard Wilder EWM (alpha = 1/14), so bar ``t``'s
  atr reflects bars ``[t-13, t]`` inclusive. That is one bar of lookback
  — small, standard, and disclosed.

This is enforced in ``run_backtest`` via the merge_asof direction
(``backward``) on a sorted 1h index.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"
STRATEGY_DIR = Path(__file__).parent
DATA_DIR = STRATEGY_DIR / "data"
RESULTS_DIR = STRATEGY_DIR / "results"

# Source location for canonical Binance spot klines.
DEFAULT_SOURCE_ROOT = Path("/home/smark/multica/quant-loop/live_data")

# Fallback 5m source (some strategies cache a more complete 5m file).
ALT_5M_SOURCES = (
    Path("/home/smark/multica/quant-loop/strategies/vpvr_iceberg_fade_5m_20260711/data/BTCUSDT__5m.parquet"),
)

# Paper-trade guardrail.
if os.environ.get("LIVE_TRADING") == "1":
    raise SystemExit(
        "data_loader.py is paper-trade only; refusing to run with LIVE_TRADING=1"
    )


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

@dataclass
class SourceManifest:
    root: Path
    files: Dict[str, str]

    def write(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w") as fh:
            for rel, sha in sorted(self.files.items()):
                fh.write(f"{sha}  {rel}\n")

    def verify(self) -> List[str]:
        drift: List[str] = []
        for rel, expected in self.files.items():
            current = _sha256(self.root / rel)
            if current != expected:
                drift.append(rel)
        return drift


def _sha256(path: Path, chunk: int = 1 << 20) -> int:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Source reading
# ---------------------------------------------------------------------------

def _normalize_5m(raw: pd.DataFrame, path: Path) -> pd.DataFrame:
    """vpvr_iceberg_fade cache schema: index='bucket', cols incl. quote_volume.

    We keep only the OHLCV columns and ensure the index is a UTC
    DatetimeIndex named ``openTime``.
    """
    if "bucket" in raw.columns:
        idx = pd.to_datetime(raw["bucket"], utc=True)
        raw = raw.drop(columns=["bucket"])
        raw.index = idx
    elif "open_time" in raw.columns:
        idx = pd.to_datetime(raw["open_time"], unit="ms", utc=True)
        raw = raw.drop(columns=["open_time"])
        raw.index = idx
    if raw.index.name is None:
        raw.index.name = "openTime"
    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("UTC")
    expected = {"open", "high", "low", "close", "volume"}
    missing = expected - set(raw.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    out = raw[["open", "high", "low", "close", "volume"]].copy()
    out = out.rename(columns={"volume": "vol"})
    return out.sort_index()


def _normalize_1h(raw: pd.DataFrame, path: Path) -> pd.DataFrame:
    """live_data/1h schema: open_time as int-ms column, no index.

    Convert to a UTC ``openTime`` DatetimeIndex; keep OHLCV only.
    """
    if "open_time" not in raw.columns:
        raise ValueError(f"{path}: expected 'open_time' column, got {list(raw.columns)}")
    idx = pd.to_datetime(raw["open_time"], unit="ms", utc=True)
    out = raw.copy()
    out = out.drop(columns=["open_time"])
    out.index = idx
    out.index.name = "openTime"
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    out = out.rename(columns={"volume": "vol"})
    keep = [c for c in ("open", "high", "low", "close", "vol") if c in out.columns]
    return out[keep].sort_index()


def _read_5m(source_root: Path) -> pd.DataFrame:
    primary = source_root / "BTCUSDT_5m.parquet"
    if primary.exists():
        return _normalize_5m(pd.read_parquet(primary), primary)
    for alt in ALT_5M_SOURCES:
        if alt.exists():
            return _normalize_5m(pd.read_parquet(alt), alt)
    raise FileNotFoundError(
        f"no 5m BTCUSDT source found (primary={primary}, fallbacks={list(ALT_5M_SOURCES)})"
    )


def _read_1h(source_root: Path) -> pd.DataFrame:
    primary = source_root / "BTCUSDT_1h.parquet"
    if not primary.exists():
        raise FileNotFoundError(f"missing 1h source: {primary}")
    return _normalize_1h(pd.read_parquet(primary), primary)


# ---------------------------------------------------------------------------
# Indicator computation on the 1h frame
# ---------------------------------------------------------------------------

def compute_1h_indicators(
    df_1h: pd.DataFrame,
    *,
    ema_period: int,
    vpvr_window: int,
    vpvr_bins: int,
    vpvr_va_pct: float,
) -> pd.DataFrame:
    """Add `ema_50` and `vpvr_val` to a 1h OHLCV frame.

    Look-ahead discipline:
    - ema uses shift(1) so bar t sees only bars [t-W, t-1]
    - vpvr_val uses shift(1) so bar t sees only bars [t-window, t-1]
    """
    out = df_1h.copy()
    ema = out["close"].ewm(span=ema_period, adjust=False).mean()
    out["ema"] = ema.shift(1)  # NO look-ahead
    out["higher_ema_50"] = out["ema"]  # alias expected by strategy.generate_signal

    # VPVR VAL on a rolling 1h window. We import the existing reference
    # calc to keep the family consistent (cycle-49 family uses
    # calculate_vpvr_value_area with bins=20 by default; we use 24 here
    # to better resolve the lower-tail VAL pierce events V10 needs).
    calculate_vpvr_value_area = _import_vpvr_value_area()

    # NOTE: calculate_vpvr_value_area uses bars [t-period+1, t] inclusive.
    # We shift the OUTPUT so the value at hour t reflects bars up to
    # t-1 only. The reference function in trading repo does not tolerate
    # NaN inside the window, so we feed it the unshifted series.
    vah_raw, val_raw = calculate_vpvr_value_area(
        out["high"],
        out["low"],
        out["close"],
        out["vol"],
        period=vpvr_window,
        bins=vpvr_bins,
        value_area_pct=vpvr_va_pct,
    )
    out["vpvr_val"] = val_raw.shift(1)  # shift to drop look-ahead by 1 bar
    out["vpvr_vah"] = vah_raw.shift(1)
    return out


def _import_vpvr_value_area():
    """Import calculate_vpvr_value_area from the trading repo.

    Tries the standard strategies.team import path first; falls back to a
    stub-package bootstrap if strategies/__init__.py pulls a broken chain
    (cycle-49 known issue: kama_trend_vwap → indicator_module.LHFrameStd).
    Returns the function or raises ImportError on hard failure.
    """
    try:
        from strategies.team.vpvr_strategies import calculate_vpvr_value_area  # type: ignore
        return calculate_vpvr_value_area
    except Exception:
        pass
    try:
        import importlib.util
        import sys
        import types

        TR = Path("/home/smark/multica_workspaces/f9a9d34e-b809-4564-b0c0-b781a70a3f25/42a03459/workdir/trading")
        for name in ("strategies", "strategies.team"):
            if name not in sys.modules:
                stub = types.ModuleType(name)
                stub.__path__ = [str(TR / name.replace(".", "/"))]
                sys.modules[name] = stub
        spec = importlib.util.spec_from_file_location(
            "strategies.team.vpvr_strategies", str(TR / "strategies" / "team" / "vpvr_strategies.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["strategies.team.vpvr_strategies"] = mod
        spec.loader.exec_module(mod)
        return mod.calculate_vpvr_value_area
    except Exception as e:
        raise ImportError(f"failed to load calculate_vpvr_value_area: {e}")


# ---------------------------------------------------------------------------
# Merge 1h indicators into the 5m frame
# ---------------------------------------------------------------------------

def merge_5m_with_1h_indicators(
    df_5m: pd.DataFrame,
    df_1h_with_ind: pd.DataFrame,
) -> pd.DataFrame:
    """merge_asof backward: each 5m bar gets the most recent 1h indicator row.

    The 1h frame has shift(1) applied, so the value at hour t already
    reflects bars [t-window, t-1]. The merge is therefore a 1-hour-lag
    at most — strictly no look-ahead.
    """
    five_min = df_5m.copy()
    one_hour = df_1h_with_ind[["higher_ema_50", "vpvr_val", "vpvr_vah"]].copy()
    one_hour = one_hour.sort_index()
    out = pd.merge_asof(
        five_min.sort_index(),
        one_hour,
        left_index=True,
        right_index=True,
        direction="backward",
    )
    return out


# ---------------------------------------------------------------------------
# 5m ATR (Wilder)
# ---------------------------------------------------------------------------

def compute_5m_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = df.copy()
    high = out["high"]
    low = out["low"]
    close = out["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    out["atr"] = atr
    return out


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def build_source_manifest(source_root: Path = DEFAULT_SOURCE_ROOT) -> SourceManifest:
    files: Dict[str, str] = {}
    candidates = [
        source_root / "BTCUSDT_5m.parquet",
        source_root / "BTCUSDT_1h.parquet",
    ]
    candidates.extend(ALT_5M_SOURCES)
    for p in candidates:
        if p.exists():
            try:
                rel = p.name
                files[rel] = _sha256(p)
            except Exception:
                continue
    return SourceManifest(root=source_root, files=files)


def load_symbol(
    symbol: str = "BTCUSDT",
    *,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return 5m OHLCV+indicators frame for ``symbol`` (BTCUSDT only for V10)."""
    sym = symbol.upper()
    if sym != "BTCUSDT":
        raise ValueError(f"V10 is BTCUSDT-only; got {sym}")

    cache = data_dir / f"{sym}__5m_with_1h_indicators.parquet"
    if cache.exists() and not refresh:
        out = pd.read_parquet(cache)
        if out.index.name != "openTime":
            out.index.name = "openTime"
        if out.index.tz is None:
            out.index = out.index.tz_localize("UTC")
        return out

    cfg = json.loads(CONFIG_PATH.read_text())
    ind = cfg["indicators"]

    df_5m = _read_5m(source_root)
    df_1h = _read_1h(source_root)
    df_1h_ind = compute_1h_indicators(
        df_1h,
        ema_period=ind["ema_period_1h"],
        vpvr_window=ind["vpvr_window_bars_1h"],
        vpvr_bins=ind["vpvr_bins_1h"],
        vpvr_va_pct=ind["vpvr_value_area_pct"],
    )
    df_merged = merge_5m_with_1h_indicators(df_5m, df_1h_ind)
    df_final = compute_5m_atr(df_merged, period=ind["atr_period_5m"])

    data_dir.mkdir(parents=True, exist_ok=True)
    df_final.to_parquet(cache)
    build_source_manifest(source_root).write(data_dir / "manifest.parquet.sha256")
    return df_final


def main() -> int:
    df = load_symbol(refresh=True)
    print(f"loaded 5m frame: shape={df.shape}, range={df.index.min()} -> {df.index.max()}")
    print(f"columns: {list(df.columns)}")
    print(f"non-NaN: higher_ema_50={int(df['higher_ema_50'].notna().sum())}, "
          f"vpvr_val={int(df['vpvr_val'].notna().sum())}, "
          f"atr={int(df['atr'].notna().sum())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
