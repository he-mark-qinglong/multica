# W5-S1 — Epoch 循环胶水（T1–T4）Round-2 执行卡（infra-sprint 2026-07-25）

> 范围：`infra_health_check.sh` 脚本化、autopilot 清单快照、epoch 三触发器 manifest+幂等 apply、comment schema linter。
> 每张卡自包含，面向零上下文 caocao-m3 执行 agent（30min 预算）。本文件是唯一产出，未改任何代码/git。
> 统一前缀：`PY=/Users/mark/sdk/mamba-envs/trading/bin/python3`（pytest 8.3.4 已实测可用）。

---

## 0. 已实测事实（执行 agent 可直接信任，2026-07-25 核对）

- **27 个 autopilot 今天已全部 active**（`multica autopilot list --output json` 实测 total=27，无 paused）。
  round1 里「3 active + 24 paused」已过时。现有清单里 **没有** 10:00 spec-select / 17:00 validation-trigger / 20:00 verdict-trigger。
- 现有标题命名惯例：`research-scout (daily 09:00)`、`epoch-retro (daily 21:00)`、`infra-health-watchdog (10m)`。
- **autopilot CLI 真实接口**（`server/cmd/multica/cmd_autopilot.go`）：
  - `multica autopilot list --output json` → `{"autopilots":[{id,title,status,execution_mode,assignee_id,last_run_at,...}], "total":N}`（:193-204）
  - `create --title --agent --mode {create_issue|run_only} [--description --priority --issue-title-template]`（:122-129）；`--issue-title-template` 只插值 `{{date}}`（UTC, YYYY-MM-DD）（:128）
  - `update <id> [--title --description --agent --status --mode --issue-title-template]`（:132-140）
  - `trigger-add <id> --kind schedule --cron '<expr>' --timezone 'Asia/Shanghai' --label '...'`（:154-158）；timezone 默认 UTC，必须显式传
  - `get <id8|uuid> --output json` → `{"autopilot":{...}, "triggers":[{id,cron_expression,timezone,enabled,label,...}]}`（已实测 research-scout：cron `0 9 * * *` + tz `Asia/Shanghai`）
  - id 支持 8 位前缀（`resolveAutopilotID`）。
- **现有 agent 名**（`multica agent list` 实测，全部 `local` runtime）：`smark-decision-maker`、`multica-strategy`、`quant-research-agent`、`multica-ops`、`knowledge-curator`、`smark-signoff-proxy` 等 14 个。create/update 的 `--agent` 接受名字（大小写不敏感子串匹配，唯一命中才行，`cmd_autopilot.go:727-762`）。
- **mac 本机 launchd**（`launchctl list` 实测，均有 PID 在跑）：`com.smark.caocao-tunnel`（18091）、`com.smark.caocao-model-proxy`（18092）、`com.smark.multica-daemon`。plist 在 `~/Library/LaunchAgents/`。
- **.105 healthz** 实测：`curl -sf -m 5 http://192.168.0.105:8080/healthz` → `{"status":"ok","checks":{"db":"ok","migrations":"ok"}}`，exit 0。
- **multica CLI** 在 mac：`/Users/mark/.local/bin/multica`，已指向 .105:8080。
- **comment schema**（AGENTS.md「Comment Schema Convention」节，强制）：首行必须是
  `[type=<TYPE>] <iso8601 timestamp+tz> <one-line summary>`，TYPE ∈ `STATUS|DECISION|EVIDENCE|KILL|ESCALATE|SIGNOFF|NUDGE|NOOP`。
  注意官方例子里时区写作 `+08`（无冒号无分钟），如 `2026-07-19T22:45+08`。
