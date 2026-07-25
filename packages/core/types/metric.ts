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
  /** Backend sends this when the rule was skipped/failed for a notable
   *  reason (e.g. "missing required metric"); omitted otherwise. */
  note?: string;
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
  /** Free-form passthrough of unknown blob keys written by the publisher.
   *  Conventional keys (all optional strings, absent on older rows):
   *  - verdict: one-line human verdict, e.g. "CV_PASS", "KILL"
   *  - kill_reason: why the strategy was killed (non-empty ⇒ killed)
   *  - kill_evidence: pointer to evidence (issue id / file path)
   *  Framework-gate keys also live here: divergence_flag, framework_validated,
   *  framework_sharpe, framework_return_pct. */
  extra: Record<string, unknown> | null;
  created_at: string | null;
  /** Strict gate outcome. "no-data" = not enough input metrics to evaluate
   *  (e.g. missing sharpe); distinct from null (never evaluated). */
  gate_status?: "pass" | "fail" | "no-data" | null;
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
