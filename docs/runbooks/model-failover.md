# Runbook — Model Failover (L1 Ops: detect + switch + verify)

> 适用范围：**multica agent runtime / kimi CLI** 当前默认模型（默认 `kimi-tang/k3`，但可被 `/model ...` 临时覆盖）出现 **401 / 403 / 429 / 5xx / 超时 / 持续 `kimi cancelled the prompt`** 等不可用症状时的**实时检测 + 切换备用模型 + 验证**手册。
> 执行机：mac 本机（需有 `multica` CLI + `~/.multica/daemon.env` + `~/.kimi-code/config.toml` + `~/.multica/healthcheck.sh`，四者已实测可用）。
> 本卡讲「模型层失效怎么办」。**与 agent-stall-ops 互补**：stall 卡讲「runtime 卡死怎么办」——同一 `daemon.log` 里 agent 级错误可能是任一来源，遇 `agent_error="kimi cancelled the prompt"` 先判层再分流，参见 §3.1。
> 本 runbook 是操作手册，**不**自动改 `daemon.env` 密钥、不动 kimi 配置里的 `default_model`、`不`清 `~/.multica/daemon.log`、不重启 LAN 后端。一切切换都留 checkpoint。

---

## 1. 用途与范围

- **目标**：当 multica 正在使用的 LLM 模型（默认 `kimi-tang/k3`，或 `/model` 设的临时模型）不可用时，**5 分钟内确认是模型层而非 infra 层**，**10 分钟内切换到备用模型（fallback model）并恢复 agent 任务**，或按 §6 升级。
- **不变更的东西**：不写 `~/.multica/daemon.env` 的密钥字段（除非 §5.2a 显式覆盖且带 checkpoint）；不删 `~/.multica/daemon.log`；不重启 LAN backend / Postgres；不批量 reassign in-flight issue。
- **当前已知的模型清单**（来自 `~/.kimi-code/config.toml` + `~/.multica/daemon.env`）：
  | 模型 id（kimi 视角） | provider | 端点 | 用途 / 何时用它 |
  |---|---|---|---|
  | `kimi-tang/k3` | managed:kimi-tang | `https://api.kimi.com/coding/v1` | **当前默认**（2026-07-26 实测） |
  | `kimi-smark/k3` | managed:kimi-smark | `https://api.kimi.com/coding/v1` | 主 kimi 失败时首选 fallback（同协议、同 SDK） |
  | `kimi-tang/k3-256k` | managed:kimi-tang | 同上 | 1M 上下文超限或 256K 更便宜时 |
  | `managed:kimi-tang/kimi-for-coding` | managed:kimi-tang | 同上 | K2.7 编码特化模型 |
  | `managed:kimi-tang/kimi-for-coding-highspeed` | managed:kimi-tang | 同上 | 同上 but fast |
  | `caocao-m3` | caocao | `http://127.0.0.1:18091`（隧道） | 隧道可达时的高质量 fallback |
  | `caocao-m2.7` | caocao | 同上 | 隧道可达时的高速 fallback |
  | `minimax-m3` | minimax | `https://api.minimax.io/anthropic` | 跨厂商 fallback（Anthropic-compatible） |
  | `glm-5.2-smart` / `glm-5.2-tang` | glm-smark / glm-tang | `https://open.bigmodel.cn/api/anthropic` | 1M 上下文兜底（GLM 详见 `~/.kimi-code/memory/GLM-5.2-config.md`） |
- **互补 / 重叠**：
  - **runtime 卡死** → `docs/runbooks/agent-stall-ops.md` §5.2（daemon 重启）；本卡不重述。
  - **LAN backend / DB / 隧道 失联** → `deploy-server-105.md` §5.1 / §5.3、`agent-stall-ops.md` §5.3 / §5.4；本卡只交叉引用。
- **不在范围**：策略 / backtest / 模型输出质量问题（属 multica-strategy / multica-orchestrator）；纯安全 / 越权问题（属 multica-code security skill）。

## 2. 模型失效信号定义

满足**任一**即视为疑似模型层失效，进入 §3 Preflight：

