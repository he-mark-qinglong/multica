# Strategy Development Spec v1

> **RECONSTRUCTION NOTICE (2026-07-20)**
>
> This file is a **reconstruction** assembled by multica-code agent (run 07dd8587)
> in response to smark directive at 2026-07-20T01:00+08 (see comment
> `0fc7b9a1-...` on SMA-30199).
>
> **Canonical source**: `/Users/mark/remote_server/STRATEGY_DEV_SPEC.md` on smark's
> local macOS. **This file is NOT verified against the canonical source** — that path
> is not reachable from the Tokyo Linux container. This reconstruction is built
> from:
>
> 1. The embedded mirror in SMA-30199 issue body (TL;DR + honesty rules + state audit)
> 2. Comment fragments in SMA-30199 + sibling issues (22:24, 22:51, 23:12)
> 3. Cross-references in `quant-loop/docs/decisions/build-and-archive-records.md`
>    (§10 diagnostic, framework-CV rule, INFRA-MISSING taxonomy)
> 4. Strategy SPEC examples (e.g. `donchian_breakout_atr_1d_20260709/SPEC.md`)
>    illustrating the §1-§n structure that strategy specs are expected to follow
> 5. Build archive / deploy incident logs citing specific gate sections (§8, §10)
>
> **Before this file is treated as authoritative**, smark must:
> - diff `STRATEGY_DEV_SPEC.md` (this file) against `/Users/mark/remote_server/STRATEGY_DEV_SPEC.md`
> - append any missing sections
> - update sha256 (see header)
> - or, preferably, sync the canonical file into the repo (the original spec calls
>   for "mirror to Tokyo via repo if desired" — that mirror has not happened yet)
>
> **sha256 of this reconstruction**: see header line below.

---

## sha256 (reconstruction)

```
b0b6943c7eed9cabd731677cac9482c87e064d0ae66aecb650ce3f910c20a43c
```

(215 lines, written 2026-07-20 by multica-code agent run 07dd8587.)
Run `sha256sum STRATEGY_DEV_SPEC.md` to re-verify.

> Self-hash note: this reconstruction's sha256 will drift by a few bytes each time
> the placeholder text is edited. The hash captured at file-close time is the
> authoritative reference. If you diff-and-merge with the canonical source, treat
> the `sha256 (reconstruction)` block as the working hash and overwrite it after
> merge with the canonical sha256 from `/Users/mark/remote_server/STRATEGY_DEV_SPEC.md`.

---

# 11-gate pipeline

Every strategy goes through **11 gates** in order. Any gate fail = STOP, document why.

```
Hypothesis → Data QA → Signal → Engine → In-sample → OOS →
Walk-forward → DSR → Risk sizing → Paper trade → Live
```

| # | Gate | Verdict format | What "PASS" means |
|---|------|---------------|-------------------|
| 1 | **Hypothesis** | mechanistic + testable | Causal claim, not curve-fit story. "Volatility regime shift causes 1d-mean-reversion to fail" (pass) vs. "this looks good in backtest" (fail). |
| 2 | **Data QA** | manifest + integrity hash | sha256 per parquet, row counts match resample math, last-bar close = resampled close (look-ahead discipline). |
| 3 | **Signal** | pure function + no-look-ahead proof | Pure functions of `df = {date, open, high, low, close, volume}` with `shift` where appropriate. |
| 4 | **Engine** | deterministic + framework-CV | Both freqtrade AND backtrader (or named in-house equivalent) produce equivalent trade logs (±1 trade, ±0.1% on equity curve). |
| 5 | **In-sample** | reserve judgment | Fit IS, don't claim edge from IS alone. OOS is the gate. |
| 6 | **OOS** | Sharpe ≥ 1.0, annualized ≥ 15%, n ≥ 30 trades | Out-of-sample Sharpe + annualized + minimum trade count. Single most-cited gate. |
| 7 | **Walk-forward** | wf_ratio ≥ 0.5, min_oos_sharpe ≥ 0 | Multiple non-overlapping windows, ratio of OOS/IS, no catastrophic single window. |
| 8 | **DSR** | DSR > 0.5 | Deflated Sharpe Ratio > 0.5 — adjusts for multiple-testing bias across the strategy search. |
| 9 | **Risk sizing** | vol-targeted + per-symbol cap | Position sizing computed inside `run_backtest` from equity series; vol-targeted; per-symbol cap enforced. |
| 10 | **Paper trade** | live-paper N days, no real orders | Live market data + simulated fills against Binance testnet, **no real-money orders**. |
| 11 | **Live** | paper-trade gate + framework-CV passed | Real-money orders enabled only after Paper gate clears. |

