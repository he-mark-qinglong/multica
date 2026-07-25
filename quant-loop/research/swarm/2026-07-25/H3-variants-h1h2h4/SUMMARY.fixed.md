# H1/H2/H3/H4 变体评估（BTC+SOL）— FIXED（2026-07-25, SMA-35145 follow-up）

> 输出目录：`/Users/mark/multica/quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/`
> 本文件取代 `SUMMARY.md` / `results/metrics.json` 中的结论（原始文件保留作为 bug 证据）。

## Bug 根因

`run_btcsol_variants.py:74`（已就地修复）：`_backtest_pair_with_cost` patch 把交易日志口径的
pair 往返成本公式 `2*2*(fee+slip) bps`（full-spread 单位）直接扣在 `bar_return` 上，而
`bar_return = pos*(a_ret-b_ret)/2` 是 half-spread（每条腿 0.5 名义）单位。正确公式是
`2*(fee+slip) bps` —— 每笔交易被**双倍扣费**。

为什么 Sharpe 会到 -43：H3 的毛利极低，41k 笔交易的 full-spread 毛利合计仅 ≈ 1.0
（≈ 2.5 bps/笔，见 `H3-baseline-repro/trades_full_history.csv`：Σpnl_pct(net) = -31.76）。
任何按真实成本从 bar return 扣费的做法都会击穿净值；双倍扣费只是雪上加霜。
旁证：原 runner 自己的 `results/h3_cost_sensitivity.csv` 在 4 bps/symbol RT
（= baseline 的 1+1 bps/side）下 Sharpe 已 = -26.3，而 baseline 同成本下为 +1.47。

注意：baseline 的方法论是**净值曲线不扣费**（成本只记在 trade log），标题指标为 gross，
费用敏感性用 fee-shock replay（每日 drag = 当日平仓笔数 × pair_rt_bps × 0.5%）。
本修复管线完全对齐该方法论。

## 修复内容

1. `run_btcsol_variants.py:74` —— 成本公式 `2*2*(fee+slip)` → `2*(fee+slip)`（half-spread 单位），含注释。
2. 新增 `run_btcsol_variants_fixed.py` —— 与 `H3-baseline-repro/repro_h3_baseline.py` 完全一致的数据窗口
   （funding 起点 2021-11-20 16:01 起裁切）、成本模型（1+1 bps/side）、指标口径
   （`compute_metrics` + daily-resampled Sharpe + 7 窗 walk-forward + bootstrap seed 42）
   和 fee-shock replay（4/24/60 bps pair RT）。

## Sanity check：H3 vs baseline —— 完全一致

| 指标 | baseline | fixed H3 | delta |
|------|----------|----------|-------|
| OOS Sharpe (7窗均值) | 1.8747696 | 1.8747696 | 0.0 |
| Bootstrap CI lower | 0.8878991 | 0.8878991 | ~1e-16 |
| Full-history Sharpe (daily-resampled) | 1.468 | 1.468 | 0 |
| Full-history trades | 40,963 | 40,963 | 0 |
| Fee shock 60bps Sharpe | -0.021 | -0.021 | 0 |

## 修正后 H1-H4 结果（BTC+SOL，1+1 bps/side，2021-11-20 → 2026-07-17，2,448,219 bars）

| 变体 | Full Sharpe (daily) | OOS Sharpe | CI lower | CI upper | OOS ann | OOS worst DD | Trades | Fee-shock 60bps Sharpe |
|------|--------------------:|-----------:|---------:|---------:|--------:|-------------:|-------:|-----------------------:|
| H1 | 1.517 | **1.597** | 0.832 | 2.295 | 18.35% | -9.84% | 14,221 | 0.728 |
| H2 | 0.215 | **0.369** | -0.543 | 1.483 | 0.63% | -9.78% | 5,301 | -0.157 |
| H3 | 1.468 | **1.875** | 0.888 | 2.936 | 31.79% | -13.30% | 40,963 | -0.021 |
| H4 | 1.967 | **1.187** | 0.378 | 2.120 | 0.07%* | -0.12%* | 550 | -3.513* |

\* H4 走 `build_h4_portfolio`，bar return 被 per_pair_notional_pct=0.02 缩放（campaign sizing 规格），
Sharpe 对标量缩放不变，但 ann/DD 被缩小 ~50×，fee-shock drag（按笔数计）相对 0.02 缩放的净值被放大，
三者不可与 H1-H3 直接比较；只有 OOS Sharpe / CI 横向可比。

## 结论

- **原相对排序 H4>H2>H1>H3 不成立。** 修正后 OOS Sharpe 排序为 **H3 (1.875) > H1 (1.597) > H4 (1.187) > H2 (0.369)** —— 完全反转。
  原排序是 H4 的 0.02 sizing 缩放（收益和扣费同缩 50×）+ 双倍扣费 × 交易频率共同制造的假象。
- H3 仍是家族内最强且唯一与既有 baseline 完全一致的配置；H1 次之且 CI lower 0.832 也过 0.5。
- H2（VPVR edge touch）OOS Sharpe 0.369、CI 跨 0，明显最弱。
- 所有变体的 fee-shock 60bps 都 ≤ 0.73，H3 ≈ -0.02，与 AGENTS.md 记录一致：该家族对高成本极度敏感，gross 毛利仅 ~2.5 bps/笔。

## 输出文件

- `results/metrics.fixed.json` —— 修正后全部指标（含 per-window、fee sensitivity、sanity 块）
- `results/equity_{H1,H2,H3,H4}_daily.fixed.csv` —— 每日净值
- `results/equity_curves.fixed.png`、`results/oos_metrics.fixed.png`
- `run_btcsol_variants_fixed.py` —— 修复后的管线（可复跑）
- 原始 `results/metrics.json` / `SUMMARY.md` 保留未动，作为 bug 证据
