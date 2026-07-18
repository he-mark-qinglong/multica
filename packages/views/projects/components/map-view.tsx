"use client";

/**
 * Project map view — every issue in the project rendered as a node on a
 * React Flow canvas, with four edge types:
 *
 *   - parent → child  (issue.parent_issue_id, goal decomposition)
 *   - blocks / related / supersedes  (issue_dependency rows)
 *   - cross-project dependency edges → dashed edges ending at dashed
 *     "external" ghost nodes (issues that live in other projects)
 *
 * Interactions:
 *   - click a node → navigate to the issue detail page
 *   - drag a connection between two nodes → type picker (sub-issue /
 *     blocks / related / supersedes) → POST dependency or PUT parent
 *   - click an edge → confirm removal (dependency row delete, or parent
 *     link cleared via PUT parent_issue_id=null)
 *
 * Isolated issues (no edges at all) are laid out in a grid below the
 * connected graph so no issue ever falls off the map. External nodes are
 * never isolated — they exist only as cross-edge endpoints.
 */

import * as dagre from "@dagrejs/dagre";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Eye, EyeOff } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@multica/core/api";
import { useWorkspaceId } from "@multica/core/hooks";
import { projectGraphOptions, projectKeys } from "@multica/core/projects/queries";
import { useUpdateIssue } from "@multica/core/issues/mutations";
import { STATUS_CONFIG } from "@multica/core/issues/config";
import { useWorkspacePaths } from "@multica/core/paths";
import type {
  IssueDependencyType,
  ProjectGraphEdge,
  ProjectGraphNode,
} from "@multica/core/types";
import { Button } from "@multica/ui/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@multica/ui/components/ui/dialog";
import { cn } from "@multica/ui/lib/utils";

import { PriorityIcon } from "../../issues/components/priority-icon";
import { useT } from "../../i18n";
import { useNavigation } from "../../navigation";

const NODE_WIDTH = 240;
const NODE_HEIGHT = 72;

/** Parent/child edges use this neutral tone; dependency types each get a
 *  distinct color so the four edge kinds are distinguishable at a glance. */
const EDGE_STYLE = {
  parent: { stroke: "#94a3b8" },
  blocks: { stroke: "#ef4444" },
  related: { stroke: "#3b82f6" },
  supersedes: { stroke: "#f59e0b", strokeDasharray: "6 3" },
} as const;

type IssueFlowNode = Node<{ issue: ProjectGraphNode }, "issue">;

function IssueNode({ data, selected }: NodeProps<IssueFlowNode>) {
  const { issue } = data;
  const cfg = STATUS_CONFIG[issue.status];
  const external = issue.external === true;
  return (
    <div
      className={cn(
        "flex h-[72px] w-[240px] flex-col justify-center gap-1 rounded-md border bg-card px-3 py-2 shadow-sm",
        external && "border-dashed opacity-80",
        selected && "border-primary ring-1 ring-primary",
      )}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground" isConnectable={!external} />
      <div className="flex items-center gap-1.5">
        <span className={cn("size-2 shrink-0 rounded-full", cfg?.dividerColor ?? "bg-muted-foreground/40")} />
        <span className="font-mono text-[10px] text-muted-foreground">
          {issue.identifier}
        </span>
        <PriorityIcon priority={issue.priority} className="ml-auto" />
      </div>
      <div className="truncate text-xs font-medium" title={issue.title}>
        {external ? `↗ ${issue.title}` : issue.title}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground" isConnectable={!external} />
    </div>
  );
}

const NODE_TYPES = { issue: IssueNode };

/** Run-log issues (decision cycles, router sweeps, CV runs, cron logs, and
 *  bare dispatch records with no task title) are operational noise rather
 *  than work breakdown. Hidden from the map by default; toggle shows them.
 *  Dispatch issues WITH a task title (e.g. "[dispatch 2026-07-18] U2 audit…")
 *  are real work and stay visible. */
const RUN_LOG_PATTERN =
  /^\s*\[(smark-decision-cycle|escalation-router|weekly-cron|framework-validate)\b/i;
const BARE_DISPATCH_PATTERN = /^\s*\[(idle-)?dispatch[^\]]*\]\s*$/i;

