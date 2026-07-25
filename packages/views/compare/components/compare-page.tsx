"use client";

import { useCallback, useMemo, useState, useEffect } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
  BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { GitCompareArrows, CheckCircle2, XCircle, HelpCircle } from "lucide-react";
import {
  CartesianGrid, Line, LineChart, XAxis, YAxis, ResponsiveContainer, Tooltip,
} from "recharts";
import { useEquitySeries, type EquityCsvResult } from "@multica/core/hooks/use-metrics";
import { Skeleton } from "@multica/ui/components/ui/skeleton";
import { api } from "@multica/core/api";
import type { RunMetric } from "@multica/core/types";
import { readVerdict } from "../utils/verdict";

// ─── manual tree layout (campaign → strategy, 2-level) ─────────────────────
const NODE_W = 220;
const NODE_H = 64;
const COL_GAP = 30;
const ROW_GAP = 12;
const CAMP_ROW = 0;

function layout(nodes: Node[], edges: Edge[]): { nodes: Node[]; edges: Edge[] } {
  // Group campaign nodes and their children to compute columns
  const campNodes = nodes.filter((n) => n.id.startsWith("camp-"));
  const childOf: Record<string, Node[]> = {};
  for (const e of edges) {
    const child = nodes.find((n) => n.id === e.target);
    if (child) (childOf[e.source] ??= []).push(child);
  }
  // Assign x by column index, y by level
  let xCursor = 0;
  const positions: Record<string, { x: number; y: number }> = {};
  for (const camp of campNodes) {
    const children = childOf[camp.id] ?? [];
    const colW = NODE_W;
    positions[camp.id] = { x: xCursor, y: CAMP_ROW };
    children.forEach((ch, i) => {
      positions[ch.id] = { x: xCursor, y: NODE_H + ROW_GAP + i * (NODE_H + ROW_GAP) };
    });
    xCursor += colW + COL_GAP;
  }
  return {
    nodes: nodes.map((n) => ({ ...n, position: positions[n.id] ?? { x: 0, y: 0 } })),
    edges,
  };
}

// ─── custom node ───────────────────────────────────────────────────────────
type StratNodeData = {
  label: string;
  campaign: string;
  sharpe: number | null;
  gate: string | null;
  isSelected: boolean;
  /** Fail rows are dimmed (opacity 0.45) rather than hidden — keeps fail
   *  nodes visible without dominating the visual field. */
  dimmed: boolean;
  /** Killed strategies (extra.kill_reason or divergence_flag ∈ {KILLED,
   *  REJECTED}) get a stronger grey-out + grayscale so the eye skips them.
   *  Hover surfaces the kill reason (or divergence flag) for triage. */
  killed: boolean;
  killReason: string | null;
};

// gate_status ∈ {"pass","fail","no-data"} (see packages/core/types/metric.ts).
// "no-data" = not enough input metrics to evaluate (e.g. missing sharpe).
const GATE_STYLE: Record<string, { bg: string; icon: typeof CheckCircle2 }> = {
  pass: { bg: "#16a34a20", icon: CheckCircle2 },
  fail: { bg: "#dc262620", icon: XCircle },
  "no-data": { bg: "#6b728020", icon: HelpCircle },
};

function gateColor(gate: string | null): string {
  if (gate === "pass") return "#16a34a";
  if (gate === "fail") return "#dc2626";
  return "#9ca3af"; // no-data / null
}

