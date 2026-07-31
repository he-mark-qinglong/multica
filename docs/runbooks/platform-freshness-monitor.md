# Runbook — Platform Freshness Monitor (L1 Ops)

> 适用范围：**multica 平台侧 Postgres 后端** 6 张核心表的写入新鲜度探针，对应 MAP-P9 监控主题第 83 张卡 `SMA-35853`（Monitor #82）。
> 与 [`status-page-monitor.md`](status-page-monitor.md)、[`agent-stall-ops.md`](agent-stall-ops.md) 互补不重叠：
>   - status-page-monitor 看 **平台 / autopilot / issue-flow** 综合健康；
>   - agent-stall-ops 看 **daemon / launchd / model-proxy** host 侧；
>   - **本卡** 只看 **Postgres 表的 max(created_at) age**——"平台还在写吗"。
> 执行机：mac 本机（与其它 monitor 同位）。

## 1. 与现有卡的边界

| 工具 / 卡 | 本卡重叠？ | 备注 |
|---|---|---|
| `data-outage` runbook | **不重叠** | 那是策略侧 `run_metric` ingest 与 `/healthz` 链路的恢复手册。本卡是平台侧表写入探针。 |
| `db-pool-monitor` autopilot | **不重叠** | 看 pg_stat_activity 池子占用。本卡只 `MAX(<ts_col>)`。 |
| `status-page-monitor` (iter-9) | **不重叠** | 看 daemon / autopilot list / issue 计数。本卡深入 DB schema。 |
| `stalled-issue-watchdog` autopilot (30m) | **不重叠** | 看 in_progress updated_at。本卡看 DB 表写入。 |

## 2. 探针与阈值

每次运行 6 个 `MAX(<ts_col>)` 查询，每张表一条阈值线（**warn / escalate**，单位秒）：

| ID | 表 | 时间戳列 | warn | escalate | 设计理由 |
|---|---|---|---|---|---|
| T1 | `comment` | `created_at` | 600 (10m) | 3600 (1h) | issue/task 评论是平台最活跃的写入源；正常情况下分钟级都有新行；一小时没评论 = 平台哑火 |
| T2 | `activity_log` | `created_at` | 600 (10m) | 3600 (1h) | 用户 + agent 活动事件；与 T1 同预期 |
| T3 | `autopilot_run` | `triggered_at` | 1200 (20m) | 7200 (2h) | autopilot 调度频率 ~10–30m；2h 还没触发意味着 dispatch 链路断了 |
| T4 | `artifact` | `created_at` | 14400 (4h) | 86400 (24h) | 发布的 run/strategy artifact；每日多次发，4h 没新 = publish 链路停滞 |
| T5 | `webhook_delivery` | `created_at` | 1800 (30m) | 14400 (4h) | 入站 webhook 处理；4h 没新 = GitHub/Lark 集成停摆 |
| T6 | `task_usage` | `created_at` | 14400 (4h) | 86400 (24h) | agent task token 计费；24h 没新 = usage writer 失败 / agent 全停 |

verdict 规则：
- `escalate` = 任一 T* verdict=escalate；
- `warn` = 任一 T* verdict=warn 且无 escalate；
- `healthy` = 6 个表全部 verdict=healthy；
- `no-op` = DB 完全不可达（所有探针 verdict=unknown）。

## 3. 实施

```bash
python3 /Users/mark/multica/platform-freshness-monitor/run.py
```

可选项：
- `--probe-only comment,autopilot_run`：只跑部分探针（加快排错）。
- `--quiet`：只写文件，不打印到 stdout（给 autopilot 调用时用）。

输出：
- stdout：完整 JSON 快照；
- `/Users/mark/multica/platform-freshness-monitor/last-snapshot.json`：最近一次快照；
- `/Users/mark/multica/platform-freshness-monitor/snapshot-<UTC>.json`：每次带时间戳；
- `/Users/mark/multica/platform-freshness-monitor/state.json`：累积（与 db-pool-monitor 同布局）；
- `/Users/mark/multica/platform-freshness-monitor/dedup-state.json`：每张表最近 verdict + age，便于跨 run 去重。

退出码总是 0——**本卡只观察，不创建 issue、不重启服务、不 @smark**。升级路径见 §6。

## 4. 实现要点

- 走 `ssh -o BatchMode=yes ... docker exec -i ... psql -X -At`，**SQL 通过 stdin 注入**，避免嵌套 shell 转义把 `to_char(...) AT TIME ZONE 'UTC'` 的单引号吃掉（这是 iter-1 实测踩到的坑）。
- 表为空（`MAX()` 返回 NULL）→ 直接判 `escalate`，原因记入 `empty_table=true`。当前生产环境 `webhook_delivery` 命中此分支。
- 每张表用独立阈值，**不共用一个全局 "alert if any > 1h"**——不同表写入节奏不同，混用会让低频表（artifact / task_usage）持续误报。
- 单表查询平均 ~0.5s，6 张表总耗时 < 4s（2026-07-26 iter-1 实测 `elapsed_sec=3.367`）。

## 5. 当前快照（2026-07-26T05:26Z, iter-1 实测）

