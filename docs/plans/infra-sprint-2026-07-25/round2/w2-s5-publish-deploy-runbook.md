# Round-2 任务卡 — 片 w2-s5：W2/T10-T12（发布侧 extra.verdict/kill_reason 写入约定 + server/web 部署 runbook）

> 来源：round1 `w2-server-compare.md` 的 T10、T11、T12。执行 agent 为 caocao-m3，零上下文，30 min 预算。
> 背景一句话：server gate 正在改 strict（缺失必填即 fail、缺 sharpe → `no-data`），compare 前端会从
> `run_metric.extra` 读 `verdict` / `kill_reason` 做 KILL 灰显和一句话 verdict（那是别的片 w2-s3/w2-s4 的任务）。
> 本片负责两件事：(a) 把「发布侧往 metrics blob 写 verdict/kill_reason」的约定固化并对齐 ledger 脚本；
> (b) 把 server/web 部署 + 回滚验证固化成可执行 runbook 文档。
> 本片共产出 **4 张卡**（T10 拆成 T10a 约定文档 + T10b 脚本对齐；T11/T12 各一张 runbook 卡）。

---

## 全局约束（四张卡都适用）

- 仓库根：`/Users/mark/multica`（下称 `$ROOT`）。所有命令在 `$ROOT` 下执行，除非卡片另注明。
- **禁止**：git 任何 mutation（commit/push/reset/checkout）；执行任何真实部署（`scripts/deploy.sh` 只能读、不能跑）；改动本片未列出的文件；跑回测。
- worktree 里有他人未提交改动，`git status` 看到脏文件是正常的，不要理会、不要回滚。
- Python 一律用 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（系统 python3 缺 pyarrow）。pytest 8.3.4 已在此 env 中（已核实）。
- 已核实的数据通路事实（2026-07-25，行号基于当前 worktree）：
  - run_metric 行**不是**由某个 Python 脚本 POST 上来的（已 grep 全 quant-loop，无 `/api/metrics` 调用、无 `artifact add` 调用）。真实通路：agent 用 CLI 上传 `kind=metrics` artifact → server `ingestRunMetric` 解析 blob 建行
    （`server/internal/handler/metric.go:247-288`）。
  - 上传命令形态（`server/cmd/multica/cmd_artifact.go:30-37`、flags 在 `:69-70`）：
    `multica artifact add <task-id> <blob.json> --kind metrics --meta '{"campaign":"<name>","iteration":"<iter>"}'`
  - **blob 里所有未识别的 key 自动落进 `run_metric.extra`**（`server/internal/handler/metric.go:140-148`，已识别 key 清单在 `metric.go:43-68`：`sharpe/sortino/calmar/ann_return/max_drawdown/profit_factor/oos_sharpe` 及其别名、`oos_windows/timeframe/tf/symbols/symbol/params`）。因此 `verdict`/`kill_reason`/`kill_evidence` 三个键写进 blob JSON 即可到达 compare 页，**零 server schema 变更**。
  - 前端消费方（别的片实现，键名已锁定）：`packages/views/compare/utils/verdict.ts` 的 `readVerdict` —— killed 判定 = `extra.kill_reason` 非空 或 `extra.divergence_flag ∈ {KILLED, REJECTED}`；一句话 verdict 渲染 `extra.verdict`。
  - ledger 现状：`quant-loop/scripts/build_results_ledger.py`（301 行）扫描 `quant-loop/strategies/` 生成 `quant-loop/results-ledger.md`（纯 markdown，无 JSON 输出、无 kill_reason）。verdict 状态机在 `_status()`（`:221-235`），当前枚举 = `{PASS, CV_PASS, HOLD, KILL, UNTESTED}`。**本片不改 `_status`**（verdict 状态机重构属 ledger workstream，见 round1 §3）。
  - 部署管线 `scripts/deploy.sh`（161 行，已通读，下文卡片内联关键行号）。部署目标机 `smark@192.168.0.105`，远端 repo `/home/smark/multica`。
  - 线上实测（2026-07-25，从 mac ssh 只读验证）：`ssh smark@192.168.0.105` 免密通；`curl localhost:8080/healthz` → `{"status":"ok","checks":{"db":"ok","migrations":"ok"}}`；`curl localhost:3000` → 200；workspace slug 含 `smark`（compare URL = `http://192.168.0.105:3000/smark/compare`）；run_metric 共 38 行；campaign 例：`mtf-xs-pairs`、`pairs-cointegration`、`vpvr-reversion` 等；已知 profit_factor 为 NULL 的行例：campaign=`mtf-xs-pairs`, iteration=`mtf_xs_pairs_1m_15m_2h_h3_20260718`（当前 gate_status=pass，strict gate 上线后应翻 fail）。