| 信号 | 含义 | 量化阈值 |
|---|---|---|
| `daemon.log` 中反复出现 `agent_error="kimi cancelled the prompt"` 或 `abort reason: model refusal` | 当前模型主动拒答 / 超时 | 单 issue 内 ≥ 3 次 或 30 s 内 ≥ 5 次 |
| `daemon.log` 中出现 `401 Unauthorized` / `403 Forbidden` + `anthropic-api-key` / `provider=kimi-tang` 字样 | 模型端鉴权失败（key 过期 / revoked） | 单次即触发 |
| `daemon.log` 中出现 `429 Too Many Requests` 持续 | 模型端限流 | 1 分钟内 ≥ 3 次且无降速迹象 |
| `daemon.log` 中出现 `5xx` + `upstream_error` / `model overload` | 上游模型服务故障 | 1 分钟内 ≥ 3 次 |
| `daemon.log` 中出现 `connection refused` / `timeout` 到 `https://api.kimi.com/coding/v1` 或 Zhipu 域名 | 模型端点网络层不可达 | 单次 + 排除 LAN 后端故障后触发 |
| `daemon.env` 中 `ANTHROPIC_BASE_URL=http://127.0.0.1:18091` 时 `:18091` 探针失联（caocao 隧道） → 但 `multica autopilot list` 报 tunnel autopilot `paused` | caocao 隧道挂了 | 1 次 + 健康检查第 1 探针 FAIL |
| kimi CLI 直接试一下，`/model <default>` 自检连续 ≥ 2 次返回非 0 | 直接验证 LLM 端点失效 | 见 §3.5 |

**关键区分（model vs infra）**：infra 故障会同时影响 `multica issue list`、`/healthz`、DB 查询；模型故障表现为 agent 任务 `aborted` 但 `multica issue list` / `daemon status` 正常。§3 Preflight 第 1 步就是做这个分流。

**已知历史案例**：
- **k3 401**：SMA-35019 / 知识-curator agent k3 403（key revoked 类）
- **kimi-tang/k3 持续 403**：2026-07-26 测试期 `agent finished ... agent_error="kimi cancelled the prompt"` 单 issue 内 ≥ 10 次
- **GLM-5.2 配额耗尽**：参见 `~/.kimi-code/memory/GLM-5.2-config.md` "Why GLM" 段（用户原话："后续 glm 的频率会远远不够使用"），属配额型失效
- **tunnel :18091 失联**：agent-stall-ops.md §1 已记录

## 3. Preflight（5 分钟内跑完）

按顺序跑，任一探针失败即可跳到对应 §4 / §5 节。

```bash
# 3.1 — 必须先排除 infra 故障（agent-stall-ops.md §3.1 的简化版）
~/.multica/healthcheck.sh && echo "[OK] local probes passed" || echo "[FAIL] infra — jump to agent-stall-ops.md §5"

# 3.1b — LAN backend（model 故障时这一条应仍为 ok）
curl -sf --max-time 5 http://192.168.0.105:8090/healthz \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('LAN backend:', d.get('status'), 'db:', d.get('checks',{}).get('db'))" \
  || echo "[FAIL] LAN backend — see deploy-server-105.md §5"
```

预期：
- local probes：OK 多行 + 退出 0。**已知 `healthcheck.sh` 末段 docker 容器探针漂移，详见 agent-stall-ops.md §3.1 注**。
- LAN backend：`status:ok` 且 `db:ok`。任一非 ok → **不是模型层问题**，跳 agent-stall-ops.md §5.3。

```bash
# 3.2 — daemon 仍在运行（agent-stall-ops.md §3.2 简化）
multica daemon status | grep -E '^Daemon:'
tail -30 ~/.multica/daemon.log | grep -E 'agent_error|aborted|401|403|429|5xx' | tail -10
```

预期：`Daemon: running`；最近 30 s 内出现 model 错误关键字（证实模型层）。无 model 关键字但 issue `aborted` 高发 → 见 §6 升级。

```bash
# 3.3 — 当前生效的 model
# 3.3a — kimi 全局默认
grep '^default_model' ~/.kimi-code/config.toml

# 3.3b — 本会话临时覆盖（kimi CLI 内 `/model` 不写盘，存在 kimi 进程 env/kv）
# 如果不知道当前会话用的什么模型：可在 kimi CLI 里跑 `/status` 或 `/model`（无参数 = 列出可用）

# 3.3c — daemon 把模型端点指哪
grep -E '^(ANTHROPIC|OPENAI|OPENROUTER|CAOCAO|MINIMAX)_' ~/.multica/daemon.env
```

