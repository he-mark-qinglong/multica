## SMA-34925 result — BTC-only cross-exchange funding delta 4h

**VERDICT: FAIL | oos_sharpe=0.00 | ann=0.00% | maxdd=0.00% | reason=STRUCTURAL: max|Δ|=0.000707 across 1650d Binance×Bybit overlap is below all 3 spec thresholds (0.0005/0.001/0.0015); 0 OOS entries; 3 frameworks agree | next=U1 family exhausted on Binance+Bybit BTC; OKX/Deribit would need fresh fetch (out of scope)**

### Data
- **Cached cross-exchange BTC funding:** Binance USDT-M (`data/funding/BTCUSDT.parquet`, 5100 rows) + Bybit (`funding_analysis/BTCUSDT_bybit_funding.parquet`, 4956 rows). No OKX/Deribit cached.
- **Overlap window:** 2022-01-01 → 2026-07-10 (1650 days, 2849 common 8h funding events).
- **Δ_funding = Binance − Bybit** distribution: mean -2e-6, std 7.1e-5, |Δ| 99th pct = 0.000245, **max |Δ| = 0.000707**. Min threshold 0.0005 fires 4× on the full overlap (0.14%); 0× on the OOS segment (last 182 days).

### Backtest (in-house + vectorbt + backtrader, daily-resampled Sharpe, freqtrade defaults: taker 4bps + slippage 2bps/leg, 12bps round-trip)
| threshold | OOS entries (in-house = vbt = bt) | OOS Sharpe | ann% | maxDD% | CI lower |
|---|---|---|---|---|---|
| 0.0005 | 0 | 0.00 | 0.00 | 0.00 | 0.000 |
| 0.001 | 0 | 0.00 | 0.00 | 0.00 | 0.000 |
| 0.0015 | 0 | 0.00 | 0.00 | 0.00 | 0.000 |

All G1/G2/G4/G6 gates FAIL (maxDD trivially 0). Bonferroni α=0.0125 not reached (signal never fires).

### Sensitivity sweep (lower thresholds, OOS only, 1096 bars)
| threshold | entries | Sharpe | ann% | maxDD% | CI lower |
|---|---|---|---|---|---|
| 0.0001 (1bp) | 55 | -1.627 | -12.03 | -6.33 | -4.070 |
| 0.0002 (2bp) | 0 | — | — | — | — |
| 0.0003–0.001 | 0 | — | — | — | — |

Even at the lowest meaningful threshold (1bp), the edge is **negative** (Sharpe -1.63, CI lower -4.07). Strategy has no positive expectancy on Binance×Bybit BTC at any threshold where it actually fires.

### W5 reproducibility (framework divergence check)
- in-house (`backtest_inhouse.py`): 0 / 0 / 0 entries at the 3 spec thresholds
- vectorbt (`backtest_vectorbt.py`): 0 / 0 / 0
- backtrader (`backtest_backtrader.py`): 0 / 0 / 0

3-way agreement. Not a W5 KILL — it's a structural FAIL: the spec thresholds exceed the natural variability of the only cross-exchange sample we have cached.

### Why the previous SMA-34733-V3 BTC leg Sharpe=6.65 claim didn't reproduce here
That run used a **multi-exchange sample** (Binance × Bybit × OKX × Deribit) on a longer cross-pair Δ. We only have Binance + Bybit cached, and the BTC-only Δ between them is too small to drive the 0.0005+ threshold strategy. The original "Sharpe 6.65 on the BTC leg" was probably an artifact of either a wider cross-exchange sample or a different Δ computation — neither reproducible from the cached data alone.

### Artefacts
- `~/multica/quant-loop/strategies/sma_34925_btc_funding_delta/backtest_inhouse.py`
- `~/multica/quant-loop/strategies/sma_34925_btc_funding_delta/backtest_vectorbt.py`
- `~/multica/quant-loop/strategies/sma_34925_btc_funding_delta/backtest_backtrader.py`
- `~/multica/quant-loop/strategies/sma_34925_btc_funding_delta/sensitivity_sweep.py`
- Results: `results/summary_inhouse.json`, `results/summary_vectorbt.json`, `results/summary_backtrader.json`, `results/summary_sensitivity.json`
- Ledger row appended to `~/multica/results-ledger.md` KILLED section; U1 marked as tested.

### Recommendation
Do **not** spend more cycles on this variant until either (a) OKX + Deribit BTC funding is fetched fresh and cached, or (b) the spec threshold is lowered to a band the Binance×Bybit sample actually supports (≤1bp, which then shows a negative edge anyway). Both look like dead-ends; the family is exhausted on this data sample.