---

## w2-s5-T10a — 发布侧字段约定文档：`extra.verdict` / `kill_reason` / `kill_evidence`

- **目标**：新建一份约定文档，任何发布回测指标的 agent/脚本照此往 blob 写键，compare 页即可消费。
- **机器**：mac · **估时**：15 min · **依赖**：无（但键名必须与下方完全一致，这是和 w2-s3/T6-T7 的硬契约）

### 读
- `server/internal/handler/metric.go:41-68`（known-keys 清单，确认三个新键不撞名）
- `server/internal/handler/metric.go:140-148`（extra 兜底逻辑，约定的事实依据）
- `server/cmd/multica/cmd_artifact.go:30-37, 69-70`（上传命令形态与 flags）
- `quant-loop/research/swarm/2026-07-25/gate-ledger-fix/ledger_proposal.py:64-113`（verdict 语义参考，仅供写文档时解释枚举含义，不合入它）

### 写
- `quant-loop/docs/metrics-blob-convention.md`（新建，唯一改动文件；`quant-loop/docs/` 目录已存在，内有 `decisions/` 子目录）

### 步骤

1. 新建 `quant-loop/docs/metrics-blob-convention.md`，包含以下章节（内容要点必须全覆盖，措辞可自由）：

   - **通路**：发布 = `multica artifact add <task-id> <blob.json> --kind metrics --meta '{"campaign":"...","iteration":"..."}'`；server ingest 把 blob 里非 known-key 的键原样存入 `run_metric.extra`（引 `server/internal/handler/metric.go:140-148`）。known-key 清单照抄 `metric.go:43-68`，并警告：**不要**把 verdict 信息塞进已知键的别名（如 `pf`、`oos`），会被当列解析。
   - **三个约定键**（全部 optional、string 类型、顶层键）：
     - `verdict`： ledger 判决。允许值（与 `build_results_ledger.py:_status` 当前输出一致）：`PASS` / `CV_PASS` / `HOLD` / `KILL` / `UNTESTED`。注明：ledger workstream 落地 `ledger_proposal.py` 后 `PASS` 将更名为 `PROFITABLE`，消费方必须把未知值当作「无 verdict」容错。
     - `kill_reason`：一句话人类可读原因。**当且仅当 `verdict=KILL` 时必填**，其他情况省略或 null。compare 页用它做悬停提示，`extra.kill_reason` 非空即判定 killed。
     - `kill_evidence`：证据指针（文件相对路径、issue URL 或 run id），KILL 时建议填。
   - **完整示例**（必须给出可直接抄的 blob JSON + 上传命令）：

     ```json
     {
       "sharpe": 1.875, "ann_return": 0.598, "max_drawdown": -0.137,
       "profit_factor": 1.62, "oos_sharpe": 2.773, "oos_windows": 7,
       "timeframe": "2h", "symbols": ["BTCUSDT", "SOLUSDT"],
       "verdict": "CV_PASS",
       "kill_reason": null,
       "kill_evidence": null
     }
     ```

     ```bash
     multica artifact add <task-id> metrics.json --kind metrics \
       --meta '{"campaign":"mtf-xs-pairs","iteration":"mtf_xs_pairs_1m_15m_2h_h3_20260718"}'
     ```

   - **验证方法**：上传后 `multica metrics query --campaign <name> --output json`（`server/cmd/multica/cmd_metric.go:23-31`），确认返回行的 `extra` 含所写键。
   - **与旧数据的关系**：存量 38 行无这三个键 → compare 页按「无 verdict、未 killed」渲染，属预期；回填由 ledger/运维流程决定，不在本约定范围。

