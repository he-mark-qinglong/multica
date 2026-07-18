"""Replay validation script for SMA-34940.

Runs the realtime_monitor in replay mode over two windows and reports
the per-symbol hit counts and per-bar latency distribution.

  * spec_window:  2026-06-17 → 2026-07-17  (the "last 30d" window from
    the issue body, funding threshold = 0.0003 i.e. 0.03%).
  * extend_window: 2024-12-02 → 2025-01-01  (a 30d window that captures
    a non-zero funding regime — sanity check that the detector emits
    alerts when the funding filter would actually fire; the recent 30d
    has been in a heavily-capped funding regime with rate ≈ 0.0001,
    so the spec window alone cannot validate the gate).
  * cap_window:   2026-06-17 → 2026-07-17, funding threshold lowered
    to 0.0001 (the practical Binance "neutral" cap observed in this
    period) — to show what the alert rate would look like in the
    current regime if the funding threshold tracked market reality.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from realtime_monitor import (  # noqa: E402
    MonitorConfig,
    replay_multi,
)


def _spec(symbol: str) -> dict[str, str]:
    """Build the per-symbol input spec."""
    if symbol == "SOLUSDT":
        bars = "~/multica/quant-loop/strategies/vpvr_volume_edge_3tf_v1_20260711/data/SOLUSDT__1m.parquet"
    else:
        bars = f"~/multica/quant-loop/data/perp_1m/{symbol}_1m.parquet"
    return {
        "symbol": symbol,
        "bars_path": bars,
        "funding_path": f"~/multica/quant-loop/data/funding/{symbol}.parquet",
    }


def _summary_to_dict(summary: dict) -> dict:
    return {
        sym: {
            "n_bars": stats.n_bars,
            "n_loid_events": stats.n_loid_events,
            "n_vpvr_proximity_bars": stats.n_vpvr_proximity_bars,
            "n_funding_pass_bars": stats.n_funding_pass_bars,
            "n_alerts": stats.n_alerts,
            "latency_ms_p50": round(stats.latency_ms_p50, 3),
            "latency_ms_p95": round(stats.latency_ms_p95, 3),
        }
        for sym, stats in summary.items()
    }


def run_window(
    name: str,
    symbols: list[str],
    *,
    out_dir: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    funding_threshold: float,
    vpvr_distance_pct: float,
) -> dict[str, dict]:
    cfg = MonitorConfig(
        funding_threshold=funding_threshold,
        vpvr_distance_pct=vpvr_distance_pct,
    )
    spec = [_spec(s) for s in symbols]
    print(f"\n=== {name} | {start.date()} → {end.date()} | "
          f"funding>{funding_threshold} vpvr<{vpvr_distance_pct*100:.2f}% ===")
    t0 = time.time()
    summary = replay_multi(
        spec,
        out_dir / name,
        start_ts=start,
        end_ts=end,
        monitor_config=cfg,
    )
    elapsed = time.time() - t0
    payload = _summary_to_dict(summary)
    for sym, stats in summary.items():
        print(
            f"  {sym:<10} bars={stats.n_bars:>6} loid={stats.n_loid_events:>5} "
            f"vpvr={stats.n_vpvr_proximity_bars:>6} funding={stats.n_funding_pass_bars:>5} "
            f"alerts={stats.n_alerts:>4} p50={stats.latency_ms_p50:.2f}ms "
            f"p95={stats.latency_ms_p95:.2f}ms"
        )
    print(f"  total wall-clock: {elapsed:.1f}s")
    payload["_meta"] = {
        "window_name": name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "funding_threshold": funding_threshold,
        "vpvr_distance_pct": vpvr_distance_pct,
        "elapsed_s": round(elapsed, 2),
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        default="~/multica/quant-loop/signals",
        help="root output directory",
    )
    parser.add_argument(
        "--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    # Spec window: last 30d, 0.03% funding threshold.
    spec_window = run_window(
        "spec_30d",
        symbols,
        out_dir=out_dir,
        start=pd.Timestamp("2026-06-17 00:00:00+00:00"),
        end=pd.Timestamp("2026-07-17 23:59:00+00:00"),
        funding_threshold=0.0003,
        vpvr_distance_pct=0.002,
    )

    # Extended window: Dec 2024 (last time funding > 0.03% in this dataset),
    # same 30d length, same 0.03% threshold — sanity check.
    extend_window = run_window(
        "extended_30d_dec24",
        symbols,
        out_dir=out_dir,
        start=pd.Timestamp("2024-12-02 00:00:00+00:00"),
        end=pd.Timestamp("2025-01-01 23:59:00+00:00"),
        funding_threshold=0.0003,
        vpvr_distance_pct=0.002,
    )

    # Cap window: same 30d as the spec, but with funding threshold lowered
    # to 0.0001 (the practical Binance cap observed in this period).
    cap_window = run_window(
        "cap_30d",
        symbols,
        out_dir=out_dir,
        start=pd.Timestamp("2026-06-17 00:00:00+00:00"),
        end=pd.Timestamp("2026-07-17 23:59:00+00:00"),
        funding_threshold=0.0001,
        vpvr_distance_pct=0.002,
    )

    out_path = out_dir / "validate_summary.json"
    out_path.write_text(json.dumps(
        {"spec_30d": spec_window, "extended_30d_dec24": extend_window, "cap_30d": cap_window},
        indent=2,
        default=str,
    ))
    print(f"\nsummary written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
