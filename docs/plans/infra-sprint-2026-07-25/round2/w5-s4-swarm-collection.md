# 片 w5-s4 — Round-2 执行卡：swarm 回收机制（W5 T11–T13 细化）

> 目标：为 2×128 swarm 执行建回收管线 —— manifest schema + 收集器、机械验收执行器、
> artifact 上传 + EVIDENCE 回执。全部落在**全新目录** `quant-loop/_shared/swarm/`。
> 执行者：caocao-m3 swarm agent（零上下文，30min/卡）。本文件每张卡自足。
> 本文件是唯一产出；未改动任何代码或 git 状态。

---

## 0. 所有卡共享的事实（已亲验，执行 agent 可直接信任）

- 仓库根：`/Users/mark/multica`（下称 `$ROOT`）。
- Python 一律用 `$PY=/Users/mark/sdk/mamba-envs/trading/bin/python3`
  （已验证：pytest 8.3.4、jsonschema 4.23.0 均可用）。系统 python3 缺包，**不要用**。
- `quant-loop/_shared/` 已存在，内有 `__init__.py` 与平铺的 `test_run_backtest.py`。
  现有测试约定（`quant-loop/_shared/test_run_backtest.py:42-44`）：文件头部
  ```python
  import sys
  from pathlib import Path
  sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
  ```
  使 `from _shared.xxx import ...` 可用；pytest 从 `$ROOT` 直接对测试文件路径调用即可。
  新测试文件在 `quant-loop/_shared/swarm/` 下时，`parents[1]` = `quant-loop/_shared`，
  **必须用 `parents[2]`**（= `quant-loop/`），见各卡代码骨架。
- `quant-loop/_shared/swarm/` **当前不存在**，由 T11a 创建（含 `__init__.py`）。
- 真实 swarm 产出样例目录：`quant-loop/research/swarm/2026-07-25/gate-ledger-fix/`，
  共 21 个条目（20 个文件 + `__pycache__/`），**无 manifest.json** —— 这是"未回收"现状的代表，
  各卡的负例验收直接对它跑。
- multica CLI 在 `/Users/mark/.local/bin/multica`（PATH 内，直接 `multica` 可调），
  指向 `.105:8080`。两个关键子命令（已验证 `--help`）：
  - `multica artifact add <task-id> <file> --kind other --meta '{"k":"v"}'`
    （kind ∈ metrics/equity/plot/log/dataset/other，见 `server/cmd/multica/cmd_artifact.go:55`）
  - `multica issue comment add <issue-id> --content-file <path>`
    （`server/cmd/multica/cmd_issue.go:157-161,386-392`）
- 评论 schema（AGENTS.md 强制）：首行必须
  `[type=<TYPE>] <iso8601 timestamp+tz> <one-line summary>`，
  TYPE ∈ STATUS/DECISION/EVIDENCE/KILL/ESCALATE/SIGNOFF/NUDGE/NOOP。
  历史实例的时区写法是 `2026-07-19T22:45+08`（小时即可，可带 `:00`）。
- **纪律**：禁止 git 任何 mutation；禁止改动 `quant-loop/_shared/swarm/` 以外的任何文件；
  不跑回测；worktree 里有别人的未提交改动，不碰。

### manifest.json 格式（T11a 定义，后续卡直接用，此处 inline 以免跨卡依赖）

```json
{
  "run_id": "2026-07-25-gate-ledger-fix",
  "created_at": "2026-07-25T14:00:00+08:00",
  "parent_issue": "SMA-XXXXX",
  "items": [
    {
      "slug": "gate-proposal",
      "owner": "swarm-agent-07",
      "files": ["gate_proposal.go", "gate_test_proposal.go"],
      "acceptance": {"cmd": "$PY -m pytest tests/test_gate.py -q", "timeout_sec": 600},
      "status": "pending"
    }
  ]
}
```

