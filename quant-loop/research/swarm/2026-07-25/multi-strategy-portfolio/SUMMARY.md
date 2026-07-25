# multi-strategy-portfolio 研究摘要

**日期**: 2026-07-25  
**输出目录**: `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/multi-strategy-portfolio/`  
**研究性质**: 工程梳理 + 方向性实验，未跑大型回测，重在给出可执行的结论与下一步验证动作。

---

## 1. gate-ledger-fix

### 1.1 当前问题

- `server/internal/gate/gate.go` 对缺失指标统一 `skip`（`Pass=true, Note="skipped: no data"`）。
  这导致 `vpvr_stable_depeg_regime_4h_20260716_p3opt_091`（Sharpe 31.7，但无 `ann_return`、无 OOS、无 `profit_factor`）仅靠 sharpe 规则就能 `pass`。
- `results-ledger.md` 的 `Verdict` 列把“框架一致 / 盈利 / 待观察 / 淘汰”混为一谈。

### 1.2 严格门控提案

- **核心字段缺失应判 fail**：`ann_return`、`max_drawdown`、`profit_factor`、`oos_sharpe`、`oos_windows` 全部设为 required。
- **`profit_factor` 可回退到 daily-return PF**：在 `Metrics` 结构里新增 `ProfitFactorDaily`；handler 解析 `profit_factor_daily` / `daily_profit_factor`，或在 blob 里提供 `equity_daily` 时现场计算。
- **OOS windows 必须 ≥ 3**：缺失或不足直接 fail。
- 仅 `sharpe` 缺失时返回 `null`（无数据），其他情况 missing mandatory metric 统一 `fail`。

### 1.3 Ledger 状态机提案

| 状态 | 含义 | 进入条件 |
|------|------|----------|
| `UNTESTED` | 无数据 | 无 metrics、无 framework CV |
| `NO_DATA` | 核心字段缺失 | sharpe 有但其他核心字段缺 |
| `PROFITABLE` | 厂内门控通过，尚未 CV | G1-G4 + OOS≥3 通过，无 framework CV |
| `CV_PASS` | 跨框架验证通过 | 至少一个 framework CV 为 PASS/W5_PASS，且厂内门控通过 |
| `HOLD` | 边界/不完整/费冲击分化 | W5_FAIL_FEE_SHOCK、OOS pending、单框架通过等 |
| `KILL` | 淘汰 | graveyard、NOT-PROFITABLE、AUTO-ARCHIVE、gate fail |

### 1.4 最小落地补丁

完整代码级方案见同目录 **`gate_ledger_patch.md`**，包括：

- `server/internal/gate/gate.go` 严格版 `Evaluate`
- `server/internal/handler/metric.go` 新增 `profit_factor_daily` 映射 + daily PF 计算 helper
- 可选 DB migration `ALTER TABLE run_metric ADD COLUMN profit_factor_daily`
- `scripts/build_results_ledger.py` 新 `_status` 逻辑与新表头

**实施顺序建议**：先合 gate 补丁 → `POST /api/metrics/reevaluate` 回填 → 再合 ledger 脚本 → 重跑 `build_results_ledger.py` → 人工审计从 `PASS` 翻转为 `fail/HOLD` 的策略。

---

## 2. knowledge-graph-old-strategies

### 2.1 扫描范围

脚本 `scan_strategies.py` 扫描了 `quant-loop/strategies/` 下的 **99 个策略目录**（active + `_graveyard`），提取了：

- 入口文件（`run_backtest.py`、`strategy.py`、`smoke_backtest.py` 等）
- 每个 Python 模块的类别（信号生成 / 风控 sizing / 执行 / 成本 / 评估）
- 导入关系与可移植性判断
- `config.json`、`results/metrics.json`、`results/framework_cv_*.json` 的关键元数据

### 2.2 输出产物

| 文件 | 说明 |
|------|------|
| `strategy_inventory.json` | 完整结构化数据（策略 + 模块级元数据） |
| `strategy_inventory.csv` | 策略概览 CSV |
| `strategy_inventory.md` | 策略总览 + 按类别分类的可复用模块清单 |

### 2.3 关键结论

