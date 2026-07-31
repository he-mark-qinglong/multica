# Runbook — Data Outage (L1 Ops: detect, contain, recover)

> 适用范围：Multica 数据链路出现不可用、延迟、空结果或写入停滞时的检测、隔离、恢复和升级。
> 执行机：mac 本机；数据库与 display backend 位于 LAN 主机 `192.168.0.105`。需要 `multica` CLI、免密 SSH 和只读诊断权限。
> 本 runbook 是操作手册，不替代数据修复脚本；未经人工确认不得删除、覆盖或整库恢复数据。

---

## 1. 目标与故障定义

目标是 5 分钟内确认故障层，10 分钟内恢复服务或完成升级。以下任一条件触发本 runbook：

| 信号 | 阈值 / 证据 | 可能层级 |
|---|---|---|
| `/healthz` 失败或 `db` / `migrations` 非 `ok` | 一次连续失败（重试一次确认） | Backend / PostgreSQL |
| `metrics query` 返回 5xx、超时或空响应 | 同一 campaign 连续 2 次 | API / ingest |
| 最近指标写入停滞 | `run_metric` 最近窗口计数为 0 | Producer / artifact ingest |
| 数据明显落后 | 最新 `created_at` 超过业务允许窗口 | Producer / scheduler / DB |
| 数据异常增长或重复 | 同一 campaign/iteration 短时重复或数量突增 | Ingest / producer |
| 数据库不可达 | TCP 5432 或 SSH 探针失败 | LAN / PostgreSQL |

**原则**：先保留现场和证据，再恢复；不执行 `DELETE`、`TRUNCATE`、整库导入或无计划重启。

## 2. 角色、边界与安全

- L1 Ops：执行本节探针、记录时间线、执行无破坏恢复、升级。
- 数据 / 策略 owner：确认数据是否可回填、重复数据是否可接受、选择回填窗口。
- 平台 owner：处理 schema、迁移、数据库损坏和长期不可用。
- 所有命令默认只读；敏感信息（`MULTICA_TOKEN`、`.env`、数据库密码）不得贴入 issue 或日志。
- 不要在故障期间 `git reset/checkout/stash`，也不要修改生产数据来“让探针变绿”。

## 3. 5 分钟 Preflight（按顺序执行）

### 3.1 记录事件起点

```bash
mkdir -p /tmp/multica-data-outage
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="/tmp/multica-data-outage/$TS.txt"
{
  echo "started_at=$TS"
  echo "host=$(hostname)"
  date -u
} | tee "$LOG"
```

把 `LOG` 路径和故障现象写入工单。后续命令输出追加到该文件：

```bash
exec > >(tee -a "$LOG") 2>&1
```

### 3.2 Backend 与迁移状态

```bash
curl -sf --max-time 5 http://192.168.0.105:8080/healthz
```

期望：JSON 中 `status=ok`、`checks.db=ok`、`checks.migrations=ok`。失败时再执行一次；仍失败则跳到 §5.1。

若当前部署端口为 `8090`，使用：

```bash
curl -sf --max-time 5 http://192.168.0.105:8090/healthz
```

### 3.3 PostgreSQL 网络与容器状态

```bash
nc -z -w 5 192.168.0.105 5432
ssh -o BatchMode=yes -o ConnectTimeout=5 smark@192.168.0.105 \
  'docker ps --format "{{.Names}}\t{{.Status}}" | grep -E "postgres|multica" || true'
```

`nc` 失败或 SSH 失败说明是主机 / 网络层问题，跳 §5.2；容器不存在或非 running，跳 §5.3。

### 3.4 API 数据路径

未认证路由先验证 mux 与服务：

```bash
curl -sS -o /tmp/multica-config.json -w 'config_http=%{http_code}\n' \
  --max-time 10 http://192.168.0.105:8080/api/config
```

再用已配置 CLI（不要打印 token）查询指标：

