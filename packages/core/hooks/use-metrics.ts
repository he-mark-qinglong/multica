import { useQueries, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { QueryMetricsResponse, RunMetric } from "../types";

export const metricKeys = {
  all: ["metrics"] as const,
  query: (campaign: string, limit: number) =>
    [...metricKeys.all, "query", campaign, limit] as const,
  equity: (metricId: string) => [...metricKeys.all, "equity", metricId] as const,
};

/**
 * Per-iteration metric rows for one campaign on the Compare page. The
 * campaign name is baked into the query key, so switching campaigns
 * fetches a fresh result set automatically.
 */
export function useMetrics(campaign: string | null, limit = 200) {
  return useQuery({
    queryKey: metricKeys.query(campaign ?? "", limit),
    queryFn: (): Promise<QueryMetricsResponse> =>
      api.queryMetrics({ campaign: campaign as string, limit }),
    enabled: !!campaign,
  });
}

export interface EquityCsvResult {
  metricId: string;
  /** Raw CSV text of the equity artifact; null when the task has no
   *  artifact of kind "equity" (or no task_id to look up). */
  csv: string | null;
}

/**
 * Fetches the equity-curve CSV for each given metric row (one query per
 * row, keyed by metric id). Per-series failures surface via that query's
 * `isError` — the page renders a per-series warning instead of failing
 * the whole chart.
 */
export function useEquitySeries(metrics: RunMetric[]) {
  return useQueries({
    queries: metrics.map((m) => ({
      queryKey: metricKeys.equity(m.id),
      enabled: !!m.task_id,
      queryFn: async (): Promise<EquityCsvResult> => {
        if (!m.task_id) return { metricId: m.id, csv: null };
        const artifacts = await api.listTaskArtifacts(m.task_id);
        const equity = (artifacts ?? []).find((a) => a?.kind === "equity");
        if (!equity?.id) return { metricId: m.id, csv: null };
        const blob = await api.downloadArtifact(equity.id);
        return { metricId: m.id, csv: await blob.text() };
      },
    })),
  });
}