2. 文档头部加一行：`> 状态：约定 v1（2026-07-25）· 消费方：packages/views/compare（w2-s3/w2-s4）· 生产方：任何上传 kind=metrics artifact 的 agent`。

### 验收

```bash
cd /Users/mark/multica
test -f quant-loop/docs/metrics-blob-convention.md && \
grep -q 'kill_reason' quant-loop/docs/metrics-blob-convention.md && \
grep -q 'kill_evidence' quant-loop/docs/metrics-blob-convention.md && \
grep -q 'multica artifact add' quant-loop/docs/metrics-blob-convention.md && \
grep -q 'CV_PASS' quant-loop/docs/metrics-blob-convention.md && \
grep -q 'PROFITABLE' quant-loop/docs/metrics-blob-convention.md && echo PASS
```

预期输出 `PASS`。

---

## w2-s5-T10b — ledger 脚本对齐：`build_results_ledger.py` 输出 JSON sidecar（含 verdict/kill_reason）

- **目标**：ledger 脚本除 markdown 外再产出 `quant-loop/results-ledger.json`，每策略一行 `{strategy_key, verdict, kill_reason, kill_evidence}`，作为发布侧合并进 blob 的数据源。**不改 `_status` 状态机、不改 markdown 表结构**（避免和 ledger workstream 正面冲突）。
- **机器**：mac · **估时**：25 min · **依赖**：w2-s5-T10a（枚举与键名以约定文档为准）

### 读
- `quant-loop/scripts/build_results_ledger.py`（全文 301 行；关键锚点：`scan_strategy_dir` `:101-153`、`scan_all` `:156-177`、`_status` `:221-235`、`write_ledger` `:238-291`、`main` `:294-301`）
- `quant-loop/scripts/test_build_results_ledger.py`（全文 153 行，pytest，内有 `_row(**overrides)` fixture 工厂 `:21-37`）
- `quant-loop/docs/metrics-blob-convention.md`（T10a 产物，键名以此为准）

### 写
- `quant-loop/scripts/build_results_ledger.py`（仅新增函数 + main 里加一行调用；不改既有函数）
- `quant-loop/scripts/test_build_results_ledger.py`（仅追加新测试，不改既有测试）

### 步骤

1. 在 `build_results_ledger.py` 的 `write_ledger` 之后新增两个纯函数：

```python
def _kill_fields(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """(kill_reason, kill_evidence) for rows whose ledger verdict is KILL.

    Mirrors the two KILL branches of _status(): graveyard archival and
    framework-driven kill (AUTO-ARCHIVE / NOT-PROFITABLE without any
    PASS/WITHIN_TOLERANCE). Returns (None, None) for non-KILL rows.
    """
    if _status(row) != "KILL":
        return None, None
    if row["status"] == "GRAVEYARD":
        family = row.get("graveyard_family", "?")
        return f"archived to strategies/_graveyard/{family}", row["path"]
    for engine, fw in row["frameworks"].items():
        v = fw.get("verdict") or ""
        if "AUTO-ARCHIVE" in v or "NOT-PROFITABLE" in v:
            return (f"framework verdict {v} ({engine})",
                    f"{row['path']}/results/framework_cv_{engine}.json")
    return "ledger verdict KILL", row["path"]


def write_ledger_json(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Machine-readable sidecar for publishers merging verdict fields into
    metrics blobs (see quant-loop/docs/metrics-blob-convention.md)."""
    import datetime
    payload = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "strategies": [
            {
                "strategy_key": row["strategy_key"],
                "verdict": _status(row),
                "kill_reason": _kill_fields(row)[0],
                "kill_evidence": _kill_fields(row)[1],
            }
            for row in rows
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"[write] {out_path} ({len(rows)} strategies)")
```

   注意：`_status(row)` 对 GRAVEYARD 行返回 `"KILL"`，`_kill_fields` 的两个分支顺序必须与 `_status` 的 KILL 分支顺序一致（先 graveyard 后 framework）。

2. `main()`（`:294-301`）在 `write_ledger(rows, out)` 后加：

```python
    write_ledger_json(rows, REPO / "results-ledger.json")
```