```bash
multica metrics query --campaign <campaign> --output json > /tmp/multica-data-outage/metrics.json
```

记录 HTTP / CLI 错误，不要重试超过 3 次；持续 5xx/超时跳 §5.1，200 但无数据跳 §4。

## 4. 数据完整性与新鲜度诊断

### 4.1 只读统计

在 LAN 主机执行，只输出计数与时间，不输出业务敏感 payload：

```bash
ssh smark@192.168.0.105 'docker exec multica-postgres-1 psql -U multica -d multica -X -Atc '
"'"'SELECT
  count(*) AS total_rows,
  count(*) FILTER (WHERE created_at > now() - interval '\''24 hours'\'') AS rows_24h,
  max(created_at) AS newest,
  min(created_at) AS oldest
FROM run_metric;'"'"''
```

记录 `total_rows`、`rows_24h`、`newest`。`rows_24h=0` 或 `newest` 超过业务 SLA 即视为 ingest 停滞，进入 §5.4。

### 4.2 按 campaign / iteration 找异常

```bash
ssh smark@192.168.0.105 'docker exec multica-postgres-1 psql -U multica -d multica -X -Atc '
"'"'SELECT campaign, iteration, count(*) AS n, max(created_at) AS newest
FROM run_metric
GROUP BY campaign, iteration
ORDER BY newest DESC NULLS LAST
LIMIT 20;'"'"''
```

重点检查：同一 iteration 多行重复、最新时间明显落后、campaign 名不符合发布约定。不要在此处删除重复行。

### 4.3 Artifact 与 metric 对账

```bash
multica artifact list --output json > /tmp/multica-data-outage/artifacts.json
multica metrics query --campaign <campaign> --output json > /tmp/multica-data-outage/query.json
```

对账规则：有 `kind=metrics` artifact 但无对应 `run_metric` 行 → ingest 失败或解析失败；有 `run_metric` 行但字段缺失 → producer blob 或 known-key 解析问题。将 artifact id、task id、campaign、iteration 记录到工单，不修改 artifact。

## 5. 分层恢复

### 5.1 API / Backend 异常（无破坏优先）

1. 保存 §3 的 `/healthz` 与错误输出。
2. 查看远端日志最近 100 行：

```bash
ssh smark@192.168.0.105 'tail -100 ~/multica-tunnel/backend-prod.log'
```

3. 如果是刚部署后出现，使用 `docs/runbooks/deploy-server-105.md` 的验证与自动 binary 回滚流程；不要直接重启服务。
4. 若日志显示 panic、migration error 或持续 5xx，跳 §6，并附时间戳和日志片段。

