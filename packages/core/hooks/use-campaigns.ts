import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { ListCampaignsResponse } from "../types";

export const campaignKeys = {
  // Workspace identity travels via the X-Workspace-Slug header (set inside
  // the client's authHeaders), so the key doesn't carry a workspace id.
  all: ["metrics", "campaigns"] as const,
};

/**
 * Campaign name list for the Compare page selector. The server resolves
 * the workspace from the request headers; a failure yields an empty list
 * and the page falls back to a free-text campaign input.
 */
export function useCampaigns() {
  return useQuery({
    queryKey: campaignKeys.all,
    queryFn: (): Promise<ListCampaignsResponse> => api.listCampaigns(),
  });
}
