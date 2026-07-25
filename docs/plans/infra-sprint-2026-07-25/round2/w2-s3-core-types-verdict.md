# Round-2 任务卡 — 片 w2-s3：W2/T6-T7（core metric 类型契约 + compare verdict 纯函数 + vitest）

> 来源：round1 `w2-server-compare.md` 的 T6、T7。执行 agent 为 caocao-m3，零上下文。
> 背景一句话：server 端 gate 即将从「缺失字段跳过=虚过」改为 strict（缺失必填即 fail、sharpe 缺失 → 新状态 `no-data`），
> 前端类型和 compare 页数据判读要先支持 `"no-data"` 这个第三种 gate 状态，并从 metric 行的 `extra` 里读 verdict / kill_reason。
> 本片只含 **2 张卡**（T6 先行，T7 依赖 T6 的类型），均为纯前端、不动 server、不动 compare-page.tsx（那是别的片的任务）。

---

## 全局约束（两张卡都适用）

- 仓库根：`/Users/mark/multica`（下称 `$ROOT`）。所有命令在 `$ROOT` 下执行。
- **禁止**：git 任何 mutation（commit/push/reset/checkout）；改动本片未列出的任何文件；删除或改动 `packages/views/compare/components/compare-page.tsx.bak.*` 备份文件。
- worktree 里有他人未提交改动，`git status` 看到脏文件是正常的，不要理会、不要回滚。
- `packages/core/types/metric.ts` 现有结构（已核实，行号基于 2026-07-25 worktree）：
  - `metric.ts:11-17` `GateDetailEntry { rule; op; threshold: number|null; actual: number|null; pass: boolean }`
  - `metric.ts:38` `extra: Record<string, unknown> | null`
  - `metric.ts:40` `gate_status?: "pass" | "fail" | null`
  - `metric.ts:41` `gate_detail?: GateDetailEntry[] | null`
- server 端 JSON 事实（已核实 `server/internal/gate/gate.go:43-50` `RuleResult`）：每条 gate detail 后端会发 `note` 字段（`json:"note,omitempty"`，跳过/缺失时带说明文本），前端类型目前没声明。
- `extra` 透传事实：server ingest 把 metric blob 里所有未知 key 自动塞进 `extra`（`server/internal/handler/metric.go:140-148`），compare 页现有 `readExtra`（`packages/views/compare/components/compare-page.tsx:166-176`）就是这么读 `divergence_flag` / `framework_validated` 的。发布侧（另一个任务 T10，不属于本片）将往 blob 写 `verdict` / `kill_reason` / `kill_evidence` 三个 optional string 键。

---

## T6 — core 类型契约同步：`no-data` 状态 + note 字段 + extra 约定键注释

- **目标**：`packages/core/types/metric.ts` 类型与 server strict-gate 语义对齐，零运行时行为变化。
- **机器**：mac · **估时**：10 min · **依赖**：无（可与 server 侧 T1-T5 并行）

### 读
- `packages/core/types/metric.ts`（唯一改动文件，全文 59 行）

### 写
- 同上，仅此一个文件。

### 步骤

1. `GateDetailEntry`（metric.ts:11-17）加 `note` 字段，改为：

```ts
/** One gate rule evaluation attached to a metric row. */
export interface GateDetailEntry {
  rule: string;
  op: string;
  threshold: number | null;
  actual: number | null;
  pass: boolean;
  /** Backend sends this when the rule was skipped/failed for a notable
   *  reason (e.g. "missing required metric"); omitted otherwise. */
  note?: string;
}
```

2. `RunMetric.gate_status`（metric.ts:40）加 `"no-data"`，并把 server 语义写进注释：

```ts
  /** Strict gate outcome. "no-data" = not enough input metrics to evaluate
   *  (e.g. missing sharpe); distinct from null (never evaluated). */
  gate_status?: "pass" | "fail" | "no-data" | null;
```

3. 在 `extra` 字段（metric.ts:38）的注释里固化发布侧约定键（纯注释，不加类型字段，因为 extra 保持 `Record<string, unknown>`）：

```ts
  /** Free-form passthrough of unknown blob keys written by the publisher.
   *  Conventional keys (all optional strings, absent on older rows):
   *  - verdict: one-line human verdict, e.g. "CV_PASS", "KILL"
   *  - kill_reason: why the strategy was killed (non-empty ⇒ killed)
   *  - kill_evidence: pointer to evidence (issue id / file path)
   *  Framework-gate keys also live here: divergence_flag, framework_validated,
   *  framework_sharpe, framework_return_pct. */
  extra: Record<string, unknown> | null;
```

4. 不改任何其他文件，不 export 新符号。

### 验收

```bash
cd /Users/mark/multica && pnpm typecheck
```

预期：exit 0，无 error。（基线若已有与本文件无关的报错，则用 `pnpm --filter @multica/core exec tsc --noEmit` 复核 core 包单独干净，并在结果里说明。）

附加机械检查：

```bash
grep -n '"no-data"' packages/core/types/metric.ts && grep -n 'note?: string' packages/core/types/metric.ts
```

预期：两条 grep 各至少命中 1 行。

---

## T7 — compare verdict 纯函数 `readVerdict` + vitest 单测

- **目标**：新建纯函数模块，把「这一行 metric 是否被 KILL、一句话 verdict 是什么」从 `extra` 里安全读出；compare 页面任务（T8/T9，不属于本片）将 import 它。
- **机器**：mac · **估时**：20 min · **依赖**：T6（import 的 `RunMetric` 类型需已含新注释；函数本身只读 `extra`，编译层面不强依赖，但按顺序走避免返工）

