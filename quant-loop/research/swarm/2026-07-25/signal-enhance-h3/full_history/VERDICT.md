# VERDICT — Signal-Enhance-H3 (BTC+SOL, 1m/15m/2h MTF)

> **Status (2026-07-26, post-fix):** KILL — `fee-shock replay`口径修正后,策略在任何 pair_rt_bps ≥ 20 的 cost tier 都是负 mean net/trade,在 24 bps 即 equity 归零。"fee-robust Sharpe 10.41 @ 60bps" 是 `per_trade_fraction=0.005` 缩放 bug 的 artifact。
>
> **Pre-registered gates:** 详情见 [`SPEC_signal_enhance_h3_fullhist.md`](SPEC_signal_enhance_h3_fullhist.md)。本 VERDICT 仅在 §3 给出 fee-shock 修正后的口径变更结论,其它 gates(Sharpe ≥ 1.0、OOS 7 窗、bootstrap CI 等)由后续 spec review pass 处理。

---

## §1 策略一句话

SE-H3 = H3 baseline + 在 `_backtest_pair` 内引入 **favorable slope entry hook**(只允许 z-score 已经往均值方向回撤时入场),叠加 `adverse_stop_z=0.7` 提前止损。理论依据是 H1 z-slope 入口的对称版:不是过滤「逆势入场」(H1 是),而是过滤「顺 z 趋势过强入场」(可能继续走到 regime_break 阈值 9.0 之前已经被截断)。

## §2 实现 lock

- `se_h3_loop.py` L62-265 — `backtest_pair_se`,favorable slope 通过 `signals["z_slope_fav_4"]`(NEVER `z_slope_15m`,那条 key 触发 H1 相反方向)实现。
- Exit 链重排为 `z_mean_revert → regime_break(9.0) → adverse_stop(0.7) → max_holding`(4 个独立 if,**不**是 elif)。
- `gross_pct` 字段新增进 trade dict(pre-cost pair return);`pnl_pct` 仍是 NET(base L576-577 语义,full pair pct)。

## §3 Fee-shock replay 口径修正(SMA-36566,本任务产物)

### §3.1 Bug

`run_btcsol_variants_fixed.py:313` 与 `repro_h3_baseline.py:282` 的 `fee_shock_metrics` 用:

```python
drag = counts * (pair_rt_bps / 10_000) * per_trade_fraction   # default 0.005
```

引擎本身在 `se_h3_loop.py:227` 把 cost 记在 trade log 里:

```python
cost = 2.0 * 2.0 * (fee_bps + slip_bps) / 10_000.0   # = 8 bps @ fee=slip=1, full pair pct
net  = pct - cost                                     # pct 也是 full pair pct
```

也就是说 trade log 的 `pnl_pct` 是 **full pair pct** 量纲的;但 fee-shock replay 里用 `per_trade_fraction=0.005` 把 cost 按 0.5% 名义扣,**比权益曲线的本金口径小 200 倍**。整族 fee-shock 结论(H1 "fee-robust +0.728"、se_h3 "60bps 还活着")都是这个 artifact。

### §3.2 Fix

`per_trade_fraction = 1.0`(默认)= full pair pct basis,与 trade log `pnl_pct` 量纲一致。脚本见 `fee_shock_fix.py`,可重入:

```bash
python3 fee_shock_fix.py \
  --equity-csv results/se_h3_equity_daily.csv \
  --trades-csv  results/se_h3_trades.csv \
  --out-json    results/se_h3_fee_shock.fixed.json \
  --buggy-out-json results/se_h3_fee_shock.buggy.json \
  --per-trade-fraction 1.0
```

可选 `0.5`(half-spread basis,严格匹配 bar-return 归一化)、`2.0`(sanity 上界)。

### §3.3 修正后的口径下 SE-H3 数字

| pair_rt_bps | Buggy Sharpe | **FIXED Sharpe** | FIXED ann.return | FIXED total | FIXED MDD | Verdict |
|------------:|-------------:|-----------------:|-----------------:|------------:|----------:|---------|
| 4           | 10.74        | **5.98**         | +126%            | +4369%      | -5.46%    | LIVE-but-marginal |
| 24          | 10.62        | **-17.33**       | -93%             | -100%       | -100%     | **DEAD** |
| 60          | 10.41        | **-38.80**       | -100%            | -100%       | -100%     | **DEAD** |

