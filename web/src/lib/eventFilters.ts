export const PERIODS = ["1h", "24h", "7d"] as const;
export type Period = (typeof PERIODS)[number] | "";

export interface EventFilters {
  channel: string;
  severity: string;
  agentId: string;
  period: Period;
  live: boolean;
}

const PERIOD_MS: Record<(typeof PERIODS)[number], number> = {
  "1h": 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
};

export function parseEventFilters(params: URLSearchParams): EventFilters {
  // Период приходит из адресной строки — чужая рука; незнакомое значение
  // молча превращалось бы в запрос без нижней границы.
  const rawPeriod = params.get("period") ?? "";
  const period = (PERIODS as readonly string[]).includes(rawPeriod) ? (rawPeriod as Period) : "";

  return {
    channel: params.get("channel") ?? "",
    severity: params.get("severity") ?? "",
    agentId: params.get("agent_id") ?? "",
    period,
    // Живое обновление включено, пока его явно не выключили.
    live: params.get("live") !== "0",
  };
}

export function toEventSearchParams(filters: EventFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.channel) params.set("channel", filters.channel);
  if (filters.severity) params.set("severity", filters.severity);
  if (filters.agentId) params.set("agent_id", filters.agentId);
  if (filters.period) params.set("period", filters.period);
  if (!filters.live) params.set("live", "0");
  return params;
}

/** Нижняя граница периода как метка времени для `since`; без периода — нет границы. */
export function periodStart(period: Period, now: Date = new Date()): string | undefined {
  if (!period) return undefined;
  return new Date(now.getTime() - PERIOD_MS[period]).toISOString();
}
