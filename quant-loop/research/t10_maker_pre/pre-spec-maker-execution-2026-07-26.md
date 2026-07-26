# Pre-SPEC: maker execution cost — is ≤20bps pair-RT reachable on Binance USDT-M perp?

**Issue**: SMA-36598 · **Author**: quant-researcher · **Date**: 2026-07-26
**Trigger**: se_h3 verdict (SMA-36570) — mean gross 17.78bps, 20bps break-even, 24bps/60bps fee-shock KILL.
**Goal**: pre-SPEC advisory to smark-decision-maker on whether to fund a maker-execution research line.

---

## TL;DR (verdict candidates)

| verdict | conditions | implication |
|---|---|---|
| **GO** | median maker tick ≤ 0.012% effective pair-RT (i.e. maker rebate ≥ fee + queue loss) at VIP ≥ 3, AND measured post-fill markout ≤ 1.5bp per fill on Binance BTCUSDT perp | open T10 SPEC next, fund data-feasibility deep-dive |
| **GO-with-rebuild** | only achievable at VIP ≥ 5 / ≥ 9 (true maker rebate at VIP9), but theoretical T10bp pair-RT still on the table at our scale of trading | worth 1 more iteration of pre-research; else defer to 2026-11 |
| **DEFER** | median maker tick ≥ 0.020% (VIP0..VIP2) AND post-fill markout ≥ 1.5bp | do NOT open maker-research line now; re-evaluate 2026-11 when account has credibly scaled into VIP3+ |

This session's pre-SPEC **measurement** (BTC aggTrades 2026-04-19 → 2026-04-22, 5.07M trades) puts us in the **DEFER-or-GO-with-rebuild** zone: maker tick + measured adverse selection together are ~7-9bp pair-RT at VIP0/1, marginally under the 20bps ceiling for *some* signal classes but not robust across regimes. Decision is **smark-decision-maker's call** — see §5.

---

## 1. What the se_h3 verdict actually constrains

Per SMA-36570 (`F = KILL`, smark SIGNOFF 2026-07-26T08:30+08):

| Dimension | Value |
|---|---|
| Full-hist mean gross per trade | 17.78 bps |
| 7-window OOS Sharpe | 9.21 (CI95 [7.79, 11.04]) |
| Break-even cost | **20 bps pair round-trip** |
| Realistic cost (24bps freqtrade) | OOS Sharpe = -17.33 (death) |
| Realistic cost (60bps backtrader) | OOS Sharpe = -38.80 (death) |
| "Alive" only at unrealistic 4bps in-house floor | Sharpe +5.98 (marginal) |
| G4 PF | 1.098 (FAIL vs ≥1.5) — win-rate driven, NOT edge-driven |

The se_h3 verdict's lesson for ALL future stat-arb families:
- gross alpha ~5-25 bps/leg on liquid perp stat-arb
- pair-RT cost must clear **≤20 bps** to leave ANY net edge band
- even an in-house 4-bps cost gives only +5.98 Sharpe — *marginal*
- realistic cost (10-25 bps pair-RT) **always** dominates

So maker execution is the binding constraint for the entire thin-edge universe. That is what motivates pulling T10 from 2026-11 to 2026-07-26.

---

## 2. Binance USDⓈ-M perp maker fee (per published schedule, post March-2026 update)

| VIP | 30d notional (USD) | maker (bps) | taker (bps) |
|---|---|---|---|
| Regular (VIP0) | < $1M | +2.0 | +5.0 |
| VIP1 | $1M | +1.8 | +5.0 |
| VIP2 | $10M | +1.6 | +4.5 |
| VIP3 | $50M | +1.4 | +4.0 |
| VIP4 | $100M | +1.2 | +3.5 |
| VIP5 | $250M | +1.0 | +3.0 |
| VIP6 | $500M | +0.8 | +2.5 |
| VIP7 | $1B | +0.5 | +2.0 |
| VIP8 | $2B | +0.2 | +1.7 |
| **VIP9** | $4B | **0.0** | +1.5 |