字段规则：`run_id`/`created_at`/`items` 必填；`parent_issue` 可选；
item 必填 `slug`（`^[a-z0-9][a-z0-9-]*$`）+ `files`（≥1，相对 run 目录的路径）+
`acceptance.cmd`；`acceptance.timeout_sec` 默认 1800、上限 1800；
`owner` 可选；`status` ∈ pending/collected/accepted/failed/skipped，默认 pending。
`files` 路径禁止 `..` 与绝对路径。

---

## T11a — swarm manifest schema + 示例（S）

- **目标**：定义 `manifest.json` 的 JSON Schema，落一个可通过校验的示例文件，并创建
  `quant-loop/_shared/swarm/` 包目录。
- **机器**：either（纯本地文件）。**估时**：15 min。
- **依赖**：无。
- **读**（了解现状即可，不改动）：
  - `quant-loop/research/swarm/2026-07-25/gate-ledger-fix/`（ls 看一眼真实 swarm 产出长什么样）
  - 本卡第 0 节的 manifest 格式定义
- **写**（仅这 3 个新文件）：
  1. `quant-loop/_shared/swarm/__init__.py` —— 空文件（0 字节或仅一行 docstring）。
  2. `quant-loop/_shared/swarm/manifest.schema.json` —— JSON Schema draft-07，
     实现第 0 节的全部字段规则。骨架（必须补全 `acceptance`/`status` 等细节）：
     ```json
     {
       "$schema": "http://json-schema.org/draft-07/schema#",
       "title": "swarm-run-manifest",
       "type": "object",
       "required": ["run_id", "created_at", "items"],
       "additionalProperties": false,
       "properties": {
         "run_id": {"type": "string", "minLength": 1},
         "created_at": {"type": "string", "minLength": 1},
         "parent_issue": {"type": "string"},
         "items": {
           "type": "array", "minItems": 1,
           "items": {
             "type": "object",
             "required": ["slug", "files", "acceptance"],
             "additionalProperties": false,
             "properties": {
               "slug": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$"},
               "owner": {"type": "string"},
               "files": {"type": "array", "minItems": 1,
                         "items": {"type": "string", "minLength": 1}},
               "acceptance": {
                 "type": "object", "required": ["cmd"],
                 "additionalProperties": false,
                 "properties": {
                   "cmd": {"type": "string", "minLength": 1},
                   "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 1800}
                 }
               },
               "status": {"enum": ["pending", "collected", "accepted", "failed", "skipped"]}
             }
           }
         }
       }
     }
     ```
     注意：schema 无法表达"禁止 `..`"，该约束由 T11b 收集器在代码里查（见 T11b）。
  3. `quant-loop/_shared/swarm/manifest.example.json` —— 一个 2-item 示例，
     内容照抄第 0 节的例子再补一个 item，必须能通过 schema 校验。
- **步骤**：
  1. `mkdir -p quant-loop/_shared/swarm`（Write 工具会自动建目录，可省）。
  2. 写上述 3 个文件。
  3. 跑下方验收命令。
- **验收**（在 `$ROOT` 下执行，两条都必须过）：
  ```bash
  PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
  $PY -c "import json,jsonschema; jsonschema.validate(json.load(open('quant-loop/_shared/swarm/manifest.example.json')), json.load(open('quant-loop/_shared/swarm/manifest.schema.json'))); print('SCHEMA OK')"
  ```
  预期输出 `SCHEMA OK`，退出码 0。
  ```bash
  $PY -c "import json; json.load(open('quant-loop/_shared/swarm/manifest.schema.json')); print('SCHEMA PARSES')"
  ```
  预期退出码 0。

---

## T11b — 收集器 collect_swarm_run.py + 测试（M）

- **目标**：给定一个 swarm run 目录，校验 manifest 与产出文件完整性，打印汇总表，
  写 `collection.json`（可合入清单），退出码机械可判。
