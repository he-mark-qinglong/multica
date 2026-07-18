import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { ListTasksResponse, TaskStatus } from "../types";

export const taskKeys = {
  all: (wsId: string) => ["tasks", wsId] as const,
  list: (wsId: string, params: UseTasksParams) =>
    [...taskKeys.all(wsId), "list", params] as const,
};

export interface UseTasksParams {
  /** Client passes an array; the hook serializes to the comma-separated
   *  subset `GET /api/tasks` expects. */
  status?: TaskStatus[];
  agent_id?: string;
  issue_id?: string;
  limit?: number;
  offset?: number;
}

/**
 * Workspace-wide task list for the global Runs page. Server-side
 * pagination via limit/offset; the response carries `total` so the page
 * can do prev/next math. Filter identity is baked into the query key, so
 * changing filters fetches a fresh page automatically.
 */
export function useTasks(wsId: string, params: UseTasksParams = {}) {
  return useQuery({
    queryKey: taskKeys.list(wsId, params),
    queryFn: (): Promise<ListTasksResponse> =>
      api.listTasks({
        status: params.status?.length ? params.status.join(",") : undefined,
        agent_id: params.agent_id || undefined,
        issue_id: params.issue_id || undefined,
        limit: params.limit,
        offset: params.offset,
      }),
  });
}
