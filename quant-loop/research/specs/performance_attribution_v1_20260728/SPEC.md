# SPEC — Performance Attribution v1 (strategy-level PnL decomposition)

- Issue: SMA-35757 (Research #87 — performance attribution), parent SMA-35669 (MAP-P8 Research & Validation Tools)
- Thread: T14 (`research/THREADS/T14-performance-attribution.md`)
- Author: quant-researcher (L2), 2026-07-28
- Status: SHIPPED (module + tests + EVIDENCE on two donor ledgers; gates A0-A5 PASS, A6 FAIL-with-finding — see §5)

## 1. Why this tool

The research loop keeps re-deriving the same question by hand: **was this
strategy killed by cost, or by mechanism?** Every family-seal in cycle-46
(`mtf_xs_pairs` 200× fee-shock, T01/T04 cost-cap kills, T11 mechanism kill)
is an attribution verdict computed ad-hoc, per issue, with no shared code.
Performance attribution makes the verdict mechanical: one function turns a
closed-trade ledger into gross-vs-cost decomposition + a classification.

Brinson allocation/selection attribution does NOT apply (no sectors, no
benchmark weights). The attribution axes that matter for crypto-perp strategy
research are:

1. **Cost attribution** — gross signal PnL − execution cost = net PnL, swept
   over cost scenarios, with break-even cost solved analytically.
2. **Conditional cuts** — per exit_reason / direction / year / holding-time
   bucket (where does the edge live, where does it bleed).
3. **Kill classification** — MECHANISM_KILL (gross ≤ 0) vs COST_CAP_KILL
   (gross > 0, net ≤ 0 at ratified cost) vs VIABLE_AT_COST.
4. **Alpha/beta split** (optional input) — OLS of daily net returns on a
   market series; answers "is this just market beta" (adjacent to T07).

## 2. Dedup vs existing work

- `execution/slippage_attribution_p7exec_043/` — per-fill live-execution
  slippage decomposition (spread vs impact). Different layer: live execution
  forensics, not strategy research attribution. No overlap.
- `_shared/execution/cost_model.py` — cost constants source (ratified 11bp/side
  = 22bp RT, SMA-34900/34913). This tool consumes those constants, does not
  redefine them.
- `portfolio-risk` skill / T07 — cross-strategy correlation + marginal Sharpe.
  Complementary; T07 question #3 ("is this just beta") is served by §4 here.
- `results-ledger.md` — verdict tracker, no decomposition. Attribution output
  is a candidate future column source.

## 3. Input contract

Canonical closed-trade ledger (DataFrame), two schemas auto-detected:

- **single**: `symbol, direction ∈ {long,short}, entry_ts, exit_ts, entry_price, exit_price`
  (aliases: `entry_date/exit_date`)
- **pair**: `pair, direction ∈ {long_a_short_b,short_a_long_b}, entry_ts, exit_ts, entry_price_a, entry_price_b, exit_price_a, exit_price_b`

Gross return is ALWAYS recomputed from prices (never trusts the ledger's
`pnl_pct`, whose cost convention varies per strategy — H3 embeds 8bp RT,
trend_multi embeds ~0). Cost is parameterized by `CostSpec`:

```python
CostSpec(fee_bps_per_side, slippage_bps_per_side, fills_per_round_trip)
# single: fills=2 (entry+exit). pair: fills=4 (2 legs × entry+exit).
```

Sentinels (reject, not warn): `exit_ts < entry_ts`, non-positive price,
unknown direction, missing/NaN required column. (Codified from the T11
round-1/round-2 look-ahead lesson: validate the measurement before trusting
the numbers.)

## 4. Public API (`_shared/attribution/`)

```python
normalize_trades(df) -> df            # schema detection + sentinel validation
attribute(trades, scenarios) -> dict  # full report (deterministic)
alpha_beta(daily_net, market_daily) -> dict   # optional beta split
write_report(report, path)            # byte-deterministic JSON
```

Report per cost scenario: n_trades, gross_sum/cost_sum/net_sum,
cost_drag_ratio = cost_sum/gross_sum, win-rate gross vs net,
mean gross/net bp per trade, daily-aggregated Sharpe (gross vs net),
break_even_bps_per_side (analytic: gross_sum·1e4 / (n·fills)), verdict.
Cuts at the ratified scenario: exit_reason, direction, year, holding-time
quartile.

## 5. Pre-registered acceptance gates

| gate | statement | result |
|---|---|---|
| A0 | Accounting identity: per scenario, net_sum == gross_sum − cost_sum to 1e-12 | PASS (test + donor) |
| A1 | Sentinels reject exit<entry, price≤0, bad direction, missing column | PASS (test) |
| A2 | Hand-computed synthetic fixtures (single + pair, 3 trades) match to 1e-15 | PASS (test) |
| A3 | Donor H3 ledger at its config cost (2bp/side, 8bp RT pair): reconstructed net_sum reproduces ledger Σpnl_pct within 1e-9 | PASS (44,845 trades, abs_err 7.8e-13) |
| A4 | Determinism: same input → byte-identical JSON across two runs | PASS (test + donor md5) |
| A5 | Classification matches known verdicts: H3 = COST_CAP_KILL at ratified 11bp/side (family-seal 2026-07-26); trend_multi BTC = MECHANISM_KILL (ledger Sharpe −3.63) | PASS (both donors) |
| A6 | Sign flip: H3 net_sum > 0 at its config cost (2bp/side) and < 0 at ratified (11bp/side) — the fee-shock family-seal reproduced mechanically | **FAIL — pre-registered premise false** (finding below) |

### A6 finding (recorded, gate not redefined)

The pre-registered premise "H3 is net-positive at its own config cost" is
FALSE: reconstructed net_sum at 8bp RT = **−33.62** (identical to the
ledger's own Σpnl_pct, per A3). Root cause: `results/summary.json`'s
"PROFITABLE" tag and Sharpe 2.32 are computed from a **cost-free per-bar
equity path** — `mtf_xs_pairs_base_20260718.py:560` (`pnl_per_bar` has no
cost term; cost exists only in the trade log at line 576). This is the
SMA-36566 fee-shock bug class, surfaced mechanically by reconstruction
rather than by manual audit. The family-seal direction (cost kill) is
confirmed with convergent evidence; the ledger has essentially zero cost
headroom (break-even 0.126 bps/side, gross edge +0.5 bp/trade over 44,845
trades). Quantitative divergence vs curator's "break-even 20bps RT" is
convention-driven (full-notional trade-log units vs half-notional × scale ×
compounded equity path) — flagged for quant-analyst as a口径 audit item,
not resolved here. Per T11 discipline: a pre-registered gate checked against
pre-registered measurement that fails is a result, not a bug to patch.

## 6. EVIDENCE donors

1. `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/trades_all.csv`
   (44,845 BTC/SOL pair trades, 2022-01 → 2026-07) — the family sealed on
   cost. Attribution must reproduce the seal mechanically (A5/A6).
2. `strategies/trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/results/trades_BTCUSDT.csv`
   (245 trades, Sharpe −3.63 in results-ledger) — mechanism-kill example.

Output: `analysis/attribution/{mtf_h3_btcsol,trend_multi_btc}_attribution.json`.

## 7. Non-goals

- No Brinson sector attribution (N/A). No live-fill forensics (p7exec_043
  owns that). No cross-strategy portfolio math (T07/portfolio-risk owns that).
- Daily-aggregated Sharpe is computed on closed-trade PnL summed by exit day
  — an approximation of the mark-to-market equity curve, documented as such;
  the authoritative equity walk remains `_shared/run_backtest.py`.
</content>
