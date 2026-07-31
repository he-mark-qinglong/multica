# Runbook — Agent Stall (L1 Ops: detect + recover)

> 适用范围：**multica daemon / agent runtime / autopilot** 出现"卡死 / 不动 / 重复 pingback / 401-403 模型拒绝"时的**实时检测 + 恢复**手册。
> 执行机：mac 本机（需有 `multica` CLI + `~/.multica/healthcheck.sh` + `~/.multica/stalled-ledger.md`，三者已实测可用）。
> 文档描述的是「信号 → 定位 → 恢复」完整链路；**写文档卡只描述动作，本卡不主动重启 daemon / 不自动 reassign issue**。
> 本 runbook 是 `docs/runbooks/` 下另一卡 `runbook_agent_stall.md`（由 knowledge-curator 交付，溯源历史 stall 案例）的 **L1 运维补充**——curator 卡讲「为什么过去 stall」，本卡讲「现在 stall 了怎么办」。

---

## 1. 用途与范围

- **目标**：`multica` 平台 agent runtime / autopilot / daemon 三层任意一层出现"无响应 / 重复触发 / 状态不前进"时，**5 分钟内定位层、10 分钟内恢复或升级**。
- **不变更的东西**：不 commit / push；不重启 daemon（除非 §5.2 显式覆盖）；不动 `.env` / `daemon.env`；不批量 reassign issue。
- **互补关系**：
  - **历史溯源 / 决策树** → `knowledge_base/runbook_agent_stall.md`（L5 knowledge-curator 产物，基于 SMA-34704 / SMA-30054 / SMA-34762 等历史事件）
  - **本卡** → 实时信号 + 命令 + 升级路径
- **不在范围**：策略 / backtest 相关 stall（属于 multica-strategy / strategy-worker 自身处理）；纯模型输出质量问题（属 multica-orchestrator 处理）。

## 2. Stall 信号定义

满足**任一**即视为 stall 信号，进入 §3 Preflight：

| 信号 | 含义 | 量化阈值 |
|---|---|---|
| `agent finished ... status=aborted ... agent_error="kimi cancelled the prompt"` 在 `daemon.log` 中反复出现 | agent runtime 主动放弃 / 超时 | 单个 task 内 ≥ 3 次 |
| `multica daemon status` 不报 `Daemon: running` | daemon 进程异常 | 一次即触发 |
| `~/.multica/healthcheck.sh` 任一探针非 0 | 隧道 / launchd-web / Postgres 容器假设（已漂移）/ Backend HTTP 探针异常 | exit ≠ 0；详见 §3.1 注 |
| `multica metrics query` 或 `multica issue list --status in_progress` 返 5xx / 超时 | LAN backend（192.168.0.105:8090）或 LAN Postgres 失联 | 1 次 curl `http://192.168.0.105:8090/healthz` 即知 |
| `curl -sf http://192.168.0.105:8090/healthz` 非 `status:ok / migrations:ok` | LAN backend 服务挂 | 见 `docs/runbooks/deploy-server-105.md` §4 |
| `in_progress` issue 24h+ 无 update | 工作流无人推进 | stall-hours ≥ 24（`stalled-issue-watchdog` 自动捕获） |
| `multica autopilot list` 显示 autopilot `status=paused` 且 cron 已到 | autopilot 跳过触发 | cron 时间窗 - 当前时间 ≥ 周期 |
| `multica issue metadata list <id>` 显示 `smark_decision_required=true` | 等人类决策，并非真 stall | 不入自动恢复；走 §6 升级 |

历史已观测 stall 类型（详见 `knowledge_base/runbook_agent_stall.md` §案例）：
- **模型 runtime 403 / 401**：kimi-code/k3 端拒绝（如 SMA-35019，知识-curator agent k3 403，**已卡 4.69 天 +**）
- **隧道 DEGRADED**：caocao-m3 端口 `:18091` 失联（健康检查第 1 探针即捕获）
- **Dispatch critic cap 5/5**：dispatcher 拒接所有新 issue（积压型 stall）
- **launchd web job 未加载**：`com.smark.multica-web` 不在 `launchctl list`

## 3. Preflight（5 分钟内跑完）

