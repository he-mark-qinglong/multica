"""W4-T05 signal parity test.

Compare two signal chains on the 2024 overlap window:
  - path A (new):   authoritative loader -> build_se_h3_signals (T03)
  - path B (ref):   data_loader_patch + run_experiments.enhance_signals (quick_verify)

Funding-independent columns (z, size_scale, z_slope) must be bitwise-equal
under reindex; fund_allow mismatch is bounded to <= 5% (funding is dual-source).

Failure diagnostic: FH/results/t05_signal_parity_failure.json
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# sys.path bootstrap (FH = full_history/, SE = signal-enhance-h3/, FIXED = H3-variants-h1h2h4/)
# ---------------------------------------------------------------------------
FH = Path(__file__).resolve().parent
SE = FH.parent                                       # signal-enhance-h3/
FIXED = SE.parent / "H3-variants-h1h2h4"            # sibling swarm dir
for p in (str(FH), str(SE), str(FIXED)):
    if p not in sys.path:
        sys.path.insert(0, p)

import data_loader_patch as dlp                              # noqa: E402
from run_experiments import enhance_signals, load_config     # noqa: E402
import se_h3_common as C                                     # noqa: E402
import se_h3_signals as S                                    # noqa: E402

START, END = "2024-01-01", "2024-12-31"
PAIR = "BTCUSDT/SOLUSDT"
EXPECTED_REF_BARS = 525601  # quick_verify.log L1 (2024 BTC bars)
FUND_ALLOW_MAX_PCT = 5.0    # card threshold; funding is dual-source so non-zero is expected


def _dump_failure(check: str, payload: dict) -> None:
    out = {
        "check": check,
        "n_bars": int(payload.get("n_bars", 0)),
        "first_mismatch_ts": payload.get("first_mismatch_ts"),
        "mismatch_count": int(payload.get("mismatch_count", 0)),
    }
    res_dir = FH / "results"
    res_dir.mkdir(exist_ok=True)
    (res_dir / "t05_signal_parity_failure.json").write_text(json.dumps(out, indent=2, default=str))


def main() -> int:
    cfg = load_config()  # H3 config.json (shared by both chains)

    # --- path B: reference chain (quick_verify) --------------------------------
    print("[path B] dlp.load_all() + dlp.load_funding() + slice_by_date ...")
    d1m_b, fund_b = dlp.slice_by_date(dlp.load_all(), dlp.load_funding(), START, END)
    sig_b = enhance_signals(d1m_b, cfg, fund_b)[PAIR]

    # --- path A: new chain (T02/T03) -------------------------------------------
    print("[path A] C.load_aligned_data() -> mask 2024 -> build_se_h3_signals ...")
    d1m_a, fund_a, _common = C.load_aligned_data()
    t0, t1 = pd.Timestamp(START), pd.Timestamp(END)
    d1m_a = {s: df.loc[(df.index >= t0) & (df.index <= t1)].copy() for s, df in d1m_a.items()}
    fund_a = {s: f.loc[(f.index >= t0) & (f.index <= t1)].copy() for s, f in fund_a.items()}
    sig_a = S.build_se_h3_signals(d1m_a, cfg, fund_a)[PAIR]

    # --- bar-count anchor ------------------------------------------------------
    n_ref = len(sig_b["z"].index)
    if n_ref != EXPECTED_REF_BARS:
        _dump_failure("ref_bar_count",
                      {"n_bars": n_ref, "mismatch_count": 0,
                       "first_mismatch_ts": None,
                       "expected": EXPECTED_REF_BARS})
        raise AssertionError(f"ref bars {n_ref} != {EXPECTED_REF_BARS}")

    # --- index intersection ----------------------------------------------------
    idx = sig_a["z"].index.intersection(sig_b["z"].index)
    if len(idx) != EXPECTED_REF_BARS:
        _dump_failure("index_overlap",
                      {"n_bars": len(idx), "mismatch_count": 0,
                       "first_mismatch_ts": None,
                       "expected": EXPECTED_REF_BARS})
        raise AssertionError(f"index overlap {len(idx)} != {EXPECTED_REF_BARS}")

    # --- bitwise equality on funding-independent series -------------------------
    series_pairs = [
        ("z", "z"),
        ("size_scale", "size_scale"),
        ("z_slope_fav_4", "z_slope_4"),  # new key vs reference key
    ]
    for ka, kb in series_pairs:
        xa = sig_a[ka].reindex(idx).to_numpy(dtype=float)
        xb = sig_b[kb].reindex(idx).to_numpy(dtype=float)
        ok = np.allclose(xa, xb, atol=1e-12, rtol=0.0, equal_nan=True)
        print(f"{ka} vs {kb}: allclose(1e-12) = {ok}")
        if not ok:
            # find first mismatch ts
            mask = ~(np.isclose(xa, xb, atol=1e-12, rtol=0.0, equal_nan=True))
            first_idx = int(np.argmax(mask)) if mask.any() else -1
            ts = str(idx[first_idx]) if first_idx >= 0 else None
            _dump_failure(f"series_mismatch:{ka}_vs_{kb}",
                          {"n_bars": len(idx),
                           "mismatch_count": int(mask.sum()),
                           "first_mismatch_ts": ts})
            raise AssertionError(f"series mismatch: {ka} vs {kb}")

    # --- fund_allow divergence (bounded, NOT bitwise) -------------------------
    fa = sig_a["fund_allow"].reindex(idx).to_numpy(dtype=int)
    fb = sig_b["fund_allow"].reindex(idx).to_numpy(dtype=int)
    mism = int((fa != fb).sum())
    pct = mism / len(idx) * 100.0
    print(f"fund_allow mismatch: {mism} bars ({pct:.3f}%)")
    if pct > FUND_ALLOW_MAX_PCT:
        mask = fa != fb
        first_idx = int(np.argmax(mask)) if mask.any() else -1
        ts = str(idx[first_idx]) if first_idx >= 0 else None
        _dump_failure("fund_allow_divergence",
                      {"n_bars": len(idx),
                       "mismatch_count": mism,
                       "first_mismatch_ts": ts,
                       "pct": pct})
        raise AssertionError(f"fund_allow divergence {pct:.2f}% > {FUND_ALLOW_MAX_PCT}%")

    print("SIGNAL PARITY OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError:
        traceback.print_exc()
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        # best-effort generic dump
        try:
            _dump_failure("uncaught_exception",
                          {"n_bars": 0, "mismatch_count": 0,
                           "first_mismatch_ts": traceback.format_exc()})
        except Exception:
            pass
        sys.exit(2)