3. `test_build_results_ledger.py` 末尾追加（复用文件里已有的 `_row` fixture 工厂与 `brl` import）：

```python
# --- JSON sidecar / kill fields (w2-s5-T10b) ---------------------------------

def test_kill_fields_none_for_non_kill():
    reason, evidence = brl._kill_fields(_row())
    assert reason is None and evidence is None


def test_kill_fields_graveyard():
    row = _row(status="GRAVEYARD", graveyard_family="1m_reversal",
               path="strategies/_graveyard/1m_reversal/synthetic_1h_20260725")
    reason, evidence = brl._kill_fields(row)
    assert reason == "archived to strategies/_graveyard/1m_reversal"
    assert evidence == "strategies/_graveyard/1m_reversal/synthetic_1h_20260725"


def test_kill_fields_framework_killed():
    row = _row(frameworks={"backtrader": {"sharpe": 0.1, "verdict": "NOT-PROFITABLE"}},
               framework_consistent=False, profitable=True,
               path="strategies/synthetic_1h_20260725")
    reason, evidence = brl._kill_fields(row)
    assert "NOT-PROFITABLE" in reason and "backtrader" in reason
    assert evidence.endswith("framework_cv_backtrader.json")


def test_write_ledger_json_schema(tmp_path):
    out = tmp_path / "ledger.json"
    brl.write_ledger_json([_row()], out)
    import json as _json
    payload = _json.loads(out.read_text())
    assert set(payload) == {"generated", "strategies"}
    entry = payload["strategies"][0]
    assert set(entry) == {"strategy_key", "verdict", "kill_reason", "kill_evidence"}
    assert entry["verdict"] == "PASS"  # profitable + framework_consistent fixture
```

   注意第 4 个测试：`_row()` 默认 fixture（sharpe 1.5 / PF 2.0 / mdd -0.10 / trades 100 / framework W5_PASS）经当前 `_status` 得 `PASS`；若 ledger workstream 已把状态机换成 `PROFITABLE` 导致此断言红，**不要改状态机**，把断言改成 `entry["verdict"] in ("PASS", "PROFITABLE")` 并在 PR 描述里注明。

4. 实跑一次（会重写 `quant-loop/results-ledger.md` 的时间戳并新建 `results-ledger.json`，两者都是生成物，属预期改动）：

```bash
cd /Users/mark/multica/quant-loop && /Users/mark/sdk/mamba-envs/trading/bin/python3 scripts/build_results_ledger.py
```

### 验收

```bash
cd /Users/mark/multica/quant-loop && \
/Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest scripts/test_build_results_ledger.py -q && \
/Users/mark/sdk/mamba-envs/trading/bin/python3 -c "
import json
p = json.load(open('results-ledger.json'))
assert p['strategies'], 'empty'
e = p['strategies'][0]
assert {'strategy_key','verdict','kill_reason','kill_evidence'} <= set(e), e
kills = [s for s in p['strategies'] if s['verdict'] == 'KILL']
assert all(s['kill_reason'] for s in kills), 'KILL without reason'
print('PASS', len(p['strategies']), 'rows,', len(kills), 'KILL')
"
```

预期：pytest 全绿；末尾打印 `PASS <N> rows, <K> KILL`（当前墓园 57 个目录，K 应 >0）。

---

## w2-s5-T11 — server 部署 runbook：固化 `deploy.sh server` + gate 回填 + 回滚验证

- **目标**：新建可照抄执行的 runbook 文档，覆盖「部署 server 到 192.168.0.105 → `/healthz` 验证 → `metrics reevaluate` 回填 → 抽查翻转行 → 回滚路径」。**本卡只写文档，不执行部署**。
- **机器**：mac（写文档；验收里的 ssh 只读检查已从 mac 实测通过）· **估时**：20 min · **依赖**：无（文档描述的执行前置是 W2 的 T1-T5 代码已合并——那些属别的片，runbook 里作为 preflight 检查项写明即可）