预期：3.3a、3.3b、3.3c 三者能拼出一个明确的"当前模型 → provider → 端点"链路。`daemon.env` 里 `ANTHROPIC_BASE_URL=http://127.0.0.1:18091` 表示走了 caocao 隧道；空值或 `https://api.anthropic.com` 表示直连官方。

```bash
# 3.4 — 探针：模型端点基本可达
# 3.4a — 如果 default 走 kimi
probe_model_url="https://api.kimi.com/coding/v1"
# 3.4b — 如果走 Zhipu GLM
probe_model_url="https://open.bigmodel.cn/api/anthropic"
# 3.4c — 如果走 minimax
probe_model_url="https://api.minimax.io/anthropic"

# 通用探针：只测端点 HTTP 状态（不消耗 token）
curl -sS -o /dev/null -w 'model_endpoint_http=%{http_code} time=%{time_total}\n' \
  --max-time 10 "$probe_model_url/v1/models" \
  -H "Authorization: Bearer $(grep '^OPENROUTER_API_KEY' ~/.multica/daemon.env | cut -d= -f2)"
```

预期：
- `http=200` 且 `time < 3s`：模型端点网络层活。
- `http in (401, 403)`：模型端点活、但 token 无效 → **§5.2 密钥轮换**。
- `http in (429, 5xx)` 或 `time > 10s`：模型端点故障或限流 → **§5.3 切换 fallback 模型**。
- `connection refused` / DNS 失败：网络层问题，但不是模型端问题，跳回 §6 升级。

```bash
# 3.5 — CLI 自检：跑一次最小 prompt 验证当前默认模型能用
# 推荐用 /usr/local/bin/multica 或 `kimi` CLI 一句话 ping
multica autopilot list --limit 1 --output json \
  | python3 -c "import json,sys; print('autopilot count =', len(json.load(sys.stdin).get('autopilots', [])))"
# 上一步只是 CLI 通路；真正的模型活要靠 §3.4 + §3.6。
```

预期：CLI 通；output 非空。若 CLI 不通 → 转 agent-stall-ops.md §5.2（daemon hung），不要在模型层上耗时间。

```bash
# 3.6 — 当前 in-flight issue 是否还堆 abort
multica issue list --status in_progress --limit 20 --output json | python3 -c "
import json, sys
d = json.load(sys.stdin)
n = len(d.get('issues', []))
print(f'in_progress_count={n}')
# 若明显多（n > 30），且 daemon 日志中 abort 关键词密集 → 真模型故障
"
tail -200 ~/.multica/daemon.log | grep -c 'kimi cancelled the prompt'
```

预期：abort 计数 < 5 / 200 行；in_progress < 30。否则 → §5。

## 4. 快速诊断（按信号找位置）

| 信号 | 看哪里 | 命令 |
|---|---|---|
| 401 / 403 + `anthropic-api-key` | `daemon.env` 的 `ANTHROPIC_AUTH_TOKEN` / `OPENROUTER_API_KEY` / `CAOCAO_API_KEY` 当前值 | `grep -E '^(ANTHROPIC|OPENROUTER|CAOCAO|MINIMAX)' ~/.multica/daemon.env` |
| 模型超时 / 5xx | 模型端点的状态页（kimi / Zhipu / minimax 官方） | `curl -sI https://status.kimi.com` 或对应域 — 注意：很多 status 页会挡 curl，不通就跳过 |
| 429 限流 | daemon.log 中 retry-after 字样 | `grep -E 'retry-after|x-ratelimit' ~/.multica/daemon.log \| tail -20` |
| caocao 隧道挂了 | `:18091` 端口健康 + launchd | `lsof -nP -iTCP:18091 -sTCP:LISTEN`、`launchctl list \| grep caocao` |
| kimi-k3 持续 1 小时内 403 而 GLM/Claude 正常 | model 配额隔离 | 切 fallback 模型（§5.3） |
| 切换后仍报 401 | 新模型的 provider key 也失效 | 跳 §6 升级 smark（pair 决策：要不要同时轮多个 provider 的 key） |
| daemon 一直在 spawn 任务但全部 abort | 模型层 verified down（§3.4 失败 + abort ≥ 5 / 30 s） | §5.3 切 fallback + §5.4 等恢复 |

