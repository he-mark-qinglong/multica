/**
 * Strategy-iteration metrics backing the Compare page (`/{slug}/compare`).
 *
 * The metrics pipeline (backtest runner → POST /api/metrics) lands
 * incrementally and rows are written by external tooling, so every field
 * beyond `id` is treated as potentially missing/empty — the view renders
 * "—" for absent values instead of trusting the shape.
 */

/** One gate rule evaluation attached to a metric row. */
export interface GateDetailEntry {
  rule: string;
  op: string;
  threshold: number | null;
  actual: number | null;
  pass: boolean;
}

export interface RunMetric {
  id: string;
  task_id: string | null;
  issue_id: string | null;
  artifact_id: string | null;
  campaign: string | null;
  /** Iteration label — a string on the wire (e.g. "iter_003", "7"). */
  iteration: string | null;
  sharpe: number | null;
  sortino: number | null;
  calmar: number | null;
  ann_return: number | null;
  max_drawdown: number | null;
  profit_factor: number | null;
  oos_sharpe: number | null;
  oos_windows: number | null;
  timeframe: string | null;
  symbols: string[] | null;
  params: Record<string, unknown> | null;
  extra: Record<string, unknown> | null;
  created_at: string | null;
  gate_status?: "pass" | "fail" | null;
  gate_detail?: GateDetailEntry[] | null;
}

export interface ListCampaignsResponse {
  campaigns: string[];
}

export interface QueryMetricsResponse {
  metrics: RunMetric[];
}

/** Metadata row from `GET /api/tasks/{taskId}/artifacts`. The equity curve
 *  CSV is the row whose `kind` is `"equity"`. */
export interface TaskArtifact {
  id: string;
  kind?: string | null;
  name?: string | null;
  size_bytes?: number | null;
}
