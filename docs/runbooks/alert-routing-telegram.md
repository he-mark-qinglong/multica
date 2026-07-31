# Runbook — Alert Routing → Telegram (L1 Ops)

> 范围：**多源监控 → Telegram** 路由层，对应 MAP-P9 alert-routing 主题下第 7 张 [SMA-35857](mention://issue/dd8b919c-89ca-4919-9146-c06cc85a61b9)（Monitor #86）。
> 与 [status-page-monitor.md](status-page-monitor.md)、[agent-stall-ops.md](agent-stall-ops.md) 同级 — 本卡不是新探针，而是把 **既有探针的 verdict 转译为可投递的 Telegram 消息**。
> 执行机：任何有 `multica` CLI + Python 3 stdlib + 出站 HTTPS（到 api.telegram.org）的机器。

## 1. 角色与边界

| 角色 | 卡 | 本卡重叠？ |
|---|---|---|
| 平台探针 | status-page-monitor (#94), db-pool-monitor | **不重叠** — 它们生成 verdict；本卡**消费** verdict。 |
| 升级路径 | status-page-monitor §5 / agent-stall-ops §6 | **不重叠** — 本卡不创建 multica issue，不 @ smark。 |
| L1 心跳 | infra-health-watchdog autopilot | **不重叠** — 那是 host 侧，本卡是 workspace 侧。 |
| Telegram 投递 | **本卡** | — |

## 2. 信号源（只读）

5 个本地 / CLI 信号汇总为 severity 等级：

| ID | 信号 | 来源 | 触发 severity |
|---|---|---|---|
| S1 | status-page-monitor verdict | `status-page-monitor/last-snapshot.json` | escalate→critical / warn→warning / healthy→healthy |
| S2 | db-pool-monitor verdict | `db-pool-monitor/last-snapshot.json` | critical→critical / warn→warning |
| S3 | daemon alive / uptime | `multica daemon status --output json` | 不可达→critical / uptime<60s→warning |
| S4 | autopilot paused% | `multica autopilot list` | >20%→warning；≥50%→critical |
| S5 | issue backlog | `multica issue list --status <s> --limit 1` × 2 | in_progress>700→warning；blocked>80→critical |

阈值与 status-page-monitor 同源，避免双卡漂移。

## 3. 投递路径

每个 alert_id 都走三步：

```
synthesize_alerts  →  filter_fresh(dedup_window_min=30)  →  telegram_send (live-mode only)
                                                        └─ append send-log.jsonl (always)
```

- **alert_id**：`sha1(source + key_fields)[:12]`，稳定可重放。
- **dedup**：相同 alert_id 在 30 分钟内只投递一次（避免 S1 verdict 反复抖动）。
- **telegram_send**：仅在 `live_mode=True` 且 env `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` 同时存在、且 severity ∈ {warning, critical} 时调用。其它情况只写 send-log，不发任何网络请求。
- **payload**：`parse_mode=Markdown`，emoji 前缀（🚨/⚠️/ℹ️/✅），带 alert_id + ts_utc，单条 ≤ 1024 chars（TG 限）。

## 4. 运行

```bash
# 默认：dry-run，只生成快照 + send-log
python3 /Users/mark/multica/alert-routing-telegram/run.py --dedup-window 30

# 真实投递：需先 export env
export TELEGRAM_BOT_TOKEN="<bot token>"
export TELEGRAM_CHAT_ID="<chat id>"
python3 /Users/mark/multica/alert-routing-telegram/run.py --dedup-window 30 --live
```

输出：
- stdout：完整 JSON 快照
- `/Users/mark/multica/alert-routing-telegram/last-snapshot.json`：每次运行覆盖
- `/Users/mark/multica/alert-routing-telegram/state.json`：累积，history 限 50 条
- `/Users/mark/multica/alert-routing-telegram/send-log.jsonl`：每行一次 send 记录（dry-run 也记）
- `/Users/mark/multica/alert-routing-telegram/dedup-state.json`：alert_id → last_seen 映射

退出码 0：路由层是**只读 + 投递**双角色；下游决定如何响应 verdict，本卡不替它决定。

## 5. 升级路径

满足任一则升级 smark（按 agent-stall-ops.md §6 模板，不带 `@agent` mention）：

- [ ] live 模式连续 4h `send_succeeded / send_attempted < 0.8`（投递失败率 > 20%）
- [ ] `last-snapshot.json` 的 `severity_counts.critical ≥ 1` 持续超过 1h（critical 卡死）
- [ ] `dedup-state.json` 文件大小超过 1MB（去重表未衰减，需检查是否 key 漂移）
- [ ] Telegram API 返回 401 / 403（凭据失效）

升级模板：

```bash
multica issue comment add SMA-35857 <<'COMMENT'
[type=ESCALATE-OPS] <iso8601+tz> alert-routing-telegram: <S1..S5> breach, see /Users/mark/multica/alert-routing-telegram/last-snapshot.json
<一段：哪条阈值、当前值、最近 healthy 时长>
COMMENT
```

## 6. 当前实测（2026-07-26T05:24Z, iter-2 实测）

```text
verdict            : (本卡不输出 verdict，只转发 severity_counts)
severity_counts    : {critical:0, warning:1, info:0, healthy:0}
raw_alert_count    : 1
fresh_alert_count  : 0   (deduped — 上一轮 22s 前已记录)
suppressed_count   : 1
send_attempted     : 0   (live_mode=false)
send_succeeded     : 0
sources_present    : status_page=true, db_pool=true, daemon_alive=true, autopilot_total=27
```

判定：
- 单源 status-page 触发 warning（verdict=warn），被 dedup 命中 → 路由层零外部流量。
- 5 路信号探针全部 alive；live 模式未启用，**所有交付走 dry-run**。
- 没有 critical — 不需要 `ESCALATE-OPS`。

## 7. iter-2 → iter-3 增量建议（待 smark 决定）

- [ ] 加 `severity=info` 触发：autopilot 状态从 active→paused 自动触发 info（而非只在 paused% 高时触发 warning）
- [ ] 把 send-log 同步到 ClickHouse / multica 内置 metrics（便于跨周查询）
- [ ] 支持多个 chat_id（不同 severity 投递到不同频道 / 用户）
- [ ] 接 `--rate-limit N/hour` 防止 critical 风暴把 channel 灌爆（默认 30/h）
- [ ] 增加 `multica metrics query` 子源：autopilot run success-rate / latency p95

## 8. 已知漂移 / 限制

- **本卡不直接产出 multica issue**：仅路由 + 投递。verdict→issue 的链路仍由 status-page-monitor §5 拥有。**不要**让本卡 create issue — 会违反 multica-agent-base §4.1。
- **dedup 用 alert_id 而非 (source,severity,summary) 三元组**：同样的 status-page verdict=warn 在 30 分钟内只投一次；这可能掩盖"持续 warn 但 unchanged"的真实情况。如需，每次 verdict=warn 都投，则把 dedup_window_min 设为 0。
- **Telegram 凭据不写入 multica 配置**：仅读 env。本卡启动时检查 `live_mode` 字段，若 `false` 则不发起任何出站请求。
- **sha1 仅取前 12 hex**：足以 dedup，不构成密码学承诺；不要把 alert_id 当 hash-of-truth 用。
- **samples 阈值**：与 status-page-monitor / agent-stall-ops 同步单源。任何对阈值的修改应当三卡一起改（**单 agent 不擅自改动**，smark 决定）。

## 附录 A：相关代码 / 文档锚点

- `/Users/mark/multica/alert-routing-telegram/run.py`（约 280 行，stdlib only）
- `/Users/mark/multica/alert-routing-telegram/test_alert_routing_telegram.py`（10 个 smoke test）
- `/Users/mark/multica/docs/runbooks/status-page-monitor.md`：本卡主要上游
- `/Users/mark/multica/docs/runbooks/agent-stall-ops.md`：升级模板同源
- `/Users/mark/multica/status-page-monitor/run.py`：本卡布局参考
- multica-agent-base §4.1：本卡不创建 issue 的依据
- multica-agent-base §Result Wire：本卡 EVIDENCE 评论模板来源