按顺序跑，任一失败即可跳到对应 §5 恢复节：

```bash
# 3.1 健康检查（实际部署：mac 端 daemon + caocao 隧道 + launchd-web；DB/Backend 在 LAN 192.168.0.105 上）
~/.multica/healthcheck.sh && echo "[OK] local probes passed" || echo "[STALL] local probe failed, jump to §5"

# 补一条 LAN backend 健康（healthcheck.sh **不会**测 LAN backend，需要单独跑）
curl -sf --max-time 5 http://192.168.0.105:8090/healthz | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print('LAN backend:', d.get('status'), 'db:', d.get('checks',{}).get('db'))" || \
  echo "[STALL] LAN backend /healthz failed, see deploy-server-105.md §5"
```

预期：
- 本机：`HEALTHCHECK OK: ...` 多行 + 退出 0。**已知 `healthcheck.sh` 末段仍引用 docker 容器（`multica-postgres-1` 等），现已不在 mac 端运行（Postgres 在 LAN 192.168.0.105:5432）——这是历史漂移，healthcheck.sh 自身的修复不在本卡范围**。
- LAN backend：`status:ok` 且 `db:ok`。任一非 ok → 跳 §5 + 交叉参考 `deploy-server-105.md` §4（验证）/ §5（恢复）。

```bash
# 3.2 daemon 进程 / 日志最近心跳
multica daemon status
tail -30 ~/.multica/daemon.log | grep -E 'heartbeat|kimi finished|agent finished|task did not complete' | tail -10
```

预期：daemon `Daemon: running`；日志最近 30s 内有 `heartbeat: skipping HTTP tick` 字样。无 → daemon hung（§5.2）。

```bash
# 3.3 stalled-ledger.md（最近 30m sweep）
ls -lt ~/.multica/stalled-ledger.md  # 看 mtime
tail -60 ~/.multica/stalled-ledger.md  # 看最近 2 次 sweep
```

预期：mtime ≤ 35 分钟（`stalled-issue-watchdog (30m)` 正常）。若 ≥ 60 分钟没刷 → watchdog 本身停了（§5.5）。

```bash
# 3.4 autopilot 自身是否 alive
multica autopilot list --output json 2>&1 | python3 -c "
import json, sys
d = json.load(sys.stdin)
for a in d.get('autopilots', []):
    n = a.get('name', '')
    s = a.get('status', '')
    c = a.get('cron', '')
    if any(k in n.lower() for k in ['stall', 'heartbeat', 'critic', 'queue', 'tunnel', 'health']):
        print(f'{s:<10} {c:<14} {n}')"
```

预期：所有关键 autopilot `status=active`。若 `paused` / `error` / `stopped` → §5.5。

```bash
# 3.5 自身 CLI 通路
multica issue list --status in_progress --limit 1 --output json | python3 -c "import json,sys;print('in_progress_count =', len(json.load(sys.stdin).get('issues', [])))"
```

预期：CLI 能返回（即 daemon + Postgres + workspace sync 全通）。若失败 → 跳 §5.2（daemon 异常）或 §5.3（DB 容器异常）。

## 4. 快速诊断（按信号找位置）

| 信号 | 看哪里 | 命令 |
|---|---|---|
| 模型 401 / 403 | `daemon.log` 末尾 + 关联 issue 评论 | `grep -E '401\|403\|API key' ~/.multica/daemon.log \| tail -20` |
| daemon hung | `daemon.log` 末尾 + 进程 | `tail -200 ~/.multica/daemon.log && ps -p "$(cat ~/.multica/daemon.pid 2>/dev/null)" -o pid,etime,stat,command 2>/dev/null` |
| 隧道 :18091 失联 | 健康检查 + launchd | `~/.multica/healthcheck.sh` 第一探针；`launchctl list \| grep caocao` |
| LAN Postgres / Backend 失联 | ssh 到 .105 + 健康探针 | `ssh -o BatchMode=yes smark@192.168.0.105 'curl -sf http://localhost:8090/healthz'`、`nc -z 192.168.0.105 5432` |
| 本机 docker 容器 down | docker ps（**已知 mac 端无 docker stack**，所有 DB 容器在 LAN 主机） | `docker ps --format '{{.Names}}\t{{.Status}}'` — mac 端应为空；不为空且 LAN 同步不通 = 走 §6 |
| launchd web job 未加载 | launchctl list + curl :3000 | `launchctl list \| grep -c com.smark.multica-web`（期望 ≥ 1） |
| Issue 24h+ 无 update | `stalled-ledger.md` 最近 2 sweep | `grep -E '\bstall=~2[4-9]\|stall=~[3-9][0-9]' ~/.multica/stalled-ledger.md \| tail -10` |
| 重复 agent 错误 | `daemon.log` 中 `agent_error` | `grep -c 'kimi cancelled the prompt' ~/.multica/daemon.log`（近期增长 ≥ 10 即异常） |

