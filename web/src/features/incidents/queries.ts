import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "../../api/client";
import type { IncidentDetail, IncidentPage, IncidentStatusUpdate } from "../../api/types";
import type { IncidentFilters } from "../../lib/incidentFilters";

export const INCIDENTS_PAGE_SIZE = 50;
const LIVE_INTERVAL_MS = 5000;

export function useIncidents(filters: IncidentFilters) {
  return useInfiniteQuery({
    queryKey: ["incidents", filters.status, filters.severity, filters.agentId],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      api.get<IncidentPage>("/incidents", {
        status: filters.status,
        severity: filters.severity,
        agent_id: filters.agentId,
        cursor: pageParam,
        limit: INCIDENTS_PAGE_SIZE,
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    // Опрос перечитывает загруженные страницы целиком; живой режим нужен,
    // чтобы увидеть новое сверху, а не чтобы листать историю.
    refetchInterval: filters.live ? LIVE_INTERVAL_MS : false,
  });
}

export function useIncident(id: string | null) {
  return useQuery({
    queryKey: ["incident", id],
    queryFn: () => api.get<IncidentDetail>(`/incidents/${encodeURIComponent(id ?? "")}`),
    enabled: id !== null,
  });
}

export function useUpdateIncident(id: string) {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (status: IncidentStatusUpdate["status"]) =>
      api.patch<IncidentDetail>(`/incidents/${encodeURIComponent(id)}`, { status }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["incidents"] });
      void client.invalidateQueries({ queryKey: ["incident", id] });
    },
  });
}
