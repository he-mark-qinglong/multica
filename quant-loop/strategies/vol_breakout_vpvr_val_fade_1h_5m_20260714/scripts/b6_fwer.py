"""V10-B6: FWER Bonferroni correction across m=4 family.

Block-bootstrap one-sided empirical p-value (H0: Sharpe = 0).
Writes: results/v10/fwer.json
"""
import csv
import json
import math
import random
from pathlib import Path

ROOT = Path("/home/smark/multica/quant-loop/strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714")
TRADES = ROOT / "results/v10/trades_BTCUSDT.csv"
OUT = ROOT / "results/v10/fwer.json"

SEED = 42
N_RESAMPLES = 10000
BLOCK_SIZE = 5
M_FAMILY = 4
ALPHA_RAW = 0.05
ALPHA_BONFERRONI = ALPHA_RAW / M_FAMILY


def load_returns():
    rets = []
    with open(TRADES) as f:
        for row in csv.DictReader(f):
            rets.append(float(row["pnl_pct"]))
    return rets


def block_bootstrap(rets, n_resamples, block_size, seed):
    """Returns resampled series; sample-with-replacement from blocks of length block_size."""
    rng = random.Random(seed)
    n = len(rets)
    n_blocks = math.ceil(n / block_size)
    block_starts = list(range(0, n, block_size))
    out = []
    for _ in range(n_resamples):
        sample = []
        for _ in range(n_blocks):
            bi = rng.choice(block_starts)
            sample.extend(rets[bi:bi + block_size])
        out.append(sample[:n])
    return out


def sharpe_like(rets):
    n = len(rets)
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / max(n - 1, 1)
    sd = var ** 0.5 if var > 0 else 0.0
    return mean / sd if sd > 0 else 0.0


def one_sided_p_value(rets, n_resamples, block_size, seed):
    """Empirical p-value: P(Sharpe_sample >= Sharpe_obs | H0 centered at 0)."""
    obs = sharpe_like(rets)
    if obs == 0.0:
        return 0.5  # flat curve — undefined, return midpoint

    # Center at null: shift all returns so observed mean is 0
    centered = [r - (sum(rets) / len(rets)) for r in rets]

    rng = random.Random(seed)
    n = len(centered)
    n_blocks = math.ceil(n / block_size)
    block_starts = list(range(0, n, block_size))
    ge_count = 0
    for _ in range(n_resamples):
        sample = []
        for _ in range(n_blocks):
            bi = rng.choice(block_starts)
            sample.extend(centered[bi:bi + block_size])
        sample = sample[:n]
        s = sharpe_like(sample)
        # one-sided: H_a is "sharpe > 0"
        if s >= obs:
            ge_count += 1
    p = ge_count / n_resamples
    return p


def main():
    rets = load_returns()
    obs_sharpe = sharpe_like(rets)
    p_raw = one_sided_p_value(rets, N_RESAMPLES, BLOCK_SIZE, SEED)
    p_bonf = min(p_raw * M_FAMILY, 1.0)

    result = {
        "variant": "vol_breakout_vpvr_val_fade_1h_5m_20260714",
        "iteration": 74,
        "hypothesis": "Sharpe > 0 (one-sided)",
        "null": "Sharpe = 0 (block-bootstrap, samples shifted to null mean)",
        "alpha_bonferroni": ALPHA_BONFERRONI,
        "alpha_bonferroni_denominator": M_FAMILY,
        "alpha_bonferroni_numerator": ALPHA_RAW,
        "sharpe_observed": obs_sharpe,
        "p_value_raw": p_raw,
        "p_value_bonferroni_adjusted": p_bonf,
        "g7_pass": p_bonf <= ALPHA_BONFERRONI,
        "method": "block bootstrap one-sided empirical p-value, samples shifted to null mean",
        "n_resamples": N_RESAMPLES,
        "block_size": BLOCK_SIZE,
        "seed": SEED,
        "n_bars": len(rets),
        "family_size_m": M_FAMILY,
        "family_context": (
            "V10 (vol_breakout_vpvr_val_fade_1h_5m_20260714, iter#74) + "
            "V9 (vol_breakout_vpvr_confluence_4h_15m_20260714) + "
            "V11 (vol_breakout_vpvr_regime_blend_4h_20260714) + "
            "V13 funding reference. m=4."
        ),
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()