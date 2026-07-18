# SMA-34791 — VPVR level × price-reversal cross-check (20 samples)

**Verdict: weakens (point estimate only; n=20 is too small to confirm).**
Both HVN reversal rules show point estimates below the random baseline,
and the strict rule's CI overlaps the baseline only because the sample
is so small. This is *not* a proof VPVR-HVN is useless — it is evidence
that on this 30-day BTC slice, the SMA-34790 detector's HVN bands did
not behave like support as the funding-carry prototype hoped.

## Predeclared rules (recorded in SPEC.md before running)
- Universe: BTCUSDT 4h bars, last 30 days of `live_data/BTCUSDT_4h.parquet`
  (181 bars: 2026-06-11 → 2026-07-10).
- Detector: reuse `strategies/_indicators/vpvr_levels.py` (SMA-34790).
  Rolling 30-bar window per bar; bins=200, value_area=0.70,
  hvn_quantile=0.85, lvn_quantile=0.15, num_hvn=5, num_lvn=5.
- Sampling: chronological first 20 unique HVN/LVN levels (centres
  within 0.5% merged) that get touched within the next 12 bars (48h).
- Reversal (primary horizon = 12 bars = 48h):
  - **HVN_loose**: max_drawdown ≤ −0.5% AND max_upside ≥ +1.0%.
  - **HVN_strict**: max_drawdown ≤ −1.0% AND max_upside ≥ +2.0%.
  - **LVN_loose**: |max excursion| ≥ 1.0% either direction.
  - **LVN_strict**: dominant move ≥ ±1.5% with opposing ≤ 0.3%.
- Baseline: same 20 first-touch timestamps; random uniform price in
  `[low.min(), high.max()]` per bar (seed 20260717); same rules.

## Raw counts (results/samples.csv)

| metric | reversals | n | rate | Wilson 95% CI | baseline | baseline CI |
|---|---|---|---|---|---|---|
| HVN_loose  | 4  | 9  | 44.4% | [19%, 73%] | 50.0% | [30%, 70%] |
| HVN_strict | 1  | 9  | 11.1% | [2%, 44%]  | 25.0% | [11%, 47%] |
| LVN_loose  | 11 | 11 | 100%  | [74%, 100%] (degenerate) | 100% | [84%, 100%] |
| LVN_strict | 4  | 11 | 36.4% | [15%, 65%] | 20.0% | [8%, 42%] |

Δ vs baseline (samples): HVN_loose −6, HVN_strict −4, LVN_strict 0,
LVN_loose −9 (degenerate rule).

## What this means

1. **HVN support is not detectable.** Both HVN rules are *below*
   random baseline. The 4h detector's HVN bands — even the heaviest
   "score=1.0" bins — do not consistently arrest price within 48h on
   this slice. The single strict hit (sample 18, HVN @ $60,980 touched
   2026-06-25T12:00:00) coincides with the broader $60k→$60.7k bounce,
   not a clean isolated test.
2. **LVN_loose is degenerate** because BTC moves >1% in 48h essentially
   always; the rule fires on every random baseline too. Not informative.
3. **LVN_strict shows a positive point estimate** (36% vs 20% baseline)
   but the CIs overlap heavily (n=11). Worth a follow-up with more
   samples before drawing conclusions.
4. **Most of the "reversal" hits cluster around 2026-06-16 → 06-18**
   when BTC dipped from $65.7k → $62.2k and bounced. That single
   macro move drives many of the positive outcomes.

## What this does NOT mean
- This is n=20 on one symbol × one timeframe. The 95% CI on a binomial
  proportion at p≈0.5 is roughly ±22pp — wide enough that "weakens"
  is a point-estimate statement, not a statistically confirmed result.
- The detector, sampling, and rules are all faithful to SMA-34790 and
  the predeclared spec, but a single 30-day window on BTC may simply
  not contain enough "support test → bounce" structure to be visible.

## Implications for SMA-34656 / funding-carry entry filter
- The funding-carry prototype's HVN-as-support logic (work-pool.md
  line 45) is not supported by this cross-check. Before that family
  ships, it would need either (a) a much larger sample, (b) a
  different detector (e.g. POC rejection at value-area edge rather
  than raw HVN proximity), or (c) explicit acknowledgement that HVN
  bands are not a reliable entry filter on 4h BTC.
- The G1–G7 hard gates still apply; this evidence alone does not
  override the campaign-level profitability requirements.

## Artifacts (paths inside the workdir)
- `~/multica/quant-loop/strategies/vpvr_reversal_check_20260717/SPEC.md` —
  predeclared rules
- `~/multica/quant-loop/strategies/vpvr_reversal_check_20260717/run_cross_check.py` —
  reproducible script (uses the SMA-34790 detector, no re-implementation)
- `~/multica/quant-loop/strategies/vpvr_reversal_check_20260717/results/samples.csv` —
  20 (level, first-touch, forward outcome) rows
- `~/multica/quant-loop/strategies/vpvr_reversal_check_20260717/results/summary.json` —
  counts, rates, Wilson 95% CIs, verdict

Run `python3 run_cross_check.py` from the strategy dir to reproduce.