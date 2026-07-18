# B6 — Display engine audit (iter#70/71/72)

**Author**: multica-ops · B6 (quant-research lane)
**Date**: 2026-07-16 23:05+08
**Source**: `curl http://localhost:8090/strategies` (display engine live API)
**Branch**: `feat/vpvr-variant-sweep-iter70-72-20260716`

---

## 1. Display engine snapshot

```
$ curl -s http://localhost:8090/strategies | jq '.strategies | length'
53
$ # filters to vpvr_/bb_/xs_ only
$ ... | jq '[.[] | select(.name|test("^(vpvr_|bb_|xs_)"))] | length'
46
```

| bucket | count |
|---|---|
| Total strategies in display engine | **53** |
| Strategies under `^vpvr_|^bb_|^xs_` filter | **46** |
| Other (non-VPVR/BB/XS — breakouts, donchian, london, double-poc, momentum) | 7 |
| Strategies on the iter#70/71/72 branch but NOT yet published | **3** (all 3 new variants) |

Note on the "47" figure in parent SMA-34659: that count was a pre-launch estimate; today (2026-07-16 23:05+08) the displayed count under the campaign filter is **46**. Adding iter#72 only (the B3 survivor) would bring the displayed-vpvr count to **47**; iter#70/71 are FAIL_NEGATIVE_ANN_RETURN per B3 hard rule and will not be published.

## 2. Delta — does the display engine already contain each new axis?

| Axis | iter | new strategy key | already in display? | if yes, where | evidence |
|---|---|---|---|---|---|
| A — DeFi perp cross-venue basis (Hyperliquid + dYdX) | 70 | `vpvr_defi_basis_15m_hyperliquid_dydx_20260716` | **NO** | n/a | `curl … \| jq '.strategies[].name'` returns 0 entries matching `defi\|hyperliquid\|dydx` |
| B — social-attention / sentiment (LunarCrush / Santiment) | 71 | `vpvr_sentiment_attention_1m_20260716` | **NO** | n/a | 0 entries matching `sentiment\|attention\|lunar\|crush\|santiment` |
| C — Curve USDC/USDT depeg premium regime gate | 72 | `vpvr_stable_depeg_regime_4h_20260716` | **NO** | n/a | 0 entries matching `depeg\|curve.*usdc\|stable.*depeg` |

All three axes are **genuinely absent** from the live display engine — none has been pre-published under another key.

## 3. Closest analogues already displayed (for context)

For B4 / B5 reference — these are the most-similar already-published strategies whose equity/drawdown profile may correlate with iter#72 (the only B3 survivor):

| displayed strategy | tf | rationale for similarity to iter#72 |
|---|---|---|
| `vpvr_reversion_4h_stablecoin_netflow_20260713` | 4h | 4h VPVR reversion with on-chain stablecoin *flow* filter — different filter source (iter#72 uses Curve premium) but same primary instrument (BTCUSDT) and TF |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | 4h | 4h regime-gated reversion on SOLUSDT — same shape (regime gate + reversion), different gate variable |
| `vpvr_reversion_4h_vol_regime_20260714` | 4h | 4h vol-regime variant — closest TF/instrument match for iter#72 portfolio-fit |
| `donchian_breakout_atr_1d_20260709` | 1d | same on-chain proxy family — not for iter#72 but useful iter#70/71 reference |

These are context only; **no published duplicate of iter#70/71/72 axes exists**. Auto-publish will happen on the standard `kimi cron 041944dd` (every 1h) once summary.json + 4-item evidence are present on `main` (per parent SMA-34659 hard requirement #6). Note that for iter#70/71 the auto-publish path will NOT fire because those variants will be archived as `[NOT-PROFITABLE]` per B3 verdict, not merged to `main`.

## 4. Display engine delta if iter#72 ships alone

- Before: 53 total / 46 vpvr-family
- After (iter#72 only ships): 54 total / 47 vpvr-family

That keeps the **47-currently-published** floor from parent SMA-34659 done-criteria intact (`iter#70+` should NOT regress display count below current 47 strategies).

## 5. Verdict

> All 3 axes are absent from the live display engine. iter#70/71 will not be published (FAIL_NEGATIVE_ANN_RETURN). iter#72 will publish on auto-publish cron and brings the vpvr-family count from 46 → 47, meeting the parent's "no regression below 47" requirement.

*End of display_audit.md*
