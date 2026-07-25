# w2-s4 — compare-page.tsx 三态徽章 + gate_detail 列表 + KILL 灰显/verdict（T8-T9 细化）

> Round-2 execution cards · 2026-07-25 · slice of round1 `w2-server-compare` T8/T9
> 全部 3 张卡串行改**同一个文件** `packages/views/compare/components/compare-page.tsx`（493 行），
> 必须按 T8a → T8b → T9 顺序派发，不得并行。
> 所有行号基于 2026-07-25 工作区实测（agent 执行前应先 Read 文件核对锚点，行号漂移时按函数名/代码片段定位）。

## 上下文（零背景 agent 必读，已内联，无需看 round1）

- 后端 gate 语义正在改为三态：`gate_status ∈ {"pass","fail","no-data"}`（`no-data` = 连 sharpe 都没有，无法评判）。每行还带 `gate_detail` 数组，逐规则结果，JSON 形如：
  `{"rule":"oos_sharpe","op":">=","threshold":1.0,"actual":0.6,"pass":false,"note":"missing required metric"}`（`note` 可缺省，`actual` 可为 null）。规则固定 6 条：sharpe>=1.0、ann_return>=0.15、max_drawdown(magnitude)<0.25、profit_factor>1.5、oos_windows>=3、oos_sharpe>=1.0。
- KILL/verdict 数据通路：发布侧往 `RunMetric.extra` 写 `verdict` / `kill_reason` 键（string，optional）；既有键 `extra.divergence_flag` 可能为 `KILLED`/`REJECTED`/`DIVERGENT`/`OK`。
- 前置任务（别的 slice 负责，本 slice 只做消费方）：
  - **T6**（types）：`packages/core/types/metric.ts` — `gate_status` 联合类型加 `"no-data"`，`GateDetailEntry` 加 `note?: string`。
  - **T7**（utils）：新建 `packages/views/compare/utils/verdict.ts`，导出
    `readVerdict(m: RunMetric): { verdict: string | null; killReason: string | null; killed: boolean }`，
    killed = `extra.kill_reason` 非空 或 `extra.divergence_flag ∈ {KILLED, REJECTED}`。若 T7 尚未落地，T9 按此签名硬编码 import，typecheck 会在 T7 合入后转绿。
- 硬约束：不动同目录 `compare-page.tsx.bak.gate-pass-only` / `compare-page.tsx.bak.gate-filter-20260720`；不改 `packages/core`、不改 `apps/web`；无 git mutation；保持现有 inline-style 风格（本文件不用 Tailwind 类名做节点样式）。

## 关键现状锚点（已亲自读码核实）

| 位置 | 现状 |
|---|---|
| `compare-page.tsx:74-77` | `GATE_STYLE` 只有 `pass`/`fail` 两态 |
| `compare-page.tsx:59-72` | `StratNodeData` 类型（label/campaign/sharpe/gate/isSelected/frameworkValidated/divergenceFlag） |
| `compare-page.tsx:114-121` | 节点 gate 徽章，`color` 三元只认 pass/fail |
| `compare-page.tsx:141` | 节点右下 `GateIcon`，颜色三元同上 |
| `compare-page.tsx:261-268` | detail 面板 `GATE:` pill，else 分支灰 "NO DATA" |
| `compare-page.tsx:334-342` | `visibleMetrics` 过滤：pass 放行，否则 ann>0.02 && mdd>=-0.30 兜底 |
| `compare-page.tsx:179-288` | `DetailPanel` 组件（props: metric/equity/gate） |
| `compare-page.tsx:383-388` | campaign 节点 data 字面量（加 StratNodeData 字段时这里也要补） |
| `compare-page.tsx:391-403` | strategy 节点 data 构造处 |
| `compare-page.tsx:488` | `<DetailPanel metric={selected} equity={...} gate={selectedGate} />` 调用处 |
| `compare-page.tsx:324-328` | `gatesByMetric` memo（T9 仿照它建 verdictsByMetric） |

