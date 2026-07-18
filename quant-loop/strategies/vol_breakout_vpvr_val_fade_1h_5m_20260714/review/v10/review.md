# V10 (iter#74) Code Review — vol_breakout_vpvr_val_fade_1h_5m_20260714

- **Reviewer**: code-reviewer (aefe1356-9356-42c6-ad58-d2cd23009d76)
- **Issue**: SMA-33961 (c46c0e1d-8b53-4f80-8e14-3293249175c3)
- **Review time**: 2026-07-14 15:30–15:45 +08 (Asia/Shanghai)
- **Verdict**: **REJECTED** — see `verdict.txt`, `gate_check.json`
- **Reason**: All 7 hard gates FAIL on missing evidence (B3 + B6 predecessors not yet done).

---

## 1. Predecessor state (mandatory precondition for B7)

| predecessor | issue | status (at review time) | deliverables |
|---|---|---|---|
| V10-B3 backtest | [SMA-33959](mention://issue/9d0c9a32-6e17-4816-a4f4-3af66e316718) | `in_progress` (no comments yet) | results/v10/{metrics.json, summary.json, equity_*.csv, trades_*.csv, walk_forward.json} — **all missing** |
| V10-B6 walk-forward + framework CV | [SMA-33960](mention://issue/72d54ba0-21fc-4124-977f-f6ae9f04f124) | `blocked` on B3 (comment ca2449b1, 15:38) | results/v10/{walk_forward_summary.json, bootstrap_ci.json, framework_cv.json} — **all missing** |

`backtest-runner` is in-flight: 4 files were written in the V10 strategy dir at
15:37–15:42 (data_loader.py, strategy.py, run_backtest.py, walk_forward.py) and
data/BTCUSDT__5m_with_1h_indicators.parquet (22 MB) was materialised at 15:39.
None of the B3 / B6 result artifacts have landed on disk yet. Per the B7 spec
("Predecessor: V10-B6 walk-forward (must show all gates passed)") B7 cannot
make an approval call before those artifacts exist.

---

## 2. Hard-gate enforcement (smark 2026-07-14 directive, non-negotiable)

| gate | threshold | measurable? | passed? |
|---|---|---|---|
| G1 | Sharpe ≥ 1.0 (full-backtest mean) | **no — metrics.json absent** | ❌ FAIL |
| G2 | min(annualized_full, mean_OOS_annualized) ≥ 15% | **no — summary.json absent** | ❌ FAIL |
| G3 | profit_factor > 1.5 | **no — metrics.json absent** | ❌ FAIL |
| G4 | max_drawdown < 25% | **no — metrics.json absent** | ❌ FAIL |
| G5 | framework CV OOS walk-forward Sharpe ≥ 1.0 BOTH freqtrade AND backtrader | **no — framework_cv.json absent** | ❌ FAIL |
| G6 | bootstrap 95% CI lower ≥ 0.5 (10000 resamples, seed=42) | **no — bootstrap_ci.json absent** | ❌ FAIL |
| G7 | FWER Bonferroni α=0.0125 | **no — fwer.json absent** | ❌ FAIL |

**0 / 7 gates pass.** Per the issue spec: *"Below any gate: status = done with
[NOT-PROFITABLE] verdict. Do NOT advance."*

A failure-mode detail: an absent artifact is a hard FAIL, not a "pending" or
"N/A" — the spec uses "FAIL any one = REJECT" with no conditional reprieve for
upstream blockers. The spirit rules (§5 §5.7 cycle-45 ship-gate) reinforce:
`evidence_gate.passed` must be true for `done`; the gate cannot be deferred.

---

## 3. Structural code review (read-only — no source changes)

This is the part of the review I *can* complete today, on the strategy code
that already exists. The four files in scope are:

- `42a03459/workdir/trading/strategies/team/combo/vol_breakout_vpvr.py` (618 lines, V10 class lines 343–476)
- `42a03459/workdir/trading/strategies/team/combo/__init__.py` (registers the three cycle-49 variants)
- `42a03459/workdir/trading/tests/unit/test_vol_breakout_vpvr.py` (18 tests)
- `quant-loop/strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/{config.json, data_loader.py, strategy.py, run_backtest.py, walk_forward.py}`

### 3.1 Scope locks (campaign-mandated, checked first)

- **vpvr_reversion_* family BANNED** — ✅ PASS. The class lives in
  `strategies/team/combo/vol_breakout_vpvr.py`, not in any `vpvr_reversion_*`
  module. The skeleton derives from `vol_breakout` (compute_vol_bands +
  bands.is_breakout) cross-coupled with a VAL pierce-reject helper.
- **val_fade BANNED if it degenerates to reversion** — ✅ PASS. V10 explicitly
  refuses to fire when `bands.is_breakout == 'up'` (lines 429–431), which
  prevents double-counting with the breakout family and forces the entry to
  be a *secondary* action, not a pure mean-reversion. The class is long-only
  (no short tail) and demands 1h trend == 'up' before any VAL fade, so the
  cross-axis (vol_breakout × vpvr_VAL) is preserved.

### 3.2 KISS / DRY / SOLID

- **DRY** — ✅ PASS (and intentionally so). The 80% shared skeleton
  (compute_vol_bands, poc_proximity_ok, val_reject_fade, higher_tf_trend) is
  factored into a single module and reused across V9 / V10 / V11. Splitting
  into three files would create 3× drift risk for the vol-band logic; the
  docstring (lines 35–42) explains this trade-off. The bar-by-bar backtest
  harness (`strategy.py` in the quant-loop dir, 420 lines) is a separate
  concern, correctly placed in the strategy directory.
- **SRP** — ✅ PASS. `strategy.py` does backtest mechanics; the V10 class
  does signal generation; `data_loader.py` does I/O + indicator pre-merge;
  `run_backtest.py` is the CLI orchestration entry point. Each file has one
  reason to change.
- **SoC** — ✅ PASS. The V10 class returns a `Signal` dataclass and never
  touches capital / position / fee math; the backtest engine never inspects
  the class's internal logic, only the public `generate_signal` contract.

### 3.3 Look-ahead discipline

- ✅ PASS. `data_loader.compute_1h_indicators` applies `shift(1)` to both
  `ema_50` (line 188) and `vpvr_val` (line 210) before merging onto 5m bars,
  so the value at hour `t` reflects bars `[t-window, t-1]`. The 5m ATR
  uses standard Wilder (1-bar lookback inside tr, lines 286–293). The
  strategy's `compute_vol_bands` anchors bands to `df.iloc[-(lookback+1):-1]`
  to avoid the trivial always-true breakout bug (documented lines 113–117).
- `merge_asof` uses `direction="backward"` (line 272) so the 5m bar at
  minute `m` reads the most-recent completed 1h indicator row — strictly
  no look-ahead.

### 3.4 Security / hygiene

- ✅ No hardcoded secrets, no API keys, no URLs, no credentials in any of
  the 5 reviewed files. All paths come from `config.json` or
  `DEFAULT_SOURCE_ROOT = Path("/home/smark/multica/quant-loop/live_data")`.
- ✅ No mutation anti-patterns: indicators are computed via `df.copy()`,
  state is held in immutable dataclasses (`Trade`, `SymbolState`), trade
  ledger is a fresh list each run.

### 3.5 Test coverage (V10-specific)

- ✅ 18 tests in `tests/unit/test_vol_breakout_vpvr.py` cover V10 with 6
  tests: `default_init`, `required_indicators`, `registered`,
  `long_when_val_reject_with_uptrend`, `no_signal_when_downtrend`,
  `no_signal_when_higher_tf_already_breakout`. All 18 tests pass:

  ```
  $ python3 -m pytest tests/unit/test_vol_breakout_vpvr.py -v
  ============================== 18 passed in 0.47s ==============================
  ```

- Coverage is decent for the *signal-logic* contract: init, registry, and
  three decision points. Missing-coverage items are scope-deferred to the
  backtest engine (e.g. "did the backtest engine queue the fill on the
  next bar?", "did the SL/TP hit before the time-stop?"). Those are properly
  tested in the backtest-engine's own test surface, not duplicated here.
- The local `redis_client` fixture (test file lines 38–41) is a clean
  override of the repo-wide autouse Redis fixture, documented inline.
  KISS-correct.

### 3.6 Fill convention & decision disclosure

- ✅ `config.json:fill_convention = "bar[t].close + cost_per_side"` is
  declared up front. The docstring in `strategy.py` (lines 28–35) explicitly
  documents the proxy: bar[t+1].open ≈ bar[t].close + 1-bp cost. SL-first
  ordering on same-bar SL/TP is also disclosed (line 33). For a
  backtest-only paper-trade, this is acceptable.
- ✅ Long-only decision is documented in the class docstring (lines
  348–358) with the explicit "short tail has historically destroyed
  Sharpe in cycle-46 family" rationale. Code review accepts this
  decision without re-litigating.

---

## 4. Findings (severity-ordered)

### Critical
*(none — code itself is sound; the rejection is for missing evidence, not for code defects)*

### Major
*(none)*

### Minor

1. **F-MIN-01** — `quant-loop/strategies/.../strategy.py:299`. The walrus
   expression `(pending_qty_units := ...)` is computed and never used. The
   actual `qty` used in the SL/TP branch is undefined in the symbol-state
   closure — the same block then computes `qty` again at line 300. This is
   dead code and a subtle correctness risk if a future maintainer assumes
   `pending_qty_units` is the canonical quantity.

   **Recommendation**: Drop the walrus + the redundant `qty` line. Compute
   `qty` once, immediately before use, and reuse it for both entry and
   exit paths.

2. **F-MIN-02** — `vol_breakout_vpvr.py:242, 255`. Entry fill uses
   `prev['close'] * (1.0 + cost_per_side)` and exit fill uses
   `prev['close'] * (1.0 - cost_per_side)`. The cost-per-side proxy
   already includes both fee and slippage, but the bar[t+1].open vs.
   prev_close gap is implicitly absorbed rather than modelled. For the
   paper-trade backtest this is fine, but a one-line comment would help
   future readers.

   **Recommendation**: Add a 1-line doc clarification:
   *"Cost-per-side on entry/exit fill proxy includes fee + slippage; bar[t+1].open-vs-prev_close gap is implicitly absorbed into the cost."*

3. **F-MIN-03** — `vol_breakout_vpvr.py:455-456`. `signal_id` uses
   `int(datetime.utcnow().timestamp() * 1000)` — fine for backtests but
   susceptible to collisions across multiple workers at sub-ms resolution.
   For V10's paper-trade use this is acceptable. No action required; just
   worth a one-liner in the class docstring if V10 ever ships to
   multi-worker paper-trade.

4. **F-MIN-04** — `quant-loop/.../data_loader.py:223-247`. The
   `_import_vpvr_value_area` fallback silently swallows the first
   ImportError. The cycle-49 known issue (kama_trend_vwp → LHFrameStd) is
   mentioned in a docstring only, not logged. A future agent debugging a
   missing function would benefit from a `logger.warning` line.

   **Recommendation**: Add `logger.warning(f"primary import failed: {e}; trying stub-package bootstrap")` before each fallback.

---

## 5. What I did NOT do (per code-reviewer boundary)

Per the agent identity, code-reviewer does not change strategy code or write
new features. The four minor findings above are **suggestions**, not
patches. The B7 deliverable is:

- `review/v10/review.md` (this file)
- `review/v10/gate_check.json` (machine-readable per-gate pass/fail)
- `review/v10/verdict.txt` (one-word verdict)

I did not edit `vol_breakout_vpvr.py`, `data_loader.py`, `strategy.py`,
`run_backtest.py`, or `walk_forward.py`. I did not commit, push, or restart
any daemon (spirit §4.2, §4.3). I did not touch the B3 / B6 issues — they
remain `in_progress` and `blocked` respectively, awaiting the backtest-runner
to land the artifacts.

---

## 6. Recommended next step (for smark / autopilot)

This issue should be re-opened (status → `todo`) **after** V10-B3 (SMA-33959)
lands the deliverables in `results/v10/` AND V10-B6 (SMA-33960) completes
walk-forward + framework CV + bootstrap + FWER. At that point the B7 review
can re-execute the gate table and either approve or reject on the merits of
the actual numbers.

**Do NOT advance V10 to ship on the basis of this review.** Per the issue
spec, the only way to close this issue today is with `[NOT-PROFITABLE]`
verdict because every gate fails on missing evidence. I am **not** flipping
the issue status to `done` with `[NOT-PROFITABLE]` from this review, because
the issue is currently `in_progress` (under my ownership as the reviewer);
flipping it to `done` while a B3 + B6 backtest is still in flight would be
a false completion (Constitutional Rule 2). The right path is to leave
`in_progress` until the missing artifacts arrive, then re-review.
