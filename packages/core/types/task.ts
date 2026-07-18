import type { AgentTask } from "./agent";

export type TaskStatus = AgentTask["status"];

/**
 * Workspace-wide task row returned by `GET /api/tasks` — backs the global
 * Runs page. Same payload as the per-issue / per-agent AgentTask plus the
 * joined issue identifier (empty string when the task has no linked issue)
 * and, when the server includes it, a minimal embedded agent record.
 */
export interface Task extends AgentTask {
  /** e.g. "MUL-42"; empty string when the task has no linked issue. */
  issue_identifier: string;
  agent?: { id: string; name: string };
}

export interface ListTasksParams {
  /** Comma-separated subset of queued|dispatched|running|completed|failed|cancelled. */
  status?: string;
  agent_id?: string;
  issue_id?: string;
  limit?: number;
  offset?: number;
}

export interface ListTasksResponse {
  tasks: Task[];
  total: number;
}
