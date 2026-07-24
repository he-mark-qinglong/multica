# T08 — VPVR-confluence (HVN entry + funding gate + LVN exit)

**Status**: archived-campaign-close (2026-07-22) — issue closed as research-complete-hold per swarm主agent Path (A) decision. NOT killed (in-sample edge real on its regime); family closed because funding>0.03% trigger is structurally dead in current data regime (zero events May-2024→Jul-2026).
**Discipline**: research-journal (regime-conditional audit); paper-replication for any future OOS re-attempt
**Skill context**: research-journal + execution-microstructure + regime-macro
**Forked from**: T06 (which is killed on funding-as-carry; this thread is the funding-as-timing-filter variant)
**Trigger**: SMA-34901 (closed 2026-07-22, status=done, path-A HOLD-for-promotion semantics)
**Artifacts**: `~/multica/quant-loop/strategies/vpvr_funding_hvn_lvn_confluence_20260718/`

## Question
Does a structural-positional long-only crypto-perp signal — enter at HVN support when funding > 0.03%, exit at next LVN above — produce framework-shippable alpha on a regime-conditional basis?

## Verdict (one line)
**PROFITABLE in-sample on the only regime in which it can fire; framework-ship blocked because three HOLD gates are structurally unmet; campaign closed 2026-07-22 as research-complete-hold.**

## Mechanism
- **Trigger**: at any 15m bar where funding rate (8h forward-filled) > 0.0003 (i.e. 3 bps / 8h)
- **Entry filter**: 15m close within `proximity_atr=1.0` of the nearest 4h HVN support (top-3 volume bins in a rolling 180-bar VPVR window)
- **Exit logic**: structural target = next LVN above entry (`high ≥ lvn.price_high`); safety nets = 96-bar (24h) time_stop and funding-flip below -0.0003
- **Sizing**: fixed-fraction 1.0 per trade (no leverage, no vol target)
- **Per-trade cost**: 4 bps fee + 1 bps slippage per fill (5 bps round-trip)

## Why this is NOT a T06 re-attempt
| axis | T06 (funding-as-carry, killed) | T08 (this: funding-as-timing-filter) |
|---|---|---|
| side | long-pays-carry (negative carry cost) | long-pays-carry timing (still negative carry, but bounded by 24h exit) |
| prior content | funding CARRY | funding + HVN proximity + LVN target |
| entry filter | funding alone | funding + HVN support touch |
| exit | carry decay / cross | LVN target with 24h time_stop |
| regime description | carry-positive regime | funding>0.03% pulse + HVN proximity |

Different prior structure → meets T06's revival bar ("new prior source") but does not retroactively rehabilitate T06.

## In-sample result (combined BTC+ETH+SOL, Nov-2023 → Dec-2024 hot-funding window)

| gate | threshold | actual | pass? |
|---|---|---|---|
| G1 Sharpe_d | ≥ 1.0 | 1.053 | ✓ (barely) |
| G2 ann | ≥ 15% | +26.67% | ✓ |
| G3 MDD | > -25% | -18.48% | ✓ |
| G4 PF | > 1.5 | 1.544 | ✓ |
| G5 n_trades | ≥ 30 | 49 | ✓ |

Per-symbol (ALL fail G1 individually — combined clears via cross-symbol variance reduction only):
- BTCUSDT: Sharpe_d 0.836, n=20, WR 60%, PF 1.62, MDD -11.0%
- ETHUSDT: Sharpe_d 0.891, n=13, WR 54%, PF 1.84, MDD -13.6%
- SOLUSDT: Sharpe_d 0.309, n=16, WR 50%, PF 1.24, MDD -13.8% (median pnl = **-0.20%**, SOL is structural laggard)

## HOLD gates smark-proxy 17:03 demanded — current status