function StrategyNode({ data, selected }: NodeProps) {
  const d = data as StratNodeData;
  const gs = d.gate ? GATE_STYLE[d.gate] : null;
  const GateIcon = gs?.icon ?? HelpCircle;
  const sharpeStr = d.sharpe != null ? d.sharpe.toFixed(2) : "—";
  const sharpeColor = d.sharpe == null ? "#888" : d.sharpe >= 1 ? "#16a34a" : d.sharpe >= 0 ? "#ca8a04" : "#dc2626";

  return (
    <div
      style={{
        width: NODE_W, height: NODE_H, borderRadius: 8, padding: "8px 10px",
        background: "#1a1a2e", border: selected ? "2px solid #6366f1" : "1px solid #333355",
        display: "flex", flexDirection: "column", justifyContent: "space-between", cursor: "pointer",
        boxShadow: selected ? "0 0 12px #6366f140" : "none",
        opacity: d.killed ? 0.35 : d.dimmed ? 0.45 : 1,
        filter: d.killed ? "grayscale(0.8)" : undefined,
      }}
      title={d.killed ? (d.killReason ?? "killed") : undefined}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <span style={{ fontSize: 10, color: "#7c7c9e", textTransform: "uppercase", letterSpacing: 0.5 }}>
          {d.campaign}
        </span>
        {gs && (
          <span style={{
            fontSize: 9, padding: "1px 6px", borderRadius: 4, background: gs.bg,
            color: gateColor(d.gate), fontWeight: 600,
          }}>
            {d.gate?.toUpperCase()}
          </span>
        )}
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, color: "#e0e0f0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {d.label}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 11, color: sharpeColor, fontFamily: "monospace", fontWeight: 700 }}>
          Sharpe {sharpeStr}
        </span>
        <GateIcon size={14} style={{ color: d.gate ? gateColor(d.gate) : "#666" }} />
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { strategy: StrategyNode };

// ─── helpers ───────────────────────────────────────────────────────────────
function shortName(s: string): string {
  return s.replace(/^vpvr_reversion_/, "vr_").replace(/^vpvr_/, "v_").replace(/_\d{8}$/, "").slice(0, 28);
}

// ─── detail panel ──────────────────────────────────────────────────────────
function DetailPanel({
  metric,
  equity,
  verdict,
}: {
  metric: RunMetric | null;
  equity: EquityCsvResult | null;
  verdict: { verdict: string | null; killReason: string | null; killed: boolean } | null;
}) {
  if (!metric) {
    return (
      <div style={{ padding: 24, color: "#666", textAlign: "center", fontSize: 13 }}>
        <GitCompareArrows size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
        <p>Select a strategy node to see details.</p>
      </div>
    );
  }
  const m = metric;
  const rows: [string, string][] = [
    ["Sharpe", m.sharpe != null ? m.sharpe.toFixed(3) : "—"],
    ["Ann Return", m.ann_return != null ? `${(m.ann_return * 100).toFixed(2)}%` : "—"],
    ["Max DD", m.max_drawdown != null ? `${(m.max_drawdown * 100).toFixed(2)}%` : "—"],
    ["Profit Factor", m.profit_factor != null ? m.profit_factor.toFixed(2) : "—"],
    ["OOS Sharpe", m.oos_sharpe != null ? m.oos_sharpe.toFixed(3) : "—"],
    ["OOS Windows", m.oos_windows != null ? String(m.oos_windows) : "—"],
    ["Timeframe", m.timeframe ?? "—"],
    ["Symbols", m.symbols?.join(", ") ?? "—"],
  ];
  const chartData = equity?.csv ? parseEquity(equity.csv) : [];

  return (
    <div style={{ padding: 16, overflowY: "auto", height: "100%" }}>
      <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 4, color: "#e0e0f0" }}>
        {shortName(m.iteration ?? m.id.slice(0, 8))}
      </h3>
      <div style={{ fontSize: 10, color: "#7c7c9e", marginBottom: 12 }}>{m.campaign}</div>

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

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 12px", marginBottom: 16 }}>
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid #222240", padding: "3px 0" }}>
            <span style={{ fontSize: 11, color: "#8888aa" }}>{k}</span>
            <span style={{ fontSize: 11, fontFamily: "monospace", color: v === "—" ? "#555" : "#c0c0e0" }}>{v}</span>
          </div>
        ))}
      </div>

      <div style={{
        padding: "4px 10px", borderRadius: 6, marginBottom: 16, display: "inline-block",
        background: m.gate_status === "pass" ? "#16a34a20" : m.gate_status === "fail" ? "#dc262620" : "#6b728020",
        color: gateColor(m.gate_status ?? null),
        fontSize: 12, fontWeight: 700,
      }}>
        GATE: {m.gate_status?.toUpperCase() ?? "NO DATA"}
      </div>

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

      {chartData.length > 0 ? (
        <div>
          <div style={{ fontSize: 11, color: "#8888aa", marginBottom: 4 }}>Equity Curve</div>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={chartData}>
              <CartesianGrid stroke="#222240" strokeDasharray="3 3" />
              <XAxis dataKey="i" tick={{ fontSize: 9, fill: "#666" }} />
              <YAxis tick={{ fontSize: 9, fill: "#666" }} domain={["auto", "auto"]} />
              <Tooltip contentStyle={{ background: "#1a1a2e", border: "1px solid #444", fontSize: 11 }} />
              <Line type="monotone" dataKey="v" stroke="#6366f1" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : equity ? (
        <div style={{ fontSize: 11, color: "#888" }}>Equity curve unavailable.</div>
      ) : null}
    </div>
  );
}

function parseEquity(csv: string): { i: number; v: number }[] {
  const lines = csv.trim().split("\n");
  if (lines.length < 2) return [];
  const header = (lines[0] ?? "").toLowerCase().split(",");
  const valCol = header.findIndex((h) => /equity|balance|value/.test(h));
  if (valCol < 0) return [];
  const out: { i: number; v: number }[] = [];
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (!line) continue;
    const parts = line.split(",");
    const raw = parts[valCol];
    const v = raw != null ? parseFloat(raw) : NaN;
    if (Number.isFinite(v)) out.push({ i: out.length, v });
  }
  return out.length > 500
    ? out.filter((_, idx) => idx % Math.ceil(out.length / 500) === 0)
    : out;
}

