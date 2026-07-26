# 契约执行审计报告 — 2026-07-26

> 审计对象：首个带契约块派单的 issue SMA-36661
> 审计时间：2026-07-26T22:55+08
> 审计者：Kimi / multica-ops

## 结论

契约机制**结构性有效**，但**执行层面有 3 个 gap**。这些 gap 不致命，但会随规模放大。建议在下一轮派单前修补，否则契约退化为"建议性注释"。

## 审计检查项

### 1. 契约块存在性 ✅

SMA-36661 description 顶部包含完整契约块：

```yaml
contract_version: 1
task_id: SMA-36661
title: VPVR edge round-2 精细口径复测
layer: L2
issuer: multica-orchestrator
assignee: quant-researcher
timeout: 6h
decision_time: 2026-07-27T12:00+08
preconditions: [...]
deliverables: [...]
acceptance_evidence: [...]
breach_policy: [...]
signoff_chain: [smark-signoff-proxy]
requires_attestation_run: true
invariants: [...]
```

### 2. Deliverables 交付 ✅

- git branch `agent/quant-researcher/sma-36661` 已推送
- `round2_summary.json` 存在
- 分支已合并到 main

### 3. Acceptance Evidence 部分有效 ⚠️

契约要求：
- `git_remote_branch_exists` ✅
- `file_in_main` ✅
- `requires_attestation_run: true` ❌ **未独立执行**

实际流程：quant-researcher 自测 → 直接提交结果 → smark 终签。没有 evidence gate 复测并出具 `attestation_id`。

**风险**：自证偏差、前视偏差、计算错误未被第二双眼睛发现。SMA-36661 结果恰好被后续可视化复核交叉验证，但这是运气，不是制度。

### 4. Signoff Chain 部分合规 ⚠️

契约指定：`signoff_chain: [smark-signoff-proxy]`

实际执行：
- smark（member）直接发布 SIGNOFF 评论
- 未经过 `smark-signoff-proxy` agent

**影响**：proxy 角色被架空，职责链不清晰。长期会削弱 L4/L5 分离的制衡设计。

### 5. Comment Schema 执行不严格 ❌

AGENTS.md 规定所有 agent 评论首行必须是：

```
[type=<TYPE>] <iso8601 timestamp+tz> <one-line summary>
```

quant-researcher 在 SMA-36661 下的评论首行：

```markdown
**VERDICT: KILL** (round-2 confirms round-1; [SMA-36661](...), this session, quant-researcher).
```

缺少 `[type=...]` 标签和 ISO8601 时间戳。属于 **OFFSPEC**。

### 6. Breach Policy 未触发 ✅

未发生超时、未交付、质量不达标等违约事件，阶梯未触发。

### 7. Issue 状态 ✅

- 尝试用 `--status closed` 失败（数据库约束不允许 `closed`）
- 实际状态为 `done`，符合 multica 状态机
- 这不是契约问题，是 CLI 用法问题

## 根因分析

| gap | 根因 |
|---|---|
| attestation 未独立执行 | 契约模板/AGENTS.md 没有明确"谁来做 attestation"和"attestation_id 格式" |
| signoff-proxy 被架空 | 没有规定 smark 直接签 vs proxy 签的触发条件 |
| comment schema OFFSPEC | 没有自动化校验工具，仅靠 agent 自觉 |

## 修补方案

### P0：立即执行

1. 更新 `AGENTS.md` 契约执行章节：
   - `requires_attestation_run: true` 必须由非 assignee agent（evidence gate）执行
   - attestation 评论格式：`[EVIDENCE] YYYY-MM-DDTHH:MM+TZ attestation pass for <task_id>: <sha>/<command>/<result>`
   - SIGNOFF 评论必须引用 attestation_id
2. 创建 `docs/templates/task-contract-template.yaml` 标准模板
3. 创建 `scripts/comment_schema_lint.py` 自动校验评论 schema

### P1：下一 epoch 落地

1. 给所有 agent 系统提示注入 comment schema 强制要求
2. signoff-proxy 职责明确化：
   - smark 提供终签决策（DECISION 评论）
   - smark-signoff-proxy 将决策转为正式 SIGNOFF 评论
   - 紧急情况下 smark 可直接 SIGNOFF，但必须在评论中说明"bypass proxy"
3. 在契约 acceptance_evidence 中增加 `attestation_id` 字段

## 建议的后续 action

- [ ] 本审计报告合并到 main
- [ ] 修补 AGENTS.md + 契约模板
- [ ] 运行 comment_schema_lint.py 对 SMA-36661 的评论做 retro 标记
- [ ] 下一派单时由 orchestrator 显式指定 evidence gate agent

## 附录：SMA-36661 评论清单

| 时间 | 作者 | 首行 | schema 合规 |
|---|---|---|---|
| 2026-07-26T21:28:09+08 | quant-researcher | `**VERDICT: KILL** (...)` | ❌ OFFSPEC |
| 2026-07-26T21:35:34+08 | smark (member) | `[SIGNOFF] 2026-07-26T21:45+08 smark 终签 ...` | ✅ |
| 2026-07-26T21:36:25+08 | smark (member) | `This is smark's final KILL sign-off ...` | ❌ 无 type tag |

> 注：smark 的人类评论可以不受 comment schema 约束，但 agent 评论必须严格遵守。