### 读
- `packages/core/types/metric.ts`（T6 改完后，`RunMetric` 定义）
- `packages/views/compare/components/compare-page.tsx:155-176`（现有 `readExtra` 的防御式取值写法，照抄风格）
- `packages/views/compare/utils/equity-csv.ts`（同目录现有 utils 模块的注释/导出风格）
- `packages/views/issues/utils/filter.test.ts:1-16`（vitest 测试写法样板：`import { describe, it, expect } from "vitest"`，工厂函数造 fixture）

### 写
- 新建 `packages/views/compare/utils/verdict.ts`
- 新建 `packages/views/compare/utils/verdict.test.ts`
- 不改任何已有文件。

### 契约（严格按此实现，不自作主张扩语义）

`readVerdict(m: RunMetric): { verdict: string | null; killReason: string | null; killed: boolean }`

- `verdict` = `m.extra?.verdict`，仅当为 string 且 trim 后非空时取 trim 后的值，否则 `null`。
- `killReason` = `m.extra?.kill_reason`，同上规则（string 且 trim 非空 → trim 值，否则 `null`）。
- `killed` = `killReason !== null` **或** `extra.divergence_flag`（string 时）大写后 ∈ `{ "KILLED", "REJECTED" }`。
  - 注意：现存数据里 `divergence_flag` 还有 `"W5_FAIL_FEE_SHOCK"` 等值（已核实 `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/metrics.json:16`），这些**不算** killed——只认 KILLED / REJECTED 两个字面值。
- `m.extra` 为 `null` / 键缺失 / 类型不对（number、object 等）时一律安全落到 null/false，绝不抛异常。

### 步骤

1. 新建 `verdict.ts`（参照 equity-csv.ts 的头部注释风格）：

```ts
/**
 * Verdict / kill-status readers for the Compare page.
 *
 * The publisher writes optional `verdict` / `kill_reason` / `kill_evidence`
 * string keys into the metric blob; the server passes unknown keys through
 * into `RunMetric.extra`. Older rows lack them — every read coerces to a
 * safe default, never throws.
 */

import type { RunMetric } from "@multica/core/types";

export interface Verdict {
  /** One-line publisher verdict (e.g. "CV_PASS", "KILL"), or null. */
  verdict: string | null;
  /** Why the strategy was killed, or null. */
  killReason: string | null;
  /** True when kill_reason is non-empty OR divergence_flag is KILLED/REJECTED. */
  killed: boolean;
}

const KILL_FLAGS = new Set(["KILLED", "REJECTED"]);

function readString(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  return s.length > 0 ? s : null;
}

export function readVerdict(m: RunMetric): Verdict {
  const ex = m.extra ?? {};
  const killReason = readString(ex.kill_reason);
  const flag = readString(ex.divergence_flag);
  return {
    verdict: readString(ex.verdict),
    killReason,
    killed: killReason !== null || (flag !== null && KILL_FLAGS.has(flag.toUpperCase())),
  };
}
```

2. 确认 import 路径：`packages/views` 既有测试用的是 `import type { Issue } from "@multica/core/types"`（filter.test.ts:2），照用。`packages/core/types/index.ts` 若 re-export 了 metric 类型则无需任何改动；若 import 报错，检查 `packages/core/package.json` 的 exports 并用与该测试一致的 specifier——不要改 core 包来迁就。

3. 新建 `verdict.test.ts`，工厂函数造最小 RunMetric（参考 filter.test.ts:16-40 的 makeIssue 模式），覆盖用例：
   - extra 为 null → 三个字段全 default（`{ verdict: null, killReason: null, killed: false }`）
   - extra 缺键 → 同上
   - `verdict: "CV_PASS"` → verdict 为 "CV_PASS"，killed=false
   - `kill_reason: "framework CV sharpe -4.86"` → killReason 原样、killed=true
   - `kill_reason: "  "`（纯空白）→ killReason=null、killed=false
   - `divergence_flag: "KILLED"` → killed=true（killReason 仍 null）
   - `divergence_flag: "rejected"`（小写）→ killed=true（大小写不敏感）
   - `divergence_flag: "W5_FAIL_FEE_SHOCK"` → killed=false
   - `divergence_flag: "OK"` → killed=false
   - 类型错误：`verdict: 42`、`kill_reason: { x: 1 }`、`divergence_flag: ["KILLED"]` → 全 default、不抛异常
   - `verdict` 前后带空白 → 返回 trim 后的值

4. 测试里 RunMetric fixture 必填字段按 `packages/core/types/metric.ts:19-42` 的 interface 造（数值字段都给 null 即可），用 `overrides: Partial<RunMetric>` 合并。

### 验收

```bash
cd /Users/mark/multica && pnpm --filter @multica/views exec vitest run compare/utils/verdict.test.ts
```

预期：exit 0，全部用例 pass（≥11 个断言用例）。

再跑：

```bash
pnpm --filter @multica/views typecheck
```

预期：exit 0。

---

## 依赖与冲突备注

- **片内顺序**：T6 → T7（串行，同一人亦可一口气做完，合计 ~30 min）。
- **下游依赖本片的任务**（别的片，供编排参考）：compare-page.tsx 的徽章三态改造（T8）依赖 T6 的 `"no-data"` 类型；KILL 灰显+verdict 区块（T9）依赖 T7 的 `readVerdict`。本片不碰 compare-page.tsx。
- **与其他片冲突**：无文件级冲突——`packages/core/types/metric.ts`、`packages/views/compare/utils/verdict.ts(.test.ts)` 均为本片独占；round1 预警的 metric.go 双任务（T3/T5）与 compare-page.tsx 串行链（T8/T9）都不涉及本片文件。
- T7 与发布侧写入任务（T10，W2 另一片）是**约定一致性**关系：键名 `verdict` / `kill_reason` 必须逐字一致（snake_case），若 T10 改了键名，T7 的测试就是唯一防线。
