# 策略可视化验收规范（Visualization Acceptance Mandate）

> 生效日期：2026-07-26
> 适用范围：所有进入 `_shared/run_backtest.py` + `compute_metrics.py` 验证路径的策略
> 签发：smark

## 核心原则

**任何策略在被 KEEP/KILL 判决之前，必须提供可逐笔审查的交易历史可视化。**

抽象的 Sharpe、profit factor、max drawdown 会掩盖以下事实：
- 单笔极端亏损是否来自数据错误 or 黑天鹅
- 收益是否集中在少数几笔交易
- 多头/空头是否对称有效
- 信号触发点是否符合假设的 market-microstructure 故事
- 前视偏差是否以"漂亮的 equity curve"形式存在

没有可视化的 verdict 是统计官僚，不是研究决策。

## 强制交付物（Mandatory Artifacts）

每个策略目录 `results/` 下必须包含以下图片/交互文件：

### 1. 资金曲线（equity_curve.png / equity_curve.html）
- 全样本 NAV / 累计收益
- 标注 major drawdown 区间（peak→trough→recovery）
- 子图 1：多头资金曲线
- 子图 2：空头资金曲线
- 子图 3：合计资金曲线
- 每个子图叠加每日/每周 bar 的透明波段

### 2. 交易历史 K 线图（trade_history_{long|short}.png）
- 在 1m/5m/15m（与策略主时间框架一致）K 线上标注：
  - 绿色向上三角 = 多头入场
  - 红色向下三角 = 空头入场
  - 绿色圆点 = 多头出场（盈利）
  - 红色叉 = 多头出场（亏损）
  - 对称颜色用于空头
- 每张图展示 50-200 笔代表性交易，覆盖不同 regime（牛/熊/震荡）
- 必须能看清：入场 bar、出场 bar、止损/止盈位、持仓时间

### 3. 特征-入场叠加图（如果策略使用 VPVR/成交量分布/指标）
- `vpvr_trades_{SYM}.png`：VPVR 成交量分布 + 所有入场/出场点投影
- `indicator_overlay_{SYM}.png`：主信号指标 + K 线 + 交易点
- 必须展示多空分别的分布

### 4. 交易诊断散点图（trade_diagnostic.png）
- X 轴：持仓时间（bar 数）
- Y 轴：单笔收益（bps）
- 颜色：多头/空头
- 子图 1：收益 vs 持仓时间
- 子图 2：收益 vs 入场时间（小时/星期）
- 子图 3：收益分布直方图
- 子图 4：累计收益 PnL 瀑布（前 10 笔最大贡献/最大拖累）

### 5. 月度/季度热力图（returns_heatmap.png）
- 月度收益热力图
- 按 symbol 分开
- 标注统计显著性（* 表示 t-stat > 2）

## 生成方式

统一使用 `quant-loop/_shared/visualization/` 模块：

```python
from quant_loop._shared.visualization import StrategyVisualizer

viz = StrategyVisualizer(
    ohlc_df=df_1m,
    trades_df=trades,
    equity_df=equity,
    vpvr_df=vpvr,  # optional
    output_dir="results/"
)
viz.generate_all(symbol="BTCUSDT", side="long")
viz.generate_all(symbol="BTCUSDT", side="short")
viz.generate_report()
```

## 契约集成

所有策略研究 issue 的 `acceptance_evidence` 必须增加：

```yaml
acceptance_evidence:
  - check: visualization_bundle_exists
    ref: results/{equity_curve,trade_history_long,trade_history_short,trade_diagnostic,returns_heatmap}.png
  - check: human_reviewable_sample
    ref: trade_history_long.png must show ≥50 trades across ≥3 regimes
```

## 验收规则

- 缺少任一强制图片 = attestation 自动失败，不得进入 SIGNOFF
- 图片必须能从 commit 中直接打开（PNG/HTML），不得依赖外部服务
- 研究 agent 必须在评论中解释："哪几笔交易导致了最大回撤/最大收益，是否与假设一致"
- smark 签核时必须至少查看 trade_history_long + trade_history_short + equity_curve

## 反例

以下情况**不接受**仅凭数字 KILL：
- "sharpe 0.8" 但不展示交易历史
- "max dd 25%" 但不说明是单笔事件还是持续失血
- "多空合计为负" 但不拆分哪一侧失败

## 首批适用对象

1. 立即为 SMA-36661 VPVR round-2 补做交易历史可视化（验证规范可行性）
2. 此后所有新策略 SPEC 必须包含可视化交付物章节
3. 所有在研策略（T10/T11/T12/T13）必须在下一验证轮次补齐
