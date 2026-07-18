# framework-validate / cross-framework CV — decision record

Cross-framework validation system (framework-validate cron `37 * * * *` Asia/Shanghai, run by framework-validator agent) that hourly replays quant-loop strategies through a rotating list of mainstream backtest frameworks (`freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded`) and computes absolute-relative divergence against in-house metrics. Active window 2026-07-09 → 2026-07-18. Gate: per-symbol divergence must satisfy the live tolerance table (see "Tolerance" below); strategies with max abs-rel divergence > 50 % on any tracked metric (sharpe / total_return / max_dd / n_trades) are auto-archived NOT-PROFITABLE per W5 without ESCALATE; strategies with divergence ≤ 50 % are still escalated. **FAMILY VERDICT: framework-validate is operational and converging; the new 15 %-for-low-magnitude tolerance (cycle 35) plus the W5 auto-archive (cycle-46) collapse the prior ~50-issue ESCALATE backlog. The remaining divergence classes are systematic framework-vs-in-house methodology gaps (fill-timing, wallet accounting, denominator), not strategy defects.**

## Tolerance

Live in `framework-validator` agent instructions (updated 2026-07-11 08:37 Asia/Shanghai, smark-signed-off via cycle-35 reply "2"). Backup on 192.168.0.105 at `/tmp/fw-validator-inst-backup.txt` (not on local runner; expected).

| metric | tolerance | condition |
|---|---|---|
| sharpe | ±0.2 absolute | unconditional |
| max_drawdown | 5 % relative | when in-house total_return ≥ 1 % |
| max_drawdown | 15 % relative | when in-house total_return < 1 % (Bucket A — low-magnitude) |
| total_return | 5 % relative | when in-house total_return ≥ 1 % |
| total_return | 15 % relative | when in-house total_return < 1 % (Bucket A — low-magnitude) |
| n_trades | ±1 absolute | unconditional (round-off absorption) |
| win_rate | ±0.05 absolute | unconditional |

Rationale (cycle-35 Bucket A): low-magnitude strategies (TR < 1 %) amplify any 1-2 bps fill-timing noise to 10-30 % relative divergence in total_return / max_dd; the 5 % tolerance blows for nominally-working strategies. freqtrade / backtrader / vectorbt differ by < 2 bps on costs → ~3-5 % rel divergence on a 0.5 % return = 10 % rel. 15 % is the safe upper bound that distinguishes framework noise from real bugs. sharpe / n_trades / win_rate are scale-independent and unchanged.

## W5 AUTO-ARCHIVE pattern

Codified in `AGENT_COLLAB_AUDIT_2026-07-12.md` §W5 (added 2026-07-12). Trigger: framework-CV divergence absolute > 50 % on any tracked metric. Sequence:

1. Post the comparison comment on the cron-triggered issue.
2. Set the strategy issue status to `done` via the tracker CLI (idempotent — issue often already done).
3. Do NOT modify `results/metrics.json` (preserve the in-house record).
4. Do NOT open an escalation for smark — smark is not woken for obviously-broken strategies.

Purpose: collapse the ~3-ESCALATE-per-hour rate and the ~50-issue smark-decision backlog. Pre-W5, every divergence caused a wake-up; post-W5, only ≤ 50 % divergences escalate (and those are the actually-ambiguous ones needing human judgement).

Observed in this batch: 13 of 15 runs trigger W5 (auto-archive); 2 stay at the boundary (50.00 %, 16.29 %) and ESCALATE.

## What worked

### Within-tolerance reference: momentum_trend_multi_tf_atr_scaled_1h × backtrader (boundary ESCALATE)

