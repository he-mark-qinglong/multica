# XS 工作包：跨所 funding 差因子事件研究

生成脚本：`scripts/xs_funding_factor.py`（纯函数核心，测试见 `_shared/validation/test_xs_funding_factor.py`）

## 方法

- 因子 = 同一标的两所 funding 之差（8h 网格；Hyperliquid 小时级聚合为 8h 均值；
  binance/bybit 时间戳亚秒抖动先 floor 到小时）
- 事件：|diff| > 历史（expanding，shift(1)，无前视）90 分位，热身期 90 根 8h bar（≈30 天）
- 方向：逆着 funding 高的一方（`direction = -sign(diff)`），高 funding 所多头更拥挤 → 预期回落
- 收益：Binance USDM perp 30m close 重采样到 8h，测 +8h/+24h/+72h
- 基线：同一方向规则在**全部** 8h 网格点上的收益（分离“极端度过滤”的增量信息）

## 数据对齐说明（如实记录）

- binance/bybit：8h，00/08/16 UTC 网格；部分行有亚秒抖动（如 `16:00:00.004`）
- **SOLUSDT 异常段**：2022-11 中旬（binance 11-09..18，bybit 11-10..12-20）funding 曾改为 2h 频率，
  本研究按 8h 桶均值处理，该段 diff 质量较低但占比极小
- hyperliquid：~1h 不规则（实测间隔 0.8h–8.4h），2023-05 起 → binance-HL 组合样本期 2023-05 至 2026-07-24
- binance-bybit 样本期 ≈ 2021-11（bybit 较晚标的）至 2026-07-24（价格数据末端）
- funding 数据到 2026-08-02，价格数据到 2026-07-24；末端事件无前向收益，自动剔除

## 汇总（跨标的 pooled）

| 组合 | horizon | n_events | mean signal ret | t | 胜率 | excess vs 基线 |
|---|---|---|---|---|---|---|
| binance-bybit | 8h | 469 | 0.263% | 0.65 | 50.959% | 0.276% |
| binance-bybit | 24h | 468 | 0.737% | 1.02 | 51.496% | 0.753% |
| binance-bybit | 72h | 468 | -0.210% | -0.18 | 46.154% | -0.102% |
| binance-hyperliquid | 8h | 1278 | -0.108% | -0.51 | 50.000% | -0.075% |
| binance-hyperliquid | 24h | 1278 | -0.515% | -1.45 | 47.887% | -0.410% |
| binance-hyperliquid | 72h | 1278 | -1.306% | -2.09 | 46.557% | -1.056% |

## 分标的明细（t>2 判为有效信号）