- **issue 评论 CLI**（`server/cmd/multica/cmd_issue.go:386-392`）：`multica issue comment add <issue-id> --content-stdin`（多行从 stdin）或 `--content-file <path>`。
- **shell 脚本风格基准**：`scripts/quant_disk_quota_alert.sh`（`set -euo pipefail` + `while/case` 旗标解析 + env 可覆盖默认值）。新脚本照此风格。
- **`ops/` 目录不存在**（已实测）；`ops-reports/` 存在。pytest 直接可跑 `scripts/test_*.py`（根目录无 pytest.ini/conftest 干扰，`scripts/__pycache__` 证明历史跑过）。
- 铁律：不改 launchd plist、不改隧道/模型代理、不建 crontab（一切新触发器走 multica autopilot）、prompt 里禁止指定模型（全部 caocao-m3 由运行时决定）。

---

## T1 — infra_health_check.sh：watchdog 探活逻辑脚本化

- **目标**：把 infra-health-watchdog 的探活逻辑落成可单测、可复用的本机脚本。
- **机器**：**mac**（launchd 探活只能在本机跑）。**估时 25min**。
- **读**（只读参考，不修改）：
  - `scripts/quant_disk_quota_alert.sh:22-43` — 旗标解析/风格模板
  - `AGENTS.md`「Infra (launchd-managed since 2026-07-25)」节 — 服务清单出处
- **写**（新建唯一文件）：`scripts/infra_health_check.sh`，`chmod +x`

### 步骤

1. 文件头：`#!/usr/bin/env bash` + 用途注释 + `set -euo pipefail`。
2. 定义 6 个探测项（每行格式 `<name>|<type>|<target>`，用 here-doc 或数组）：

   | name | type | target |
   |---|---|---|
   | `launchd:caocao-tunnel` | launchd | `com.smark.caocao-tunnel` |
   | `launchd:caocao-model-proxy` | launchd | `com.smark.caocao-model-proxy` |
   | `launchd:multica-daemon` | launchd | `com.smark.multica-daemon` |
   | `port:18091` | port | `127.0.0.1:18091` |
   | `port:18092` | port | `127.0.0.1:18092` |
   | `http:server-105-healthz` | http | `$HEALTH_URL`（默认 `http://192.168.0.105:8080/healthz`） |

3. 探活函数（写清楚给 m3）：
   - launchd：`pid="$(launchctl list 2>/dev/null | awk -v l="$label" '$3==l {print $1}')"`；`[[ "$pid" =~ ^[0-9]+$ ]]` 为 ok（`-` 或空 = fail）。
   - port：`nc -z -G 3 127.0.0.1 "$port"`（macOS 的 nc 用 `-G` 做超时；没有 nc 则退化 `curl -sf -m 3 "http://127.0.0.1:$port/" -o /dev/null` 不算 fail 除非连接被拒——用 `nc` 优先即可，macOS 自带）。
   - http：`curl -sf -m 5 "$HEALTH_URL" | grep -q '"status":"ok"'`。
4. 旗标（照 quota 脚本的 `while/case`）：
   - `--json`：输出 `{"generated_at":"<utc iso>","ok":<bool>,"probes":[{"name","type","target","ok","detail"},...]}`，一个失败整体 exit 1，全过 exit 0。
   - `--self-heal`：对失败的 launchd 项先 `launchctl kickstart -k "gui/$(id -u)/$label"`，sleep 2 后重测一次；只自愈 launchd 类型，port/http 失败只报告不动手。
   - 默认（无旗标）：人类可读逐行 `[OK]/[FAIL] <name> — <detail>`。
   - 未知旗标 → usage 到 stderr，exit 2。
5. exit 码约定写进文件头注释：0 全过 / 1 有失败 / 2 用法错。
6. env 可覆盖：`HEALTH_URL`、`TUNNEL_PORT`（默认 18091）、`PROXY_PORT`（默认 18092）——禁止硬编码后无出口（参考 `scripts/autopilot_loop.sh:5` 硬编码 `/home/smark/multica` 的反面教材）。

### 验收（逐条可跑）