## 5. 恢复动作（按层分级）

> **铁律**：§5.2（重启 daemon）和 §5.4（restart tunnel）属于**破坏性操作**，按 AGENTS.md §4.2 "Don't blindly restart prod" 走：
> 1. 先在 `~/.multica/daemon.log` 确认根因；
> 2. 留一行 `before-restart` checkpoint；
> 3. 重启后**跑 §3 Preflight 全套确认恢复**，否则立刻升级（§6）。

### 5.1 无破坏：参数 / 状态修复

适用：`multica autopilot update --status active`（watchdog 暂停了）、issue 元数据键缺失、`daemon.env` 临时键值过期。

```bash
# 5.1a 重启一个 paused autopilot（watchdog 自身 → 其它需走 multica autopilot activate helper）
multica autopilot update <autopilot-id> --status active

# 5.1b 给 wait-for-smark 的 issue 写明 escape hatch（参考 stalled-ledger 规则 (3)）
multica issue metadata set <issue-id> --key smark_decision_required --value true --type bool
# 或直接 close:
multica issue status <issue-id> cancelled
# 或转 blocked 等人工:
multica issue status <issue-id> blocked
```

### 5.2 重启 daemon（破坏性 — 须满足全部条件才做）

满足**全部**再做：
- [ ] §3.2 确认 daemon hung（`multica daemon status` 不报 running）
- [ ] `daemon.log` 最近 30s 无 `kimi finished` / `agent finished`，表明 daemon 已不再 spawn 新任务
- [ ] 已在 issue / scratchpad 写一行 `before-restart: <timestamp> <reason>`

```bash
# Step 1：checkpoint
echo "$(date +%FT%T%z) before-restart: daemon hung, last heartbeat <XX>s ago" >> ~/.multica/scratchpad/agent-stall.log

# Step 2：daemon 自带重启（首选，比 systemctl 干净）
multica daemon restart    # 或: launchctl kickstart -k gui/$(id -u)/com.smark.multica-daemon
sleep 5

# Step 3：验证
multica daemon status     # 期望: Daemon: running
~/.multica/healthcheck.sh # 期望: 全 OK
```

不自动执行：连续 2 次 60s 内重启失败、或 healthcheck 恢复后 5min 内再次 stall → 跳 §6。

### 5.3 LAN 后端服务恢复（Postgres / Backend / Redis）

> 当前部署：DB 容器 + Backend 都在 **LAN `192.168.0.105`**（mac 上没有 docker container /docker-compose 跑这部分，per multica-agent-base SKILL.md §5.1 "DB: 192.168.0.105:5432"）。本节讲的"容器重启"等价于"LAN 主机上 docker compose up"或"systemctl 重启"——具体手段在 `deploy-server-105.md` §5，不再这里重复。

```bash
# Step 1：LAN backend 当前状态
ssh -o BatchMode=yes -o ConnectTimeout=5 smark@192.168.0.105 'curl -sf http://localhost:8090/healthz' \
  || echo "[STALL] LAN backend unreachable from mac or down"

# Step 2：LAN DB 端口可达
nc -z -w 5 192.168.0.105 5432 || echo "[STALL] LAN postgres:5432 unreachable"

# Step 3：LAN 主机上的实际恢复走两条路（详见 deploy-server-105.md §5.1~§5.3，这里只列举入口）
#   - 自动回滚：deploy.sh 已内置；走 `cd /home/smark/multica && scripts/deploy.sh server` 同入口
#   - binary 手滚：`deploy-server-105.md` §5.2（破坏性较小）
#   - DB 整库回退：`deploy-server-105.md` §5.3（**破坏性**，须 smark 确认）

# Step 4：跑 §3.5 验证 CLI 通路（用 mac CLI 直连 LAN backend）
export MULTICA_SERVER_URL=http://192.168.0.105:8090
multica issue list --status in_progress --limit 1 --output json \
  | python3 -c "import json,sys;print(len(json.load(sys.stdin).get('issues', [])))"
```

