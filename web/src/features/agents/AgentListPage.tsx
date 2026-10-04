import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Pagination } from "../../components/Pagination";
import { Select } from "../../components/Select";
import { Spinner } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { AgentStatusBadge } from "../../components/StatusBadge";
import { Table } from "../../components/Table";
import { ru } from "../../i18n/ru";
import { parseFilters, toSearchParams, type AgentFilters } from "../../lib/agentFilters";
import { relativeTime } from "../../lib/format";
import page from "../../styles/page.module.css";
import { PAGE_SIZE, useAgents, useGroups } from "./queries";

export function AgentListPage() {
  const [params, setParams] = useSearchParams();
  const filters = parseFilters(params);
  const agents = useAgents(filters);
  const groups = useGroups();
  const [draft, setDraft] = useState(filters.q);

  // Кнопка «назад» меняет q в адресе; поле должно за ним последовать.
  useEffect(() => {
    setDraft(filters.q);
  }, [filters.q]);

  function update(patch: Partial<AgentFilters>) {
    setParams(toSearchParams({ ...filters, page: 1, ...patch }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    update({ q: draft.trim() });
  }

  const filtered = Boolean(filters.q || filters.status || filters.groupId);

  const statusOptions = [
    { value: "", label: ru.agents.anyStatus },
    ...Object.entries(ru.status.agent).map(([value, label]) => ({ value, label })),
  ];
  const groupOptions = [
    { value: "", label: ru.agents.anyGroup },
    ...(groups.data ?? []).map((group) => ({ value: group.id, label: group.name })),
  ];
  // Группа из адреса может быть недоступна или удалена: без своего пункта
  // select показал бы «Любая группа», хотя список отфильтрован.
  if (filters.groupId && !groupOptions.some((option) => option.value === filters.groupId)) {
    groupOptions.push({ value: filters.groupId, label: filters.groupId });
  }

  function body() {
    if (agents.isPending) return <Spinner label={ru.common.loading} />;
    if (agents.isError) {
      return <ErrorState error={agents.error} onRetry={() => void agents.refetch()} />;
    }

    const { items, total } = agents.data;

    if (items.length === 0) {
      // Список пуст, но агенты есть: оператор ушёл за последнюю страницу.
      if (total > 0) {
        return (
          <EmptyState
            title={ru.agents.emptyPage}
            hint={ru.agents.emptyPageHint}
            action={<Button onClick={() => update({ page: 1 })}>{ru.agents.firstPage}</Button>}
          />
        );
      }
      if (filtered) {
        return (
          <EmptyState
            title={ru.agents.emptyFilter}
            hint={ru.agents.emptyFilterHint}
            action={
              <Button onClick={() => setParams(new URLSearchParams())}>{ru.agents.reset}</Button>
            }
          />
        );
      }
      return <EmptyState title={ru.agents.emptyFleet} hint={ru.agents.emptyFleetHint} />;
    }

    return (
      <>
        <Table caption={ru.agents.caption}>
          <thead>
            <tr>
              <th>{ru.agents.columns.host}</th>
              <th>{ru.agents.columns.status}</th>
              <th>{ru.agents.columns.os}</th>
              <th>{ru.agents.columns.group}</th>
              <th>{ru.agents.columns.heartbeat}</th>
              <th>{ru.agents.columns.version}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((agent) => (
              <tr key={agent.id}>
                <td>
                  <Link to={`/agents/${agent.id}`}>{agent.hostname}</Link>
                </td>
                <td>
                  <AgentStatusBadge status={agent.status} />
                </td>
                <td>
                  {agent.os} {agent.os_version}
                </td>
                <td>{agent.group_name ?? ru.agents.noGroup}</td>
                <td>{relativeTime(agent.last_heartbeat_at)}</td>
                <td>{agent.agent_version}</td>
              </tr>
            ))}
          </tbody>
        </Table>
        <Pagination
          total={total}
          limit={PAGE_SIZE}
          offset={(filters.page - 1) * PAGE_SIZE}
          onChange={(offset) => update({ page: offset / PAGE_SIZE + 1 })}
        />
      </>
    );
  }

  return (
    <>
      <h1 className={page.title}>{ru.agents.title}</h1>

      <form className={page.toolbar} onSubmit={submit}>
        <Input
          label={ru.agents.search}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Select
          label={ru.agents.status}
          value={filters.status}
          options={statusOptions}
          onChange={(event) => update({ status: event.target.value })}
        />
        <Select
          label={ru.agents.group}
          value={filters.groupId}
          options={groupOptions}
          onChange={(event) => update({ groupId: event.target.value })}
        />
        <Button type="submit">{ru.agents.searchSubmit}</Button>
      </form>

      {body()}
    </>
  );
}