| 标的 | 组合 | horizon | n | mean ret | t | 胜率 | 基线 mean | excess |
|---|---|---|---|---|---|---|---|---|
| BTC | binance-bybit | 8h | 12 | 0.034% | 0.05 | 58.333% | 0.038% | -0.003% |
| BTC | binance-bybit | 24h | 12 | -1.082% | -0.83 | 58.333% | 0.063% | -1.146% |
| BTC | binance-bybit | 72h | 12 | -3.164% | -1.42 | 33.333% | 0.028% | -3.192% |
| BTC | binance-hyperliquid | 8h | 162 | -0.018% | -0.15 | 46.914% | -0.026% | 0.008% |
| BTC | binance-hyperliquid | 24h | 162 | -0.342% | -1.40 | 46.914% | -0.082% | -0.260% |
| BTC | binance-hyperliquid | 72h | 162 | -1.323% | -3.32 | 45.679% | -0.201% | -1.121% |
| ETH | binance-bybit | 8h | 19 | 0.328% | 0.72 | 52.632% | 0.028% | 0.300% |
| ETH | binance-bybit | 24h | 19 | 1.482% | 1.99 | 57.895% | 0.030% | 1.452% |
| ETH | binance-bybit | 72h | 19 | 0.686% | 0.57 | 68.421% | 0.089% | 0.597% |
| ETH | binance-hyperliquid | 8h | 171 | 0.049% | 0.33 | 50.877% | 0.014% | 0.035% |
| ETH | binance-hyperliquid | 24h | 171 | -0.121% | -0.50 | 51.462% | -0.013% | -0.108% |
| ETH | binance-hyperliquid | 72h | 171 | -0.343% | -0.86 | 43.860% | -0.106% | -0.237% |
| SOL | binance-bybit | 8h | 138 | 0.846% | 1.91 | 55.072% | -0.020% | 0.866% |
| SOL | binance-bybit | 24h | 138 | 1.216% | 1.59 | 55.797% | 0.010% | 1.206% |
| SOL | binance-bybit | 72h | 138 | 0.127% | 0.13 | 48.551% | 0.044% | 0.083% |
| SOL | binance-hyperliquid | 8h | 214 | -0.070% | -0.35 | 45.327% | -0.102% | 0.033% |
| SOL | binance-hyperliquid | 24h | 214 | -0.511% | -1.44 | 45.794% | -0.271% | -0.240% |
| SOL | binance-hyperliquid | 72h | 214 | -2.092% | -3.49 | 44.860% | -0.479% | -1.614% |
| BNB | binance-bybit | 8h | 101 | -0.191% | -1.21 | 48.515% | -0.029% | -0.162% |
| BNB | binance-bybit | 24h | 101 | -0.264% | -0.86 | 47.525% | -0.045% | -0.219% |
| BNB | binance-bybit | 72h | 101 | -1.252% | -3.08 | 37.624% | -0.189% | -1.063% |
| BNB | binance-hyperliquid | 8h | 195 | -0.113% | -0.81 | 56.923% | -0.019% | -0.094% |
| BNB | binance-hyperliquid | 24h | 195 | -0.560% | -2.81 | 44.615% | -0.023% | -0.536% |
| BNB | binance-hyperliquid | 72h | 195 | -0.568% | -1.65 | 41.538% | -0.073% | -0.495% |
| AVAX | binance-bybit | 8h | 105 | 0.285% | 0.74 | 51.429% | -0.024% | 0.309% |
| AVAX | binance-bybit | 24h | 104 | 0.434% | 0.62 | 46.154% | -0.072% | 0.506% |
| AVAX | binance-bybit | 72h | 104 | -0.430% | -0.41 | 43.269% | -0.253% | -0.177% |
| AVAX | binance-hyperliquid | 8h | 218 | -0.236% | -0.99 | 50.917% | -0.027% | -0.209% |
| AVAX | binance-hyperliquid | 24h | 218 | -0.856% | -2.23 | 46.330% | -0.055% | -0.801% |
| AVAX | binance-hyperliquid | 72h | 218 | -2.120% | -2.93 | 46.330% | -0.195% | -1.925% |
| DOGE | binance-bybit | 8h | 89 | -0.048% | -0.11 | 46.067% | 0.017% | -0.065% |
| DOGE | binance-bybit | 24h | 89 | 1.509% | 1.79 | 52.809% | 0.024% | 1.485% |
| DOGE | binance-bybit | 72h | 89 | 0.486% | 0.44 | 51.685% | -0.154% | 0.639% |
| DOGE | binance-hyperliquid | 8h | 140 | -0.272% | -0.81 | 46.429% | -0.012% | -0.260% |
| DOGE | binance-hyperliquid | 24h | 140 | -1.125% | -1.88 | 45.714% | -0.126% | -0.999% |
| DOGE | binance-hyperliquid | 72h | 140 | -2.412% | -2.23 | 52.143% | -0.361% | -2.051% |
| LINK | binance-bybit | 8h | 5 | -1.232% | -1.42 | 40.000% | -0.004% | -1.228% |
| LINK | binance-bybit | 24h | 5 | 1.824% | 1.30 | 60.000% | -0.086% | 1.911% |
| LINK | binance-bybit | 72h | 5 | 7.393% | 1.23 | 60.000% | 0.034% | 7.358% |
| LINK | binance-hyperliquid | 8h | 178 | -0.095% | -0.40 | 51.685% | -0.040% | -0.055% |
| LINK | binance-hyperliquid | 24h | 178 | -0.110% | -0.29 | 55.056% | -0.147% | 0.037% |
| LINK | binance-hyperliquid | 72h | 178 | -0.210% | -0.34 | 53.371% | -0.325% | 0.115% |

## 结论