### 5.4 隧道 :18091 恢复（caocao-m3）

健康检查第 1 探针会先报 FAIL。`~/.multica/healthcheck.sh` 自身**只读** `daemon.env` 拿 key，**不**碰隧道 / 守护。隧道恢复要走 launchd 或手动：

```bash
# Step 1：看隧道进程
lsof -nP -iTCP:18091 -sTCP:LISTEN   # 期望：非空

# Step 2：launchd 拉起
launchctl kickstart -k gui/$(id -u)/com.smark.caocao-m3-tunnel   # 服务名按实际调整
sleep 3
lsof -nP -iTCP:18091 -sTCP:LISTEN

# Step 3：healthcheck 验证
~/.multica/healthcheck.sh
```

> 注：caocao-m3 隧道服务名以 launchd plist 实际为准（`~/Library/LaunchAgents/` 下找 `caocao*`）；改名前先 `launchctl list | grep -i caocao` 核对。

### 5.5 autopilot 自身停了

watchdog 类 autopilot 自身停 = 整个 L1 监控断电，必须恢复：

```bash
# Step 1：定位
multica autopilot list --output json | python3 -c "
import json, sys
d = json.load(sys.stdin)
for a in d.get('autopilots', []):
    if a.get('status') != 'active':
        print(f'{a.get(\"status\"):<12} {a.get(\"cron\"):<14} {a.get(\"name\")}')"

# Step 2：用 activate helper（推荐，per multica-agent-base §5.8 cadence tiers）
python3 ~/.multica/scripts/multica_autopilot_activate.py <autopilot-id>

# Step 3：跑 §3.3 / §3.4 验证
ls -lt ~/.multica/stalled-ledger.md   # mtime ≤ 35m
```

## 6. 升级路径

满足任一即**不再自行恢复**，立刻升级：

- [ ] §5.2 / §5.3 / §5.4 重启后 5min 内再次 stall → 升级 smark（issue 评论 `@smark`，附 §3 输出）
- [ ] `daemon.log` 出现 panic / fatal / nil pointer → 升级 smark + multica-orchestrator
- [ ] 模型 401/403 且**非 daemon.env 键值过期**（键还在但 token 失效） → 升级 smark，附 `daemon.env` 末 5 行（不要贴完整键）
- [ ] 单 issue 卡 24h+ 且无 `smark_decision_required` → 由 `stalled-issue-watchdog` 接管；本卡只确认 watchdog 还活着（§3.3）
- [ ] 隧道 host（caocao / Tokyo server 43.167.9.219）不可达且非本地重启能修 → 升级 multica-ops（self）留 issue 评论 + 标记 `external_dependency`

升级用 `--content-stdin` HEREDOC（per AGENTS.md ## Comment Formatting），**不带 `@agent` mention**（避免 pingback loop），示例：

```bash
multica issue comment add SMA-35935 <<'COMMENT'
[type=ESCALATE-OPS] Agent-stall detected; §3 preflight failed at §3.1 (healthcheck: tunnel :18091 not listening).
Already attempted §5.4 once at <ISO>; tunnel came back but went DEGRADED again in <N> minutes.
Hypothesis: caocao-m3 remote host issue (Tokyo server 43.167.9.219:60034?). Need smark decision on whether to switch tunnel endpoint.
COMMENT
```

## 7. 自检脚本

跑 §3 Preflight 的最小可重复版本（与本 runbook 同目录：`scripts/agent_stall_selftest.sh`）：

```bash
bash ~/multica/docs/runbooks/scripts/agent_stall_selftest.sh
```

预期：每个 §3 探针打印一行 `[OK] <name>` 或 `[STALL] <name>`；exit code = `STALL` 探针数（0 = 全过）。