**Sizing-independent break-even**(只看 trade log,无 equity curve 假设):

| pair_rt_bps | mean_net_bps/trade | pct trades net>0 | verdict |
|------------:|-------------------:|-----------------:|---------|
|  0          | +17.78             | 72.4%            | LIVE |
|  8 (engine) |  +9.78             | 69.6%            | LIVE |
| 12          |  +5.78             | 67.7%            | LIVE |
| 16          |  +1.78             | 64.9%            | LIVE |
| **20**      |  **-2.22**         | 60.9%            | **DIE** |
| 24          |  -6.22             | 56.8%            | DIE |
| 60          | -42.22             | 20.6%            | DIE |

break-even pair_rt_bps = **20.0**(正好在 mean gross 17.78 bps/trade 之上,加引擎 8 bps 后净 9.78 bps/trade → 任何 pair_rt_bps > 17.78 都会让每笔交易平均亏损)。

### §3.4 WF window 3 修正

只有 window 3 的 OOS fee-shock 跑过(`se_h3_wf_window_3.json`),其它 6 窗的 fee-shock 待 W4-T08* 系列重跑。修正后:

| pair_rt_bps | Buggy Sharpe | **FIXED Sharpe** |
|------------:|-------------:|-----------------:|
| 4           | 14.10        | **10.23**        |
| 24          | 14.10        | **6.90**         |
| 60          | 14.10        | **1.86**(MARGINAL)|

> Caveat: window-3 fee-shock 把 window-3 trades 应用到 FULL-HISTORY equity 曲线(因为没有保存 window-3 独立 equity CSV);严格意义要做 window-3 独立 equity 重跑,留作 follow-up。

### §3.5 H1/H3 baseline 在修正口径下

H3-variants 全家族(fee-shock 同样用 `per_trade_fraction=0.005`):

| Hyp  | Buggy 60bps Sharpe | **FIXED 60bps Sharpe** | mean gross bps | mean net @8bps | break-even RT |
|-----:|-------------------:|-----------------------:|---------------:|---------------:|--------------:|
| H1   | +0.728             | **-21.61**             | 1.18           | -6.82          | 4 bps         |
| H2   | -0.157             | **-15.19**             | 0.38           | -7.62          | 4 bps         |
| H3   | -0.021             | **-47.95**             | 0.52           | -7.48          | 4 bps         |
| H4   | -3.513             | **-7.74**              | 9.42           | +1.42          | 12 bps        |

修正口径下 H1 "fee-robust" 结论**完全失效**:H1 mean gross 仅 1.18 bps/trade,引擎 8 bps cost 已经把它打成净 -6.82 bps/trade,任何额外 cost tier 都死。H1 + H3 + H4 全员 dead @4 bps。

### §3.6 决策建议(转交 smark-decision-maker)

1. **不要 KEEP se_h3**:break-even 20 bps,只有 maker rebate + VIP3+ 才可能压到 <20 bps;Binance VIP0 实际 RT 6-12 bps,freqtrade 默认 24 bps RT(对应 strategy 死区)。
2. **不要 KEEP 任何 H3-variants 家族**:H1/H2/H3 mean gross < 1.2 bps/trade,基础 4 bps cost 已死。
3. **如果坚持要 ship**:必须先证明 (a) 真实 aggTrades 执行 < 20 bps RT,(b) se_h3 favorable slope 的 edge 在 real cost 下仍然 > 0——两个 gate 都过才考虑。
4. **本任务产物已归档**:
   - `results/se_h3_fee_shock.fixed.json`(4/24/60 bps 修正结果 + break-even table)
   - `results/se_h3_fee_shock.buggy.json`(对照,per_trade_fraction=0.005)
   - `results/se_h3_wf_fee_shock_3.fixed.json`(window 3 修正)
   - `results/se_h3_full_history_metrics.fixed.json`(VERDICT §3 数字)
   - `fee_shock_fix.py`(可重入 fix 脚本)
   - `../H3-variants-h1h2h4/run_full_history_fixedfee.py`(H1-H4 修正重跑)
   - `../H3-variants-h1h2h4/results/SUMMARY.fixedfee.md`(H1-H4 修正 cross-table)

> 在修正 fee-shock 结论出来之前,**不得**对 se_h3 或 H3-variants 任何成员下 KEEP/KILL final verdict——这是本任务的强制 precondition,本任务的成果提供了 verdict 所需的 cost-adjusted Sharpe。