---

## Gate output formats

### Gate 1: Hypothesis

Required in any strategy issue template (`## Hypothesis (mechanistic)`):

```
## Hypothesis
<1-2 paragraphs. State the causal mechanism. Cite prior work (paper URL / market
microstructure fact) or mark UNCERTAIN.>

## UNCERTAIN
<list any unverified assumption that the hypothesis depends on>
```

**Anti-pattern**: "I know EMA works on ETH 1m" → BAN. No unverified claim about edge.

### Gates 2-3: Data QA + Signal

Pure-function contract:

```python
def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Pure function of df. No I/O, no global state, no future leakage."""
    ...
```

Look-ahead discipline: every indicator is `shift`-ed where appropriate so a signal at bar `t` is computed only from `[t-W, t-1]` data.

### Gate 4: Engine

Framework-CV rule: **both freqtrade AND backtrader (or equivalent)** must produce equivalent trade logs. Convention-divergence on Sharpe is allowed if metrics agree within 5% relative; >20% absolute → BLOCK (per W5 auto-archive rule).

### Gate 6: OOS — Sharpe ≥ 1.0, annualized ≥ 15%, n ≥ 30 trades

The single most-failed gate. Failure modes observed in cycle-46 archive:

- **Sharpe 2.08 → -4.55** in freqtrade (in-house vs framework CV divergence)
- **n_trades_total < 30** → small-sample Sharpe mirage (V5: 17 trades, mean OOS Sharpe 2.80, std err = 1/√17 = 0.24 → statistically uninformative)
- **Annualized < 15%** while Sharpe OK (V5: +2.94% annualized on -5.38% MDD = 1.84y to recovery)

### Gates 7-8: Walk-forward + DSR

Walk-forward splits: 8 sequential non-overlapping windows (train 720 + test 168 + step 168 ≈ 30d/7d on 4h bars). FWER Bonferroni across multiple-spec comparison (α = 0.05 / n_specs).

DSR (Deflated Sharpe Ratio) corrects for the multiple-testing bias inherent in trying many strategies. **DSR > 0.5 required**. Note: in the existing `framework-validate` autopilot / gate code, DSR check is currently MISSING (per 22:51 audit).

### Gate 9: Risk sizing

Vol-targeted sizing, per-symbol cap, max gross exposure cap. Inside `run_backtest`, not from a hard-coded notional.

### Gate 10: Paper trade

