import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { EventPage } from "../../api/types";
import { periodStart, type EventFilters } from "../../lib/eventFilters";

export const EVENTS_PAGE_SIZE = 50;
const LIVE_INTERVAL_MS = 5000;

export function useEvents(filters: EventFilters) {
  return useInfiniteQuery({
    queryKey: ["events", filters.channel, filters.severity, filters.agentId, filters.period],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      api.get<EventPage>("/events", {
        channel: filters.channel,
        severity: filters.severity,
        agent_id: filters.agentId,
        since: periodStart(filters.period),
        cursor: pageParam,
        limit: EVENTS_PAGE_SIZE,
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    // Опрос перечитывает загруженные страницы целиком; живой режим нужен,
    // чтобы увидеть новое сверху, а не чтобы листать историю.
    refetchInterval: filters.live ? LIVE_INTERVAL_MS : false,
  });
}
