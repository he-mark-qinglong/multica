# XS funding 差因子 — 严格 OOS 验证

生成脚本：`scripts/xs_funding_oos_validation.py`（测试见 `scripts/test_xs_funding_oos_validation.py`）

## 协议

- **Train 2022-01-01 → 2024-12-31**：全部决策（所对、阈值分位数、horizon、方向）只在 train 上选择。
- **Test 2025-01-01 → 2026-07-24**（价格数据末端）：冻结配置原样应用，不调参。
- 事件阈值仍为 expanding 分位数（shift(1)，热身 90 根 8h bar）；test 段阈值合法地使用全部历史（含 train）——划分约束的是参数选择，不是信息集。
- Newey-West t：lag = 2 × horizon_bars，修正重叠前向窗口的自相关。
- 搜索族：36 个配置（2 所对 × 3 分位数 × 3 horizon × 2 方向），另对原报告 42 格子族做 Bonferroni。
- 非重叠执行版：事件触发后 horizon 小时内不再开新仓（与实盘一致）。

## Train 段选出的冻结配置

- 所对：**binance-hyperliquid**，阈值分位数 **0.9**，horizon **24h**，方向 **动量（顺着 funding 高的一方）**

### Train leaderboard（top 10，按 train NW-t 排序）

| pair | q | horizon | 方向 | n_train | mean | naive t | NW t |
|---|---|---|---|---|---|---|---|
| binance-hyperliquid | 0.9 | 24h | momentum | 1060 | 0.652% | 4.32 | 3.29 |
| binance-hyperliquid | 0.85 | 24h | momentum | 1496 | 0.545% | 4.28 | 3.13 |
| binance-hyperliquid | 0.95 | 24h | momentum | 552 | 0.654% | 3.23 | 2.65 |
| binance-hyperliquid | 0.85 | 72h | momentum | 1496 | 1.315% | 6.09 | 2.53 |
| binance-hyperliquid | 0.9 | 72h | momentum | 1060 | 1.530% | 5.72 | 2.40 |
| binance-bybit | 0.95 | 72h | reversal | 110 | 2.711% | 2.68 | 2.32 |
| binance-hyperliquid | 0.95 | 72h | momentum | 552 | 1.688% | 4.47 | 2.24 |
| binance-hyperliquid | 0.85 | 8h | momentum | 1496 | 0.150% | 2.07 | 2.15 |
| binance-hyperliquid | 0.9 | 8h | momentum | 1060 | 0.172% | 1.96 | 2.07 |
| binance-bybit | 0.95 | 24h | reversal | 110 | 2.230% | 2.46 | 2.02 |

## 分段结果（冻结配置）