## 5. 分层动作（按代价递增）

> **铁律**：本卡的所有恢复动作 = 「配置 / 内存层切换」，**不是**破坏性恢复（不需要 ssh 到 .105 / 不需要重启 LAN backend），但仍按 AGENTS.md §4.2 "Don't blindly restart prod" 留 checkpoint：
> 1. 先在 `~/.multica/scratchpad/model-failover.log` 写一行 `before-switch`;
> 2. 动作做完后**跑 §3 全套确认恢复**，否则立刻升级（§6）。

### 5.1 无破坏：当前会话 `/model` 切换（首选）

> **适用**：本 kimi 会话的单次失败（例如 prompt 太长卡了），不需要全局改 default。

```bash
# 1. checkpoint
echo "$(date +%FT%T%z) before-switch: issue=<SMA-XXX> from=<old_model> to=<new_model>" \
  >> ~/.multica/scratchpad/model-failover.log

# 2. 在当前 kimi CLI 中执行（无副作用：只改本会话的 runtime model override）
/model kimi-smark/k3
# 或：/model minimax-m3
# 或：/model glm-5.2-smart
# 完整可用列表见 /Users/mark/.kimi-code/config.toml 13-156 行的 [models.*]

# 3. 验证
/status        # 应显示新模型
/think effort high  # （可选）强制 thinking effort，不被 fallback 改坏
```

适用：常驻 kimi 进程的人工干预。**自动化场景**（autopilot runner、夜间 cron）则走 §5.2 / §5.3，因为它们读的是 `default_model`。

### 5.2 无破坏：`default_model` 配置切 fallback（永久）

适用：daemon 长时间跑同一 default 失败（例如持续 30 分钟 401）。

```bash
# 1. checkpoint
echo "$(date +%FT%T%z) before-switch-default: from=$(grep '^default_model' ~/.kimi-code/config.toml) reason=<...>" \
  >> ~/.multica/scratchpad/model-failover.log

# 2. 备份当前 config
cp ~/.kimi-code/config.toml ~/.multica/scratchpad/config.toml.bak-$(date +%s)

# 3. 切换 default（用 sed 单行精确替换，只动 line 11，不要影响 [models.*] 块）
# 已知当前 default: kimi-tang/k3  →  切到: kimi-smark/k3
sed -i '' 's|^default_model = "kimi-tang/k3"|default_model = "kimi-smark/k3"|' \
  ~/.kimi-code/config.toml

# 验证
grep '^default_model' ~/.kimi-code/config.toml

# 4. 重启 daemon（让 default_model 重新加载；agent-stall-ops.md §5.2 已用同一动作）
multica daemon status   # 看是否已经在跑
multica daemon restart  # 干净的重启
sleep 5
multica daemon status   # 期望: Daemon: running

# 5. 跑 §3 Preflight 全套确认切换生效
~/.multica/healthcheck.sh
multica issue list --status in_progress --limit 5 --output json
```

> **回退**（恢复原 default）：
> ```bash
> cp ~/.multica/scratchpad/config.toml.bak-<stamp> ~/.kimi-code/config.toml
> multica daemon restart
> ```

### 5.2a 密钥轮换（仅当 §3.4 报 401/403 而非 5xx 时用）

**破坏性较低，但仍写盘**，先打 checkpoint：

```bash
# 1. checkpoint
echo "$(date +%FT%T%z) before-key-rotate: key=<which> from=<old-masked> to=<new-masked>" \
  >> ~/.multica/scratchpad/model-failover.log

# 2. 备份 daemon.env
cp ~/.multica/daemon.env ~/.multica/scratchpad/daemon.env.bak-$(date +%s)
chmod 600 ~/.multica/scratchpad/daemon.env.bak-*  # 密钥文件权限收窄

# 3. 拿到新 key 后，仅改对应那一行；不要整体覆盖（避免破坏注释 / 其他键）
# 例：把 ANTHROPIC_AUTH_TOKEN 从旧值换成新值
# 注：以下 <NEW_KEY> 由 smark 通过安全渠道（不贴 issue / 不贴 git / 不贴聊天）发给你
sed -i '' "s|^ANTHROPIC_AUTH_TOKEN=.*|ANTHROPIC_AUTH_TOKEN=<NEW_KEY>|" ~/.multica/daemon.env
chmod 600 ~/.multica/daemon.env

# 4. daemon 自带重载机制（首选 agent-stall-ops.md §5.2 的重启方式）
multica daemon restart
sleep 3

# 5. 验证
curl -sS -o /dev/null -w 'auth_check_http=%{http_code}\n' \
  --max-time 10 https://api.kimi.com/coding/v1/models \
  -H "Authorization: Bearer $(grep '^OPENROUTER_API_KEY' ~/.multica/daemon.env | cut -d= -f2)"
```

