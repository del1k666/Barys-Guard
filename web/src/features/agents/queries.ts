import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { AgentPage, GroupSummary } from "../../api/types";
import type { AgentFilters } from "../../lib/agentFilters";

export const PAGE_SIZE = 25;

export function useAgents(filters: AgentFilters) {
  return useQuery({
    queryKey: ["agents", filters],
    queryFn: () =>
      api.get<AgentPage>("/agents", {
        q: filters.q,
        status: filters.status,
        group_id: filters.groupId,
        limit: PAGE_SIZE,
        offset: (filters.page - 1) * PAGE_SIZE,
      }),
    // Таблица не должна мигать пустотой, пока грузится следующая страница.
    placeholderData: keepPreviousData,
  });
}

export function useGroups() {
  return useQuery({
    queryKey: ["groups"],
    queryFn: () => api.get<GroupSummary[]>("/groups"),
    staleTime: 60_000,
  });
}
