import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type {
  AgentDetail,
  AgentPage,
  CommandPage,
  CommandResponse,
  CommandType,
  GroupSummary,
} from "../../api/types";
import type { AgentFilters } from "../../lib/agentFilters";
import { OVERVIEW_KEY } from "../overview/useOverview";

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

export function useAgent(id: string) {
  return useQuery({
    queryKey: ["agent", id],
    queryFn: () => api.get<AgentDetail>(`/agents/${id}`),
  });
}

export function useAgentCommands(id: string) {
  return useQuery({
    queryKey: ["agent-commands", id],
    queryFn: () => api.get<CommandPage>("/commands", { agent_id: id, limit: 20 }),
  });
}

export function useSendCommand(id: string) {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (type: CommandType) => api.post<CommandResponse>(`/agents/${id}/commands`, { type }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["agent-commands", id] });
      void client.invalidateQueries({ queryKey: OVERVIEW_KEY });
    },
  });
}

export function useRevokeAgent(id: string) {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (reason: string) => api.post<null>(`/agents/${id}/revoke`, { reason }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["agent", id] });
      void client.invalidateQueries({ queryKey: ["agents"] });
      void client.invalidateQueries({ queryKey: OVERVIEW_KEY });
    },
  });
}