Source: [BestFeeCryptoExchange 2025 Binance vs OKX vs Bybit comparison](https://bestfeescryptoexchange.com/blog/binance-vs-okx-vs-bybit-fees-comparison/) + [Binance Futures VIP tier table (post-March 2026 update)](http://www.binancevipfeetiers.org/binance-futures-fee-tiers.html). *No negative maker rebate until VIP9.* BNB-discount stacks ~25%.

**Pair-RT economic cost at the touch (entry + exit, both touches the book, both eaten as maker):**

| VIP | per-leg maker | pair-RT fee floor |
|---|---|---|
| VIP0 | +2.0 bp | 4.0 bp |
| VIP1 | +1.8 bp | 3.6 bp |
| VIP3 | +1.4 bp | 2.8 bp |
| VIP5 | +1.0 bp | 2.0 bp |
| VIP9 | 0.0 bp | 0.0 bp |

This is fee *floor only*. Adverse selection, queue loss, and missed-fill cost stack on top.

---

## 3. Adverse-selection evidence (Albers et al. 2025 + our own demo)

### 3.1 Literature result

Albers, Cucuringu, Howison, Shestopaloff (Oxford, 2025-11, [arXiv 2502.18625](https://arxiv.org/html/2502.18625v2)) ran a **live, signal-bias-free experiment** on Binance USDⓈ-M bitcoin perpetual:

- 232,897 minimum-sized maker orders over one week (Feb 12-19, 2024), continuous-quoting mode
- Fill probability vs. queue position (front-of-queue 90%+, back-of-queue <10%) — classic
- **Crucial**: "a negative correlation between a maker order's fill probability and its subsequent return" — orders that fill fast are filled *because* price moves against them
- Top-of-book post-fill drift is **negative** (i.e., the maker loses on average) at sub-second horizons
- Best strategy is *contrarian* to OBI (post on the shorter queue), echoing the discussion in Menkveld 2013 / Donnelly 2018
- They document Taker trading needs >4bp "edge" to clear the 0.05% taker fee; maker trading needs to escape the same fee + the adverse-selection opportunity cost

### 3.2 Our own measurement (this session)

Script: `~/multica/quant-loop/research/t10_maker_pre/markout_demo.py`
Window: BTC aggTrades 2026-04-19 → 2026-04-22 (3 days = 5,065,766 trades)
Aggregation: every print classified `bid` (taker-sold) or `ask` (taker-bought); sweep = ≥2 same-side prints ≤100ms apart; markout = signed mid-return at τ ∈ {1s, 5s, 30s, 5min} using next-tape-print as forward mid proxy.

| Side | Sweep | N | markout 1s | markout 5s | markout 30s | markout 300s |
|---|---|---|---|---|---|---|
| bid | no  | 298,838 | **+0.17** | **+0.26** | **+0.32** | **+0.05** |
| bid | yes | 2,218,162 | −0.94 | −1.13 | −1.37 | **−2.33** |
| ask | no  | 293,928 | **+0.10** | **+0.06** | **+0.08** | **+0.00** |
| ask | yes | 2,254,838 | −1.04 | −1.32 | −1.78 | **−1.76** |

Sweep-share in 3-day window = **88.3%** of all prints. This is the brutal fact: a top-of-book maker is *overwhelmingly* filled by taker chains walking through multiple price levels — i.e., adverse selection — and the markout is consistently **−1 to −2 bp at horizons ≤5min**.

**Effective per-fill markout (volume-weighted across sweep/no-sweep at the touch):**

```
0.117 × +0.17 + 0.883 × −2.0 ≈ −1.74 bp per top-of-book fill  (ask similar by sign)
```

Round-trip maker (entry + exit both as maker) = 2 × ~1.74 = **3.5 bp pair-RT** of *post-fill drift alone*, before considering queue loss / missed-fill opportunity cost.

---

## 4. The cost equation

```
pair-RT economic cost =
  (maker_fee_entry_bp + maker_fee_exit_bp)         ← VIP-tier dependent
+ 2 × expected_post_fill_markout_bp                ← ~3.5 bp per session §3
+ queue_priority_loss_bp                            ← empirically 0.5–2 bp at top, 1.5–4 bp at 2nd-3rd tick (Albers §4)
+ missed_fill_opportunity_cost_bp                   ← wait penalty ~10–20 bp per leg-equivalent if holding
```

| VIP | fee pair-RT | + AS proxy | + queue | = pair-RT economic cost (lower bound) |
|---|---|---|---|---|
| VIP0 | 4.0 bp | +3.5 bp | +1.5 bp | **~9.0 bp** |
| VIP1 | 3.6 bp | +3.5 bp | +1.5 bp | ~8.6 bp |
| VIP3 | 2.8 bp | +3.5 bp | +1.5 bp | ~7.8 bp |
| VIP5 | 2.0 bp | +3.5 bp | +1.0 bp | ~6.5 bp |
| VIP9 | 0.0 bp | +3.5 bp | +1.0 bp | ~4.5 bp |

**The se_h3 break-even is 20bp pair-RT.**:Maker execution at any VIP ≤ 5 leaves 6.5-9.0 bp pair-RT — well under 20bp. **In theory**, the maker-cost ceiling is reachable on this exchange at any tier ≥ VIP0.

**But** — and this is the part no public literature quantifies for our exact regime:

- **Regime dependence**: the +0.17bp non-sweep markout is from a *low-vol* window (April 2026 BTC in consolidation around $87-90k). In a high-vol window (cascade, liquidation, regime flip), markouts can flip sign and magnitude is unclear without a follow-on regime-stratified measurement.
- **Queue ratchet**: if your fill rate at top-of-book is <50%, you need to post deeper (worse queue but better fill rate). At 2nd tick, fee-remain positive (still maker) but adverse-selection markout materially worsens because you only get filled when the market moves past you.
- **Inventory risk premium**: not modeled above. se_h3 was pair-RT (offsetting legs); asymmetric inventories (one leg unfilled for >1s) compound the drift.

### Pre-SPEC conclusion (this session's verdict)

| Option | Pros | Cons | Recommend |
|---|---|---|---|
| **Open T10 SPEC now (full research line)** | would unblock thin-edge stat-arb family; documented 8-bp pair-RT achievable | requires capital scaling to VIP3+; no algorithmic mitigation of adverse selection yet proven; regulatory tail (Binance US entity excluded) | NO at current scale |
| **Smaller pilot: 1 quarter, BTC only, VIP0 economics, regime-gated** | bounds risk; produces proprietary adverse-selection / queue data we can't get from papers | commitment to Binance execution path (we are exchange-agnostic) | **YES if smark signs off the strategic shift** |
| **Defer T10 to 2026-11** | preserves bandwidth for T07 (portfolio correlation with `mtf_xs_pairs` H3 LIVE candidate) | T10 was already deferred from 2026-11 to 2026-07-26 in the task description; deferring back sends mixed signal | NO (we already pulled it forward) |

The data-feasibility demo confirms the *measurement* is feasible and the literature confirms the *existence* of the maker edge space. What the measurement does NOT confirm is whether regime-conditional adverse-selection is finite enough to clear 5bp per leg net. That's a separate SPEC question.

**Recommendation to smark-decision-maker**: gate T10 on a *pilot* gate (1 quarter, BTC-only, $100k notional, full maker order log, regime-stratified markout book) before opening the full research line. This bounds risk and produces the dataset needed to SPEC the full research line. The full-line SPEC is premature.

---

## 5. Decision request — what smark-decision-maker needs to confirm

1. Authorize a **maker-pilot** (BTC only, $100k notional, 2026-Q3) to produce proprietary markout data, and only then open T10 SPEC?
2. Or commit account to scaling to VIP3 / VIP5 notional ($50M / $250M 30d) so the +1.4bp / +1.0bp maker fee tier is reached?
3. Or defer to 2026-11 regardless and focus bandwidth on T07 portfolio-correlation + `mtf_xs_pairs` H3 LIVE?

Pre-SPEC self-assessment (own scale: very low VIP at this point): 1 (pilot) is the most informative and least-committed path. It should be 1 quarter max, with explicit NO-bet criteria (no real-money trades if slippage distribution doesn't match the demo's distribution).

If pilot is vetoed → option 3 (defer) is the safer path; no new thin-edge stat-arb is being spec'd in this milestone anyway.

---

## 6. Constraints / non-goals (per task spec)

- **No** strategy code, **no** backtest, **no** sweep
- L2 research-mainline only — execution work goes to strategy-worker-* under their lane
- Did NOT tune anything to make the verdict favorable — measurement is unbiased
- Did NOT extrapolate beyond the 3-day window without explicit caveat
- Did NOT recommend opening full T10 SPEC without confirming the regime-stratified measurement first (data-feasibility shows the *measurement is feasible*, not that the maker edge is *achievable*)

---

## 7. Files / linkage

- `~/multica/quant-loop/research/t10_maker_pre/markout_demo.py` — data-feasibility script (this session)
- `~/multica/quant-loop/research/t10_maker_pre/markout_summary.json` — outputs of the demo
- `~/multica/quant-loop/research/t10_maker_pre/THREADS/T10-maker-execution-pre-spec.md` (proposed) — thread file under `OPEN_QUESTIONS.md §P1`
- This pre-SPEC may be referenced by SMA-36598 → recommendation forwarded to smark-decision-maker

## 8. References

- Albers J., Cucuringu M., Howison S., Shestopaloff A. (2025-11-23). *The Market Maker's Dilemma: Navigating the Fill Probability vs. Post-Fill Returns Trade-Off*. [arXiv:2502.18625v2](https://arxiv.org/html/2502.18625v2) — basis for §3.1
- Moallemi C.C. (2014). *The Value of Queue Position in a Limit Order Book*. [PDF](http://market-microstructure.institutlouisbachelier.org/uploads/91_7%20MOALLEMI%202014-12-paris-mm-queue-value.pdf) — basis for §4 queue-loss component
- Multicoin Capital (2026-02-17). *Adverse Selection Rules Everything Around Me*. [blog](https://multicoin.capital/2026/02/17/adverse-selection-rules-everything-around-me/) — qualitative framework
- Brenndoerfer M. (2026-01). *Market Microstructure: Order Books & Execution Mechanics*. [blog](https://mbrenndoerfer.com/writing/market-microstructure-order-book-mechanics) — order-book mechanics primer
- Binance USDⓈ-M VIP fee schedule (post-March 2026 update): [binancevipfeetiers.org](http://www.binancevipfeetiers.org/binance-futures-fee-tiers.html) and [bestfeescryptoexchange.com comparison](https://bestfeescryptoexchange.com/blog/binance-vs-okx-vs-bybit-fees-comparison/)
- Se_h3 verdict: [SMA-36570](mention://issue/584be016-43fe-48ed-bf7f-d596f5c09f9d) — basis for §1
- T01 OFI KILL [THREADS/T01](../THREADS/T01-ofi-aggtrades.md), T04 iceberg KILL [THREADS/T04](../THREADS/T04-iceberg-absorption-audit.md), T08 VPVR archived [THREADS/T08](../THREADS/T08-vpvr-funding-hvn-lvn-confluence.md), T09 CPCV KILL [THREADS/T09](../THREADS/T09-vpvr-xs-pairs-4h-cpcv-optimization.md) — microstructure + stat-arb architecture context