预期：`http=200`。非 200 → 跳 §6。

> **不要**：直接 `vim daemon.env` 后无 backup；用 `env $(cat daemon.env | xargs)` 暴露密钥到 `ps`；把新 key 写进 issue 评论或 git commit。**完整 0/0/0 准则**：写盘前备份，写盘后 600，写盘后只 reload 不 reboot。

### 5.3 有破坏：跨厂商 fallback（GLM / minimax / caocao）

> **适用**：kimi 全家桶（managed:kimi-tang / kimi-smark / kimi-for-coding）都报 401 或持续 5xx，但 GLM / minimax / caocao 至少有一个能跑。

```bash
# Step 1：先 §3.4 探针确认 fallback 端点活
fallback_url="https://open.bigmodel.cn/api/anthropic"     # GLM
fallback_url="https://api.minimax.io/anthropic"            # minimax
# 或 fallback_url="http://127.0.0.1:18091"                  # caocao 隧道（需先确认隧道活）

curl -sS -o /dev/null -w 'fallback_endpoint_http=%{http_code}\n' \
  --max-time 10 "${fallback_url}/v1/models"

# Step 2：检查对应 provider key 在 daemon.env 里非空
grep -E '^(ZHIPU|MINIMAX|CAOCAO|ANTHROPIC)' ~/.multica/daemon.env
# GLM key 不在 daemon.env 里——它在 kimi config.toml 的 [providers.glm-smart] / [providers.glm-tang]
grep -A 2 'providers\.glm-' ~/.kimi-code/config.toml

# Step 3：checkpoint + 切 default（按 §5.2 第 2-4 步；模型选 §1 表的对应行）
# 例如 kimi -> glm：
sed -i '' 's|^default_model = "kimi-tang/k3"|default_model = "glm-5.2-smart"|' ~/.kimi-code/config.toml
multica daemon restart

# Step 4：跨厂商验证 — 不光要 200，还要真生成一个 token 才算通
multica autopilot list --limit 1 --output json >/dev/null 2>&1 && echo "[OK] cli-path healthy"

# 真正验证由 daemon 走一个最小任务 — 等下一次 in-flight 任务自然触发，或人工写一个 ping issue
multica issue create --title "[PING] $(date +%s) model-failover sanity" \
  --description "model-failover runbook §5.3 ping test — assign back to multica-ops after verify" \
  --assignee multica-ops --status backlog
```

**回退**：和 §5.2 一样用 `config.toml.bak-<stamp>` 覆盖即可。

### 5.4 有破坏：等待上游恢复（兜底）

> **适用**：所有 fallback 都不可用，且升级已经发出（§6），但 smark 还没接手。

```bash
# Step 1：在 daemon.log 留标记，方便监控恢复
echo "$(date +%FT%T%z) model-failover hold: all-providers-down, awaiting upstream" \
  >> ~/.multica/scratchpad/model-failover.log

# Step 2：暂停不需要 LLM 的 autopilot（避免它们空白转）
# 不能直接 disable（SLA 风险），但可以把高频 cadence 的拉到 backlog：
multica autopilot list --output json | python3 -c "
import json, sys
d = json.load(sys.stdin)
for a in d.get('autopilots', []):
    cron = a.get('cron', '')
    if any(c in cron for c in ['*/2 *', '*/5 *', '*/10 *']) and a.get('status') == 'active':
        print(a.get('id'), a.get('name'), cron)
" | head -10

# Step 3：每 5 分钟刷一次 §3.4 探针直到恢复
while true; do
  curl -sS -o /dev/null -w 'upstream_http=%{http_code} ts=%{time_total}\n' \
    --max-time 10 https://api.kimi.com/coding/v1/models || echo upstream-timeout
  sleep 300
done
```

