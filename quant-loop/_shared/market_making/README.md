# Market Making Modules — Jane Street Core Domain Supplement

对照 Jane Street《Probability & Markets》指南，为 multica quant-loop 补充做市核心能力的模块套件。

## 设计理念

Jane Street 的核心交易哲学可以归纳为三个支柱：

| Jane Street 概念 | 本模块 | 核心函数 |
|---|---|---|
| **Expected Value** — "buy for less, sell for more" | `fair_value.py` + `reservation_price.py` | `compute_fair_value()`, `reservation_price()` |
| **Making Markets** — "I'm 2 at 4, 10 up" | `inventory.py` + `quoting_engine.py` | `generate_quotes()`, `update_inventory()` |
| **Adverse Selection** — "my fill means I'm probably wrong" | `adverse_selection.py` | `on_fill()`, `belief_update()`, `is_quoting_allowed()` |

## 模块清单

```
_shared/market_making/
├── __init__.py              # 统一导出
├── fair_value.py            # 公允价值：microprice / VWAP / VPVR-POC 合成
├── reservation_price.py     # Avellaneda-Stoikov 预留价格 r = s - q·γ·σ²·(T-t)
├── inventory.py             # 库存状态：不可变更新、偏斜系数、强制平仓
├── adverse_selection.py     # 逆向选择：fill→penalty、sweep 检测、冷却期、信念更新
├── quoting_engine.py        # 报价引擎：动态 spread + 库存偏斜 + tick 对齐
├── maker_simulator.py       # 回测模拟器：aggTrades 逐 tick 回放 → list[Trade]（single_position / continuous 双模式，见下）
└── tests/                   # 单元测试（pytest 全绿）
    ├── test_fair_value.py
    ├── test_reservation_price.py
    ├── test_inventory.py
    ├── test_adverse_selection.py
    ├── test_quoting_engine.py
    ├── test_maker_simulator.py
    └── test_maker_simulator_continuous.py
```

## 模拟器模式（2026-08-02, SMA-36939）

`MakerSimConfig.mode`：

- `single_position`（默认，legacy）— 单仓位：fill 后停止报价，TP/SL/time 退出后进入下一 round-trip；reservation price 恒见 `inventory_qty=0`。
- `continuous` — 连续做市：fill 后继续双边报价，库存贯穿全程并输入 `reservation_price`（A-S 库存偏斜激活）；仅当 `inventory.flatten_required` 触发（达限/超时/止损）才 taker 平仓；EOD 强制平仓。减仓侧 fill  capped 在当前库存，残余 dust（<10% 一手）整仓了结，PnL 按 average-cost cash-exact 记账。

`MakerSimConfig.spread_mode = "optimal"` 时以 `optimal_spread.optimal_half_spread`（A-S 闭式半价差）替代启发式 base spread（vol 分量已含在闭式解中，自动置 0 避免重复计）。

已知边界：默认 γ=0.1 时 A-S 库存偏移量在 BTC 价格尺度下远低于 tick（~1e-10 USD vs 0.01 USD），偏斜数学上正确但无实际减仓压力；实战需放大 γ 或依赖达限单边报价。详见 `reports/maker_sim_continuous_vs_single_2026-08-02.md`。

## 参数调优指南

### 公允价值权重

`compute_fair_value()` 默认权重：microprice 0.4 / VWAP 0.3 / VPVR-POC 0.3

- 高波动环境 → 降低 VPVR 权重（历史 POC 可能过时）
- 趋势市场 → 提高 microprice 权重（反映实时买卖压力）

### Avellaneda-Stoikov γ

`gamma` 控制库存风险厌恶强度：

- γ = 0.1（默认）— 适度偏移，适合 $5,000 库存上限
- γ = 0.5 — 激进减仓，适合高波动环境
- γ = 0.01 — 几乎不偏移，适合做市初期的低风险测试

### 逆向选择校准

`expected_sweep_cost_bp = 1.74` 来自 T10 pre-SPEC 实测（BTCUSDT aggTrades, 2026-04-19→22, 5M trades）

- Sweep markout 88.3% 的成交来自 sweep（连续同向打印）
- 冷却期 `sweep_cooldown_seconds = 5.0` 覆盖大部分 adverse drift 窗口

## 与现有基础设施的集成

```
maker_simulator.py
    ├─► fair_value ← vpvr.py (已有)
    ├─► quoting_engine ← MCLS sizing (已有，通过 mcls_size_multiplier)
    └─► output: list[Trade]
            ├─► run_backtest.py (已有) → equity curve
            └─► gates/enforce.py (已有) → G1-G7 + T1 验证
```

## 已知限制

1. **无 L2 数据** — fill 判定基于 aggTrades 推断（last_price ± spread），非真实 order book。T10 markout_demo 已验证此方法的可行性。
2. **单标的** — 初始版本仅支持 BTCUSDT，架构预留多标的扩展。
3. **未实盘** — 本模块构建的是可回测的能力，实盘部署需 T10 pilot 决策通过后另行规划。
4. **maker_fee 假设** — 使用 VIP0 费率（2bp/side），达到 VIP3+ 后可调整 config。

## 参考文献

- Avellaneda, M. & Stoikov, S. (2008). "High-frequency trading in a limit order book"
- Glosten, L. & Harris, L. (1988). "Estimating the Components of the Bid/Ask Spread"
- Albers, P. et al. (2025). "The Market Maker's Dilemma", arXiv:2502.18625v2
- Bailey, D. & López de Prado, M. (2014). "The Deflated Sharpe Ratio"
- Jane Street. "Probability & Markets Guide"
