import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type {
  RuleCreateRequest,
  RuleSummary,
  RuleTestRequest,
  RuleTestResponse,
  RuleUpdateRequest,
  RuleVersionItem,
  TermPage,
} from "../../api/types";

export const TERMS_PAGE_SIZE = 100;

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

export function useCreateRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RuleCreateRequest) => api.post<RuleSummary>("/rules", body),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["rules"] }),
  });
}

/** Тестовый текст уходит только в этот запрос и нигде не кешируется. */
export function useTestRule() {
  return useMutation({
    mutationFn: (body: RuleTestRequest) => api.post<RuleTestResponse>("/rules/test", body),
  });
}

export function useTerms(ruleId: string, q: string, limit: number) {
  return useQuery({
    queryKey: ["rule-terms", ruleId, q, limit],
    queryFn: () =>
      api.get<TermPage>(`/rules/${encodeURIComponent(ruleId)}/terms`, { q, limit }),
    placeholderData: keepPreviousData,
  });
}

export function useAddTerms(ruleId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (terms: string[]) =>
      api.post<{ added: number }>(`/rules/${encodeURIComponent(ruleId)}/terms`, { terms }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["rule-terms", ruleId] });
      void client.invalidateQueries({ queryKey: ["rules"] });
      void client.invalidateQueries({ queryKey: ["rule-versions", ruleId] });
    },
  });
}

export function useDeleteTerm(ruleId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (termId: string) =>
      api.delete<void>(`/rules/${encodeURIComponent(ruleId)}/terms/${encodeURIComponent(termId)}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["rule-terms", ruleId] });
      void client.invalidateQueries({ queryKey: ["rules"] });
      void client.invalidateQueries({ queryKey: ["rule-versions", ruleId] });
    },
  });
}

export function useVersions(ruleId: string) {
  return useQuery({
    queryKey: ["rule-versions", ruleId],
    queryFn: () => api.get<RuleVersionItem[]>(`/rules/${encodeURIComponent(ruleId)}/versions`),
  });
}
