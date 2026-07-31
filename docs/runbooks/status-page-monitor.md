# Runbook — Status Page Monitor (L1 Ops)

> 范围：**multica workspace 平台侧** 健康聚合探针，对应 MAP-P9 multica-status-page 主题下第 10 张（iter-9）卡 [SMA-35865](mention://issue/cbbd54a2-12d2-4ed9-9913-8cb1754702c6)（Monitor #94）。
> 与 [agent-stall-ops.md](agent-stall-ops.md) 同级，不重叠：本卡讲 **workspace / autopilot / issue-flow** 平台指标，agent-stall-ops 讲 **daemon / tunnel / launchd-host** 指标。infra-health-watchdog autopilot（每 10m）讲 **infra-host side**。
> 执行机：任何有 `multica` CLI + Python 3 stdlib 的机器（mac / LAN / Tokyo 都可，**只读**）。

## 1. 信号与阈值

4 条原始信号一次性聚合到一个 JSON 快照：

| ID | 信号 | 来源 | 阈值（warn / escalate） |
|---|---|---|---|
| S1 | daemon | `multica daemon status --output json` | uptime < 60s → warn（重启循环）；active_task_count=0 → warn |
| S2 | autopilots | `multica autopilot list` + `multica autopilot get <id>`（拉 `next_run_at`） | paused% > 20% → warn；任意 autopilot `now > next_run_at`（missed scheduled）→ escalate |
| S3 | issue flow | `multica issue list --status <s> --limit 1` × 5 状态 | in_progress > 700 → warn；blocked > 80 → escalate |
| S4 | stalled in_progress 样本 | `multica issue list --status in_progress --limit 200` 头 200 条 | stale%（>24h 未更新）仅信息，不升级（stalled-issue-watchdog 拥有） |

verdict 规则：
- escalate = S2 missed-run ∪ S3 blocked-threshold
- warn    = S1 (restart-loop 或 idle) ∪ S2 paused-too-many ∪ S3 in_progress-backlog ∪ S4 info-only
- healthy = 其余
- no-op   = daemon not reachable（交给 deploy-fail-detect）

## 2. 不在范围 / 与现有工具的边界

| 工具 / 卡 | 本卡重叠？ | 备注 |
|---|---|---|
| `infra-health-watchdog`（autopilot 10m） | **不重叠** | 那张卡讲 host 侧 tunnel / launchd / model-proxy。这张卡讲 **平台侧** workspace 状态。 |
| `stalled-issue-watchdog`（30m） | **不重叠** | 看 stalled-ledger.md。本卡 S4 只是抽样粗略估计，**不接管** stalled 升级。 |
| `db-pool-monitor`（autopilot） | **不重叠** | 看 pg_stat_activity。本卡只看 issue flow + autopilot + daemon。 |
| `Multica Dispatch`（autopilot 5m） | **不重叠** | 那是 issue 喂入器；本卡是只读快照。 |

## 3. 运行

```bash
python3 /Users/mark/multica/status-page-monitor/run.py
```

输出：
- stdout：完整 JSON 快照
- 文件：`/Users/mark/multica/status-page-monitor/last-snapshot.json`
- 文件：`/Users/mark/multica/status-page-monitor/status-<UTC>.json`（每次带时间戳）
- 文件：`/Users/mark/multica/status-page-monitor/state.json`（累积，db-pool-monitor 同布局）

退出码 0 即使 verdict=escalate：本卡只发[STATUS]，**不发 issue、不 @smark、不动 autopilot**。升级路径见 §5。

## 4. 当前快照（2026-07-26T05:12Z, iter-9 实测）

```text
verdict        : warn
escalations    : (none)
warnings       : "stalled in_progress sample: 4/100 (>24h)" — 信息性，不升级

daemon         : alive, pid 834, uptime 15h18m, agents=[claude, codex, kimi], active_task_count=12
autopilots     : total=27, status_dist={active:27}, paused%=0.0, missed=[]  (含 weekly 周一类卡不误报)
issue_counts   : todo=205  in_progress=627  in_review=427  blocked=57  done=357
stalled sample : 4/100 (=4%) of first 200 in_progress are >24h stale
```

判定：
- 后端进程链稳定（uptime 15h+），daemon 属健康。
- autopilot 调度 100% active 且 0 个 next_run_at 错过——**调度环路正常**。
- in_progress backlog 627 < 阈值 700，blocked=57 < 阈值 80——**两条阈值线均未越界**。
- "stalled in_progress sample 4/100" 是 S4 信息性结论（`stalled-issue-watchdog` 拥有真正的 stalled 检），本卡不重复升级。

## 5. 升级路径

满足**任一**则升级 smark（per agent-stall-ops.md §6 模板，不带 `@agent` mention）：

- [ ] S1 daemon uptime 持续 < 60s 多次（重启循环：launchd / daemon 二选一持续崩）
- [ ] S2 autopilot paused 比例持续 > 50%（不是 weekly 周一类的预期空窗）
- [ ] S3 blocked backlog 持续 > 80 超过 4h
- [ ] S2 任意 hourly / 30m cadence autopilot `now > next_run_at + 1h`（已是 misfire，不是 weekly）

升级模板：

```bash
multica issue comment add SMA-35865 <<'COMMENT'
[type=ESCALATE-OPS] <iso8601+tz> status-page-monitor: <S1|S2|S3> breach, see /Users/mark/multica/status-page-monitor/last-snapshot.json
<一段：哪条阈值、当前值、最近 healthy 时长>
COMMENT
```

## 6. iter-9 → iter-10 增量建议（待 smark 决定）

- [ ] 接入 `multica metrics query` 抽出 autopilot run success-rate / latency p95（比 next_run_at missed 更深入）
- [ ] 把 verdict=`escalate` 的快照自动落到 `~/.multica/status-page-ledger.md`（与 stalled-ledger 同布局），给跨日对比用
- [ ] 导出 /api/v1/status public JSON（真正的 "status page" 形态）；当前卡只对 *agent* 暴露 JSON，**用户侧公开页仍是手工 / 后续卡**
- [ ] 关联 SMA-35775 → SMA-35865 这 10 张 iter 卡，把 iteration 0（canonical）与 iteration 9（本卡）作为 reference pair，写进 multica-status-page 主题 wiki（**不在本 iter 范围**）

## 7. 已知漂移 / 限制

- 阈值是 2026-07-26 的工作样本，未与 smark 校准。改阈值是 §6 的 `[smark_decision_required]` 行为，**单 agent 不擅自改动**。
- `multica issue list --limit 1 --output json` 返回 `total` 字段时是正确的全表统计；但当 workspace issue 跨千级时该列表接口若分页未给 total，监控会失败。本卡目前 `total` 路径有值（205/627/427/57/357），属正常。
- 卡里的 sample 抽样只看 `--limit 200` 的头 200 条 in_progress；workspace 在 627 量级上这个样本代表性中等。**不要把它当成真实 stalled 计数**。

## 附录 A：相关代码 / 文档锚点

- `/Users/mark/multica/status-page-monitor/run.py`：本卡完整执行脚本（108 行，stdlib only）。
- `/Users/mark/multica/docs/runbooks/agent-stall-ops.md`：互补的 host-side runbook。
- `/Users/mark/multica/db-pool-monitor/run.py`：本卡布局参考（state.json / last-snapshot / 增量快照）。
- `multica-issue-workflow-rules`（root AGENTS.md §Result Wire）：本卡 EVIDENCE 评论模板来源。