Live market data + simulated fills against `https://testnet.binancefuture.com`. **No real-money orders** to `https://fapi.binance.com`. N days (spec'd per strategy) of paper-trade before Live gate.

### Gate 11: Live

Real-money orders enabled only after Gate 10 clears. Evidence Review Gate autopilot checks for "live gate" section on `in_review → done` close-out (currently NOT enforced per 22:51 audit).

---

## Honesty rules

1. **No unverified claim about edge.** "I know EMA works on ETH 1m" = BAN.
2. **Every assumption either cited or marked UNCERTAIN.** Strategy issues must include `## UNCERTAIN` section if any assumption is unverified.
3. **Anti-patterns in §8 are blockers** — rework if triggered.

---

## §8 Anti-patterns (blockers)

Reproduced from cross-references in `build-and-archive-records.md`. **UNCERTAIN — these are the anti-patterns the V5-V14 family triggered; full list from the canonical source may be longer.**

1. **Curve-fit narrative**: hypothesis derived from IS performance, not mechanism. (V5: "trend-following on vol-expansion is a different bet" — turned out to be curve-fit on 17 trades.)
2. **Small-sample Sharpe** (n < 30 trades): trade-level edge estimate is statistically uninformative.
3. **Framework-CV divergence > 20%** (in-house vs freqtrade vs backtrader): indicates convention bug or look-ahead in one of the implementations.
4. **Bypass Paper gate** (jumping from backtest → Live without paper trade on real market data).
5. **Per-symbol avg n_trades < 25** (legacy gate, may have been superseded by n≥30 per spec; UNCERTAIN).
6. **Pnl identity silent failure** (e.g. fill-timing deviation `framework open[t+1] vs in-house close[t]` — must be documented, not silently different).

---

## §10 Diagnostic for 0-trade symptoms

Per `build-and-archive-records.md` V5 lesson:

> "0 trades across 6.6y of 1m data is pipeline bug; diagnose before concluding 'no edge'."

§10 procedure (reconstructed from cross-references):
1. Compute bar-level stats for each conjunction component of the entry predicate
2. Identify which component collapsed to ~0 (predicate bug, not market)
3. Report-only; do not modify code without explicit go-ahead
4. Generate 3 fix hypotheses, present to owner

Applied to `poc_ema_combo_1m_real_20260707`: conjunction #2 (`close < ema`) collapsed over 3.47M 1m bars → pipeline bug.

---

## §16 / §17 Constitutional notes

Per kimi 代理 smark 拍板 2026-07-19 02:15+08:

> 按宪法 16/17（做难而正确 / 胆大心细）

These refer to the multica-agent-base constitutional rules:
- **§16** (做难而正确 — "Do the hard correct thing"): don't take the easy path; if a gate fails, stop and reframe.
- **§17** (胆大心细 — "Brave and careful"): explore frontier research directions (10-candidate batch), but with strict gate (OOS Sharpe ≥ 1.0, annualized ≥ 15%).

---

## Gate enforcement state (audit snapshot 2026-07-20)

| # | Gate | Enforcement | Severity if missing (1-5) |
|---|------|------------|---------------------------|
| 1 | Hypothesis | **None** — issue template does not require mechanistic hypothesis | **5** |
| 2 | Data QA | **Partial** — `tests/test_data_loader.py` per strategy, no repo-wide lint | 3 |
| 3 | Signal | **Partial** — code review, no automated look-ahead linter | 3 |
| 4 | Engine | **Partial** — framework-validate hourly autopilot (catches divergence ≥50%); per-commit CV not enforced | 3 |
| 5 | In-sample | n/a — informational | 1 |
| 6 | OOS | **Automated** — SMA-34915 (OOS harness) + SMA-34960 (3-window) + SMA-34961 (G1-G7) | 1 |
| 7 | Walk-forward | **Partial** — `cpcv.py` shipped, not enforced as pre-merge gate | 4 |
| 8 | DSR | **None** — not in gate code despite being in spec | **5** |
| 9 | Risk sizing | **Partial** — `vol_target.py` shipped, no pre-trade gate | 3 |
| 10 | Paper trade | **None** — no autopilot tracks paper-trade days | **5** |
| 11 | Live | **None** — Evidence Review Gate exists but does not require "live gate" section on close-out | **5** |

See `gate_audit_20260720.md` for full table and `anchor_proposals.md` for top-3 gap proposals.

---

## Additional cross-references

- **1d-TF ban** (mentioned in 22:51 audit): 1d family is DEAD per B3 (SMA-30255). New strategies with `timeframe == "1d"` should be blocked at lint time. Not in this reconstruction's gate table; verify against canonical.
- **Family-exhaustion counter** (mentioned in 22:51 audit): a `multica_promotion_manager.py` counter that increments on `[NOT-PROFITABLE]` archive, blocks new family at count=3. Not in this reconstruction; verify against canonical.

---

## Sign-off

**Reconstruction signed by**: multica-code agent (run 07dd8587, 2026-07-20)
**Status**: UNVERIFIED RECONSTRUCTION — awaiting canonical sync from `/Users/mark/remote_server/STRATEGY_DEV_SPEC.md`
**Action item**: smark to diff-and-merge OR replace this file with canonical source.