function isRunLogTitle(title: string): boolean {
  return RUN_LOG_PATTERN.test(title) || BARE_DISPATCH_PATTERN.test(title);
}

const PARENT_EDGE_PREFIX = "parent:";
const DEP_EDGE_PREFIX = "dep:";

/**
 * Dagre layered layout, rankdir LR. Connected nodes go through dagre;
 * isolated nodes (no parent link, no dependency edge in either direction)
 * are placed in a grid below the main graph's bounding box so they stay
 * visible instead of being scattered through rank 0. External (cross-project
 * counterpart) nodes join the dagre layout via their cross edges and never
 * land in the isolated grid.
 */
function layoutGraph(
  graphNodes: ProjectGraphNode[],
  graphEdges: ProjectGraphEdge[],
  externalNodes: ProjectGraphNode[],
  crossEdges: ProjectGraphEdge[],
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const allNodes = [...graphNodes, ...externalNodes];
  if (allNodes.length === 0) return positions;

  const hasParent = new Set<string>();
  for (const n of graphNodes) {
    if (n.parent_issue_id) {
      hasParent.add(n.id);
      hasParent.add(n.parent_issue_id);
    }
  }
  const inDep = new Set<string>();
  for (const e of [...graphEdges, ...crossEdges]) {
    inDep.add(e.issue_id);
    inDep.add(e.depends_on_issue_id);
  }
  const externalIds = new Set(externalNodes.map((n) => n.id));
  const isIsolated = (id: string) => !externalIds.has(id) && !hasParent.has(id) && !inDep.has(id);

  const connected = allNodes.filter((n) => !isIsolated(n.id));
  const isolated = graphNodes.filter((n) => isIsolated(n.id));

  let maxY = 0;
  if (connected.length > 0) {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 100, marginx: 24, marginy: 24 });
    g.setDefaultEdgeLabel(() => ({}));
    for (const n of connected) {
      g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    }
    const ids = new Set(connected.map((n) => n.id));
    for (const n of connected) {
      if (n.parent_issue_id && ids.has(n.parent_issue_id) && !externalIds.has(n.id)) {
        g.setEdge(n.parent_issue_id, n.id);
      }
    }
    for (const e of [...graphEdges, ...crossEdges]) {
      if (ids.has(e.issue_id) && ids.has(e.depends_on_issue_id)) {
        g.setEdge(e.issue_id, e.depends_on_issue_id);
      }
    }
    dagre.layout(g);
    for (const n of connected) {
      const p = g.node(n.id);
      if (!p) continue;
      // Dagre writes center coordinates; React Flow wants top-left.
      const pos = { x: p.x - NODE_WIDTH / 2, y: p.y - NODE_HEIGHT / 2 };
      positions.set(n.id, pos);
      maxY = Math.max(maxY, pos.y + NODE_HEIGHT);
    }
  }

  const COLS = 4;
  const GAP_X = 40;
  const GAP_Y = 32;
  isolated.forEach((n, i) => {
    positions.set(n.id, {
      x: 24 + (i % COLS) * (NODE_WIDTH + GAP_X),
      y: (connected.length > 0 ? maxY + 80 : 24) + Math.floor(i / COLS) * (NODE_HEIGHT + GAP_Y),
    });
  });
  return positions;
}