```bash
bash -n scripts/infra_health_check.sh && echo SYNTAX_OK
bash scripts/infra_health_check.sh --json > /tmp/ihc.json; echo "exit=$?"
$PY -c "import json;d=json.load(open('/tmp/ihc.json'));assert d['ok'] is True;assert len(d['probes'])==6;assert all(p['ok'] for p in d['probes']);print('ALL_6_PROBES_OK')"
```

预期：`SYNTAX_OK`、`exit=0`、`ALL_6_PROBES_OK`（当前实测三项 launchd 均在跑、healthz 通，应全绿；若某项恰好宕，`--self-heal` 后重跑）。

### 依赖

无。

---

## T2 — dump_autopilots.sh：autopilot 清单快照

- **目标**：一键把 27 个 autopilot 的状态 dump 成 markdown 清单，供恢复决策/防重复造轮子/T3 撞名检查。
- **机器**：**mac 或 105**（两边都有 multica CLI；已在 mac 实测通）。**估时 15min**。
- **读**（只读参考）：
  - `server/cmd/multica/cmd_autopilot.go:193-221` — list JSON 字段：`id/title/status/execution_mode/assignee_id/last_run_at`，顶层 `total`
  - `scripts/quant_disk_quota_alert.sh:22-43` — 风格模板
- **写**：
  - `scripts/dump_autopilots.sh`（新建，`chmod +x`）
  - `ops-reports/autopilot-inventory.md`（脚本生成的产物；agent 验收时会真实生成一版）

### 步骤

1. `scripts/infra_health_check.sh` 同款头：`set -euo pipefail` + 注释。
2. 旗标：`--out <path>`（默认 `ops-reports/autopilot-inventory.md`，相对 repo 根；脚本内 `cd "$(dirname "$0")/.."` 定位 repo 根）。
3. 数据获取：`json="$(multica autopilot list --output json)"`；失败（非 0 退出）则 stderr 报错 exit 1。
4. 渲染：内嵌 heredoc 调 `$PY`（脚本里写 `PY_BIN="${PY_BIN:-/Users/mark/sdk/mamba-envs/trading/bin/python3}"`，`"$PY_BIN" - "$json" <<'EOF'` 不行——用环境变量传：`INVENTORY_JSON="$json" "$PY_BIN" - <<'EOF'` 读 `os.environ`）：
   - 头部：`# Autopilot Inventory`、`> Generated: <utc iso> by scripts/dump_autopilots.sh`、`> Total: N (active: A, paused: P)`
   - 表格列：`| ID | Title | Status | Mode | Assignee | Last run |`，ID 取前 8 位；`last_run_at` 为 null 显示 `—`；按 title 排序。
   - 尾部列出「按 status 分组的 title 列表」可选，不做强制。
5. assignee 是 UUID 不是名字——直接显示 UUID 前 8 位即可，**不要**为解析名字额外调 agents API（保持脚本 <5s）。

### 验收

```bash
bash -n scripts/dump_autopilots.sh && bash scripts/dump_autopilots.sh && echo RUN_OK
test "$(grep -c '^|' ops-reports/autopilot-inventory.md)" -ge 28 && echo ROWS_OK
grep -q 'research-scout (daily 09:00)' ops-reports/autopilot-inventory.md && echo CONTENT_OK
```

预期：`RUN_OK`、`ROWS_OK`（表头+分隔行+27 行数据=29 行 `^|`）、`CONTENT_OK`。

### 依赖

无。

---

## T3 — epoch 三触发器：manifest 化 + 幂等 apply

- **目标**：补 epoch 主循环缺的 3 个日触发器（10:00 spec-select / 17:00 validation-trigger / 20:00 verdict-trigger），manifest 即代码，apply 幂等可重跑不产生重复。
- **机器**：**mac 或 105**（CLI 两边可用；验收只跑 `--dry-run`）。**估时 30min**。
- **读**（只读参考）：
  - `server/cmd/multica/cmd_autopilot.go:122-169` — create/update/trigger-add 的旗标全集（agent 名子串解析 :727-762）
  - `docs/plans/multica-quant-permanent-loop-2026-07-25.md:96-101` — epoch 循环原文（各时点职责）
  - `ops-reports/autopilot-inventory.md`（T2 产物；确认无撞名——已实测现有 27 个无此三个 title）
