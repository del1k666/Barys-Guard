import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Button } from "../../components/Button";
import { Select } from "../../components/Select";
import { Spinner } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { IncidentStatusBadge, SeverityBadge } from "../../components/StatusBadge";
import { Table } from "../../components/Table";
import { ru } from "../../i18n/ru";
import { formatDateTime } from "../../lib/format";
import {
  INCIDENT_SEVERITIES,
  INCIDENT_STATUSES,
  parseIncidentFilters,
  toIncidentSearchParams,
  type IncidentFilters,
} from "../../lib/incidentFilters";
import page from "../../styles/page.module.css";
import { IncidentDetailPanel } from "./IncidentDetailPanel";
import styles from "./incidents.module.css";
import { useIncidents } from "./queries";

export function IncidentsPage() {
  const [params, setParams] = useSearchParams();
  const filters = parseIncidentFilters(params);
  const incidents = useIncidents(filters);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  function update(patch: Partial<IncidentFilters>) {
    setParams(toIncidentSearchParams({ ...filters, ...patch }));
  }

  const filtered = Boolean(filters.status || filters.severity || filters.agentId);

  const statusOptions = [
    { value: "", label: ru.incidents.anyStatus },
    ...INCIDENT_STATUSES.map((value) => ({ value, label: ru.status.incident[value] ?? value })),
  ];
  const severityOptions = [
    { value: "", label: ru.incidents.anySeverity },
    ...INCIDENT_SEVERITIES.map((value) => ({
      value,
      label: ru.events.severities[value] ?? value,
    })),
  ];

  function body() {
    if (incidents.isPending) return <Spinner label={ru.common.loading} />;
    if (incidents.isError) {
      return <ErrorState error={incidents.error} onRetry={() => void incidents.refetch()} />;
    }

    const items = incidents.data.pages.flatMap((entry) => entry.items);

    if (items.length === 0) {
      if (filtered) {
        return (
          <EmptyState
            title={ru.incidents.emptyFilter}
            hint={ru.incidents.emptyFilterHint}
            action={
              <Button onClick={() => setParams(new URLSearchParams())}>
                {ru.incidents.reset}
              </Button>
            }
          />
        );
      }
      return <EmptyState title={ru.incidents.empty} hint={ru.incidents.emptyHint} />;
    }

    return (
      <>
        <Table caption={ru.incidents.caption}>
          <thead>
            <tr>
              <th>{ru.incidents.columns.time}</th>
              <th>{ru.incidents.columns.agent}</th>
              <th>{ru.incidents.columns.severity}</th>
              <th>{ru.incidents.columns.score}</th>
              <th>{ru.incidents.columns.title}</th>
              <th>{ru.incidents.columns.status}</th>
              <th>{ru.incidents.columns.events}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((incident) => (
              <tr key={incident.id}>
                <td>{formatDateTime(incident.last_event_at)}</td>
                <td>
                  <Link to={`/agents/${incident.agent_id}`}>{incident.hostname}</Link>
                </td>
                <td>
                  <SeverityBadge severity={incident.severity} />
                </td>
                <td>{incident.score}</td>
                <td className={styles.title}>{incident.title}</td>
                <td>
                  <IncidentStatusBadge status={incident.status} />
                </td>
                <td>{incident.events_count}</td>
                <td>
                  <Button onClick={() => setSelectedId(incident.id)}>
                    {ru.incidents.details}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
        {incidents.hasNextPage ? (
          <div className={styles.more}>
            <Button
              loading={incidents.isFetchingNextPage}
              onClick={() => void incidents.fetchNextPage()}
            >
              {ru.incidents.loadMore}
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <h1 className={page.title}>{ru.incidents.title}</h1>

      <div className={page.toolbar}>
        <Select
          label={ru.incidents.status}
          value={filters.status}
          options={statusOptions}
          onChange={(event) => update({ status: event.target.value })}
        />
        <Select
          label={ru.incidents.severity}
          value={filters.severity}
          options={severityOptions}
          onChange={(event) => update({ severity: event.target.value })}
        />
        <label className={styles.live}>
          <input
            type="checkbox"
            checked={filters.live}
            onChange={(event) => update({ live: event.target.checked })}
          />
          {ru.incidents.live}
        </label>
      </div>

      {filters.agentId ? <p className={styles.note}>{ru.incidents.agentFilter}</p> : null}

      {body()}

      <IncidentDetailPanel incidentId={selectedId} onClose={() => setSelectedId(null)} />
    </>
  );
}