### 读
- `scripts/deploy.sh`（全文 161 行；server 相关锚点：构建 `:64-70`，上传 `:73-75`，迁移文件 rsync `:80`，远端：DB 备份 `:91-93`、`migrate up` `:96-98`、换 binary+重启 `:101-109`、healthz+路由 smoke `:120-134`、自动回滚函数 `:111-118`、daemon 空闲才重启 `:146-156`）
- `Makefile:295-299`（`make test` = ensure-postgres + migrate up + `go test ./...`）
- `server/cmd/multica/cmd_metric.go:39-47, 203-252`（`multica metrics reevaluate` / `query` 用法与响应字段）
- `server/cmd/multica/cmd_agent.go:250, 270, 304`（CLI 配置解析顺序：`--server-url` flag → `MULTICA_SERVER_URL` env → `multica config set server_url`；workspace 同理用 `MULTICA_WORKSPACE_ID`；token 用 `MULTICA_TOKEN`，见 `cmd_auth.go:72`）

### 写
- `docs/runbooks/deploy-server-105.md`（新建；`docs/runbooks/` 目录不存在，直接创建）

### 步骤

新建 `docs/runbooks/deploy-server-105.md`，章节与内容要点如下（命令必须原样可抄；方括号是给你填的说明，文档里写实际命令）：

1. **用途与范围**：部署 server binary + DB 迁移到 `smark@192.168.0.105:/home/smark/multica`，并回填 strict-gate 结果。从 mac 本机执行（需要 repo worktree + ssh 免密，两者已实测可用）。

2. **Preflight（逐项列出，任一不过则停止）**：
   - `git status --short server/` —— **警告**：`deploy.sh` 从本地 worktree 交叉编译（`scripts/deploy.sh:64-70`），worktree 里任何未提交改动都会被编译进线上 binary。确认 `server/` 下只有自己预期的改动；有他人改动时先从干净 worktree 部署。
   - `ls server/migrations/125_*.sql | wc -l` → `2`（gate_status CHECK 约束迁移，W2/T5 产物）。
   - `make test`（本地起 postgres + migrate + `go test ./...`，全绿才继续）。
   - `bash -n scripts/deploy.sh`（语法自检）。
   - `ssh -o BatchMode=yes -o ConnectTimeout=5 smark@192.168.0.105 true && echo SSH_OK`。
   - `ssh smark@192.168.0.105 'curl -sf http://localhost:8080/healthz'` → 部署前基线，期望含 `"migrations":"ok"`。

3. **部署**：`scripts/deploy.sh server`。文档内联说明它会自动做什么（照 deploy.sh 行号写）：交叉编译 3 个 binary → scp 到 `server/bin/.deploy-<stamp>/` → rsync 迁移 SQL → 远端 `pg_dump` 备份到 `~/multica-backups/pre-deploy-<stamp>.sql.gz` → `migrate up` → 旧 binary 存为 `server/bin/server.bak-<stamp>` 后换新 → 重启 → `/healthz` + `/api/tasks`、`/api/metrics/query`、`/api/artifacts` 三条路由 smoke（期望 401/200，404 即失败）→ **任一验证失败自动回滚到 `server.bak-<stamp>` 并以 exit 1 退出** → 最后查 `agent_task_queue`，无在跑任务才 `systemctl --user restart multica-daemon`，否则打印 SKIP 提示（属正常）。
   - 部署窗口注意（照 round1 §4 写）：与其他 workstream 的部署共用同一窗口，避免连续多次重启 daemon。

4. **部署后验证（逐条命令 + 期望输出）**：
   - `ssh smark@192.168.0.105 'curl -sf http://localhost:8080/healthz'` → `{"status":"ok","checks":{"db":"ok","migrations":"ok"}}`（2026-07-25 实测格式）。
   - 回填 gate：本机 CLI 指向 .105 ——
     ```bash
     export MULTICA_SERVER_URL=http://192.168.0.105:8080
     export MULTICA_WORKSPACE_ID=<smark workspace 的 UUID>   # 未配置过时用 multica config 查询
     multica metrics reevaluate
     ```
     期望表格 `REEVALUATED/PASS/FAIL/SKIPPED/ERRORS` 中 `ERRORS = 0`；T5 落地后响应 JSON 还会带 `no-data` 计数（`--output json` 可见）。
   - 抽查翻转行（strict gate 生效的铁证，行例已从线上 DB 核实）：
     ```bash
     multica metrics query --campaign mtf-xs-pairs --output json | \
       /Users/mark/sdk/mamba-envs/trading/bin/python3 -c "
     import json,sys
     rows=[m for m in json.load(sys.stdin)['metrics'] if m['iteration']=='mtf_xs_pairs_1m_15m_2h_h3_20260718']
     assert rows and rows[0]['gate_status']=='fail', rows
     print('PASS: sharpe-only row now fails strict gate')"
     ```
     （该行 profit_factor 为 NULL，旧语义 pass、strict 语义 fail。）

