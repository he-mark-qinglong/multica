# KILLED — mtf_xs_pairs 全家族（H1/H2/H3/H4 + signal-enhance-h3）

**封存日期**：2026-07-26 ｜ **判决**：SMA-36570（smark-decision-maker KILL + 人类 signoff）
**修正口径**：SMA-36566（fee-shock per_trade_fraction 200× bug，0.005 → 1.0）

## 死因

- 每笔试毛利：se_h3 17.78bps，H1-H4 0.38–9.42bps；break-even 成本 ≈20bps pair-RT
- 修正费冲击：se_h3 4bps +5.98 / **24bps −17.33 / 60bps −38.80**；H1-H4 全 dead
- G4 PF 1.098 < 1.5：胜率驱动的结构性脆弱（赢小亏大），成本上升先吃光小赢端
- 7 窗 OOS Sharpe 9.21 为真，但在可达成本（≥24bps）下不构成可执行 edge

## 纪律

- 本家族（含 se_h3 及其信号增强思路在 mtf_xs_pairs 上的任何变体）**禁止重新扫参**
- 历史「H1 fee-robust +0.728」结论系同一 200× bug artifact，一并作废
- 唯一复活条件：执行成本实证 ≤20bps（maker 执行研究出结果后，由 research-scout 显式提案、人类批准）

## 证据指针

- 判决+四问答复：issue SMA-36570
- 修正复算：issue SMA-36566，`research/swarm/2026-07-25/signal-enhance-h3/full_history/VERDICT.md`
- fee_shock_fix.py（可重跑）：同目录
- 修正后共享模块：`_shared/validation/fee_shock.py`（SMA-36467）