- **写**（全部新建，`ops/` 目录由本任务创建）：
  - `ops/autopilots/spec-select.json`
  - `ops/autopilots/validation-trigger.json`
  - `ops/autopilots/verdict-trigger.json`
  - `ops/autopilots/apply.sh`（`chmod +x`）

### Manifest schema（三个 json 同构）

```json
{
  "title": "epoch-spec-select (daily 10:00)",
  "agent": "smark-decision-maker",
  "mode": "create_issue",
  "priority": "medium",
  "issue_title_template": "epoch spec-select {{date}}",
  "description": "<prompt 全文，见下>",
  "trigger": { "cron": "0 10 * * *", "timezone": "Asia/Shanghai", "label": "daily 10:00 +08", "enabled": true }
}
```

三个 manifest 的差异项：

| 文件 | title | agent | cron | issue_title_template |
|---|---|---|---|---|
| spec-select.json | `epoch-spec-select (daily 10:00)` | `smark-decision-maker` | `0 10 * * *` | `epoch spec-select {{date}}` |
| validation-trigger.json | `epoch-validation-trigger (daily 17:00)` | `multica-strategy` | `0 17 * * *` | `epoch validation {{date}}` |
| verdict-trigger.json | `epoch-verdict-trigger (daily 20:00)` | `smark-decision-maker` | `0 20 * * *` | `epoch verdict {{date}}` |

`description` prompt 要求（每个 8-15 行，内容按 `multica-quant-permanent-loop-2026-07-25.md:96-101` 的时点职责写；执行 agent 需先读该文件该段再动笔）：

- 开头一句：`Read /Users/mark/multica/docs/plans/multica-quant-permanent-loop-2026-07-25.md §4/§6 first.`
- 明确本时点什么算「无活可干」→ 发 `[type=NOOP]` 评论收工（含原因），这是合法结果。
- 所有产出发言必须首行符合 comment schema（`[type=X] <iso8601+tz> <summary>`）。
- **禁止在 prompt 里指定任何模型**（m3 由运行时默认；写模型名 = 验收失败）。
- 幂等纪律：当日 issue 已存在（按 title `... {{date}}` 搜）就续用，不重复建。
- spec-select：从 SPEC 池（research-scout 产出的 backlog SPEC issues）选 1-2 个进入当日实现，发 `[type=DECISION]` 说明选/不选理由；池空 → NOOP。
- validation-trigger：对当日实现完成的策略走完整验证管线（§6），触发 smark-signoff-proxy 签核；无待验证策略 → NOOP。
- verdict-trigger：KEEP/KILL 判决，KEEP → ledger LIVE 候选，KILL → `[type=KILL]` 证据+复活条件归档；无待判决项 → NOOP。

### apply.sh 步骤

1. 同款头 + `cd "$(dirname "$0")"`；旗标：`--dry-run`（默认**就是 dry-run**，只有显式 `--apply` 才真正写服务器——防呆）。
2. 取现状一次：`current="$(multica autopilot list --output json)"`，用 `$PY` 提取成 `title→id` 映射（env 传递同 T2）。
3. 遍历 `ops/autopilots/*.json`（排序，保证输出稳定），逐个用 `$PY` 解析 manifest 字段到 shell 变量。
4. 判定逻辑（幂等核心）：
   - title 不存在 → 计划 `CREATE <title>`
   - title 存在 → 调 `multica autopilot get <id> --output json`，逐项比对 `description/agent/mode/issue_title_template/cron/timezone`；有差异 → 计划 `UPDATE <title> (<字段列表>)`；全同 → 计划 `SKIP <title>`
   - 注意：比对 trigger 时只比第一个 schedule trigger 的 `cron_expression`+`timezone`；autopilot 存在但**无 trigger** → 视为差异（计划里注明 `+trigger`）。
