import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { RuleSummary, RuleUpdateRequest } from "../../api/types";

export function useRules() {
  return useQuery({ queryKey: ["rules"], queryFn: () => api.get<RuleSummary[]>("/rules") });
}

export function useUpdateRule(id: string) {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (body: RuleUpdateRequest) =>
      api.patch<RuleSummary>(`/rules/${encodeURIComponent(id)}`, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["rules"] });
      void client.invalidateQueries({ queryKey: ["rule-versions", id] });
    },
  });
}