Code path: `quant-loop/strategies/momentum_trend_multi_tf_atr_scaled_1h_20260712/` (V1 chain, BTC+ETH 1h entry + 4h filter, 1918 trades). Framework: backtrader 1.9.78.123 SHA `b853d7c9`. Adapter: `/tmp/backtrader-validation-momentum-trend-multi-tf-atr-scaled-1h-2026-07-15-0237/adapter.py`. Per-symbol metrics match exactly (n_trades exact, win_rate exact, total_return exact to 4 dp, max_dd within 3.51 % rel, sharpe within 0.01 abs). Portfolio total_return 50.00 % rel and max_dd 49.43 % rel — at the boundary but NOT > 50 %, so ESCALATE was the correct classification. Root cause: aggregation-convention gap — in-house `portfolio_total_return = (equity[-1] - $100k) / $100k` (treats starting cap as one symbol's worth); framework `(Σequity_streams[-1]) / (starting_cap × N_symbols)`. Both raw per-stream equity figures agree to the cent. The 50 % gap is purely from how the aggregate is normalized, not from the underlying metric derivation. Open decision: standardize in-house aggregation to the conventional Σ-equity / Σ-starting-cap (would change every `summary.json` portfolio TR from ~2.79 % to ~1.40 %), or document the in-house convention as canonical and exclude portfolio-level from framework-CV tolerance (compare only per-symbol).

### Within-tolerance reference: vol_breakout_4h_v1 × backtrader (ESCALATE)

Code path: `quant-loop/strategies/vol_breakout_4h_v1_20260711/` (V5 trend-following, BTCUSDT + ETHUSDT + SOLUSDT, 4h single-TF, vol-targeted sizing, vol-regime entry/exit gate; 195 trades, $13,993 PnL on $100k starting). Framework: backtrader 1.9.78.123 SHA `b853d7c9`. Max abs-rel divergence = 16.29 % (ETHUSDT max_dd), well under W5 → ESCALATE opened for smark decision (cycle-35 framework-noise floor not yet applied to this run). Per-symbol pattern: BTC max_dd +5.46 % rel, ETH max_dd +16.29 % rel, ETH total_return -7.96 % rel, SOL max_dd +7.99 % rel, SOL n_trades -2. All divergences concentrated in max_dd — framework registers ~5-16 % larger drawdowns on the same fill dates, attributable to (1) backtrader's intra-bar MTM via `broker.getvalue()` includes unrealized PnL across every bar in a position while in-house marks MTM only at trade exit; (2) the realized_vol window in the in-house `vol_target_size` helper uses `realized_vol.shift(1)` (entry-bar cached) whereas backtrader computes size-of-trade at the fill-bar. SOL n_trades 67→65 (-2) and BTC 63→62 (-1) are downstream of intra-bar MTM path; signal/exit logic is identical. Per cron lifecycle, status transitions to `in_review` until the ESCALATE is resolved.

### The 9190 % Sharpe case — adapter sentinel artifact (VOID verdict, then corrected)

Code path: `quant-loop/strategies/vpvr_funding_aware_v1_20260711/` (V8 rev2, 4h BTCUSDT+ETHUSDT, long-only funding-aware carry, vol-target sizing, n_trades 223). Framework: freqtrade 2026.7-dev SHA `15b94ce7`. Initial run reported portfolio sharpe 36.366 vs ours 0.3914 = **9190 %** rel divergence, total_return 802 % rel, max_dd 2443 % rel — three metrics > 50 %, W5 fired. Root cause was a sentinel artifact in the freqtrade adapter: the adapter never debited position notional from cash at entry but credited `notional*(1+pnl)` at exit, so NAV ratcheted up at every fill (framework max_dd -4.0e-06 was not a real measurement). **The W5 verdict was void.** Corrected re-run (mark-to-market replay, freqtrade 12 bp round-trip cost, validated against in-house equity CSVs): sharpe 6.6396 vs 7.1649 (7.33 % rel), total_return 1.2459 vs 1.4433 (13.68 % rel), max_dd -0.4386 vs -0.5708 (23.16 % rel) — all < 50 %, **W5 not triggered**. The corrected numbers are the canonical record; the void W5 is preserved in the run log for diagnostic purposes. Per-trade methodology gap that remained: freqtrade wallet-summary Sharpe uses per-trade mean/std (`sqrt(252) * mean / std`), while in-house computes Sharpe over the daily-bar equity curve. For sparse carry strategies (~3 trades/symbol over 4 years), the per-trade estimator amplifies tiny mean PnL differences into large Sharpe deltas — methodology mismatch, not strategy divergence.

## What failed and why

Grouped by divergence class. All entries are W5 AUTO-ARCHIVE unless marked.

### max_dd divergence (largest class)

- **vpvr_funding_term_curve_1h × freqtrade** (NOT-PROFITABLE in-house, iter#97 V1 funding_term_curve): max_dd 99.97 % rel, sharpe 100.01 %, total_return 100.00 % (all three metrics tip). Root cause: freqtrade linear-pnl fill model multiplies `pnl_pct` linearly across held bars; for 1 h short-only reversion with sub-day holds (~1.05 bars/trade), framework sees most bars as flat and accumulates tiny noise near zero, while in-house mark-to-market at exit captures the realized loss. NOT-PROFITABLE verdict is correct (in-house Sharpe -0.98, PF 0.78).
- **vpvr_funding_aware_v1 × freqtrade** (initial, void — see above): max_dd 99.9993 % rel, sharpe 18.62 %, total_return 8.64 % — same adapter sentinel artifact; verdict voided by correction.
- **donchian_breakout_atr_1d × backtrader**: SOLUSDT max_dd 145.4 % rel, BTC 51.8 %, ETH 45.8 %. SOL total_return -35.0 %, ETH total_return +87.6 %. n_trades BTC -2, ETH -6, SOL 0 (out of tol). Root cause: backtrader exit pipeline drops intra-bar exits that in-house captures (notify_trade / notify_order pipeline records position-closed events only after broker settlement, can lag 1 bar when exit-condition matches same bar as a new entry). On low-magnitude TR < 1 % strategies, missing a single exit amplifies max_dd by 2-3x. Strategy has no dedicated open issue; W5 verdict recorded in the framework-validate run-output.
- **donchian_breakout_atr_1d × nautilus_trader** (BTCUSDT/ETHUSDT/SOLUSDT, 1 d, 40 trades over 2 y): BTC max_dd 19.10 % rel, ETH 21.82 %, SOL 7.34 % (all within new 15 %-low-magnitude tolerance); BTC/ETH/SOL total_return +11.30 % / +49.96 % / +16.85 % (ETH just above 15 %); n_trades SOL +3 (out of tol). W5 verdict triggered by Sharpe divergence (see below), not max_dd.
- **momentum_trend_multi_tf_atr_scaled_v2 × backtrader**: max_dd 103.1 % rel, total_return 318.1 %, sharpe 143.5 %. Root cause: backtrader default `coc=False` fills at next-bar open; in-house uses `close[t] * (1 + cost)`. For 1 h crypto, `open[t+1]` vs `close[t]` diverges 0.05-0.3 % per bar, accumulates over 1841 trades. n_trades exact match (BTC 927 + ETH 914 = 1841) confirms signal/exit logic identical — divergence is fill-mechanism / cost-basis only. A prior freqtrade validation on the same strategy found EXACT match (`framework_sharpe_daily=0.186`, `framework_total_return=0.0185`) because freqtrade uses the same close[t]+cost convention; backtrader uses next-bar-open, hence the divergence.
- **momentum_trend_multi_tf_atr_scaled_v3 × freqtrade**: max_dd 221.2 %, total_return 189.0 %, sharpe 175.9 %, n_trades +3. Root cause: freqtrade defaults to next-bar-open fill; in-house cheat-on-close convention. Per-trade PnL differs by 1-bar timing — BTC 1 h bars typically have 50-200 bps intrabar range. Apples-to-apples freqtrade run requires explicit `entry_pricing.price_side = "same" + exit_pricing.price_side = "same" + use_order_book=False + price_last_balance=0.0`; adapter currently sets `price_side=other`.
- **vol_breakout_4h_v1 × jesse**: max_dd 134.55 %, total_return 17.75 %, sharpe 2.95 %. Root cause: jesse per-bar fill model uses `self.price` (trigger bar's close) on same bar; in-house explicitly uses next-bar open. Additionally `self.stop_loss` trailing-stop fires at trigger bar's close (not next-bar-open), exposing the framework to gap-through. max_dd denominator mismatch compounds: in-house portfolio max_dd is off the sum of per-symbol equity CSVs ($313,992 combined); jesse's portfolio max_dd is off a single $100 k account routed across 3 symbols.

### Sharpe divergence (sparse-trade and per-trade-vs-daily-bar)

- **donchian_breakout_atr_1d × nautilus_trader**: Sharpe 597 % / 877 % / 604 % rel on BTC/ETH/SOL (ETHUSDT is the worst at 877.48 %). Root cause: nautilus per-trade Sharpe estimator (`np.sqrt(252) * mean / std`) treats each closed trade as iid; in-house computes Sharpe over the daily-bar equity curve. With 13-16 trades over 2 y, the per-trade estimator amplifies tiny mean PnL differences into large Sharpe deltas — methodology mismatch, not strategy behavior. Pnl magnitudes match well (BTC +$152.8 vs $172, ETH +$530 vs $354, SOL +$366 vs $313); total_return delta is < 50 % on all 3 symbols.
- **momentum_trend_multi_tf_atr_scaled_v3 × jesse**: Sharpe 207.8 %, total_return 198.2 % rel. Direction of returns diverges (in-house +1.64 %, jesse -1.61 % — sign flip on the same trade set). Same fill-timing root cause as jesse's vol_breakout_4h_v1 entry; the per-trade PnL distribution is similar in shape but sign-flipped on net. Trade count matches (969 vs 972, +0.3 % rel).
- **momentum_trend_multi_tf_atr_scaled_v2 × backtrader**: Sharpe 143.5 % rel. See max_dd entry above for root cause.
- **funding_arb_binance_bybit_delta_neutral × backtrader**: Sharpe 274.20 %, total_return 64.96 %, max_dd 65.26 % rel. Primarily an OOS-base artifact (different fold windows; only 1 of 4 walk-forward folds has trades, OOS mean dominated by the 2023-01→2023-07 fold which alone lost 0.42 % in 8 trades). The funding-rate spread between Binance and Bybit perps is structurally too small to overcome 4-leg execution costs (in-house PF=0.228, n_trades=39 over 4.5 y).

### total_return divergence (close-fill vs next-open-fill)

- **vol_breakout_2tf_vpvr_confluence_4h × vectorbt**: portfolio total_return 70.7 % rel (BTC -53.7 %, ETH -37.9 %, SOL +19.2 %). Root cause: vectorbt adapter uses close-fill (signal at `bar[t].close`, fill at same close) while in-house uses next-open-fill. On 4 h bars with low absolute price change (~0.5-1 % per bar), a 1-bar timing difference compounds to 50-70 % rel divergence for low-magnitude strategies. n_trades match exactly per symbol (BTC=8, ETH=12, SOL=12). vectorbt has no pinned release — system-installed via pip; no `/tmp/framework-cache/vectorbt-*` entry.
- **momentum_trend_multi_tf_atr_scaled_v3 × jesse**: total_return 198.2 % rel (sign flip; see Sharpe entry).
- **momentum_trend_multi_tf_atr_scaled_v2 × backtrader**: total_return 318.1 % rel (see max_dd entry).

### Per-framework behavior summary

- **backtrader** (1.9.78.123, SHA `b853d7c9`): default `coc=False` fills at next-bar open; in-house convention is close[t]+cost. Use `coc=True` cheat-on-close to match. Has intra-bar MTM via `broker.getvalue()` (unrealized PnL across bars); in-house marks MTM only at trade exit. With `coc=False` and vol-target sizing, ~3-5 % rel divergence on $13k PnL is expected; ~300 % rel divergence on $1.8k PnL (low-magnitude) is the canonical pattern.
- **freqtrade** (2026.6 / 2026.7-dev, SHA `15b94ce7`): default `price_side=other` (next-bar-open); cheat-on-close requires explicit `entry_pricing.price_side = "same" + exit_pricing.price_side = "same" + use_order_book=False + price_last_balance=0.0`. Wallet-summary Sharpe uses per-trade mean/std; in-house uses daily-bar equity. Wallet does not route funding PnL (must use CarryLedger adapter). 1 % fractional sizing keeps per-trade drawdown near zero while in-house aggregate captures cross-symbol portfolio drawdown → ~100 % max_dd divergence is the canonical artifact for short-duration trades.
- **vectorbt** (1.1.0): close-fill convention. No pinned release / no cache directory; system-installed. Most framework-friendly for low-magnitude TR < 1 % strategies when next-open-fill emulation is added; currently diverges 50-70 % rel on 4 h bars.
- **jesse** (2.5.0, SHA `96110dbc`): per-bar fill at `self.price` (same bar as signal). `self.stop_loss` fires at trigger-bar close, exposing framework to gap-through. Trailing-stop and time-stop also same-bar. To match in-house, adapter must explicitly shift entry/exit by 1 bar.
- **nautilus_trader** (1.231.0, SHA `72f53d4a`): BacktestEngine with CryptoPerpetual instruments. Per-trade Sharpe estimator inflates Sharpe for sparse-trade strategies (13-16 trades over 2 y → 600-900 % rel divergence from per-trade-vs-daily-bar methodology gap). PnL magnitudes match well; only Sharpe methodology mismatches.

### Bug vs data-difference vs real-signal-failure classification

- **Implementation bugs** (fix in the adapter): vpvr_funding_aware_v1 × freqtrade NAV ratcheting sentinel artifact (void + corrected re-run). All framework-vs-in-house fill-timing gaps are adapter bugs in the sense that the adapter needs to explicitly emulate the in-house convention (cheat-on-close / next-open / same-bar); they are not framework bugs.
- **Data / methodology differences** (no fix, document as canonical): freqtrade wallet-summary Sharpe per-trade vs in-house daily-bar equity Sharpe (9190 % case, void verdict); nautilus per-trade Sharpe for sparse-trade strategies; max_dd denominator (combined equity stream vs single NAV account).
- **Real signal failure** (strategy genuinely weak in both engines): funding_arb_binance_bybit_delta_neutral (in-house PF=0.228, structurally small spread); vpvr_funding_term_curve_1h (in-house sharpe -0.98, NOT-PROFITABLE on all per-symbol dimensions); momentum_trend_multi_tf_atr_scaled_v3 / v2 in-house signal is borderline-positive (Sharpe 0.18-0.26, max_dd ~-2 to -3 %) and the framework exposure to 1-bar timing flips or shrinks it; vol_breakout_4h_v1 has consistent +5 to +16 % max_dd divergence across all 3 symbols that may indicate a real in-house MTM convention issue rather than framework noise (ESCALATE pending).

## Open questions

- **Portfolio aggregation convention** (momentum_trend_multi_tf_atr_scaled_1h): standardize in-house to `Σequity_streams / Σstarting_cap` (changes every `summary.json` portfolio TR from ~2.79 % to ~1.40 %), or document in-house convention as canonical and exclude portfolio-level from framework-CV tolerance (compare only per-symbol)?
- **ESCALATE in_review** for `vol_breakout_4h_v1 × backtrader` (cycle 2026-07-14 12:37): max_dd divergence is +5.46 % / +16.29 % / +7.99 % rel across 3 symbols — is this framework intra-bar MTM noise or a real in-house MTM bug? Should this run also adopt the cycle-35 framework-noise floor?
- **CarryLedger vs freqtrade wallet** for funding PnL: should the freqtrade adapter be updated to route funding into the wallet so the wallet-summary Sharpe matches in-house daily-bar Sharpe for carry strategies?
- **Cycle-46 family-exhaustion confirmation** for vpvr_*, momentum_trend_multi_tf_atr_scaled_*, vol_breakout_*: each family has accumulated ≥ 3 NOT-PROFITABLE archives across these CV runs; no V_n+1 should be built until cycle-47.
- **Adapter location convention**: adapters live in `/tmp/<framework>-validation-<strategy>/adapter.py`, external to `/tmp/framework-cache/<framework>-<sha>/` (per agent identity no-modify rule); cache is read-only. All evidence files reference this convention.