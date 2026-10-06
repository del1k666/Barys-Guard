export const INCIDENT_STATUSES = ["open", "acknowledged", "closed"] as const;
export const INCIDENT_SEVERITIES = ["info", "low", "medium", "high", "critical"] as const;

export interface IncidentFilters {
  status: string;
  severity: string;
  agentId: string;
  live: boolean;
}

function known(value: string | null, allowed: readonly string[]): string {
  // Значение приходит из адресной строки — чужая рука; незнакомое
  // молча превращалось бы в запрос, на который сервер ответит 422.
  return value !== null && allowed.includes(value) ? value : "";
}

export function parseIncidentFilters(params: URLSearchParams): IncidentFilters {
  return {
    status: known(params.get("status"), INCIDENT_STATUSES),
    severity: known(params.get("severity"), INCIDENT_SEVERITIES),
    agentId: params.get("agent_id") ?? "",
    // Живое обновление включено, пока его явно не выключили.
    live: params.get("live") !== "0",
  };
}

export function toIncidentSearchParams(filters: IncidentFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.severity) params.set("severity", filters.severity);
  if (filters.agentId) params.set("agent_id", filters.agentId);
  if (!filters.live) params.set("live", "0");
  return params;
}