- **机器**：either。**估时**：25 min。
- **依赖**：T11a（需要 `manifest.schema.json` 存在）。
- **读**：
  - `quant-loop/_shared/swarm/manifest.schema.json`（T11a 产物；校验时加载它）
  - 真实负例目录 `quant-loop/research/swarm/2026-07-25/gate-ledger-fix/`（无 manifest → 非零退出）
  - 测试头部 sys.path 约定见第 0 节（本目录下用 `parents[2]`）
- **写**（仅这 2 个新文件）：
  1. `quant-loop/_shared/swarm/collect_swarm_run.py`
  2. `quant-loop/_shared/swarm/test_collect.py`
- **collect_swarm_run.py 规格**：
  - CLI：`collect_swarm_run.py <run_dir> [--strict]`（argparse）。
  - 逻辑顺序：
    1. `<run_dir>/manifest.json` 不存在或 JSON 解析失败 → stderr 报错，**exit 2**。
    2. 用 `jsonschema.validate` 对 schema 校验（schema 路径用
       `Path(__file__).resolve().parent / "manifest.schema.json"` 定位，不从 cwd 拼）→
       失败 → stderr 打出具体校验错误，**exit 2**。
    3. 对每个 item：检查 `files` 里每条相对路径。拒绝含 `..` 或以 `/` 开头的路径
       （计入 missing 并在 stderr 警告）。文件不存在 → item 状态 `missing-files`，
       记录缺失清单；全部存在 → `collected`。
    4. 扫描 run 目录顶层（不递归 `__pycache__`），找出**未被任何 item 声明**的文件
       （排除 `manifest.json`、`collection.json`、`acceptance.json` 本身）→ warnings 列表。
       `--strict` 且 warnings 非空 → **exit 1**；非 strict 仅打印警告。
    5. 任何 item 为 `missing-files` → **exit 1**；否则 **exit 0**。
    6. 无论退出码（除 exit 2 外），写 `<run_dir>/collection.json`：
       ```json
       {"run_id": "...", "collected_at": "<iso8601>",
        "items": [{"slug": "...", "status": "collected|missing-files",
                   "missing": ["..."]}],
        "mergeable": ["<全部文件齐的 item slug>"],
        "warnings": ["undeclared file: xxx"]}
       ```
    7. stdout 打印每个 item 一行：`COLLECTED <slug> (<n> files)` 或
       `MISSING <slug>: <file1>,<file2>`。
  - 顶部写成 `def collect(run_dir: Path, strict: bool) -> int` + `if __name__ == "__main__":`
    薄壳，方便测试直接 import 函数。
- **test_collect.py 规格**（pytest，全部用 `tmp_path` 造 fixture，不碰真实数据）：
  头部：
  ```python
  import sys
  from pathlib import Path
  sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # -> quant-loop/
  from _shared.swarm.collect_swarm_run import collect  # noqa: E402
  ```
  用例（helper 造 manifest + 文件）：
  1. `test_all_present`：1 个 manifest + 2 item，声明的文件全部 touch 出来 →
     `collect()` 返回 0，`collection.json` 的 `mergeable` 含全部 slug。
  2. `test_missing_file`：item 声明了不存在的 `nope.csv` → 返回 1，
     `collection.json` 里该 item `missing == ["nope.csv"]`。
  3. `test_missing_manifest`：目录里无 manifest.json → 返回 2。
  4. `test_invalid_manifest`：manifest 缺 `items` 字段 → 返回 2。
  5. `test_path_traversal_rejected`：files 含 `"../escape.txt"` → 返回 1 且计入 missing。
  6. `test_strict_undeclared_file`：目录里多一个未声明的 `extra.txt`，
     `strict=True` → 返回 1；`strict=False` → 返回 0（同一 fixture 跑两次断言）。
- **验收**（在 `$ROOT` 下）：
  ```bash
  PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
  $PY -m pytest quant-loop/_shared/swarm/test_collect.py -q
  ```
  预期 6 passed。
  ```bash
  $PY quant-loop/_shared/swarm/collect_swarm_run.py quant-loop/research/swarm/2026-07-25/gate-ledger-fix --strict; echo "exit=$?"
  ```
  预期 `exit=2`（无 manifest，证明检查生效；且**不得**在该目录写出任何文件——
  exit 2 路径不写 collection.json）。