预期：恢复后立即走 §5.2 切回 default。若 30 min 内未恢复 → §6 升级不再等待。

### 5.5 已知坑位速查

| 症状 | 一秒识别 | 修复 |
|---|---|---|
| 切到 `glm-5.2*` 后报 `provider glm-smart not registered` | 退到 line 168 的 `[providers.glm-smart]` 块；这台机 `~/.kimi-code/config.toml` 已有 | kimi 进程未 reload，daemon restart 已含此步 |
| 切到 `caocao-m3` 后 `provider caocao 401` | `:18091` 没起来（隧道挂了） | 走 agent-stall-ops.md §5.4 恢复隧道，然后重试 |
| 切到 `minimax-m3` 后 401 | `MINIMAX_API_KEY` 在 daemon.env 顶上是空 / 过期 | §5.2a 轮 `MINIMAX_API_KEY` |
| 切 fallback 后 daemon 立刻 spawn 但全部 abort | 新 default 也挂了，没真正探针 | §3.4 整套重跑一次 |
| 切后 daemon 不起来 | config.toml 写坏（sed 转义错误） | `cp ~/.multica/scratchpad/config.toml.bak-<stamp> ~/.kimi-code/config.toml` 回滚 + `multica daemon restart` |

## 6. 升级路径

满足任一即**不再自行恢复**，立刻升级：

- [ ] §3.4 所有候选模型端点（kimi / GLM / minimax / caocao）连续 2 次均非 200 → 升级 smark + multica-orchestrator（multi-provider outage）
- [ ] §5.2a 轮换 key 后仍 401 → 升级 smark（怀疑 key 颁发流程坏了 / 跨厂商全失效）
- [ ] `daemon.log` 出现 panic / fatal / nil pointer 与 model 错误交织 → 升级 smark + multica-code
- [ ] 单 issue 因模型反复 abort 卡 ≥ 24 h 未升级（agent-stall-ops.md 也捕获这一项） → 由 `stalled-issue-watchdog` 接管；本卡只确认 watchdog 还活着
- [ ] 任何切了 `default_model` 后 10 min 内无法 revert → 升级 smark，留 §3.4 探针历史

升级用 `--content-stdin` HEREDOC（per AGENTS.md ## Comment Formatting），**不带 `@agent` mention**（避免 pingback loop），模板：

```bash
multica issue comment add <issue-id> <<'COMMENT'
[type=ESCALATE-OPS] Model failover — no fallback viable.

Window: <UTC start>–<UTC now>
Detected via: <daemon.log signal, e.g. "401 ANTHROPIC + kimi-tang/3 cancelled-prompt ×10">
Layer: confirmed model-side (daemon ok, LAN backend ok per §3.1b).

Already attempted:
- §5.2 switch default → kimi-smark/k3 — failed <reason>
- §5.2a rotate ANTHROPIC_AUTH_TOKEN — failed <reason>
- §5.3 cross-vendor fallback → glm-5.2 — <probe result>

§3.4 probe history: <paste 3-5 lines>
Hypothesis: <coincident provider outage / mass-key-revocation / quota burn>
Need smark decision on multi-provider routing policy.
COMMENT
```

## 7. 自检脚本

跑 §3 Preflight 的最小可重复版本（与本 runbook 同目录：`scripts/model_failover_selftest.sh`）：

```bash
bash ~/multica/docs/runbooks/scripts/model_failover_selftest.sh
```

预期：每个 §3 探针打印一行 `[OK] <name>` 或 `[FAIL] <name>`；exit code = `FAIL` 探针数（0 = 全过）。

实现要点（**不是**完整脚本，仅清单）：

- §3.1：跑 `~/.multica/healthcheck.sh`；末段 docker 漂移按 agent-stall-ops.md §3.1 注视为 INFO。
- §3.1b：单独跑 `curl http://192.168.0.105:8090/healthz`；失败按 WARN 不按 FAIL（跨环境）。
- §3.2：`multica daemon status | grep -c '^Daemon:.*running'`。
- §3.3：grep `default_model`、`grep -E '^(ANTHROPIC|OPENAI|OPENROUTER|CAOCAO|MINIMAX)_' ~/.multica/daemon.env`，断言 key 非空。
- §3.4：基于 `default_model` 选 `probe_model_url`，跑 `curl -sI` 即可，不消耗 token。
- §3.5：`multica issue list --status in_progress --limit 1` 解析 JSON 长度。
- §3.6：`tail -200 ~/.multica/daemon.log | grep -c 'kimi cancelled the prompt'`，期望 < 5。