|t|>2 且 n≥30 的格子：
- **SOL binance-hyperliquid 72h**：t=-3.49, n=214, mean=-2.092%, 胜率=44.860% — ❌ 反向（信号方向反了）
- **BTC binance-hyperliquid 72h**：t=-3.32, n=162, mean=-1.323%, 胜率=45.679% — ❌ 反向（信号方向反了）
- **BNB binance-bybit 72h**：t=-3.08, n=101, mean=-1.252%, 胜率=37.624% — ❌ 反向（信号方向反了）
- **AVAX binance-hyperliquid 72h**：t=-2.93, n=218, mean=-2.120%, 胜率=46.330% — ❌ 反向（信号方向反了）
- **BNB binance-hyperliquid 24h**：t=-2.81, n=195, mean=-0.560%, 胜率=44.615% — ❌ 反向（信号方向反了）
- **DOGE binance-hyperliquid 72h**：t=-2.23, n=140, mean=-2.412%, 胜率=52.143% — ❌ 反向（信号方向反了）
- **AVAX binance-hyperliquid 24h**：t=-2.23, n=218, mean=-0.856%, 胜率=46.330% — ❌ 反向（信号方向反了）

### 解读

- 显著格子 7 个（正向 0 / 反向 7），噪声期望约 2 个，且符号高度一致 → 存在真实效应。
- **原假设（逆着 funding 高的一方）被拒绝**：显著格子全部为反向，即 |跨所 funding 差| 极端时，
  价格在随后 24h–72h **顺着** funding 高的一方走（拥挤方向延续，而非回落）。
  跨所 funding 差是**动量/延续**信号，不是反转信号。
- 最强组合：**binance-hyperliquid @ 72h**（BTC t=-3.32、SOL t=-3.49、AVAX t=-2.93、DOGE t=-2.23，
  反向执行即顺着 binance funding 高的一方，mean +1.3%~+2.4%/72h，胜率 54%~55%）。
- binance-bybit 组合几乎无显著性：两家 CEX funding 机制相近、差值小而稳，事件少且信息含量低。
- 8h horizon 全部不显著：效应需要时间展开，短线不可交易。
- 若将方向取反（顺着 funding 高的一方 @ 72h, binance-HL），pooled t≈+2.1，
  但注意这是在看到结果后翻转方向，属事后选择，需样本外验证才能采信。

## 统计口径警示

- 24h/72h horizon 的收益窗口重叠，t 值被高估；8h 事件在 8h 网格上相邻亦自相关。
  t 值按 iid 公式报告，未做 Newey-West 修正，解读时保守看待。
- 未计手续费/资金费收付本身；纯价格收益视角。
- 42 个格子（7 标的 × 2 组合 × 3 horizon）的多重检验下，约 2 个 |t|>2 是噪声期望。

---

## 附录：严格 OOS 验证（2026-08-02）— 因子判定 **KILL**

> 本节为人工追加的后续验证结论，完整数据见 `research/xs_funding/OOS_VALIDATION.md`
> （生成脚本 `scripts/xs_funding_oos_validation.py`，测试 `scripts/test_xs_funding_oos_validation.py`）。
> 注意：本文件主体由 `scripts/xs_funding_factor.py` 自动生成，重跑该脚本会覆盖本节。

针对上文"统计口径警示"逐一对冲后的结果：

- **协议**：train 2022-01 → 2024-12 选出全部参数（所对 / 阈值分位数 / horizon / 方向），
  test 2025-01 → 2026-07-24 冻结应用。train 段独立选出的配置为
  **binance-hyperliquid、q=0.90、24h、动量方向**（train pooled NW t=3.29，n=1060）——
  即"顺着 funding 高的一方"这一方向在 train 段内同样成立，并非只能在全样本里事后看到。
- **Test 段失败**：重叠版 pooled n=218，mean=**-0.149%**，naive t=-0.56，**NW t=-0.51**，胜率 45.9%；
  非重叠执行版（事件后 24h 内不再开仓）n=164，mean=**-0.172%**，NW t=-0.52，胜率 45.7%。
  方向与 train 选出的方向**相反**（test mean < 0）。
- **Newey-West 修正**（lag = 2×horizon_bars）：train 段 naive t=4.32 → NW t=3.29，
  重叠窗口确实抬高了 t（约 24%）；test 段无论哪种口径都不显著。
- **多重检验**：Bonferroni 临界 |t|（36 配置搜索族）= 3.20，test |NW t|=0.51 远低于此；
  Deflated Sharpe（复用 `_shared/validation/cpcv.py`）DSR 值 = -0.18 < 0，校正后不显著。
- **结论**：该因子的"显著性"无法外推到 2025-2026 样本。原报告的 7 个显著格子
  主要由 2023-2024 段贡献，且方向规则在该段内外不稳定。**因子标记为 KILL，不落地策略**。
  一个值得记录的结构性缺陷：expanding 分位数阈值只升不降，test 段 BTC/DOGE 事件仅 7 个，
  因子定义本身会随时间"饿死"自己的事件流。