---

## T8a — gate 徽章三态 + 可见性过滤语义修正

- **目标**：`gate_status === "no-data"` 有独立灰色徽章/图标；新增统一 `gateColor()` helper；过滤逻辑改为 pass/fail 均展示（fail 置灰），no-data/null 行走 ann/mdd 兜底。
- **Reads**：`packages/views/compare/components/compare-page.tsx`（全文件）
- **Writes**：同上（仅此一个文件）
- **依赖**：T6（`gate_status` 类型含 `"no-data"`；未合入前 typecheck 会报联合类型错误，可先写代码，验收以 T6 落地后为准）
- **机器**：mac · **估时**：20 min

### 步骤

1. Read 全文件，核对锚点。
2. `:74-77` `GATE_STYLE` 加第三态（`HelpCircle` 已在 `:16` import，无需新 import）：
   ```ts
   const GATE_STYLE: Record<string, { bg: string; icon: typeof CheckCircle2 }> = {
     pass: { bg: "#16a34a20", icon: CheckCircle2 },
     fail: { bg: "#dc262620", icon: XCircle },
     "no-data": { bg: "#6b728020", icon: HelpCircle },
   };
   ```
3. `GATE_STYLE` 下方新增 helper（消灭三处 pass/fail 三元）：
   ```ts
   function gateColor(gate: string | null): string {
     if (gate === "pass") return "#16a34a";
     if (gate === "fail") return "#dc2626";
     return "#9ca3af"; // no-data / null
   }
   ```
4. `:117` 徽章 `color:` 改为 `color: gateColor(d.gate)`；`:141` `GateIcon` 的 `style={{ color: ... }}` 改为 `style={{ color: d.gate ? gateColor(d.gate) : "#666" }}`。（`:82` 的 `gs?.icon ?? HelpCircle` 已自动覆盖 no-data，无需改。）
5. `StratNodeData`（`:59-72`）加字段 `dimmed: boolean;` 并配一行注释（fail 行默认可见但降透明度）。
6. `StrategyNode` 根 div style（`:103-107`）加 `opacity: d.dimmed ? 0.45 : 1,`。
7. 过滤 `:334-342` 整体替换为：
   ```ts
   // strict gate 语义（2026-07-25）：pass/fail 均默认展示——fail 置灰而不是
   // 隐身，避免 strict gate 落地后页面被 ann/mdd 兜底逻辑掏空。no-data /
   // 未评估行仍需过 ann>2% & mdd>=-30% 质量兜底。
   const visibleMetrics = useMemo(() => {
     if (showAll) return allMetrics;
     return allMetrics.filter((m) => {
       if (m.gate_status === "pass" || m.gate_status === "fail") return true;
       const ann = typeof m.ann_return === "number" ? m.ann_return : null;
       const mdd = typeof m.max_drawdown === "number" ? m.max_drawdown : null;
       return ann !== null && mdd !== null && ann > 0.02 && mdd >= -0.30;
     });
   }, [allMetrics, showAll, gatesByMetric]);
   ```
8. strategy 节点 data 构造（`:395-401`）加 `dimmed: m.gate_status === "fail",`；campaign 节点 data（`:383-388`）加 `dimmed: false,`。
9. detail 面板 GATE pill（`:261-268`）三元改 helper：
   ```tsx
   background: m.gate_status === "pass" ? "#16a34a20" : m.gate_status === "fail" ? "#dc262620" : "#6b728020",
   color: gateColor(m.gate_status ?? null),
   ```
   文案 `:267` 保持 `m.gate_status?.toUpperCase() ?? "NO DATA"`（`"no-data"` → `NO-DATA`，可接受，不改）。

### 验收

```bash
cd /Users/mark/multica
pnpm --filter @multica/views typecheck        # 0 errors
grep -n '"no-data"' packages/views/compare/components/compare-page.tsx   # ≥2 行命中（GATE_STYLE + pill background）
grep -n 'dimmed' packages/views/compare/components/compare-page.tsx      # ≥5 行命中
```
预期：typecheck 通过；grep 命中数达标。

