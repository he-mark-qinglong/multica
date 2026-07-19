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
};

const GATE_STYLE: Record<string, { bg: string; icon: typeof CheckCircle2 }> = {
  pass: { bg: "#16a34a20", icon: CheckCircle2 },
  fail: { bg: "#dc262620", icon: XCircle },
};

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
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <span style={{ fontSize: 10, color: "#7c7c9e", textTransform: "uppercase", letterSpacing: 0.5 }}>
          {d.campaign}
        </span>
        {gs && (
          <span style={{
            fontSize: 9, padding: "1px 6px", borderRadius: 4, background: gs.bg,
            color: d.gate === "pass" ? "#16a34a" : "#dc2626", fontWeight: 600,
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
        <GateIcon size={14} style={{ color: gs ? (d.gate === "pass" ? "#16a34a" : "#dc2626") : "#666" }} />
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
function DetailPanel({ metric, equity }: { metric: RunMetric | null; equity: EquityCsvResult | null }) {
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
        background: m.gate_status === "pass" ? "#16a34a20" : m.gate_status === "fail" ? "#dc262620" : "#333",
        color: m.gate_status === "pass" ? "#16a34a" : m.gate_status === "fail" ? "#dc2626" : "#888",
        fontSize: 12, fontWeight: 700,
      }}>
        GATE: {m.gate_status?.toUpperCase() ?? "NO DATA"}
      </div>

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
          isSelected: false,
        } as StratNodeData,
        draggable: true,
      });
      for (const m of metrics) {
        rfNodes.push({
          id: m.id, type: "strategy", position: { x: 0, y: 0 },
          data: {
            label: shortName(m.iteration ?? m.id.slice(0, 8)),
            campaign, sharpe: m.sharpe, gate: m.gate_status,
            isSelected: selectedId === m.id,
          } as StratNodeData,
          draggable: true,
        });
        rfEdges.push({ id: `e-${campId}-${m.id}`, source: campId, target: m.id });
      }
    }
    return layout(rfNodes, rfEdges);
  }, [allMetrics, selectedId]);

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
          <DetailPanel metric={selected} equity={eqResult ?? null} />
        </div>
      </div>
    </div>
  );
}