---

## T12 — 机械验收执行器 accept.py + 测试（M）

- **目标**：读 run 目录的 manifest.json，逐 item 跑其声明的 `acceptance.cmd`
  （隔离 cwd、per-item 超时、环境白名单），结果写 `acceptance.json`，任一失败整体 exit 1。
- **机器**：either。**估时**：25 min。
- **依赖**：T11a（schema 文件；manifest 格式已 inline 在第 0 节，不看 T11a 卡也能做）。
- **读**：
  - 第 0 节 manifest 格式定义
  - `quant-loop/_shared/swarm/manifest.schema.json`（T11a 产物，运行前校验用）
- **写**（仅这 2 个新文件）：
  1. `quant-loop/_shared/swarm/accept.py`
  2. `quant-loop/_shared/swarm/test_accept.py`
- **accept.py 规格**：
  - CLI：`accept.py <run_dir> [--timeout-sec N]`（--timeout-sec 覆盖所有 item 的超时，仍受 1800 上限钳制）。
  - 逻辑：
    1. 加载 `<run_dir>/manifest.json`（缺失/解析失败/schema 校验失败 → exit 2，同 T11b 口径）。
    2. 构造子进程环境白名单：
       ```python
       import os
       ALLOWED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SHELL")
       child_env = {k: v for k, v in os.environ.items() if k in ALLOWED_ENV}
       ```
       （manifest 是可信输入——它由调度方生成——所以 cmd 用 `shell=True` 执行是可接受的，
       在模块 docstring 里注明这一假设。）
    3. 逐 item 顺序执行：
       ```python
       import subprocess, time
       t0 = time.monotonic()
       try:
           proc = subprocess.run(
               item["acceptance"]["cmd"], shell=True,
               cwd=str(run_dir), env=child_env,
               capture_output=True, text=True,
               timeout=min(item["acceptance"].get("timeout_sec", 1800), 1800),
           )
           status = "passed" if proc.returncode == 0 else "failed"
           exit_code = proc.returncode
           tail = (proc.stdout + proc.stderr)[-4000:]
       except subprocess.TimeoutExpired as e:
           status, exit_code = "timeout", -1
           tail = ((e.stdout or "") + (e.stderr or ""))[-4000:] if isinstance(e.stdout, str) else ""
       ```
       注意 `TimeoutExpired.stdout/stderr` 可能是 bytes，做 defensive 处理（decode errors="replace"）。
    4. 汇总写 `<run_dir>/acceptance.json`：
       ```json
       {"run_id": "...", "ran_at": "<iso8601>",
        "results": [{"slug": "...", "cmd": "...", "status": "passed|failed|timeout",
                     "exit_code": 0, "duration_sec": 1.23, "output_tail": "..."}],
        "overall": "passed|failed"}
       ```
    5. 任一 item 非 passed → exit 1；全部 passed → exit 0。
       stdout 每 item 一行：`PASS <slug> (1.2s)` / `FAIL <slug> exit=3` / `TIMEOUT <slug> (>600s)`。
  - 同样暴露 `def run_acceptance(run_dir: Path, timeout_override: int | None = None) -> int` 供测试 import。
- **test_accept.py 规格**（sys.path 同 T11b 用 `parents[2]`；fixture 全用 `tmp_path`）：
  helper `write_manifest(dir, items)` 直接 dict → json.dump。
  1. `test_two_pass_one_fail`：3 个 item，cmd 分别为 `exit 0`、`true`、`exit 3`
     → 返回 1；`acceptance.json` 里 failed 的正是第三个 slug，前两个 passed。
  2. `test_all_pass`：2 个 `exit 0` item → 返回 0，`overall == "passed"`。
  3. `test_timeout`：item cmd `sleep 5`、`timeout_sec: 1` → 返回 1，
     该 result `status == "timeout"`（此用例耗时约 1s，可接受）。
  4. `test_missing_manifest`：无 manifest → 返回 2。
  5. `test_cwd_isolation`：item cmd 为 `pwd > where.txt`，断言 `where.txt`
     落在 run 目录内（证明 cwd=run_dir）。