5. dry-run：逐行打印 `CREATE|UPDATE|SKIP <title> [详情]`，exit 0。
6. `--apply` 分支（本 sprint 验收**不执行**，但代码要写好）：
   - CREATE：`multica autopilot create --title "$t" --agent "$a" --mode "$m" --priority "$p" --issue-title-template "$tpl" --description "$desc" --output json` → 取返回 id → `multica autopilot trigger-add "$id" --kind schedule --cron "$cron" --timezone "$tz" --label "$label"`。
   - UPDATE：`multica autopilot update "$id" --description ... --agent ...`（只传有差异的字段）；trigger 差异用 `trigger-update "$id" "$trigger_id" --cron ... --timezone ...`，无 trigger 则 `trigger-add`。
   - description 多行：先写 tmp 文件（`mktemp`），用 `--description "$(cat "$tmp")"`，用完 `rm`。
7. 输出里若任何命令失败：stderr + exit 1（`set -euo pipefail` 兜住）。

### 验收（全部非变更性）

```bash
bash -n ops/autopilots/apply.sh && echo SYNTAX_OK
$PY -c "import json,glob;[json.load(open(f)) for f in sorted(glob.glob('ops/autopilots/*.json'))];print('JSON_OK')"
bash ops/autopilots/apply.sh --dry-run | tee /tmp/t3.plan; test "$(grep -cE '^(CREATE|UPDATE|SKIP) ' /tmp/t3.plan)" -eq 3 && echo PLAN_OK
! grep -qiE 'k2|kimi-k|claude|gpt|model:' ops/autopilots/*.json && echo NO_MODEL_PIN_OK
```

预期：`SYNTAX_OK`、`JSON_OK`、`PLAN_OK`（今天首次跑应为 3 行 `CREATE`）、`NO_MODEL_PIN_OK`。
**禁止**在本卡执行 `bash ops/autopilots/apply.sh --apply`（真实创建需指挥层批准，留给后续步骤）。

### 依赖

- **T2**（先产出清单确认无撞名；若 T2 未完成，以 `multica autopilot list --output json | grep -i 'epoch-'` 为空作为替代前置检查）。

---

## T4 — comment schema linter（comment-janitor 验证器落地）

- **目标**：把 AGENTS.md 里 TBD 的 comment schema 校验器落成脚本 + 测试，供 swarm 回收（T13）和日常自检复用。
- **机器**：**either**（纯 Python，无外部依赖）。**估时 20min**。
- **读**（只读参考）：
  - `AGENTS.md`「Comment Schema Convention (mandatory 2026-07-19)」节 — schema 权威定义 + 3 个官方例子
- **写**（全部新建）：
  - `scripts/comment_schema_lint.py`
  - `scripts/test_comment_schema_lint.py`

### lint 规则（权威，照此实现）

一条评论的首行（`text.splitlines()[0]`，允许前导空行？——**不允许**，首行即第一行）必须匹配：

```
^\[type=(STATUS|DECISION|EVIDENCE|KILL|ESCALATE|SIGNOFF|NUDGE|NOOP)\]\s+\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})\s+\S
```

要点（全是真实坑，写进代码注释）：

- 官方例子的时区是 `+08`（`2026-07-19T22:45+08`）——`[+-]\d{2}` 无分钟**也要接受**，即 `(Z|[+-]\d{2}(:?\d{2})?)`。
- 秒可选（例子都没秒）。
- summary 必须非空（`\S`）。
- type 必须大写精确命中 8 个之一；`[STATUS]`（无 `type=`）、`[type=status]`（小写）、`[type=FOO]` 均 fail。
- tag 必须在第 1 行；首行是空行/正文、tag 在第 2 行 → fail。
- body（第 2 行起）自由，不校验。

### comment_schema_lint.py 结构