5. **回滚**：
   - **自动**：verify 失败 deploy.sh 已自动回滚（`:111-118`），退出码非 0，日志含 `VERIFY FAILED — rolling back`。
   - **手动 binary 回滚**（照抄命令块）：ssh 到 .105 → `cd /home/smark/multica` → `cp -a server/bin/server.bak-<stamp> server/bin/server` → `pkill -f 'server/bin/server$'; sleep 2` → `bash -c 'set -a; . ./.env; set +a; nohup ./server/bin/server >> "$HOME/multica-tunnel/backend-prod.log" 2>&1 & disown'` → `curl -sf localhost:8080/healthz` 确认。
   - **DB 回滚**：仅在迁移本身造成问题时才做，属破坏性操作，需人类确认。步骤：`cd server && ./bin/.deploy-<stamp>/migrate down 1`（回退 125）优先；整库恢复（`gunzip -c ~/multica-backups/pre-deploy-<stamp>.sql.gz | docker exec -i multica-postgres-1 psql -U multica multica`）只在万不得已时用，并先停 server。
   - **回滚验证**：healthz ok + `multica metrics query --campaign mtf-xs-pairs` 可正常返回（HTTP 200）。

6. **故障线索**小节：`~/multica-tunnel/backend-prod.log`（server stdout）、`ls -lt ~/multica-backups/ | head`（找备份）、deploy 输出末尾的 stamp 是所有回滚制品的索引。

### 验收

```bash
cd /Users/mark/multica
test -f docs/runbooks/deploy-server-105.md && \
grep -q 'scripts/deploy.sh server' docs/runbooks/deploy-server-105.md && \
grep -q 'multica metrics reevaluate' docs/runbooks/deploy-server-105.md && \
grep -q 'server.bak-' docs/runbooks/deploy-server-105.md && \
grep -q 'pre-deploy-' docs/runbooks/deploy-server-105.md && \
grep -q 'mtf_xs_pairs_1m_15m_2h_h3_20260718' docs/runbooks/deploy-server-105.md && \
grep -q 'MULTICA_SERVER_URL' docs/runbooks/deploy-server-105.md && \
bash -n scripts/deploy.sh && echo PASS
```

预期输出 `PASS`。

---

## w2-s5-T12 — web 部署 runbook：固化 `deploy.sh web` + compare 冒烟 + 回滚说明

- **目标**：同 T11，覆盖 web（Next.js）部署到 .105:3000 与 compare 页面冒烟清单。**只写文档，不执行部署**。
- **机器**：mac · **估时**：15 min · **依赖**：无（文档执行前置是 W2 的 T6-T9 前端代码已合并——别的片；建议排在 T11 所描述的 server 部署之后，以便有真实 fail/no-data 数据可看）

### 读
- `scripts/deploy.sh:27-61`（web 分支全文：rsync 源码 `:30-35`，远端 pnpm install + build + 重启 `:37-58`）
- `apps/web/app/[workspaceSlug]/(dashboard)/compare/page.tsx`（12 行薄壳，确认页面路由存在）
- round1 背景（内联即可）：compare 页应展示三态 gate 徽章（pass/fail/no-data）、KILL 灰显+悬停 kill_reason、一句话 verdict 区块

### 写
- `docs/runbooks/deploy-web-105.md`（新建，唯一改动文件）

### 步骤

新建 `docs/runbooks/deploy-web-105.md`，章节要点：

1. **用途与范围**：把 `apps/web` 部署到 `smark@192.168.0.105:3000`（远端 `pnpm --filter @multica/web build` + 重启 `next start`）。从 mac 执行。