function buildElements(
  graphNodes: ProjectGraphNode[],
  graphEdges: ProjectGraphEdge[],
  externalNodes: ProjectGraphNode[],
  crossEdges: ProjectGraphEdge[],
): { nodes: IssueFlowNode[]; edges: Edge[] } {
  const positions = layoutGraph(graphNodes, graphEdges, externalNodes, crossEdges);
  const allNodes = [...graphNodes, ...externalNodes];
  const ids = new Set(allNodes.map((n) => n.id));
  const nodes: IssueFlowNode[] = allNodes.map((n) => ({
    id: n.id,
    type: "issue",
    position: positions.get(n.id) ?? { x: 0, y: 0 },
    data: { issue: n },
  }));
  const edges: Edge[] = [];
  for (const n of graphNodes) {
    if (n.parent_issue_id && ids.has(n.parent_issue_id)) {
      edges.push({
        id: `${PARENT_EDGE_PREFIX}${n.id}`,
        source: n.parent_issue_id,
        target: n.id,
        type: "smoothstep",
        style: EDGE_STYLE.parent,
        markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_STYLE.parent.stroke },
      });
    }
  }
  for (const e of graphEdges) {
    const style = EDGE_STYLE[e.type] ?? EDGE_STYLE.related;
    edges.push({
      id: `${DEP_EDGE_PREFIX}${e.id}`,
      source: e.issue_id,
      target: e.depends_on_issue_id,
      type: "smoothstep",
      animated: e.type === "supersedes",
      label: e.type,
      labelStyle: { fontSize: 10, fill: style.stroke },
      style,
      markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
    });
  }
  // Cross-project edges: same per-type color, dashed + dimmed so they're
  // visually distinct from intra-project links.
  for (const e of crossEdges) {
    if (!ids.has(e.issue_id) || !ids.has(e.depends_on_issue_id)) continue;
    const base = EDGE_STYLE[e.type] ?? EDGE_STYLE.related;
    const style = { ...base, strokeDasharray: "4 3", opacity: 0.7 };
    edges.push({
      id: `${DEP_EDGE_PREFIX}${e.id}`,
      source: e.issue_id,
      target: e.depends_on_issue_id,
      type: "smoothstep",
      animated: false,
      label: e.type,
      labelStyle: { fontSize: 10, fill: base.stroke },
      style,
      markerEnd: { type: MarkerType.ArrowClosed, color: base.stroke },
    });
  }
  return { nodes, edges };
}

/** All transitive descendants of `rootId` in the parent/child tree — used
 *  to disable "make sub-issue" when it would create a cycle. */
function descendantsOf(rootId: string, graphNodes: ProjectGraphNode[]): Set<string> {
  const children = new Map<string, string[]>();
  for (const n of graphNodes) {
    if (n.parent_issue_id) {
      const list = children.get(n.parent_issue_id) ?? [];
      list.push(n.id);
      children.set(n.parent_issue_id, list);
    }
  }
  const out = new Set<string>();
  const queue = [rootId];
  while (queue.length > 0) {
    const cur = queue.pop()!;
    for (const c of children.get(cur) ?? []) {
      if (!out.has(c)) {
        out.add(c);
        queue.push(c);
      }
    }
  }
  return out;
}

export function ProjectMapView({ projectId }: { projectId: string }) {
  return (
    <ReactFlowProvider>
      <ProjectMapCanvas projectId={projectId} />
    </ReactFlowProvider>
  );
}

type PendingConnect = { source: string; target: string };
type PendingDelete =
  | { kind: "parent"; childId: string }
  | { kind: "dep"; edge: ProjectGraphEdge };