// ─── main page ─────────────────────────────────────────────────────────────
export function ComparePage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [allMetrics, setAllMetrics] = useState<RunMetric[]>([]);

  // Fetch all metrics across all campaigns — API returns all when no campaign filter
  useEffect(() => {
    api.queryMetrics({ campaign: "", limit: 500 }).then((r) => setAllMetrics(r.metrics ?? [])).catch(() => {});
  }, []);

  const gateSummary = useMemo(() => {
    const pass = allMetrics.filter((m) => m.gate_status === "pass").length;
    const fail = allMetrics.filter((m) => m.gate_status === "fail").length;
    const nodata = allMetrics.length - pass - fail;
    return { pass, fail, nodata, total: allMetrics.length };
  }, [allMetrics]);

  // Read verdict / kill-state once per metric; used both to grey-out nodes
  // and to drive the detail-panel verdict banner.
  const verdictsByMetric = useMemo(() => {
    const map: Record<string, { verdict: string | null; killReason: string | null; killed: boolean }> = {};
    for (const m of allMetrics) map[m.id] = readVerdict(m);
    return map;
  }, [allMetrics]);

  // Build campaign → strategy tree for DAG
  const { nodes, edges } = useMemo(() => {
    const byCampaign: Record<string, RunMetric[]> = {};
    for (const m of allMetrics) {
      const c = m.campaign || "uncategorized";
      (byCampaign[c] ??= []).push(m);
    }

    const rfNodes: Node[] = [];
    const rfEdges: Edge[] = [];

    for (const [campaign, metrics] of Object.entries(byCampaign)) {
      const campId = `camp-${campaign}`;
      rfNodes.push({
        id: campId, type: "strategy", position: { x: 0, y: 0 },
        data: {
          label: campaign, campaign, sharpe: null, gate: null,
          isSelected: false, dimmed: false,
          killed: false, killReason: null,
        } as StratNodeData,
        draggable: true,
      });
      for (const m of metrics) {
        const v = verdictsByMetric[m.id];
        rfNodes.push({
          id: m.id, type: "strategy", position: { x: 0, y: 0 },
          data: {
            label: shortName(m.iteration ?? m.id.slice(0, 8)),
            campaign, sharpe: m.sharpe, gate: m.gate_status,
            isSelected: selectedId === m.id,
            dimmed: m.gate_status === "fail",
            killed: v?.killed ?? false,
            killReason: v?.killReason ?? null,
          } as StratNodeData,
          draggable: true,
        });
        rfEdges.push({ id: `e-${campId}-${m.id}`, source: campId, target: m.id });
      }
    }
    return layout(rfNodes, rfEdges);
  }, [allMetrics, selectedId, verdictsByMetric]);

  const selected = allMetrics.find((m) => m.id === selectedId) ?? null;
  const equity = useEquitySeries(selected ? [selected] : []);
  const eqResult = equity[0]?.data ?? null;

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    if (node.id.startsWith("camp-")) return;
    setSelectedId(node.id);
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* header */}
      <div style={{ padding: "8px 16px", borderBottom: "1px solid #222240", display: "flex", alignItems: "center", gap: 16, flexShrink: 0 }}>
        <GitCompareArrows size={18} className="text-muted-foreground" />
        <span style={{ fontWeight: 600, fontSize: 14 }}>Strategy Development Map</span>
        <div style={{ display: "flex", gap: 12, fontSize: 11, marginLeft: "auto" }}>
          <span style={{ color: "#16a34a" }}>✓ {gateSummary.pass} pass</span>
          <span style={{ color: "#dc2626" }}>✗ {gateSummary.fail} fail</span>
          <span style={{ color: "#666" }}>? {gateSummary.nodata} no-data</span>
          <span style={{ color: "#888" }}>/ {gateSummary.total} total</span>
        </div>
      </div>

      {/* body */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* DAG */}
        <div style={{ flex: 1, position: "relative" }}>
          {allMetrics.length === 0 ? (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ textAlign: "center" }}>
                <Skeleton className="h-8 w-48 mb-2" />
                <p className="text-sm text-muted-foreground">Loading strategy metrics…</p>
              </div>
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodeClick={onNodeClick}
              fitView
              fitViewOptions={{ padding: 0.15 }}
              proOptions={{ hideAttribution: true }}
              style={{ background: "#0d0d1a" }}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#1a1a30" />
              <Controls showInteractive={false} />
            </ReactFlow>
          )}
        </div>

        {/* detail panel */}
        <div style={{
          width: 320, borderLeft: "1px solid #222240", flexShrink: 0,
          overflowY: "auto", background: "#12122a",
        }}>
          <DetailPanel metric={selected} equity={eqResult ?? null}
            verdict={selected ? verdictsByMetric[selected.id] ?? null : null} />
        </div>
      </div>
    </div>
  );
}