2. **Preflight**：
   - `pnpm typecheck && pnpm test` 全绿。
   - `git status --short apps/web packages/` —— **警告**：web 分支用 `rsync -au` 同步**整个 worktree**（`deploy.sh:30-35`，无 `--delete`），他人未提交改动会一起上线；有脏文件先从干净 worktree 部署。
   - `ssh -o BatchMode=yes smark@192.168.0.105 true && echo SSH_OK`。

3. **部署**：`scripts/deploy.sh web`。内联说明：rsync（排除 `.git/node_modules/data/dist/.next/.turbo/.env` 等，清单见 `deploy.sh:32-34`）→ 远端 `pnpm install --frozen-lockfile` → `pnpm --filter @multica/web build`（数分钟）→ `pkill` 旧 `next start`/`next-server` → `nohup pnpm start`（日志 `~/multica-tunnel/web-prod.log`）→ 最多 120s 轮询 `curl -sf localhost:3000`，起不来则 exit 1（**注意：web 分支没有自动回滚**，见下）。

4. **冒烟（逐条）**：
   - `curl -s -o /dev/null -w '%{http_code}\n' http://192.168.0.105:3000` → `200`（2026-07-25 实测）。
   - 浏览器开 `http://192.168.0.105:3000/smark/compare`（slug `smark` 已从线上 DB 核实），对照清单打勾：
     1. gate 徽章出现三态（pass/fail/no-data），no-data 为灰色；
     2. 被 KILL 的策略行灰显，悬停显示 kill_reason（无 kill_reason 的 KILL 行显示 divergence_flag）；
     3. 有 verdict 的行 detail 面板顶部有一句话 verdict 区块，无 verdict 不渲染；
     4. 过滤后页面不为空（strict gate 后 fail 行应置灰展示而非消失）。
   - 页面异常时看 `ssh smark@192.168.0.105 'tail -100 ~/multica-tunnel/web-prod.log'`。

5. **回滚**（如实写明限制）：web 无 binary 备份机制。回滚 = 在本机 worktree 恢复旧代码（git 层面）后重跑 `scripts/deploy.sh web`；若只是构建产物问题，可 ssh 到 .105 `cd /home/smark/multica && pnpm --filter @multica/web build` 重建后手动 `pkill -f 'next start'` 再 `cd apps/web && nohup pnpm start >> ~/multica-tunnel/web-prod.log 2>&1 &`。回滚后同样跑第 4 节冒烟。

### 验收

```bash
cd /Users/mark/multica
test -f docs/runbooks/deploy-web-105.md && \
grep -q 'scripts/deploy.sh web' docs/runbooks/deploy-web-105.md && \
grep -q 'smark/compare' docs/runbooks/deploy-web-105.md && \
grep -q 'web-prod.log' docs/runbooks/deploy-web-105.md && \
grep -q 'frozen-lockfile' docs/runbooks/deploy-web-105.md && \
grep -q 'no-data' docs/runbooks/deploy-web-105.md && echo PASS
```

预期输出 `PASS`。

---

## 片内依赖与跨片冲突备注

- 执行顺序：T10a → T10b（同文件链无交集但语义依赖）；T11、T12 互相独立、与 T10 独立，可并行。
- **跨片冲突 1（最重要）**：T10b 改 `quant-loop/scripts/build_results_ledger.py` + 其测试文件。round1 §4 已预警 ledger verdict 状态机重构（`ledger_proposal.py` 合入）大概率属另一 workstream 且碰同一文件。本卡刻意只加新函数、不动 `_status`/markdown 表，把冲突面降到最小；若两流并行，**必须串行合入**（谁先谁后都行，后合者 rebase）。
- **跨片冲突 2**：T10a 的键名（`verdict`/`kill_reason`/`kill_evidence`）是 w2-s3（T6/T7 `readVerdict`）的消费契约，两边已对齐；若 w2-s3 改键名，本约定文档必须同步。
- **跨片冲突 3**：T11/T12 只产文档不部署。真实部署执行（无论哪个流触发）应共用一个部署窗口（round1 §4）；且 `deploy.sh` 两条分支都从本地 worktree 出发（server 编译、web rsync），**并行片的未提交代码会互相搭车上线**，各流部署前务必过 runbook 里的 `git status` preflight。