function ProjectMapCanvas({ projectId }: { projectId: string }) {
  const { t } = useT("projects");
  const { t: tCommon } = useT("common");
  const wsId = useWorkspaceId();
  const paths = useWorkspacePaths();
  const nav = useNavigation();
  const qc = useQueryClient();

  const graphQuery = useQuery(projectGraphOptions(wsId, projectId));
  const graphNodes = useMemo(() => graphQuery.data?.nodes ?? [], [graphQuery.data]);
  const graphEdges = useMemo(() => graphQuery.data?.edges ?? [], [graphQuery.data]);
  const externalNodes = useMemo(() => graphQuery.data?.external_nodes ?? [], [graphQuery.data]);
  const crossEdges = useMemo(() => graphQuery.data?.cross_edges ?? [], [graphQuery.data]);

  // Run-log filter (default on): drop operational-log issues from the map,
  // plus any edge that touches a hidden node. External (cross-project)
  // nodes follow the same rule — a filtered external node takes its cross
  // edges down with it.
  const [hideRunLogs, setHideRunLogs] = useState(true);
  const { visibleNodes, visibleEdges, visibleExternal, visibleCrossEdges, hiddenLogCount } = useMemo(() => {
    if (!hideRunLogs) {
      return {
        visibleNodes: graphNodes,
        visibleEdges: graphEdges,
        visibleExternal: externalNodes,
        visibleCrossEdges: crossEdges,
        hiddenLogCount: 0,
      };
    }
    const kept = graphNodes.filter((n) => !isRunLogTitle(n.title));
    const keptExt = externalNodes.filter((n) => !isRunLogTitle(n.title));
    const ids = new Set([...kept, ...keptExt].map((n) => n.id));
    return {
      visibleNodes: kept,
      visibleEdges: graphEdges.filter(
        (e) => ids.has(e.issue_id) && ids.has(e.depends_on_issue_id),
      ),
      visibleExternal: keptExt,
      visibleCrossEdges: crossEdges.filter(
        (e) => ids.has(e.issue_id) && ids.has(e.depends_on_issue_id),
      ),
      hiddenLogCount: graphNodes.length + externalNodes.length - kept.length - keptExt.length,
    };
  }, [hideRunLogs, graphNodes, graphEdges, externalNodes, crossEdges]);

  const initial = useMemo(
    () => buildElements(visibleNodes, visibleEdges, visibleExternal, visibleCrossEdges),
    [visibleNodes, visibleEdges, visibleExternal, visibleCrossEdges],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<IssueFlowNode>(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges);
  useEffect(() => {
    setNodes(initial.nodes);
    setEdges(initial.edges);
  }, [initial, setNodes, setEdges]);

  const [pendingConnect, setPendingConnect] = useState<PendingConnect | null>(null);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);

  const invalidateGraph = useCallback(
    () => qc.invalidateQueries({ queryKey: projectKeys.all(wsId) }),
    [qc, wsId],
  );

  const updateIssue = useUpdateIssue();
  const createDep = useMutation({
    mutationFn: ({ source, target, type }: { source: string; target: string; type: IssueDependencyType }) =>
      api.createIssueDependency(source, { depends_on_issue_id: target, type }),
    onSuccess: invalidateGraph,
    onError: (err) =>
      toast.error(`${t(($) => $.map.toast_create_failed)}: ${err instanceof Error ? err.message : String(err)}`),
  });
  const deleteDep = useMutation({
    mutationFn: ({ issueId, depId }: { issueId: string; depId: string }) =>
      api.deleteIssueDependency(issueId, depId),
    onSuccess: invalidateGraph,
    onError: (err) =>
      toast.error(`${t(($) => $.map.toast_delete_failed)}: ${err instanceof Error ? err.message : String(err)}`),
  });

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_, node) => {
      nav.push(paths.issueDetail(node.id));
    },
    [nav, paths],
  );

  const handleConnect = useCallback((conn: Connection) => {
    if (!conn.source || !conn.target || conn.source === conn.target) return;
    setPendingConnect({ source: conn.source, target: conn.target });
  }, []);

  const handleEdgeClick = useCallback(
    (_: unknown, edge: Edge) => {
      if (edge.id.startsWith(PARENT_EDGE_PREFIX)) {
        setPendingDelete({ kind: "parent", childId: edge.id.slice(PARENT_EDGE_PREFIX.length) });
      } else if (edge.id.startsWith(DEP_EDGE_PREFIX)) {
        const depId = edge.id.slice(DEP_EDGE_PREFIX.length);
        // Cross edges carry real dependency ids too — same DELETE endpoint.
        const dep = graphEdges.find((e) => e.id === depId) ?? crossEdges.find((e) => e.id === depId);
        if (dep) setPendingDelete({ kind: "dep", edge: dep });
      }
    },
    [graphEdges, crossEdges],
  );

  // "Make sub-issue" would set target.parent = source. That's a cycle when
  // source sits inside target's subtree — disable the option in that case.
  const childOptionDisabled = useMemo(() => {
    if (!pendingConnect) return false;
    return descendantsOf(pendingConnect.target, graphNodes).has(pendingConnect.source);
  }, [pendingConnect, graphNodes]);

  const pickConnectType = useCallback(
    (type: "child" | IssueDependencyType) => {
      if (!pendingConnect) return;
      const { source, target } = pendingConnect;
      setPendingConnect(null);
      if (type === "child") {
        updateIssue.mutate(
          { id: target, parent_issue_id: source },
          {
            onSuccess: invalidateGraph,
            onError: (err) =>
              toast.error(`${t(($) => $.map.toast_create_failed)}: ${err instanceof Error ? err.message : String(err)}`),
          },
        );
      } else {
        createDep.mutate({ source, target, type });
      }
    },
    [pendingConnect, updateIssue, invalidateGraph, createDep, t],
  );

  const confirmDelete = useCallback(() => {
    if (!pendingDelete) return;
    const del = pendingDelete;
    setPendingDelete(null);
    if (del.kind === "parent") {
      updateIssue.mutate(
        { id: del.childId, parent_issue_id: null },
        {
          onSuccess: invalidateGraph,
          onError: (err) =>
            toast.error(`${t(($) => $.map.toast_delete_failed)}: ${err instanceof Error ? err.message : String(err)}`),
        },
      );
    } else {
      deleteDep.mutate({ issueId: del.edge.issue_id, depId: del.edge.id });
    }
  }, [pendingDelete, updateIssue, deleteDep, invalidateGraph, t]);

  if (graphQuery.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        {tCommon(($) => $.loading)}
      </div>
    );
  }

  return (
    <div className="relative flex-1 min-h-[480px]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onConnect={handleConnect}
        onEdgeClick={handleEdgeClick}
        deleteKeyCode={null}
        fitView
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
        <Controls />
        <MiniMap pannable zoomable className="!bg-card" />
      </ReactFlow>

      {/* Run-log visibility toggle */}
      <button
        type="button"
        onClick={() => setHideRunLogs((v) => !v)}
        className="absolute left-3 top-3 z-10 flex items-center gap-1.5 rounded-md border bg-card/90 px-2.5 py-1.5 text-[10px] text-muted-foreground shadow-sm hover:bg-accent"
      >
        {hideRunLogs ? <EyeOff className="size-3" /> : <Eye className="size-3" />}
        {hideRunLogs
          ? `${t(($) => $.map.hide_run_logs)} (${hiddenLogCount})`
          : t(($) => $.map.show_run_logs)}
      </button>

      {/* Edge-type legend */}
      <div className="absolute right-3 top-3 z-10 flex flex-col gap-1 rounded-md border bg-card/90 px-3 py-2 text-[10px] text-muted-foreground shadow-sm">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4" style={{ background: EDGE_STYLE.parent.stroke }} />
          {t(($) => $.map.legend_parent)}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4" style={{ background: EDGE_STYLE.blocks.stroke }} />
          blocks
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4" style={{ background: EDGE_STYLE.related.stroke }} />
          related
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0 w-4 border-t-2 border-dashed" style={{ borderColor: EDGE_STYLE.supersedes.stroke }} />
          supersedes
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0 w-4 border-t border-dashed opacity-70" style={{ borderColor: EDGE_STYLE.parent.stroke }} />
          {t(($) => $.map.legend_external)}
        </span>
      </div>

      {/* Connection type picker */}
      <Dialog open={pendingConnect !== null} onOpenChange={(open) => !open && setPendingConnect(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t(($) => $.map.connect_title)}</DialogTitle>
            <DialogDescription>{t(($) => $.map.connect_description)}</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Button
              variant="outline"
              className="justify-start"
              disabled={childOptionDisabled}
              onClick={() => pickConnectType("child")}
            >
              {t(($) => $.map.type_child)}
            </Button>
            <Button variant="outline" className="justify-start" onClick={() => pickConnectType("blocks")}>
              blocks
            </Button>
            <Button variant="outline" className="justify-start" onClick={() => pickConnectType("related")}>
              related
            </Button>
            <Button variant="outline" className="justify-start" onClick={() => pickConnectType("supersedes")}>
              supersedes
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Edge removal confirm */}
      <Dialog open={pendingDelete !== null} onOpenChange={(open) => !open && setPendingDelete(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t(($) => $.map.delete_edge_title)}</DialogTitle>
            <DialogDescription>
              {pendingDelete?.kind === "parent"
                ? t(($) => $.map.delete_edge_child_hint)
                : t(($) => $.map.delete_edge_description)}
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setPendingDelete(null)}>
              {tCommon(($) => $.cancel)}
            </Button>
            <Button variant="destructive" onClick={confirmDelete}>
              {tCommon(($) => $.delete)}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