---

## T8b — detail 面板渲染 gate_detail 逐规则列表（含 note）

- **目标**：`DetailPanel` 在 GATE pill 下方新增 "Gate Rules" 区块，逐条渲染 6 条规则的 `rule / actual op threshold`，失败红色、通过绿色、`note`（如 `missing required metric`）灰色小字并挂 `title` 悬停。
- **Reads**：`packages/views/compare/components/compare-page.tsx`；类型参考 `packages/core/types/metric.ts:11-17`（`GateDetailEntry`，T6 后含 `note?: string`）
- **Writes**：仅 `compare-page.tsx`
- **依赖**：T6（`GateDetailEntry.note`）、T8a（同文件串行，T8a 先合入）
- **机器**：mac · **估时**：20 min

### 步骤

1. Read 文件，定位 `DetailPanel` 中 GATE pill 区块（T8a 后约在 `:261-269`，特征字符串 `GATE: {m.gate_status`）。
2. 在该 pill 的闭合 `</div>` 之后、`{chartData.length > 0 ? (` 之前插入：
   ```tsx
   {m.gate_detail && m.gate_detail.length > 0 && (
     <div style={{ marginBottom: 16 }}>
       <div style={{ fontSize: 10, color: "#7c7c9e", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }}>
         Gate Rules
       </div>
       {m.gate_detail.map((r) => (
         <div
           key={r.rule}
           title={r.note ?? undefined}
           style={{ display: "flex", justifyContent: "space-between", gap: 8, borderBottom: "1px solid #222240", padding: "3px 0" }}
         >
           <span style={{ fontSize: 11, color: "#8888aa" }}>
             {r.rule}
             {r.note && (
               <span style={{ display: "block", fontSize: 9, color: "#666" }}>{r.note}</span>
             )}
           </span>
           <span style={{ fontSize: 11, fontFamily: "monospace", color: r.pass ? "#16a34a" : "#dc2626" }}>
             {r.actual != null ? r.actual.toFixed(3) : "—"} {r.op} {r.threshold ?? "—"}
           </span>
         </div>
       ))}
     </div>
   )}
   ```
   说明：样式完全沿用文件内既有 inline-style 词汇（`#222240` 分隔线、`#8888aa` 标签色、monospace 数值列）；`actual` 为 null（指标缺失）时显示 `—`，此行因 `pass=false` 自动红色。
3. 不改 `DetailPanel` props 签名——`gate_detail` 已在 `RunMetric` 上。

### 验收

```bash
cd /Users/mark/multica
pnpm --filter @multica/views typecheck
grep -n 'Gate Rules' packages/views/compare/components/compare-page.tsx        # 1 行
grep -n 'r.note ?? undefined' packages/views/compare/components/compare-page.tsx # 1 行
```
预期：typecheck 通过；grep 均命中。可选手测：`pnpm dev:web` 后开 compare 页点选任一节点，detail 面板出现 6 行规则列表。

---

## T9 — KILL 灰显 + 悬停原因 + detail 顶部 verdict 区块

- **目标**：killed 节点整体降透明度+去饱和、`title` 悬停显示 kill_reason（无则 divergence_flag）；`DetailPanel` 顶部渲染一句话 verdict 区块（无 verdict 不渲染）。
- **Reads**：`compare-page.tsx`；`packages/views/compare/utils/verdict.ts`（T7 产物，确认导出签名）
- **Writes**：仅 `compare-page.tsx`
- **依赖**：T7（`readVerdict`）、T8b（同文件串行链尾）
- **机器**：mac · **估时**：25 min

### 步骤

1. Read 文件与 `../utils/verdict.ts` 核对签名：`readVerdict(m: RunMetric): { verdict: string | null; killReason: string | null; killed: boolean }`。
2. 文件顶部 import 区（`:20-23` 附近）加：
   ```ts
   import { readVerdict } from "../utils/verdict";
   ```