- **可直接搬进 `_shared/` 的模块**（generic、无 hardcoded symbol、无策略本地路径）：
  - `pairs_cointegration_1d_20260709/cointegration.py` — 协整检验/OLS/EG/滚动窗口
  - `pairs_cointegration_1d_20260709/portfolio.py` / `xs_momentum_rank_1d_20260709/portfolio.py` — 组合权重/再平衡
  - `impl_vpvr_multi_tf_funding/build_signals.py`、`combine_signals.py` — 多 TF 信号合成
  - `vol_breakout_2tf_vpvr_confluence_4h_20260712/indicators.py` — 通用技术指标
  - `vpvr_tod_session_filter_15m_20260715/tod_calendar.py` — 交易日历过滤
  - `vpvr_funding_carry_asym_v2_20260718/state_machine.py`、`trend_filter.py` — 状态机/趋势过滤器

- **只能当反面教材或需重写**：
  - 所有 `data_loader.py`（大多 hardcode symbols/本地路径）
  - 所有 `framework_adapter_*.py`（框架胶水，策略相关）
  - 大多数 `strategy.py` / `run_backtest.py`（入口脚本，含策略参数与执行流程）
  - Graveyard 里的 `*_backtest.py`、`run_u5*.py`、`run_param_scan*.py`（过度特化、过度拟合产物）

- **可提取思想但需抽象**：
  - `vpvr_*` 家族中的 VPVR/POC/体积节点计算逻辑可沉淀为 `_shared/indicators/vpvr.py`
  - `funding_*` 家族中的 funding 率差/期限结构/资金费率过滤逻辑可抽象为通用 funding 模块
  - `loid_iceberg_v4_1m_20260720/iceberg_detector.py` 中的大单拆分检测可作为通用微观结构信号

完整清单见 `strategy_inventory.md` 的 **Reusable module catalog** 章节。

---

## 3. multi-strategy-portfolio

### 3.1 目标与方法论

验证“把多个弱正期望 / 低相关策略组合”是否能得到比单一 H3 更好的 portfolio。

使用了以下可用数据：

| 策略 | 数据 | 说明 |
|------|------|------|
| `h3_baseline` | `equity_winner_atr_mult_1_00_1d.csv` | H3 BTC+SOL 日收益 |
| `vpvr_xs_basis_zscore_15m` | `equity_A_iter72_BTCUSDT_ETHUSDT.csv` | 15m equity → 日收益 |
| `momentum_trend_btc_1h` | `equity_BTCUSDT.csv` | 1h equity → 日收益 |
| `pairs_cointegration_1d` | `portfolio_equity.csv` | 日 equity，稀疏日 forward-fill |
| `donchian_breakout_1d` | `equity_{BTC,ETH,SOL}USDT.csv` | 多标的等权日收益 |
| `signal_enhance_h3_2024` | **合成日收益** | 按 quick_verify_2024.json 校准（Sharpe 8.07, ann 111.6%, maxDD -3.15%），与 H3 2024 目标相关 0.55 |

三种权重方案：

1. **equal**：1/N
2. **risk_parity**：inverse daily volatility
3. **correlation-off**：inverse-vol × (1 − 平均 pairwise correlation)

费用敏感度近似：额外 Δbps RT 年费冲击 = Σ weightᵢ × trades_per_yearᵢ × Δbps / 10000，再按 252 个交易日均摊。

### 3.2 实验结果

#### A. Long history（2022-01 ~ 2026-07，H3 + vpvr + momentum）

| 组合 | Sharpe | 年化收益 | maxDD | PF | 权重要点 |
|------|--------|----------|-------|----|----------|
| H3 单一 | 1.12 | 16.5% | -13.7% | 1.22 | — |
| equal | 1.12 | 6.0% | -5.5% | 1.21 | 1/3  each |
| risk_parity | 0.89 | 1.4% | -2.4% | 1.19 | 78.6% momentum（低波） |
| correlation-off | 0.89 | 1.4% | -2.4% | 1.19 | 79.2% momentum |

- 等权组合 **maxDD 从 -13.7% 降到 -5.5%**，但年化收益被低收益策略稀释到 6.0%。
- Sharpe 与单一 H3 基本持平，没有“比任何单一策略都好”的明显优势。
- 低波策略（momentum_trend_btc）在风险平价中权重过高，压低了收益。

#### B. Full history（2024-08 ~ 2026-05，5 策略）

| 组合 | Sharpe | 年化收益 | maxDD | PF |
|------|--------|----------|-------|----|
| H3 单一 | 1.29 | 14.7% | -9.3% | 1.24 |
| equal | 1.17 | 3.1% | -3.2% | 1.21 |
| risk_parity | 1.56 | 0.5% | -0.3% | 1.37 |
| correlation-off | 1.55 | 0.5% | -0.3% | 1.37 |

