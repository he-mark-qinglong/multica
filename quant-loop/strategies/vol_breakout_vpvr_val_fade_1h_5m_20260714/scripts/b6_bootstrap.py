"""V10-B6: Bootstrap 95% CI on trade returns, 10000 resamples, seed=42.

Reads: results/v10/trades_BTCUSDT.csv (pnl_pct column).
Writes: results/v10/bootstrap_ci.json
"""
import csv
import json
import random
from pathlib import Path

ROOT = Path("/home/smark/multica/quant-loop/strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714")
TRADES = ROOT / "results/v10/trades_BTCUSDT.csv"
OUT = ROOT / "results/v10/bootstrap_ci.json"

SEED = 42
N_RESAMPLES = 10000
CI_LEVEL = 0.95


def load_returns():
    rets = []
    with open(TRADES) as f:
        for row in csv.DictReader(f):
            rets.append(float(row["pnl_pct"]))
    return rets


def bootstrap_sharpe_like(rets, n_resamples, seed):
    rng = random.Random(seed)
    n = len(rets)
    samples = []
    for _ in range(n_resamples):
        idxs = [rng.randrange(n) for _ in range(n)]
        sample = [rets[i] for i in idxs]
        mean = sum(sample) / n
        # SD of returns; protect against zero
        var = sum((x - mean) ** 2 for x in sample) / max(n - 1, 1)
        sd = var ** 0.5 if var > 0 else 0.0
        if sd == 0:
            sharpe_like = 0.0
        else:
            sharpe_like = mean / sd
        samples.append(sharpe_like)
    samples.sort()
    return samples


def percentile(sorted_samples, pct):
    n = len(sorted_samples)
    # Lower-indexed percentile (linear interp on lower side)
    pos = pct * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac


def main():
    rets = load_returns()
    n_trades = len(rets)
    samples = bootstrap_sharpe_like(rets, N_RESAMPLES, SEED)
    alpha = 1 - CI_LEVEL
    lo = percentile(samples, alpha / 2)
    hi = percentile(samples, 1 - alpha / 2)
    median = percentile(samples, 0.5)
    mean = sum(samples) / len(samples)

    result = {
        "n_trades": n_trades,
        "n_resamples": N_RESAMPLES,
        "seed": SEED,
        "ci_level": CI_LEVEL,
        "sharpe_like_mean": mean,
        "sharpe_like_median": median,
        "ci_lower": lo,
        "ci_upper": hi,
        "g6_threshold": 0.5,
        "g6_passed": lo >= 0.5,
        "trade_returns": rets,
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()