"""Tests for vol_target.py. Plain asserts, prints N/N passed at end."""
import numpy as np
import pandas as pd

from vol_target import (
    apply_vol_target,
    rolling_realized_vol,
    sharpe_lift,
    vol_target_weights,
)

LOOKBACK = 20
PY = 365
TARGET_VOL = 0.15
np.random.seed(42)

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


# --- Test 1: constant-vol returns -> weights ~ 1.0 throughout ---------------
# Build a series whose realized vol matches TARGET_VOL.
sigma = TARGET_VOL / np.sqrt(PY)  # per-bar std so annualized == TARGET_VOL
n = 200
ret_const = pd.Series(np.random.normal(0.0, sigma, size=n))
w_const = vol_target_weights(ret_const, target_vol=TARGET_VOL, lookback=LOOKBACK, periods_per_year=PY)
post_warmup = w_const.iloc[LOOKBACK:]
check("T1 constant-vol weights ~1.0", (post_warmup - 1.0).abs().mean() < 0.20)

# --- Test 2: vol doubles -> weights halve (within floor/cap) ----------------
ret_dbl = pd.Series(np.random.normal(0.0, 2 * sigma, size=n))
w_dbl = vol_target_weights(ret_dbl, target_vol=TARGET_VOL, lookback=LOOKBACK, periods_per_year=PY)
post_dbl = w_dbl.iloc[LOOKBACK:]
# Expect ~0.5 but capped at floor=0.1 only when realized vol is 30x; here ~0.5
check("T2 doubled-vol weights ~0.5", 0.3 < post_dbl.mean() < 0.8)

# --- Test 3: vol halves -> weights double (within cap) ----------------------
ret_half = pd.Series(np.random.normal(0.0, 0.5 * sigma, size=n))
w_half = vol_target_weights(ret_half, target_vol=TARGET_VOL, lookback=LOOKBACK, periods_per_year=PY)
post_half = w_half.iloc[LOOKBACK:]
# Expect ~2.0 but capped at 3.0; here ~2.0
check("T3 halved-vol weights ~2.0", 1.5 < post_half.mean() < 2.7)

# --- Test 4: early bars (< lookback) -> weights = 1.0 -----------------------
w_early = vol_target_weights(ret_const, target_vol=TARGET_VOL, lookback=LOOKBACK, periods_per_year=PY)
check("T4 warmup weights == 1.0", (w_early.iloc[:LOOKBACK] == 1.0).all())

# --- Test 5: apply_vol_target preserves starting equity ---------------------
equity = (1 + ret_const).cumprod() * 100.0
equity_vt = apply_vol_target(equity, target_vol=TARGET_VOL, lookback=LOOKBACK, periods_per_year=PY)
check("T5 starting equity preserved", abs(equity_vt.iloc[0] - equity.iloc[0]) < 1e-9)
check("T5 finite & monotone length", len(equity_vt) == len(equity))

# --- Test 6: sharpe_lift on calm-then-volatile regime > 0 -------------------
# Strategy has positive drift in calm period, but the volatile period wipes it
# out (high vol + slightly negative drift). Vol-targeting scales down during
# the volatile period -> Sharpe improves.
n_calm, n_vol = 300, 300
calm = np.random.normal(0.0005, sigma, size=n_calm)            # positive drift, target vol
volatile = np.random.normal(-0.0002, 6 * sigma, size=n_vol)    # small neg drift, 6x vol
regime = pd.Series(np.concatenate([calm, volatile]))
base_equity = (1 + regime).cumprod() * 1000.0
sized_equity = apply_vol_target(base_equity, target_vol=0.15, lookback=20, periods_per_year=PY)
lift = sharpe_lift(base_equity, sized_equity, periods_per_year=PY)
print(f"    sharpe_lift = {lift:+.4f}")
check("T6 regime-shifted sharpe_lift > 0", lift > 0)

# --- summary ----------------------------------------------------------------
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
exit(0 if passed == len(results) else 1)