- **验收**（在 `$ROOT` 下）：
  ```bash
  PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
  $PY -m pytest quant-loop/_shared/swarm/test_accept.py -q
  ```
  预期 5 passed（总耗时 <30s）。
  ```bash
  $PY quant-loop/_shared/swarm/accept.py quant-loop/research/swarm/2026-07-25/gate-ledger-fix; echo "exit=$?"
  ```
  预期 `exit=2`（无 manifest），且该目录不被写入任何文件。

---

## T13 — artifact 上传 + EVIDENCE 回执（S）

- **目标**：把 swarm run 目录的产物经 `multica artifact add` 推到 .105，并在父 issue 发
  `[type=EVIDENCE]` 回执评论。默认 dry-run（只打印计划），真正执行需显式 `--apply`。
- **机器**：**mac**（--apply 需要本机 multica CLI 的 .105 凭据与网络；测试本身离线可跑）。
  **估时**：25 min。
- **依赖**：T11a（manifest 格式，已 inline 第 0 节）。
  软依赖 T4（另一片的 `scripts/comment_schema_lint.py`）——**不存在时用卡内 fallback，不阻塞**。
- **读**：
  - 第 0 节的 multica CLI 用法与评论 schema
  - `server/cmd/multica/cmd_artifact.go:30-37`（`artifact add <task-id> <file>` 参数形式，仅参考）
  - 真实目录 `quant-loop/research/swarm/2026-07-25/gate-ledger-fix/`（dry-run 验收对象，21 条目）
- **写**（仅这 2 个新文件）：
  1. `quant-loop/_shared/swarm/upload_artifacts.py`
  2. `quant-loop/_shared/swarm/test_upload.py`
- **upload_artifacts.py 规格**：
  - CLI：
    ```
    upload_artifacts.py <run_dir> --task-id <id> --issue-id <id> [--apply] [--kind other]
    ```
    无 `--apply` 一律 dry-run。
  - 文件枚举：`sorted(p for p in run_dir.iterdir() if p.is_file())`，
    排除 `__pycache__`（iterdir 不递归天然排除）、`*.pyc`。
    （只收顶层文件；子目录产物本 sprint 不支持，在 docstring 注明。）
  - dry-run 输出（每文件一行，**前缀必须严格是 `PLAN upload `**，验收靠它计数）：
    ```
    PLAN upload gate_proposal.go -> multica artifact add <task-id> gate_proposal.go --kind other
    ...
    PLAN comment <issue-id> (EVIDENCE receipt, N files)
    DRY-RUN: no changes made. Re-run with --apply to execute.
    ```
  - --apply 路径：逐文件
    `subprocess.run(["multica", "artifact", "add", task_id, str(f), "--kind", kind,
                     "--meta", json.dumps({"run_id": run_id, "file": f.name})],
                    capture_output=True, text=True, timeout=120)`；
    任一非零 → 收集到 failures，继续剩余文件，最后统一报告并 exit 1。
  - 回执评论：上传全部完成后构造评论文本，首行
    `[type=EVIDENCE] <timestamp> swarm run <run_id>: <n>/<total> artifacts uploaded to task <task-id>`
    （timestamp 用 `datetime.now().astimezone().isoformat(timespec="minutes")`，
    形如 `2026-07-25T15:04+08:00`，符合 schema）。
    正文列：run_dir、文件清单、failures（若有）、`acceptance.json` 的 overall（若该文件存在则读）。
    写入临时文件后 `multica issue comment add <issue-id> --content-file <tmp>`。
  - **评论自检（发前必过）**：抽函数 `valid_comment_first_line(line: str) -> bool`：
    ```python
    import re
    _RE = re.compile(
        r"^\[type=(STATUS|DECISION|EVIDENCE|KILL|ESCALATE|SIGNOFF|NUDGE|NOOP)\]"
        r" \d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}(:?\d{2})?|Z) .+"
    )
    def valid_comment_first_line(line: str) -> bool:
        return bool(_RE.match(line))
    ```
    若 `scripts/comment_schema_lint.py` 已存在（T4 产物），优先 import 它的校验函数
    （`sys.path.insert(0, str(Path(__file__).resolve().parents[3]))` 后
    `from scripts.comment_schema_lint import ...`，import 失败静默回退到上面的内置正则）。
    自检不过 → 不发评论，exit 1。
  - manifest 处理：`<run_dir>/manifest.json` 存在则读 `run_id`/`parent_issue`；
    不存在也能跑（run_id 用目录名代替），stderr 打警告。**不强制要求 manifest**。