3. `StratNodeData` 加字段：`killed: boolean; killReason: string | null;`（沿用 T8a 注释风格一行说明）。
4. `StrategyNode` 根 div style：T8a 的 `opacity` 行改为 `opacity: d.killed ? 0.35 : d.dimmed ? 0.45 : 1,`，并加 `filter: d.killed ? "grayscale(0.8)" : undefined,`；根 div 加 `title={d.killed ? (d.killReason ?? d.divergenceFlag ?? "killed") : undefined}`。
5. `ComparePage` 内仿照 `gatesByMetric`（`:324-328`）加：
   ```ts
   const verdictsByMetric = useMemo(() => {
     const map: Record<string, { verdict: string | null; killReason: string | null; killed: boolean }> = {};
     for (const m of allMetrics) map[m.id] = readVerdict(m);
     return map;
   }, [allMetrics]);
   ```
6. strategy 节点 data 构造（T8a 后约 `:395-402`）加：
   ```ts
   killed: verdictsByMetric[m.id]?.killed ?? false,
   killReason: verdictsByMetric[m.id]?.killReason ?? null,
   ```
   campaign 节点 data 加 `killed: false, killReason: null,`。`useMemo` 依赖数组把 `verdictsByMetric` 加进 nodes/edges 那个 memo（T8a 后约 `:408`）。
7. `DetailPanel`：props 加 `verdict: { verdict: string | null; killReason: string | null; killed: boolean } | null;`；在 `{m.campaign}` 行（`:223`）之后、metrics grid（`:225`）之前插入：
   ```tsx
   {verdict?.verdict && (
     <div style={{
       padding: "6px 10px", borderRadius: 6, marginBottom: 12, fontSize: 11, lineHeight: 1.5,
       background: verdict.killed ? "#dc262615" : "#6366f115",
       border: `1px solid ${verdict.killed ? "#dc262640" : "#6366f140"}`,
       color: "#c0c0e0",
     }}>
       <span style={{ fontWeight: 700, color: verdict.killed ? "#ff6b6b" : "#818cf8" }}>
         {verdict.killed ? "KILLED — " : "Verdict — "}
       </span>
       {verdict.verdict}
       {verdict.killed && verdict.killReason && (
         <span style={{ display: "block", fontSize: 10, color: "#8888aa", marginTop: 2 }}>
           {verdict.killReason}
         </span>
       )}
     </div>
   )}
   ```
8. 调用处（`:488`）改为：
   ```tsx
   <DetailPanel metric={selected} equity={eqResult ?? null} gate={selectedGate}
     verdict={selected ? verdictsByMetric[selected.id] ?? null : null} />
   ```

### 验收

```bash
cd /Users/mark/multica
pnpm --filter @multica/views typecheck
pnpm --filter @multica/views test             # vitest 全绿（含 T7 的 verdict.test.ts）
grep -n 'readVerdict' packages/views/compare/components/compare-page.tsx   # ≥3 行（import + memo + 节点构造）
grep -n 'grayscale' packages/views/compare/components/compare-page.tsx     # 1 行
```
预期：typecheck 0 errors、vitest 全绿、grep 命中。可选手测截图：killed 行灰显+悬停出原因，detail 顶部出现 verdict 区块。

---

## 派发顺序与冲突备忘

- 串行链：**T8a → T8b → T9**（同文件，绝不并行；每张卡基于上一张合入后的工作区）。
- 跨 slice 依赖：T6（types，w2 前端 slice）、T7（verdict utils，w2 前端 slice）必须先落地或至少同批合入，否则验收 typecheck 不过。
- 冲突预警：round1 §4 已声明 `compare-page.tsx` 整条链归本 workstream——任何其他 slice 对该文件的改动必须排在本链之后；`.bak.*` 两个备份文件禁止触碰。
