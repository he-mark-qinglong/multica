# Runbook — Agent Health Dashboard (L1 Ops)

> 范围：**agent-side 平台指标聚合探针**，对应 MAP-P9 agent-health-dashboard 主题下第 10 张（iter-10）卡 [SMA-35864](mention://issue/5669b372-e20f-4a5f-a866-ae366946f730)（Monitor #93）。
> 与 [status-page-monitor.md](status-page-monitor.md) 同级，不重叠：本卡讲 **agent 健康 + per-agent task 负荷 + failure-rate**，status-page 讲 **workspace / autopilot / issue-flow** 平台指标。infra-health-watchdog autopilot（每 10m）讲 **infra-host side**（tunnel / launchd / model-proxy）。
> 执行机：任何有 `multica` CLI + Python 3 stdlib 的机器（mac / LAN / Tokyo 都可，**只读**）。

## 1. 信号与阈值

5 条原始信号一次性聚合到一个 JSON 快照：

| ID | 信号 | 来源 | 阈值（warn / escalate） |
|---|---|---|---|
| S1 | agent_definitions | `multica agent list --output json` | 仅汇总：active / archived / `sum(max_concurrent_tasks)` |
| S2 | task flow | `multica task list --status <s> --limit 1` × 6 状态（queued/dispatched/running/completed/failed/cancelled） | 仅汇总 |
| S3 | per_agent_active_load | `multica task list --status running --limit 200` 按 `agent_id` 分桶 | `active_load > max_concurrent_tasks` → escalate |
| S4 | per_agent_failure_24h | `multica task list --agent-id <id> --limit 50` 时间窗口 24h | `fail% > 30%` → warn；`fail% > 50%`（且 tasks_24h ≥ 3）→ escalate |
| S5 | long_running_tasks | 同 S3，按 `started_at` 计算 age_min | `age_min > 30` → warn（多任务时 worst 列） |
| (附) | idle-no-success | S3 active_load=0 且 last_success_age_h > 7d | warn（信息性；说明 agent 长期闲置） |

verdict 规则：
- escalate = S3 任意 agent 过载 ∪ S4 任意 agent fail% > 50% 且样本 ≥ 3
- warn    = S4 fail% ∈ (30, 50] ∪ S5 long-running ∪ idle-no-success（info）
- healthy = 其余
- no-op   = `multica agent list` 不可达（交给 deploy-fail-detect）

注：S4 的 30%/50% 阈值要求 `tasks_24h ≥ 3`，避免对低流量 agent 的零样本误报。

## 2. 不在范围 / 与现有工具的边界

| 工具 / 卡 | 本卡重叠？ | 备注 |
|---|---|---|
| `status-page-monitor` (iter-9 #94) | **不重叠** | 那张卡讲 daemon / autopilot / issue flow 平台指标。本卡讲 **per-agent 任务层** 指标。两者都通过 `multica task list` 拉取，但本卡的 per-agent 分桶和 last_success_age 是新增维度。 |
| `agent-stall-ops.md` | **不重叠** | agent-stall-ops 讲 host-side daemon / tunnel / launchd。 |
| `infra-health-watchdog` (autopilot 10m) | **不重叠** | host-side tunnel / launchd / model-proxy。 |
| `db-pool-monitor` (autopilot) | **不重叠** | pg_stat_activity。 |
| `Dispatch-Balance` (autopilot 30m) | **不重叠** | issue 喂入器 + reroute 决策；本卡是只读快照。 |
| `stalled-issue-watchdog` (autopilot 30m) | **不重叠** | 看 stalled-ledger.md。 |

## 3. 运行

```bash
python3 /Users/mark/multica/agent-health-dashboard/run.py
```

输出：
- stdout：完整 JSON 快照
- 文件：`/Users/mark/multica/agent-health-dashboard/last-snapshot.json`
- 文件：`/Users/mark/multica/agent-health-dashboard/agent-<UTC>.json`（每次带时间戳）
- 文件：`/Users/mark/multica/agent-health-dashboard/state.json`（累积，status-page-monitor 同布局）

退出码 0 即使 verdict=escalate：本卡只发[STATUS]，**不发 issue、不 @smark、不动 autopilot**。升级路径见 §5。

## 4. 当前快照（2026-07-26T07:30Z, iter-10 实测）

```text
verdict        : escalate
escalations    : "2 agents fail% > 50% over 24h: multica-code(84.0%), multica-strategy(86.0%)"
warnings       : (none beyond the escalations)

agents_probe   : total=14, active=14, archived=0, sum_max_concurrent_tasks=215
task flow      : queued=77  dispatched=0  running=32  completed=27279  failed=5505  cancelled=348
running_total  : 32
long_running   : 0

per_agent (cap | load | 24h | fail% | last_success_age_h):
  knowledge-curator     20 |  0 | 11 | 27.3 | 10.7
  multica-code          20 |  3 | 50 | 84.0 | 0.2  ← escalate
  multica-ops           20 |  5 | 50 | 30.0 | 0.0
  multica-orchestrator  20 |  4 | 50 | 10.0 | 0.0
  multica-strategy      20 |  0 | 50 | 86.0 | 0.3  ← escalate
  ops-worker-1          20 |  0 | 50 |  2.0 | 0.1
  persona-advisor       20 |  0 |  0 |  0.0 | 26.8
  quant-analyst         20 |  0 | 14 |  7.1 | 3.5
  quant-research-agent   3 |  0 |  1 |  0.0 | 6.3
  quant-researcher       6 |  0 | 50 |  6.0 | 0.2
  smark-decision-maker   3 |  0 |  7 | 14.3 | 7.2
  smark-signoff-proxy    3 |  0 |  3 |  0.0 | 8.0
  strategy-worker-1     20 | 11 | 50 |  0.0 | 0.0
  strategy-worker-2     20 |  9 | 50 |  0.0 | 0.0
```

判定：
- **escalate**：multica-code 24h fail%=84.0%（样本 50），multica-strategy 24h fail%=86.0%（样本 50）。两个都是高流量策略/代码型 agent，fail% 远超 50% 阈值——属于高置信度健康告警。
- **warn（边缘）**：multica-ops 24h fail%=30.0%，恰好不在 (30, 50] 开区间，未触发 warn；这说明阈值边界设计是按 *strictly greater than* 而不是 ≥ 30，与 status-page-monitor 的 paused% > 20% 阈值语义一致。
- 调度环正常：queued=77（待执行），running=32，无 long-running。
- 容量充足：sum_cap=215，current_load=32 → headroom=183。
- 闲置 agent 提示：persona-advisor `last_success_age_h=26.8`（~1.1d），未触及 7d warn；smark-decision-maker / smark-signoff-proxy 在 7~8h；均属正常工作域。

## 5. 升级路径

满足**任一**则升级 smark（per agent-stall-ops.md §6 模板，不带 `@agent` mention）：

- [ ] S3 任意 agent `active_load > max_concurrent_tasks`（持续 ≥ 30 min，非瞬时尖峰）
- [ ] S4 任意 agent fail% > 50% 持续 ≥ 4h（不是单跑噪声）
- [ ] S5 long-running task 数 ≥ 5 且 worst age > 60min
- [ ] idle-no-success 连续 3 个 cycle 命中同一 agent（确认真死寂，非 cron 静默窗口）

升级模板：

```bash
multica issue comment add SMA-35864 <<'COMMENT'
[type=ESCALATE-OPS] <iso8601+tz> agent-health-dashboard: <S3|S4|S5|idle> breach, see /Users/mark/multica/agent-health-dashboard/last-snapshot.json
<一段：哪条阈值、当前值、最近 healthy 时长>
COMMENT
```

## 6. iter-10 → iter-11 增量建议（待 smark 决定）

- [ ] 接入 `multica metrics query` 抽出 task latency p50/p95（per agent），把 verdict 升级条件改为 latency 阈值而非 fail% 单维度
- [ ] 把 verdict=`escalate` 的快照自动落到 `~/.multica/agent-health-ledger.md`（与 stalled-ledger 同布局），给跨日对比用
- [ ] 关联 SMA-35770 这 100 张 iter 卡，把 iteration 0（canonical）与 iteration 10（本卡）作为 reference pair，写进 agent-health-dashboard 主题 wiki（**不在本 iter 范围**）
- [ ] 接入 autopilot 层（`multica autopilot list`）把 per-autopilot run-success-rate 也拉进同一快照（这是 status-page 的 S2，不在本卡重做；后续若合并，document 迁移到 status-page 的 §6）

## 7. 已知漂移 / 限制

- `multica task list --status <s> --limit 1` 返回 `total` 字段时是正确的全表统计；但当 workspace task 跨万级时（已完成 27k+），CLI 在 `has_more_by_status.completed=true` 时仍报 `total=27279`——意味着 CLI 内部是全量统计而非分页。本卡 S2 信任这个 total。
- `multica task list --agent-id <id> --limit 50` 是按 agent 过滤 + page 限制，**不**保证返回所有 24h 内任务；当某 agent 24h 量 > 50 时 fail% 是下界估计。本卡阈值要求 tasks_24h ≥ 3 部分缓解了这个问题，但样本足够大（>50）时仍可能漏报。
- 卡里的 `last_success_age_h` 优先从 S3 running_tasks 拉（含 completed_at），缺失时再 fallback 到 `multica task list --agent-id <id> --status completed --limit 10`。这只看头 10 条 completed 任务的最新一条，可能漏报长期 idle agent 的真实最后成功时间。
- 阈值是 2026-07-26 的工作样本，未与 smark 校准。改阈值是 §6 的 `[smark_decision_required]` 行为，**单 agent 不擅自改动**。

## 附录 A：相关代码 / 文档锚点

- `/Users/mark/multica/agent-health-dashboard/run.py`：本卡完整执行脚本（stdlib only）。
- `/Users/mark/multica/agent-health-dashboard/test_agent_health_dashboard.py`：8 个 smoke test。
- `/Users/mark/multica/docs/runbooks/status-page-monitor.md`：互补的 workspace-side runbook（同样 L1 ops 视图，不同维度）。
- `/Users/mark/multica/docs/runbooks/agent-stall-ops.md`：host-side runbook。
- `multica-issue-workflow-rules`（root AGENTS.md §Result Wire）：本卡 EVIDENCE 评论模板来源。