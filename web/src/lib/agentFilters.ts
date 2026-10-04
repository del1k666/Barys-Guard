export interface AgentFilters {
  q: string;
  status: string;
  groupId: string;
  page: number;
}

export function parseFilters(params: URLSearchParams): AgentFilters {
  // Номер страницы — целое с единицы; всё остальное в адресной строке
  // чужая рука, и запрос с отрицательным смещением сервер отвергнет.
  const raw = params.get("page") ?? "";
  const page = /^\d+$/.test(raw) ? Number.parseInt(raw, 10) : 1;

  return {
    q: params.get("q") ?? "",
    status: params.get("status") ?? "",
    groupId: params.get("group_id") ?? "",
    page: page >= 1 ? page : 1,
  };
}

export function toSearchParams(filters: AgentFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.status) params.set("status", filters.status);
  if (filters.groupId) params.set("group_id", filters.groupId);
  if (filters.page > 1) params.set("page", String(filters.page));
  return params;
}