- **test_upload.py 规格**（sys.path 用 `parents[2]`；全离线，**严禁**测试触碰真实 multica CLI）：
  1. `test_dry_run_enumerates_real_dir`：对真实
     `quant-loop/research/swarm/2026-07-25/gate-ledger-fix/` 跑 main（argv 注入或 subprocess
     调脚本均可），capture stdout，`stdout.count("PLAN upload ") >= 10`，
     退出码 0，且含 `DRY-RUN` 行。（该目录有 20 个文件，阈值 10 留裕量。）
  2. `test_comment_first_line_schema`：对 `valid_comment_first_line` 跑 4 正 3 反
     （正：含 `+08`、`+08:00`、`Z` 三种时区写法；反：缺 type 标签、非法 type、无时区）。
  3. `test_apply_constructs_commands`：monkeypatch `subprocess.run` 为记录调用的 fake
     （返回 returncode=0），tmp_path 造 3 文件，跑 --apply → 断言恰好 3 次 artifact add 调用
     + 1 次 comment add 调用，且第一次调用的 argv 含 `"artifact", "add"` 与 task-id。
  4. `test_apply_failure_continues`：fake 让第 2 个文件 returncode=1 → 仍尝试第 3 个，
     最终 exit 1。
- **验收**（在 `$ROOT` 下）：
  ```bash
  PY=/Users/mark/sdk/mamba-envs/trading/bin/python3
  $PY -m pytest quant-loop/_shared/swarm/test_upload.py -q
  ```
  预期 4 passed。
  ```bash
  $PY quant-loop/_shared/swarm/upload_artifacts.py quant-loop/research/swarm/2026-07-25/gate-ledger-fix --task-id dryrun --issue-id dryrun | grep -c '^PLAN upload '
  ```
  预期输出 ≥ 10（且命令本身退出码 0；**不得**真的发起任何网络请求）。

---

## 执行顺序与冲突备注

- 顺序：T11a 先行（15min），T11b / T12 / T13 在其后可并行（三者文件两两不相交：
  collect/accept/upload 各管各的 .py + test_*.py）。
- **跨片依赖**：T13 软依赖 T4 `scripts/comment_schema_lint.py`（G1 组，另一片 w5-s? 的卡）。
  已设计为存在即用、不存在用内置正则，**不构成硬阻塞**。两片都落地后行为一致（同一 schema）。
- **跨 workstream 冲突**：
  1. `quant-loop/_shared/` 目录同时被 w1（adapter 收敛）与 w5-s?（paper/ 子目录，T8–T10）
     触碰；本片只新增 `swarm/` 子目录，与 `paper/` 平级、与现有平铺文件无交集。
     adapter 收敛若重组 `_shared` 结构需知晓 `swarm/` 与 `paper/` 两个新子目录。
  2. 本片不改 `server/`、不改 `scripts/deploy.sh`、不写 `ops/`——与 w2/w5 其它片零文件交集。
  3. 唯一外部写副作用是 T13 --apply 的 artifact/comment API 调用（.105），
     默认 dry-run 兜底，swarm 执行阶段不会误触。