```python
"""Lint multica issue-comment first lines against the AGENTS.md comment schema."""
import re, sys

TYPES = ("STATUS", "DECISION", "EVIDENCE", "KILL", "ESCALATE", "SIGNOFF", "NUDGE", "NOOP")
FIRST_LINE_RE = re.compile(...)  # 上面的规则

def lint_comment(text: str) -> list[str]:
    """Return list of human-readable violations; [] = pass. (T13 复用此函数签名，勿改)"""
    ...

def main(argv: list[str]) -> int:
    # 用法: comment_schema_lint.py <file> [<file>...]；无参数或 '-' 读 stdin
    # 逐文件: 通过打 "OK <path>"，失败打 "FAIL <path>: <violation>"（stderr）
    # 全过 exit 0，任一失败 exit 1，文件不存在 exit 2
```

`lint_comment` 的违规信息要具体（如 `"missing or unknown [type=X] tag"`、`"timestamp missing timezone offset"`、`"empty summary"`），测试会断言其中关键词。

### test_comment_schema_lint.py fixture（8 正 6 反，用 pytest.mark.parametrize）

正例（8 个 type 各一，覆盖时区变体）：

- `[type=STATUS] 2026-07-19T22:45+08 run 3c4ddf23 started`（`+08` 短时区）
- `[type=DECISION] 2026-07-19T23:25:30+08:00 chose X over Y because cost`（带秒+冒号）
- `[type=EVIDENCE] 2026-07-20T08:00Z CV sharpe -4.86`（Z）
- `[type=KILL] 2026-07-19T23:25+0800 vpvr_xs_pairs killed, framework CV sharpe -4.86`（`+0800`）
- `[type=ESCALATE] 2026-07-19T20:00+08 question: top up token-plan quota?`
- `[type=SIGNOFF] 2026-07-25T10:00+08 approving deliverable per gate evidence`
- `[type=NUDGE] 2026-07-25T10:05+08 re-dispatch to strategy-worker-1`
- `[type=NOOP] 2026-07-25T21:00+08 nothing to do: SPEC pool empty`

反例（6）：

1. 未知 type：`[type=PROGRESS] 2026-07-19T22:45+08 foo`
2. 无时区：`[type=STATUS] 2026-07-19T22:45 foo`
3. 空 summary：`[type=STATUS] 2026-07-19T22:45+08`（行尾）
4. tag 不在首行：`hello\n[type=STATUS] 2026-07-19T22:45+08 foo`
5. 无 `type=`：`[STATUS] 2026-07-19T22:45+08 foo`
6. 空字符串

另加 1 个 CLI 测试：tmp_path 写 1 正 1 反两文件，`main([str(f1), str(f2)]) == 1`，单正文件 `== 0`。

### 验收

```bash
$PY -m pytest scripts/test_comment_schema_lint.py -q
echo '[type=STATUS] 2026-07-19T22:45+08 ok summary' | $PY scripts/comment_schema_lint.py && echo STDIN_OK
```

预期：pytest 全过（≥15 个 parametrize 用例）；stdin 正例 exit 0 + `STDIN_OK`。

### 依赖

无。**注意：T13（另一 slice）会 `from comment_schema_lint import lint_comment`——函数名/签名/返回 `list[str]` 是跨卡契约，验收后不得改。**

---

## 跨 slice 冲突预警

1. **`ops/` 新目录约定**（T3）：round1 §4.5 已标——若别的 workstream 定了 ops-as-code 目录规范，manifest 按其迁移；目前 `ops/` 不存在，本卡先行创建。
2. **T4 的 `lint_comment(text) -> list[str]` 是被 T13（swarm 回收组）复用的公共接口**，属于跨 slice 契约，已在卡内固化。
3. **T3 只 dry-run**：真实 `--apply` 会写 .105 服务器状态（新建 3 个 active autopilot），需指挥层批准后单独执行，不在本批 swarm 内。
4. T1/T2/T4 文件互不相交，与 T3 也不相交；四卡可同批并行（T3 的 dry-run 对 T2 产物只有软依赖，有替代前置检查）。