## 8. 故障线索索引

| 现象 | 跳到 |
|---|---|
| 健康检查第 1 探针 FAIL（`:18091`） | agent-stall-ops.md §5.4（这是隧道，不是模型；本卡只交叉引用） |
| 健康检查第 2 探针 FAIL（`Daemon not running`） | agent-stall-ops.md §5.2 |
| `daemon.log` 中 401 + kimi-tang/`OPENROUTER` | §5.2a（轮 `OPENROUTER_API_KEY`） |
| `daemon.log` 中 401 + caocao | §5.2a（轮 `CAOCAO_API_KEY` + 确认隧道） |
| `daemon.log` 中 403 持续而非 401 | 模型端主动拒（quota / 滥用） → §5.3 跨厂商 fallback |
| `daemon.log` 中 429 + 短时大量 | 模型端限流，等 5 min 不缓解就 §5.3 |
| `daemon.log` 中 5xx + `upstream_error` / `overload` | §5.4 等待 + §3.4 每 5 min 刷一次 |
| `multica autopilot list` 中 `model-routing` 类 paused | 升级 smark；这是策略层 autopilot，不是 L1 跑得动的 |
| 切 fallback 后 daemon 不起 | §5.5 回滚 config.toml + 重启 daemon |
| kimi CLI `/model` 提示 `model not found` | 拼写错；可用列表见 `~/.kimi-code/config.toml` `[models.*]` 段 |

## 附录 A：相关代码 / 文档锚点

- `~/.kimi-code/config.toml:1-242`：所有 model id、provider、base_url 的真源；改 `default_model` 之前请先 `grep` 此文件确认拼写。
- `~/.multica/daemon.env:1-N`：daemon 用的密钥文件；改键前备份到 `~/.multica/scratchpad/daemon.env.bak-<stamp>`，并 `chmod 600`。
- `~/.multica/daemon.log`：模型错误主信源；按 `~/.multica/scripts/multica_log_rotate.sh`（per agent-stall-ops.md）轮转保留 26 MB。
- `~/.multica/healthcheck.sh:1-N`：本卡 §3.1 第 1 探针；末段 docker 漂移是已知问题。
- `~/.kimi-code/memory/GLM-5.2-config.md`：GLM-5.2 详细切换与定价信息（用户配额型失效的关键参考）。
- `docs/runbooks/agent-stall-ops.md` §3.1 / §5.2 / §5.3 / §5.4：infra 层失联时的恢复入口，本卡交叉引用。
- `docs/runbooks/deploy-server-105.md` §5：LAN backend 恢复入口，本卡交叉引用。
- multica-agent-base SKILL.md §4.2 "Don't blindly restart prod"：本卡 §5.2 / §5.2a / §5.3 三个写盘恢复的总约束来源。

## 附录 B：环境变量 / 配置

- `~/.kimi-code/config.toml`：所有 model + provider 配置真源。 `default_model` 改前必备份到 `scratchpad/`。
- `~/.multica/daemon.env`：daemon 用的密钥文件。任何改前必 600 备份到 `scratchpad/`。
- `MULTICA_TOKEN` / `MULTICA_WORKSPACE_ID` / `MULTICA_SERVER_URL`：CLI 鉴权 + 服务端定位，与本卡无关，列入防混淆。
- `ANTHROPIC_BASE_URL=http://127.0.0.1:18091`：daemon 走 caocao 隧道（实测 2026-07-26）。caocao 隧道挂了时模型端点亦随之不可用，先 §3.1 健康检查确认。
- `~/.multica/scratchpad/model-failover.log`：本卡所有 §5 checkpoint 的目标文件，留作回溯。

---

> 本卡与 `~/.kimi-code/config.toml` + `~/.multica/daemon.env` 同源，**两边任一关键值变动时本卡必须同步更新**（详见 §5.2 / §5.2a / §5.5）。下次 failover 演练前如果看到这两个文件有新 commit，先 diff 一遍、确认 §1 表 + §5.3 fallback URL 仍然准。