实现要点：
- §3.1 跑 `~/.multica/healthcheck.sh`（外部脚本，已含 5 探针）
- §3.2 用 `multica daemon status | grep -c '^[[:space:]]*Daemon:[[:space:]]\+running'`
- §3.3 用 `find ~/.multica/stalled-ledger.md -mmin -45`（期望找到）
- §3.4 用 `multica autopilot list` + 过滤关键字，要求至少一个 critical autopilot `status=active`
- §3.5 用 `multica issue list --status in_progress --per-page 1 --output json` 解析长度

## 8. 故障线索索引

| 现象 | 跳到 |
|---|---|
| 健康检查第 1 探针 FAIL（:18091） | §5.4 |
| 健康检查第 2 探针 FAIL（Daemon not running） | §5.2 |
| 健康检查第 3 / 5 探针 FAIL（launchd / 本机 docker 假设漂移） | launchd 重启（web）；docker 假设 FAIL 视为已知漂移，已记录在 §3.1 注 |
| LAN backend `/healthz` 非 ok | `docs/runbooks/deploy-server-105.md` §5.1 自动回滚 / §5.2 手滚 |
| `stalled-ledger.md` 60min 没刷 | §5.5 |
| autopilot list 全 paused | §5.5 |
| 同一 issue 被 watchdog 连刷 ≥ 5 次 | §6 升级 smark |
| `daemon.log` 出现 `kimi cancelled the prompt` 短时间内 ≥ 10 次 | §5.2（多为模型 runtime 401/403，需配合 daemon.env 校验） |

## 附录 A：相关代码 / 文档锚点

- `~/.multica/healthcheck.sh:1-150`：本卡 §3.1 跑的 5-探针基线（2026-07-25 修订后版本）。**已知漂移**：末段 docker 容器探针已在本机失效（DB 已迁 LAN），需另写补丁或重写为 LAN 探针。
- `~/.multica/stalled-ledger.md`：watchdog 每 30m 写入的本地镜像（LAN 主机原版路径 `/home/smark/.multica/stalled-ledger.md`，mac 端同步落本文件）
- `~/.multica/scripts/multica_autopilot_activate.py`：§5.5 用的 autopilot preflight 助手（multica-agent-base §5.8 推荐）
- `docs/runbooks/deploy-server-105.md` §5：§5.3 LAN 后端恢复的二进制回滚 / DB 整库回退入口
- `knowledge_base/runbook_agent_stall.md`（knowledge-curator 交付）：本卡的**历史溯源互补**——同一议题但讲"为什么过去 stall"，本卡讲"现在 stall 了怎么办"
- multica-agent-base SKILL.md §4.2 "Don't blindly restart prod"：本卡 §5.2 / §5.3 / §5.4 三个破坏性恢复的总约束来源
- multica-agent-base SKILL.md §5.1 "DB: 192.168.0.105:5432, db multica"：§5.3 LAN 模式的源头
- multica-agent-base SKILL.md §5.8 cadence tiers：stalled-issue-watchdog (30m) 处于 L1 tier

## 附录 B：环境变量 / 配置

- `MULTICA_SERVER_URL`：默认 `http://127.0.0.1:8090`，本机直连 daemon 用；远端 (`smark@192.168.0.105`) 部署后验真时切 `http://192.168.0.105:8080`
- `MULTICA_WORKSPACE_ID`：见 `multica config show --output json`
- `MULTICA_TOKEN`：CLI 鉴权 token，缺时 `multica config set token <...>`
- `caocao-m3` 隧道：`/etc/hosts` + `~/.multica/daemon.env` 中的 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` 决定模型端点；键值过期是 §5.1a / §5.4 后仍 403 的最常见根因
- `daemon.log`：`~/.multica/daemon.log`（mac 端镜像；LAN 主端原版路径 `/home/smark/.multica/daemon.log`），轮转保留最近 ~26MB

---

> 本卡与 `~/.multica/healthcheck.sh` 同源；**healthcheck.sh 增删探针时本卡 §3.1 / §5.3 步骤必须同步更新**。下次 stall 复盘前如果 `git log` 看到 `healthcheck.sh` 有新 commit，先 diff 一遍、确认 §3.1 探针列表仍然准。