```text
verdict        : escalate
escalations    : webhook_delivery empty-table;
                 task_usage age=4d04h (>= escalate 86400s)
warnings       : artifact age=8h01m (>= warn 14400s)

comment        : 5s   healthy  (活跃)
activity_log   : 1m   healthy
autopilot_run  : 1m   healthy
artifact       : 8h   warn      (publish-metrics-signed 链路可能停滞)
webhook_delivery: --  escalate  (整表空——infra 未启用 / 无集成)
task_usage     : 4d   escalate  (usage writer 4 天未写)
```

判定：
- T1/T2/T3 健康——平台写路径核心在跑；
- T4 warn——artifact 8 小时没新行；这与 `publish-metrics-signed` autopilot（hourly :25）的最近一次 `last_run_at=2026-07-26T12:25` 不一致——**疑点**：autopilot 显示在跑，但 DB artifact 表没有新写入。需要在 §6.3 单独排查，不能当 false positive 放过；
- T5 escalate (empty)——`webhook_delivery` 整表空，说明 multica 没接入 GitHub App / Lark 集成，**或** 已配置但从未收到过 webhook。需确认这是设计状态还是 missed config；
- T6 escalate——`task_usage` 4 天没写入。这是 `task_usage` 写入器（agent 后端每跑一个 task 写一行）的明显失败。**这条最该升级**。

## 6. 升级路径

### 6.1 立即升级（满足任一）

- [ ] T6 task_usage escalate 持续 ≥ 1h（usage writer 失败，意味着 agent 在跑但不计费；下游 budget / 多 agent 限流失真）；
- [ ] T1+T2+T3 **同时** escalate ≥ 30m（平台写路径全停——daemon 卡死 / DB 拒写）；
- [ ] T4 artifact escalate（artifact 24h 无新行 = publish 链路断，会让策略侧 metrics 落库停滞）；
- [ ] T5 webhook_delivery 之前非空、现在 escalate（曾经接入了 webhook，现在停了——集成挂掉）。

### 6.2 信息性不升级

- T4 artifact 单次 warn（artifact 写入本来就日级，4h 没新常见）；
- T5 webhook_delivery empty-table（如果**整个生命周期**都是空，意味着没启用集成，不是 outage——只在配置变更后才升级）。

### 6.3 排查顺序

1. **T6 task_usage**：登 LAN 主机 → `docker logs multica-backend-1 --tail 200 | grep -i "task_usage\|usage"` → 看是不是 backend 在写失败；
2. **T4 artifact**：查 `~/.multica/scripts/publish_metrics.py --check-stale` 最近输出，对比 `last_run_at` 和 DB `MAX(artifact.created_at)` 的时间差；
3. **T5 webhook_delivery**：问 smark 是否有意不接入 GitHub / Lark；
4. **T1+T2+T3 全 escalate**：`multica daemon status`、`multica daemon ping`、DB `pg_stat_activity` 看是否有长事务阻塞写入。

升级评论模板：

```bash
multica issue comment add SMA-35853 <<'COMMENT'
[type=ESCALATE-OPS] <iso8601+tz> platform-freshness-monitor: <T1..T6> verdict=escalate, see /Users/mark/multica/platform-freshness-monitor/last-snapshot.json
<一段：哪张表、age、阈值、最近 healthy 时长>
COMMENT
```

## 7. iter-1 → iter-2 增量建议（待 smark 决定）

- [ ] 增加 `multica_audit_log` 类自定义写入表（如果产品引入）；
- [ ] 加 `--since-cron <duration>`：和上次 healthy 跑对比，输出 age delta 趋势；
- [ ] 把 verdict=`escalate` 的快照自动落到 `~/.multica/platform-freshness-ledger.md`，给跨日对比；
- [ ] 引入 Prometheus exporter（`/metrics` endpoint + scrape config），让现有 Grafana 直接接，不用单独跑 run.py；
- [ ] 把 `webhook_delivery` 从探针里拆出（或者标 enabled=false），等 smark 决定是否接入 GitHub App 再启用——避免持续 escalate 噪声。

## 8. 已知漂移 / 限制

- 阈值是 2026-07-26 的工作样本，未与 smark 校准。改阈值属于 `[smark_decision_required]` 行为，**单 agent 不擅自改动**。
- 当前 LAN host `192.168.0.105` 是硬编码；多 DC / 远端时需参数化（待 §7 iter-2）。
- ssh 走 BatchMode 但**没有**走 `~/.ssh/config` 的 ProxyJump；如果未来 host 拓扑变化，需要重写 §3 实现。
- 当前 `task_usage` 4 天没新行——这到底是 backend bug 还是没 task 在跑？需要先把 T6 escalate 升级 smark 后才能定论。**不要在没确认前当 false positive 删掉**。

## 附录 A：相关代码 / 文档锚点

- `/Users/mark/multica/platform-freshness-monitor/run.py`：本卡完整执行脚本（stdlib only）；
- `/Users/mark/multica/docs/runbooks/data-outage.md`：策略侧 `run_metric` 新鲜度互补卡；
- `/Users/mark/multica/docs/runbooks/status-page-monitor.md`：平台综合状态互补卡；
- `/Users/mark/multica/db-pool-monitor/run.py`：本卡布局参考（state.json / last-snapshot / 增量快照）；
- `multica-issue-workflow-rules`（root AGENTS.md §Result Wire）：本卡 EVIDENCE 评论模板来源。