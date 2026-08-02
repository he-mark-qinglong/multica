# maker_simulator 连续做市模式 vs 单仓位模式 — 对比报告

- 日期: 2026-08-02
- Issue: SMA-36939 (P1-1 depth-review)
- 数据: `data/trades/BTCUSDT_aggtrades.parquet`, 2026-04-19 00:00–02:00 UTC (59,013 trades)
- 配置: `MakerSimConfig` 默认 (size $1,000, 库存上限 $5,000, maker 2bp / taker 5bp, γ=0.1, base_spread 2bp)

## 结果对比

| 指标 | single_position (legacy) | continuous (heuristic spread) | continuous (A-S optimal spread) |
|---|---|---|---|
| n_trades (round-trips) | 90 | 40 | 20 |
| quotes_generated | 20,897 | 48,136 | 52,684 |
| quotes_filled | 90 | 154 | 53 |
| fill_rate | 0.43% | 0.32% | 0.10% |
| maker_ratio | 0.50 | 0.81 | 0.76 |
| avg_pnl_bp | -2.97 | -0.88 | +0.04 |
| median_pnl_bp | -3.60 | -1.73 | +0.39 |
| win_rate | 10% | 35% | 65% |
| max_drawdown (USD) | -27.4 | -30.5 | -4.9 |
| flatten_count | — | 36 | 17 |
| max_abs_inventory_qty | — | 0.0662 | 0.0662 |
| avg_abs_inventory_usd | — | 1,318 | 1,284 |
| realized_pnl_usd | — | -26.07 | -2.82 |
| exit_reasons | sl 81 / time 9 | flatten_limit 24 / flatten_sl 2 / flatten_time 10 / spread_capture 4 | flatten_limit 3 / flatten_sl 4 / flatten_time 10 / spread_capture 3 |

## 发现

1. **连续做市机制已激活**: fill 后继续报价 (48k quotes vs legacy 21k), 库存贯穿全程
   (avg |inv| $1,318), `reservation_price` 每 tick 接收 `inventory.net_qty`,
   `flatten_required` 按 cap/time/SL 触发 taker 平仓 (36 次)。
2. **默认 γ=0.1 的 A-S 库存偏移在 BTC 价格尺度下低于浮点分辨率**
   (q·γ·σ²·τ ~ 1e-10 USD vs tick 0.01 USD) — 偏斜存在但无实际减仓压力,
   库存单侧堆积至上限, 24/40 退出为 flatten_limit (taker 5bp) → realized -26 USD。
   实际可用的 γ 需放大若干数量级, 或改用 quote 不对称 (quoting_engine 的
   is_at_limit 单边报价已生效)。
3. **A-S optimal spread 显著改善质量**: 更宽报价 → fill 数 154→53, 但
   win_rate 65%, avg +0.04bp, flatten_limit 24→3, realized -26→-2.8 USD。
4. **两种配置在此 2h 窗口均未盈利** — 与 T10 maker-dilemma 结论一致
   (Albers et al. 2025: fill 概率与 post-fill return 负相关; VIP0 maker 2bp 成本地板)。
   本任务是基础设施升级, 非策略 PASS 声明。
5. **PnL 记账为 cash-exact average-cost**: 部分减仓保留原成本基础,
   翻转仓位的余仓基础重置为成交价, 残余 dust (<10% 一手) 直接整仓了结
   (避免混合 VWAP 产生的伪基础)。

## 复现

```bash
python3.12 - <<'EOF'
import sys; sys.path.insert(0, '.')
import pandas as pd
from _shared.market_making.maker_simulator import MakerSimConfig, simulate_market_making
df = pd.read_parquet('data/trades/BTCUSDT_aggtrades.parquet',
                     columns=['ts','price','qty','is_buyer_maker'],
                     filters=[('ts','>=',pd.Timestamp('2026-04-19', tz='UTC')),
                              ('ts','<',pd.Timestamp('2026-04-19 02:00:00', tz='UTC'))])
for mode in ('single_position', 'continuous'):
    cfg = MakerSimConfig(mode=mode, start_ts='2026-04-19', end_ts='2026-04-19 02:00:00')
    trades, metrics = simulate_market_making(df, cfg)
    print(mode, metrics['n_trades'], metrics['avg_pnl_bp'])
EOF
```