### 5.2 LAN / PostgreSQL 不可达

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 smark@192.168.0.105 true
nc -z -w 5 192.168.0.105 5432
```

两者失败：标记 `external_dependency`，升级平台 owner；不要反复重启 LAN 主机。

SSH 可达但 5432 失败：记录 `docker ps`、容器状态、磁盘和日志：

```bash
ssh smark@192.168.0.105 'df -h; docker ps -a --format "{{.Names}}\t{{.Status}}"; docker logs --tail 100 multica-postgres-1'
```

只在平台 owner 明确批准后执行容器恢复。

### 5.3 PostgreSQL 容器异常

- `docker ps` 显示容器 restarting/exited：保存 `docker inspect` 和最后 100 行日志。
- 磁盘不足：先升级，不删除数据目录或旧备份。
- migration 不一致：停止在应用层回填，交给平台 owner；不得手改 migration 表。
- 恢复后必须重新跑 §3.2、§3.3、§4.1，并确认 `healthz` 的 migrations 为 `ok`。

### 5.4 Producer / ingest 停滞

1. 确认 artifact 是否持续产生（§4.3）。
2. 确认最近 task 状态：

```bash
multica issue list --status in_progress --limit 20 --output json > /tmp/multica-data-outage/in_progress.json
multica task list --output json > /tmp/multica-data-outage/tasks.json
```

3. 若有 artifact 无 metric，保留 artifact id，检查对应 iteration 的 metrics blob 是否为合法 JSON；不要重复上传同一 blob，除非 owner 指定新版本 / 新 idempotency 语义。
4. 若 producer task 停滞，按 `docs/runbooks/agent-stall-ops.md` 做 runtime 诊断；不要把数据故障伪装成策略 issue。
5. 恢复后观察至少一个 ingest 周期，重复执行 §4.1，记录 `rows_24h` 与 `newest` 的变化。

### 5.5 误报或数据延迟但服务正常

如果 `/healthz` 正常、API 200、数据库可达，但 `newest` 落后：

- 先确认业务数据源是否在该时间段应当产出；
- 检查 producer / scheduler 是否处于 paused、backlog 或等待人工批准；
- 记录 expected window 与 observed window；
- 不要手工补造指标；由数据 owner 决定重跑 / 回填。

## 6. 升级条件与证据包

立即升级平台 / 数据 owner，满足任一：

- `/healthz` 连续 2 次失败，或 `db/migrations` 非 `ok`；
- PostgreSQL 不可达超过 5 分钟；
- 有 artifact 但 ingest 不落库超过一个完整处理周期；
- 发现重复、截断、跨 campaign 污染或 schema/migration 错误；
- 恢复动作后 5 分钟内再次故障；
- 需要重启生产服务、回滚 migration、恢复备份或修改数据。

证据包至少包含：

- UTC 起止时间、受影响 campaign/iteration；
- `/healthz` 原始响应与 HTTP code；
- `total_rows`、`rows_24h`、`newest` 原始 SQL 输出；
- artifact/task id 与对应 query 输出路径；
- 远端日志文件位置与关键错误行；
- 已执行的无破坏动作及结果；
- 未执行的破坏性动作与原因。

评论模板（不要内联 token 或密码）：

```text
[type=ESCALATE-DATA] Data outage detected.
Window: <UTC start>–<UTC now>
Scope: campaign=<...>, iteration=<...>
Health: <HTTP code + redacted JSON summary>
DB evidence: total_rows=<...>, rows_24h=<...>, newest=<...>
Artifacts/query evidence: <artifact/task IDs and /tmp evidence paths>
Actions: <read-only probes and results>
Blocked on: <owner decision / platform dependency / rollback approval>
```

## 7. 恢复验收与关闭标准

只有全部满足才可将故障转为 resolved / in_review：

- [ ] `/healthz` 连续 3 次成功，`db=ok`、`migrations=ok`；
- [ ] `nc 192.168.0.105 5432` 成功；
- [ ] `multica metrics query --campaign <campaign> --output json` 返回 200 且 JSON 可解析；
- [ ] `run_metric` 的 `newest` 在业务 SLA 内，且 `rows_24h` 恢复增长；
- [ ] 新 ingest 行与对应 metrics artifact/task 可对账；
- [ ] 没有未经批准的删除、覆盖、migration 回退或整库恢复；
- [ ] 证据包已附 issue，owner 已确认数据是否需要回填；
- [ ] 若发生回填，单独记录输入版本、行数、耗时、结果与重复保护方式。

## 8. 可复现证据命令清单

以下命令不修改数据，适合作为验收基线：

```bash
curl -sf --max-time 5 http://192.168.0.105:8080/healthz
nc -z -w 5 192.168.0.105 5432
multica metrics query --campaign <campaign> --output json
multica artifact list --output json
ssh smark@192.168.0.105 'docker exec multica-postgres-1 psql -U multica -d multica -X -Atc "SELECT count(*), count(*) FILTER (WHERE created_at > now() - interval '\''24 hours'\''), max(created_at) FROM run_metric;"'
```

本 runbook 的验证证据应保留在 `/tmp/multica-data-outage/<UTC timestamp>.txt` 和关联 issue comment 中；超过保留期的临时文件可按 workspace 清理规则处理。
