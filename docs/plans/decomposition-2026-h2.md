# 2026 下半年目标拆解（十年规划的季度/月度层）

> 上层：`docs/plans/vision-10y-2026-2036.md` 阶段一（地基与自持循环）。
> 本层把 2026 里程碑拆到月；每月末 epoch-retro 月报对照验收，偏差进下月修正。
> 当前执行层：`docs/plans/infra-sprint-2026-07-25/`（68 任务 6 波次）。

---

## Q3 2026（7–9 月）：让循环转起来

### 7 月（剩余 1 周）：基建收口
- [ ] infra sprint 68 任务 6 波次执行完毕，Wave 5 全量回归 9 条不变量全 PASS
- [ ] W4：signal-enhance-h3 全历史 7 窗 + 60bps fee shock 出 verdict（KEEP/KILL 都算交付）
- [ ] server gate 严格化 + migration 125 部署到 .105（部署窗口 INT-04）
- [ ] compare 页面：三态徽章 + KILL 灰显 + verdict 区块上线
- 验收：INT-05 回归 sweep 全绿 + W4 VERDICT.md 落盘 + .105 gate 对 sharpe-only 上传判 fail

### 8 月：日循环磨合期
- [ ] 日循环天天跑（09:00 scout → 21:00 retro），目标：允许失败但不允许静默——
      每个坏掉的触发点 24h 内有 [type=ESCALATE] 或修复记录
- [ ] paper trading 新 harness（_shared/paper/）上线，H1（费后唯一幸存变体）首个入住
- [ ] signal-enhance-h3 后续分支：KEEP → 进 paper；KILL → 证据归档，scout 按轮换表开下一族
- [ ] 数据日更自动化（W3-T15 之后）：klines/funding 每日增量，staleness >48h 告警
- 验收：连续 30 天日循环产出可追溯（issue + digest 齐全）；人工介入 ≤3 次/周

### 9 月：组合雏形 + Q3 验收
- [ ] 组合管理 v0：H1 + 当月新验证策略（若有）的相关性矩阵 + 等权组合净值（paper）
- [ ] 研究吞吐达标：≥5 SPEC 预检/周、≥2 全管线验证/周、判决周期 <48h
- [ ] maker 执行研究 pre-SPEC（文献 + 数据可行性，不写策略代码）
- **Q3 验收（9 月 30 日）**：日循环 30 天无人工干预 + ≥1 个策略在全管线中走完全程
  + 重复犯错率 = 0（无已 KILL 家族被重做）

## Q4 2026（10–12 月）：从循环到产出

### 10 月：首个全验证策略
- [ ] ≥1 个策略通过全 G 门 + 双框架 CV + 60bps fee shock，进入 paper（这是 2026 年度里程碑）
- [ ] 若当月无候选：epoch-retro 出「验证漏斗分析」——SPEC 死在哪个环节，针对性修
- [ ] compare 页面成为唯一事实源：所有策略状态以页面为准，ledger 自动生成

### 11 月：执行层研究启动
- [ ] maker 执行研究正式开 thread（T10）：队列位置模型 + 真实撮合回放，
  verdict 目标 = maker 有效成本 ≤2bps 是否可达
- [ ] 组合费后评估框架（组合级 Sharpe/回撤/容量进入 run_metric）

### 12 月：年度验收
- [ ] 对照十年规划 2026 里程碑逐项验收：基建✅/日循环/全管线策略 verdict
- [ ] 未达标项：差距分析 + 2027 Q1 修正计划（写入十年规划修订记录）
- [ ] 2027 年度计划（阶段一收尾 + paper ≥6 个月运行目标）

---

## 追踪方式

- 每月最后一天的 epoch-retro 自动生成本文 checklist 的对照表（逐项 ✅/❌ + 证据链接）
- 连续 2 个月同一项 ❌ → 自动 ESCALATE 人类（说明系统性问题，不是执行问题）
- 本文只拆到 2026 年底；2027 的季度拆解在 12 月年度验收时生成
