# V11 (iter#75) Code Review + Hard-Gate Enforcement

**Variant:** `vol_breakout_vpvr_regime_blend_4h_20260714`
**Strategy class:** `strategies/team/combo/vol_breakout_vpvr.py::VolBreakoutVPVRRegimeBlend4h`
**Test class:** `tests/unit/test_vol_breakout_vpvr.py::TestVolBreakoutVPVRRegimeBlend4h*`
**Worktree:** `/home/smark/multica_workspaces/f9a9d34e-b809-4564-b0c0-b781a70a3f25/42a03459/workdir/trading` (branch `agent/indicator-engineer-clone-17/42a03459`)
**Reviewer:** code-reviewer agent `aefe1356-9356-42c6-ad58-d2cd23009d76`
**Review date:** 2026-07-14
**Review issue:** [SMA-33964](mention://issue/22ec4983-1eb5-4900-a030-27b3615c2168)

---

## Verdict: **REJECTED** — `[INSUFFICIENT-EVIDENCE] + [CODE-DEFECTS]`

Two independent reasons, both of which by themselves block approval:

1. **No backtest / walk-forward evidence exists.** V11-B3 (`SMA-33962`) is still `in_progress`. V11-B6 has not been created (not in the in_progress list). The B7 spec explicitly says "Predecessor: V11-B6 walk-forward (must show all gates passed)" — that condition is not satisfied.
2. **Two critical code defects** in `regime` and `trend` axes — both depend on upstream contract keys that are NOT produced by any module in the worktree. Without upstream producers, the backtest will produce zero trades (silent None returns).

Plus three majors and three minors — see [Code review findings](#code-review-findings) below.

---

## Hard-gate enforcement (G1-G7)

| gate | threshold | result | evidence |
|------|-----------|--------|----------|
| G1 | Sharpe (full-backtest mean) ≥ 1.0 | **FAIL** | no_backtest_results |
| G2 | min(annualized_full, mean_OOS_annualized) ≥ 15% | **FAIL** | no_backtest_results |
| G3 | profit_factor > 1.5 | **FAIL** | no_backtest_results |
| G4 | max_drawdown < 25% | **FAIL** | no_backtest_results |
| G5 | framework CV OOS walk-forward Sharpe ≥ 1.0 BOTH freqtrade AND backtrader | **FAIL** | no_walk_forward_results |
| G6 | bootstrap 95% CI lower ≥ 0.5 | **FAIL** | no_bootstrap_results |
| G7 | FWER Bonferroni α=0.0125 | **FAIL** | no_bonferroni_results |

**0/7 gates passed.** Per the B7 spec: "Below any gate: status = done with [NOT-PROFITABLE] verdict. Do NOT advance."

**Predecessor state (verified by `multica issue list --status in_progress`):**

```
SMA-33964 (V11-B7, this review)  in_progress
SMA-33962 (V11-B3, backtest)     in_progress  ← still running
[V11-B6 walk-forward]            NOT FOUND in in_progress list  ← never started
```

The spec says "Predecessor: V11-B6 walk-forward (must show all gates passed)". Since B6 has not been started, the predecessor condition fails structurally — we cannot pass B7 even if B3 produces a miracle result, because cycle-45 lesson V7: "in-sample != OOS Sharpe (full-period overfit)" and G5 explicitly requires both freqtrade AND backtrader OOS walk-forward.

---

## Extra scrutiny for V11 (per the issue brief)

### 3-axis contribution (trend + regime + vpvr POC)

- **Result: PARTIAL** — code implements 3-axis gate, not a blend. Per-axis attribution (ablation) was not provided.
- All three axes are hard-AND conditions (any single failure → return None). The "blend" terminology is misleading. To prove the regime axis is non-redundant, an ablation study (V11 with `regime_min_atr_pct = 0`) is required. None was provided.
- The regime axis fires BEFORE the vol_breakout check (line 555-557), so it could be doing real work, but this is unverified.

### Asymmetric RR 1:2 (stop=1.0 ATR, target=2.0 ATR)

- **Result: PASS_CODE_LEVEL** — `vol_breakout_vpvr.py:508-509` defines `stop_loss_atr=1.0, take_profit_atr=2.0`. Test `test_long_when_all_three_axes_aligned` (line 314-317) verifies the produced Signal has RR in (1.8, 2.2). Asymmetric RR 1:2 is correctly implemented at the strategy level.
- However, this only verifies the *static config*. Without backtest results, we cannot confirm that 1:2 is profitable in the realized distribution. Tail-risk events (extreme ATR spikes) could blow through the 1.0 ATR stop, making realized RR worse than nominal. This needs empirical validation in B3.

### Multi-symbol consistency (BTCUSDT / ETHUSDT / SOLUSDT)

- **Result: FAIL_NO_EVIDENCE** — Strategy class has no per-symbol logic; it runs identically on all three symbols. Without per-symbol backtest results, BTC/ETH/SOL divergence cannot be evaluated. The campaign spec says "multi-symbol BTC/ETH/SOL all required" but B3 has not produced per-symbol results.

---

## Code review findings

### Critical (must fix before any ship)

**CR-1: `regime_atr_pct` has no producer in the worktree**
- File: `strategies/team/combo/vol_breakout_vpvr.py:555`
- V11 reads `data.get("regime_atr_pct")` to gate the regime axis. A worktree-wide grep finds this symbol ONLY in the test file and the strategy file — no `src/` module computes or injects it. When B3 runs against the real indicator pipeline, every bar will silently return None (line 556: `if regime_pct is None`).
- Impact: zero trades on V11. The regime axis is dead-on-arrival in production wiring.
- Fix (recommended): compute `regime_atr_pct` from `df['atr'].rolling(N).rank(pct=True).iloc[-1]` inside the strategy. See concrete replacement in [gate_check.json](gate_check.json) `concrete_modification_suggestions[0]`.

**CR-2: `higher_ema_50` column has no producer in the worktree**
- File: `strategies/team/combo/vol_breakout_vpvr.py:230, 362, 502, 550` (all three variants)
- `higher_tf_trend(df, ema_col="higher_ema_50")` reads `df.iloc[-1].get(ema_col)`. The string `higher_ema_50` is not produced by any module under `src/` in this worktree. Like CR-1, the trend axis will silently fail and produce zero signals.
- Impact: same as CR-1 — zero trades. Trend axis is dead-on-arrival in production wiring.
- Fix (recommended): compute the higher-TF EMA inside the strategy. With 4h trend on 15m entry, that's `ema = df['close'].ewm(span=16, adjust=False).mean().iloc[-1]`. See concrete replacement in [gate_check.json](gate_check.json) `concrete_modification_suggestions[1]`.

### Major

**MR-1: '3-axis blend' is actually a 3-axis AND-gate, not a blend**
- File: `strategies/team/combo/vol_breakout_vpvr.py:536-585`
- The docstring claims "3-axis blend" but the implementation is sequential AND: `trend != None AND regime_pct >= threshold AND vol_breakout fires AND POC proximity`. A blend implies weighted attribution or confidence fusion. The current logic cannot show per-axis contribution because all axes are hard gates.
- Impact: cannot prove the regime axis is non-redundant. Could be that trend + vol_breakout + POC alone would give identical Sharpe. The campaign's "extra scrutiny on whether regime blend degenerates to single-axis" is unanswerable without ablation.
- Fix: rename to "3-axis gate" OR implement `confidence = base * f_trend * f_regime * f_poc` where each `f` is in (0, 1], then ablate each axis to measure contribution.

**MR-2: `regime_atr_pct` read once — no persistence/stability check**
- File: `strategies/team/combo/vol_breakout_vpvr.py:555-557`
- A single-point threshold check is susceptible to regime flicker. A 1h ATR percentile of 0.4 can be a momentary spike that reverses next bar, causing the strategy to enter on a regime-confirmed bar and exit on a regime-denied bar. A persistence window is standard practice and missing here.
- Fix: add `regime_persistence_bars` (default 3) and require the threshold to hold for the last K bars.

**MR-3: signal_id collision risk at millisecond resolution**
- File: `strategies/team/combo/vol_breakout_vpvr.py:588, 316, 456`
- `f'..._{int(datetime.utcnow().timestamp() * 1000)}'` collides on burst signals (multiple symbols in the same ms). Downstream backtest typically dedupes by signal_id, which silently drops one of the signals.
- Fix: append symbol + monotonic counter. See concrete replacement in [gate_check.json](gate_check.json) `concrete_modification_suggestions[2]`.

### Minor

**MN-1:** `datetime.utcnow()` called twice in same Signal (lines 588, 592). Use a single `ts` variable.

**MN-2:** Default `poc_k_atr=0.7` (line 507) is tight. No sensitivity report. Recommend k_atr sweep {0.5, 0.7, 1.0, 1.5} in B3 deliverables.

**MN-3:** No explicit symbol-availability guard. `data.get('symbol', 'UNKNOWN')` (line 591) should be `if not data.get('symbol'): return None` early in generate_signal.

---

## KISS / DRY / SOLID audit

- **KISS: PASS.** Three variants share ~80% skeleton (signal dataclass, vol-band math, ema/trend helper). Single file instead of three. Module docstring explicitly justifies this choice.
- **DRY: PASS.** `compute_vol_bands`, `poc_proximity_ok`, `val_reject_fade`, `higher_tf_trend`, `_safe_float` are shared helpers. No copy-paste across V9/V10/V11.
- **SOLID: MIXED.** Single Responsibility OK. Open/Closed OK. Liskov OK. Interface Segregation OK. **Dependency Inversion VIOLATED** — V11 depends on data dict keys (`regime_atr_pct`) and df columns (`higher_ema_50`) that are not produced by any known module. High-level policy depends on undeclared low-level details. Fix by making the strategy self-contained (CR-1, CR-2 fixes above).

---

## Security audit

| check | result |
|-------|--------|
| hardcoded secrets | NONE |
| SQL injection | N/A (no SQL in strategy code) |
| XSS | N/A |
| CSRF | N/A |
| Auth | N/A |
| Rate limit | N/A |
| Error info leakage | NONE — signal_id encodes only the variant name + timestamp; no PII / no exchange keys / no account info. |

---

## Test coverage

```
$ python3 -m pytest tests/unit/test_vol_breakout_vpvr.py -v
collected 18 items
... 18 passed in 0.73s
```

All 18 unit tests pass. Tests cover:
- Skeleton helpers (`compute_vol_bands`, `poc_proximity_ok`, `val_reject_fade`, `higher_tf_trend`)
- Per-variant init / required_indicators / registry / signal logic
- LONG direction on each variant
- Mismatch / no-signal paths

**Tested code coverage gap:** tests do NOT cover wiring to upstream indicator pipeline (which is where CR-1 and CR-2 hit). Tests use synthetic `_force_breakout` helpers; no integration test with real OHLCV + VPVR + ATR + EMA data. No test for SHORT direction (only LONG path exercised).

---

## Predecessor context (cycle-46 vpvr_regime_blend_4h_20260714)

The same family (regime blend) was attempted in cycle-46. Results from `quant-loop/strategies/vpvr_regime_blend_4h_20260714/results/`:

- `metrics.json`: Sharpe = -2.29 (FAILED G1)
- `bootstrap_ci.json`: CI 95% lower = -6.78 (FAILED G6)
- `bonferroni.json`: G7_pass = false
- All gates failed

V11 is the cycle-49 pivot: same regime-blend family name, but rebuilt on vol_breakout skeleton with multi-TF confirm + asymmetric RR 1:2. This is the family's last shot per cycle-46 family-exhaustion rule. **But: with CR-1 + CR-2 unresolved, V11 will produce zero signals and the pivot fails before the backtest even starts.** The structural fix is to make the strategy self-contained per the suggestions in `gate_check.json`.

---

## Recommendation

**REJECT V11-B7 (this issue) with [INSUFFICIENT-EVIDENCE] + [CODE-DEFECTS].** Do NOT advance to `done`. Two paths forward:

1. **Strict path (per cycle-45 lesson V7):** wait for V11-B3 to complete, then dispatch V11-B6 (walk-forward + framework CV + bootstrap + Bonferroni), then re-run V11-B7. This is the only path that respects G1-G7.
2. **Fast path:** indicator-engineer applies CR-1 + CR-2 fixes (self-contained regime + trend axes), pushes the working tree to the indicator-engineer-clone-17 branch, then re-runs B3 → B6 → B7 in sequence.

**Authority:** as code-reviewer for V11-B7, my authority is REJECT/ADVISE-ONLY. I do not have the authority to fix the code, restart the B3 backtest, or create B6. Those are smark-level decisions.

**Smark sign-off required for:** restart V11-B3, dispatch V11-B6, push CR-1/CR-2 fixes, or close this issue as `[NOT-PROFITABLE]` per the spec's "Below any gate: status = done with [NOT-PROFITABLE] verdict" rule.

---

## Deliverable paths

- `/home/smark/multica/quant-loop/strategies/vol_breakout_vpvr_regime_blend_4h_20260714/review/v11/review.md` (this file)
- `/home/smark/multica/quant-loop/strategies/vol_breakout_vpvr_regime_blend_4h_20260714/review/v11/gate_check.json`
- `/home/smark/multica/quant-loop/strategies/vol_breakout_vpvr_regime_blend_4h_20260714/review/v11/verdict.txt`