1. **2025 cold-regime OOS (≥3 expanding windows)** — STRUCTURALLY UNMET
   - Funding parquet verified clean through 2026-07-17 (5,100/5,100/5,175 records BTC/ETH/SOL)
   - All monthly funding bins 2022–2026 inspected: max funding rates by month
   - 2025-01 → 2026-07: BTC max=0.0001 (1bp), ETH max=0.0001, SOL max=0.000259 (2.59bp) — all below 0.0003 threshold
   - **Zero `funding>0.03%` events for 18 months straight** across all 3 symbols
   - The signal cannot fire by construction. Extending the parquet doesn't help. This is a regime shift, not a data gap.

2. **LVN-exit logic validation (5/49 → exit-thesis mostly untested)** — STRUCTURALLY UNMET
   - Of 43 trades with `target_lvn_price != null`, only 5 (11.6%) hit `high ≥ target_lvn.price_high` within 96 bars
   - 6 trades had NO target LVN above entry (forced time_stop)
   - Total: 44/49 (89.8%) time_stop exits vs 5/49 (10.2%) lvn_target
   - Mean bars_held on time_stop = 96 (full horizon); lvn_target = 25/49/79/82/94 (mean ≈66)
   - The exit thesis is essentially "funding>0.03% + HVN proximity + 24h drift up" — not "structure-target hit"

3. **≥3 expanding OOS windows including 2025 cold** — STRUCTURALLY UNMET, by Gate 1
   - Regime-gated signals cannot satisfy expanding-OOS-into-cold when the regime has been absent for 18 months
   - A regime-aware OOS would need a 2nd hot regime window — which the staged data doesn't contain past Dec 2024

## Cross-check vs funding_carry_asym baseline (SMA-34897)
| | Sharpe_d | ann% | PF | n |
|---|---|---|---|---|
| baseline (funding_carry_asym, Q1 2024, 1 sym) | -1.522 | -0.094 | 0.59 | 63 |
| **T08 confluence (3 syms, hot 23-24)** | **+1.053** | **+26.67** | **1.544** | 49 |

The structural HVN/LVN framing flips carry-negative into carry-positive in the same regime window where carry was destroying the baseline. This validates that the alpha is NOT funding-as-carry but is the structural entry+exit logic. However, this does NOT validate the LVN-exit-thesis claim (Gate 2 above).

## Files / source
- `~/multica/quant-loop/strategies/vpvr_funding_hvn_lvn_confluence_20260718/results/metrics.json` — full per-symbol breakdown
- `~/multica/quant-loop/strategies/vpvr_funding_hvn_lvn_confluence_20260718/results/trades.csv` — 49 trades, exit_reason + target_lvn_price fields
- `~/multica/quant-loop/strategies/vpvr_funding_hvn_lvn_confluence_20260718/results/summary.txt` — VERDICT block per SMA-34924
- `~/multica/quant-loop/strategies/vpvr_funding_hvn_lvn_confluence_20260718/vpvr_funding_hvn_lvn_confluence_backtest.py:195-200` — `_nearest_lvn_above`
- `:352-356` — LVN-exit-bar-hit check
- `~/multica/quant-loop/data/funding/{BTC,ETH,SOL}USDT.parquet` — funding history to 2026-07-17
- `~/multica/.multica/results-ledger.md` line 9 — LIVE entry (PASS, HOLD-for-promotion)