| 段 | 变体 | 范围 | n | mean | naive t | NW t | 胜率 |
|---|---|---|---|---|---|---|---|
| train | overlap | BTC | 155 | 0.432% | 1.71 | 1.25 | 54.839% |
| train | overlap | ETH | 149 | 0.106% | 0.41 | 0.30 | 48.322% |
| train | overlap | SOL | 155 | 0.831% | 1.93 | 1.64 | 58.065% |
| train | overlap | BNB | 176 | 0.577% | 2.70 | 2.39 | 56.250% |
| train | overlap | AVAX | 138 | 1.384% | 2.55 | 1.69 | 55.072% |
| train | overlap | DOGE | 133 | 1.330% | 2.14 | 1.82 | 56.391% |
| train | overlap | LINK | 154 | 0.064% | 0.15 | 0.13 | 44.805% |
| train | overlap | POOLED | 1060 | 0.652% | 4.32 | 3.29 | 53.396% |
| train | nonoverlap | BTC | 70 | 0.091% | 0.24 | 0.24 | 54.286% |
| train | nonoverlap | ETH | 65 | 0.004% | 0.01 | 0.01 | 46.154% |
| train | nonoverlap | SOL | 82 | 0.172% | 0.28 | 0.32 | 57.317% |
| train | nonoverlap | BNB | 91 | 0.766% | 2.37 | 2.14 | 54.945% |
| train | nonoverlap | AVAX | 73 | 1.413% | 1.76 | 1.38 | 54.795% |
| train | nonoverlap | DOGE | 66 | 1.132% | 1.23 | 1.36 | 53.030% |
| train | nonoverlap | LINK | 68 | 0.258% | 0.38 | 0.39 | 50.000% |
| train | nonoverlap | POOLED | 515 | 0.555% | 2.43 | 2.21 | 53.204% |
| test | overlap | BTC | 7 | -1.644% | -3.34 | -3.36 | 14.286% |
| test | overlap | ETH | 22 | 0.223% | 0.29 | 0.46 | 50.000% |
| test | overlap | SOL | 59 | -0.329% | -0.55 | -0.41 | 44.068% |
| test | overlap | BNB | 19 | 0.402% | 0.76 | 1.37 | 47.368% |
| test | overlap | AVAX | 80 | -0.055% | -0.12 | -0.15 | 51.250% |
| test | overlap | DOGE | 7 | -2.778% | -2.13 | -5.91 | 14.286% |
| test | overlap | LINK | 24 | 0.406% | 0.64 | 0.47 | 45.833% |
| test | overlap | POOLED | 218 | -0.149% | -0.56 | -0.51 | 45.872% |
| test | nonoverlap | BTC | 6 | -1.522% | -2.70 | -3.27 | 16.667% |
| test | nonoverlap | ETH | 19 | 0.049% | 0.06 | 0.08 | 47.368% |
| test | nonoverlap | SOL | 38 | -0.268% | -0.35 | -0.27 | 44.737% |
| test | nonoverlap | BNB | 18 | 0.452% | 0.82 | 1.42 | 50.000% |
| test | nonoverlap | AVAX | 58 | -0.291% | -0.55 | -0.66 | 48.276% |
| test | nonoverlap | DOGE | 4 | -3.614% | -2.81 | -8.58 | 0.000% |
| test | nonoverlap | LINK | 21 | 0.634% | 0.90 | 0.73 | 52.381% |
| test | nonoverlap | POOLED | 164 | -0.172% | -0.57 | -0.52 | 45.732% |

## 多重检验校正

- Bonferroni 临界 |t|（双侧 α=0.05）：搜索族 36 → **3.20**；原报告格子族 42 → **3.24**。
- Deflated Sharpe（重叠版 test 事件流，n_trials=36）：per-event SR=-0.04，DSR 值（observed − 多重检验障碍）=-0.16（skew=0.13，kurt=3.96）。
- Deflated Sharpe（非重叠版 test 事件流，n_trials=36）：per-event SR=-0.04，DSR 值=-0.18。
- 判定口径（与 `_shared/validation/cpcv.py` 一致）：DSR 值 > 0 ⇒ 经多重检验校正后仍显著。

## 判定

**KILL**

- test 重叠版 pooled：n=218, mean=-0.149%, naive t=-0.56, NW t=-0.51, 胜率=45.872%
- test 非重叠执行版 pooled：n=164, mean=-0.172%, naive t=-0.57, NW t=-0.52, 胜率=45.732%
- NW t > 2 要求：不满足；方向一致性要求（test mean > 0 与 train 选出方向一致）：不满足；非重叠事件数 ≥ 30：满足
- Bonferroni 临界（搜索族 36）：|t| > 3.20 — 未通过
- DSR（非重叠版）：observed SR -0.04，DSR 值 -0.18（>0 才算校正后显著）— 未通过

## 残余警示（如实记录）

- binance-HL 所对的 train 段只有 2023-05 → 2024-12（HL 数据起点），约 19 个月。
- 未计手续费与资金费收付本身；纯价格收益视角。执行价用 8h 网格 close，实盘滑点未建模。
- 跨标的 pooled 统计把同时段不同标的的事件当作独立观测，相关性会抬高 pooled t；分标的 NW-t 更可信。
- 方向虽由 train 段独立选出，但本研究整体仍受原报告启发（研究者自由度无法完全消除）。
- expanding 分位数阈值只升不降（历史极端值永久抬升阈值），导致 test 段部分标的事件稀少（BTC 仅 7 个、DOGE 仅 7 个）——这本身就是该因子定义的一个结构性缺陷。
- train  leaderboard 顶部配置 NW-t 超过 Bonferroni 临界属预期：那是**选择后**的最大值，不代表任何单一配置的先验显著性；真正的检验只有 test 段，而 test 段失败。
