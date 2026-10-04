import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { Overview } from "../../api/types";

export const OVERVIEW_KEY = ["overview"] as const;

export function useOverview() {
  return useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: () => api.get<Overview>("/overview"),
  });
}