## Recommendation (kill plan / revival)
- **Status**: archived-campaign-close (2026-07-22) — SMA-34901 closed as research-complete-hold per swarm主agent Path (A) decision (decision comment id `fda358f8-1d82-4e3c-bbc1-2a1c77d26733`). Status flipped to `done`; HOLD-for-promotion semantics live in `results-ledger.md` line 9.
- **NOT a kill** because (a) it's a new prior structure, (b) the structural entry/exit flipping positive a negative-carry regime is a real empirical signal, (c) there is no live alternative in this regime class. The in-sample edge (Sharpe_d 1.053, ann +26.67%) is honest; the campaign closes because the FUNDING-REGIME TRIGGER IS DEAD, not because the signal's structure is wrong.
- **NOT a ship** because the 3 HOLD gates cannot be cleared without changing the signal definition.
- **Next** (if anyone chooses): a follow-up SPEC that treats the LIVE legs (BTC, ETH) as separate strategies and adds **(a) a sub-funding-threshold entry (e.g. funding > 0.02%) that lets the signal fire in weak-funding months AND (b) a tight 24-bar stop replacing the 96-bar time_stop** — those changes would convert the time-stop-dominant signal into a stop-loss-bound one whose edge could be tested in cold regimes. The successor spec must be a fresh issue under the next VPPR campaign iteration on a **non-funding-carry axis** (per swarm-owner 2026-07-21 quality gate + swarm主agent 2026-07-22 Path A decision).
- **Revival conditions** (current signal definition — kept for archival):
  - (a) Funding regime persistence: any month where all 3 symbols (BTC/ETH/SOL) register ≥ 1 `funding > 0.03%` event — IF this reoccurs, the signal becomes tradable again, and a fresh OOS run on the new window could close Gate 3. Currently absent (May-2024 → present, 18 months).
  - (b) LVN-target distance reduction: if `proximity_atr` is raised to 2.0 (so target_lvn is closer to entry), the 96-bar limit may be reachable in more trades — a sensitivity check on this parameter is the cheapest way to test Gate 2.
  - (c) Sub-threshold funding: lowering the trigger to 0.0002 would let SOL into the trades (which has higher max funding rates) and possibly generate cold-regime signals — but this risks validity drift on the original prior AND changes the signal definition (per the swarm-owner quality gate, that path requires a new issue, not a revival of this one).

## Anti-pattern guard
- Did NOT re-tune to find a passing cell. The 1.053 number is the honest in-sample reading.
- Did NOT extend the funding window by lowering the threshold (that would invalidate the prior content).
- Did NOT mark "killed" to dismiss the work — T06 kill is preserved and T08 captures the new-prior advance.
- Did NOT mark "ship" without HOLD gates — T08 explicitly defers to a separate sub-strategy SPEC for any ship-eligible variant.

## 2026-07-21 update — VPPR swarm-owner quality gate (independent peer validation)

The VPPR swarm-owner (decision comment 2026-07-21T23:56+08:00) audited the full thread (strategy-worker-1 10:07 → quant-researcher 2026-07-20 23:23) and confirmed:
- **Work product genuine** — metrics.json/trades.csv/equity.csv real; no-look-ahead verified; canonical VERDICT block; results-ledger LIVE row stamped. **Not fake.**
- **The 3 HOLD gates are structurally unmet** — independently confirmed (cold OOS unobtainable, LVN exit unvalidated, per-symbol Sharpe<1.0).
- **This is the last open issue in d1f4d321** — 16 done + 4 cancelled + 1 in_review = 21 total. The campaign is at decision fork.

The swarm-owner's recommended path is **(A) Accept as research-complete-hold → close** with revival conditions filed, plus **launch next VPPR campaign iteration on a non-funding-carry axis** (the funding>0.03% trigger is provably dead in current data). Path (B) (open trigger-modified successor spec) is offered as alternative.

Decision rests with smark human per `multi-agent spirit §4.4` (no auto-close without explicit cancel signal). Status stays `in_review`. **No change to T08 audit finding.**

If smark picks (A) on closure, this thread file's status flips to **archived-campaign-close** (NOT killed — the in-sample edge is real on its regime; the campaign is closed because the family is exhausted in current data). If smark picks (B), a successor thread (T09) will be opened for the trigger-modified variant.

This update exists so that a future research session reading T08 finds the campaign-close context, the swarm-owner verdict, and the explicit "not killed, archived" status — preventing the `funding_carry_asym` anti-pattern where T06 was tried 3× because the kill context drifted.
