"""Smoke test for se_h3_signals on a 60k-bar slice. Run: python3 smoke_signals.py"""
import json
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
QL_ROOT = HERE.parents[4]  # full_history -> signal-enhance-h3 -> 2026-07-25 -> swarm -> research -> quant-loop
# NOTE: parents[4] from the FILE (HERE is already the file's dir): count carefully:
# HERE=full_history, .parent=signal-enhance-h3, .parent=2026-07-25, .parent=swarm,
# .parent=research, .parent=quant-loop  => HERE.parents[4]

from se_h3_signals import build_se_h3_signals, SLOPE_KEY  # noqa: E402

N_BARS = 60_000

# Sanity check the parents[] walk immediately so a wrong count fails loud
# (per round-2 card T03 implementation note), not silently:
assert (QL_ROOT / "data" / "perp_1m").is_dir(), f"QL_ROOT wrong: {QL_ROOT}"


def load_1m(symbol: str) -> pd.DataFrame:
    # Mirrors fixed runner load_perp_1m (run_btcsol_variants_fixed.py L90-98).
    p = QL_ROOT / "data" / "perp_1m" / f"{symbol}_1m.parquet"
    df = pd.read_parquet(p)
    df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("open_time").sort_index()
    df.index = df.index.tz_convert(None)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep].astype(float)


def main() -> None:
    t0 = time.time()
    d1m = {s: load_1m(s) for s in ("BTCUSDT", "SOLUSDT")}
    common = d1m["BTCUSDT"].index.intersection(d1m["SOLUSDT"].index)[:N_BARS]
    d1m = {s: df.loc[common].copy() for s, df in d1m.items()}
    cfg = json.loads((QL_ROOT / "strategies" / "mtf_xs_pairs_1m_15m_2h_h3_20260718"
                      / "config.json").read_text())
    # funding={} -> base _fund_2h defaults fund_allow=1 (base L347-348); fine for smoke.
    sigs = build_se_h3_signals(d1m, cfg, funding={})
    pair = cfg["pairs"][0]
    sig = sigs[pair]
    assert SLOPE_KEY in sig, f"missing {SLOPE_KEY}"
    slope = sig[SLOPE_KEY]
    assert slope.index.equals(sig["z"].index), "slope/z index mismatch"
    frac = float(slope.notna().mean())
    assert frac > 0.9, f"slope non-NaN fraction {frac:.3f} <= 0.9"
    assert "z_slope_15m" not in sig, "forbidden key z_slope_15m present"
    print(f"SMOKE OK: bars={len(common)} slope_nonNaN={frac:.4f} "
          f"elapsed={time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()