- 加入 pairs_cointegration（高 Sharpe 但容量极低）和 donchian 后，组合风险进一步下降，但收益被严重稀释。
- 等权组合 Sharpe 低于单一 H3；风险平价/相关去权组合 Sharpe 更高，但年化收益仅 ~0.5%，实际 deploy 价值有限。

#### C. 2024 subsample（含 signal-enhance H3，合成数据）

| 组合 | Sharpe | 年化收益 | maxDD | PF |
|------|--------|----------|-------|----|
| H3 单一 | 2.14 | 26.5% | -9.8% | 1.41 |
| signal_enhance H3（单一） | ~9.0 | 126.1% | -3.9% | 4.24 |
| equal | 6.56 | 31.6% | -1.7% | 2.86 |
| risk_parity | 5.12 | 2.1% | -0.15% | 2.54 |
| correlation-off | 4.89 | 1.9% | -0.15% | 2.49 |

- 等权组合 **Sharpe 6.56、maxDD -1.7%**，显著优于单一 H3（Sharpe 2.14、maxDD -9.8%）。
- 这主要由 signal-enhance H3 驱动；但它目前是 **2024 in-sample 合成序列**，必须先用真实 walk-forward OOS 验证。

### 3.3 费率敏感度

组合内 H3 与 vpvr_xs_basis 换手率极高，导致对额外费用极度敏感：

| 组合 | 额外 10bps RT 冲击（年化） | 额外 22bps RT 冲击（年化） |
|------|----------------------------|----------------------------|
| long_history equal | -3.8% | -8.4% |
| full_history equal | -2.3% | -5.0% |
| 2024 equal | -2.1% | -5.1% |

结论：**multi-strategy portfolio 的优势高度依赖低成本执行**。在 22bps+ RT 的框架下，高换手策略会迅速把组合 Sharpe 拖为负值。

### 3.4 下步验证动作

1. **跑 signal-enhance H3 的完整 walk-forward OOS**，拿到真实日收益后再纳入组合（当前为合成数据，只能证明“如果成立”的潜力）。
2. **补充更多低相关、正期望、低换手的策略**（如 longer-horizon carry、期权结构信号）来稀释 H3 的费冲击。
3. **用真实成交级数据重新计算换手率与费冲击**，而不是用 trades/year 近似。
4. 测试 **动态 rebalancing**（周/月再平衡而非日再平衡）和 **风险预算上限**（限制任一高换手策略权重 ≤15%）。

---

## 4. 产出文件清单

```
/Users/mark/multica/quant-loop/research/swarm/2026-07-25/multi-strategy-portfolio/
├── SUMMARY.md                              # 本文件
├── gate_ledger_patch.md                    # gate + ledger 补丁方案
├── scan_strategies.py                      # 旧策略扫描脚本
├── strategy_inventory.json                 # 完整扫描 JSON
├── strategy_inventory.csv                  # 策略概览 CSV
├── strategy_inventory.md                   # 策略总览 + 可复用模块分类
├── portfolio_experiment.py                 # 组合实验脚本
├── portfolio_metrics.json                  # 组合指标（long/full/2024）
├── portfolio_weights_*.csv                 # 各实验权重
├── portfolio_correlation_matrix_*.csv      # 各实验相关矩阵
└── portfolio_equity_curves_*.csv           # 各实验组合净值曲线
```

---

## 5. 关键结论一句话

- **gate-ledger-fix**: 当前 gate 的“skip-on-missing”让 Sharpe-only 策略虚过，应把核心字段缺失判 fail、profit_factor 用 daily PF 回退、OOS windows 必须 ≥3；ledger 应拆分为 `CV_PASS / PROFITABLE / HOLD / KILL`。
- **knowledge-graph-old-strategies**: 99 个旧策略中，通用模块（协整、组合、信号合成、技术指标、状态机）可迁入 `_shared/`，但 `data_loader` / `framework_adapter` / `strategy.py` 仍要策略本地维护。
- **multi-strategy-portfolio**: 在现有可用数据上，**等权组合能降低 maxDD，但高换手策略稀释收益并放大费冲击**；2024 子样本中加入 signal-enhance H3 后组合显著优于单一 H3，但该结果目前基于 in-sample 合成数据，必须先过 walk-forward OOS 才能进入实盘候选。
