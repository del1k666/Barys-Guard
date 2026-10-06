import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { EventSummary } from "../../api/types";
import { Button } from "../../components/Button";
import { Select } from "../../components/Select";
import { Spinner } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { SeverityBadge } from "../../components/StatusBadge";
import { Table } from "../../components/Table";
import { ru } from "../../i18n/ru";
import {
  PERIODS,
  parseEventFilters,
  toEventSearchParams,
  type EventFilters,
} from "../../lib/eventFilters";
import { describeEvent } from "../../lib/eventSummary";
import { formatDateTime } from "../../lib/format";
import page from "../../styles/page.module.css";
import { EventDetailPanel } from "./EventDetailPanel";
import styles from "./events.module.css";
import { useEvents } from "./queries";

const CHANNELS = Object.keys(ru.events.channels);
const SEVERITIES = Object.keys(ru.events.severities);

export function EventsPage() {
  const [params, setParams] = useSearchParams();
  const filters = parseEventFilters(params);
  const events = useEvents(filters);
  const [selected, setSelected] = useState<EventSummary | null>(null);

  function update(patch: Partial<EventFilters>) {
    setParams(toEventSearchParams({ ...filters, ...patch }));
  }

  const filtered = Boolean(filters.channel || filters.severity || filters.agentId || filters.period);

  const channelOptions = [
    { value: "", label: ru.events.anyChannel },
    ...CHANNELS.map((value) => ({ value, label: ru.events.channels[value] ?? value })),
  ];
  const severityOptions = [
    { value: "", label: ru.events.anySeverity },
    ...SEVERITIES.map((value) => ({ value, label: ru.events.severities[value] ?? value })),
  ];
  const periodOptions = [
    { value: "", label: ru.events.anyPeriod },
    ...PERIODS.map((value) => ({ value, label: ru.events.periods[value] ?? value })),
  ];

  function body() {
    if (events.isPending) return <Spinner label={ru.common.loading} />;
    if (events.isError) {
      return <ErrorState error={events.error} onRetry={() => void events.refetch()} />;
    }

    const items = events.data.pages.flatMap((entry) => entry.items);

    if (items.length === 0) {
      if (filtered) {
        return (
          <EmptyState
            title={ru.events.emptyFilter}
            hint={ru.events.emptyFilterHint}
            action={
              <Button onClick={() => setParams(new URLSearchParams())}>{ru.events.reset}</Button>
            }
          />
        );
      }
      return <EmptyState title={ru.events.empty} hint={ru.events.emptyHint} />;
    }

    return (
      <>
        <Table caption={ru.events.caption}>
          <thead>
            <tr>
              <th>{ru.events.columns.time}</th>
              <th>{ru.events.columns.agent}</th>
              <th>{ru.events.columns.channel}</th>
              <th>{ru.events.columns.action}</th>
              <th>{ru.events.columns.severity}</th>
              <th>{ru.events.columns.subject}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((event) => (
              <tr key={event.event_id}>
                <td>{formatDateTime(event.occurred_at)}</td>
                <td>
                  <Link to={`/agents/${event.agent_id}`}>{event.hostname}</Link>
                </td>
                <td>{ru.events.channels[event.channel] ?? event.channel}</td>
                <td>{event.action}</td>
                <td>
                  <SeverityBadge severity={event.severity} />
                </td>
                <td className={styles.subject}>{describeEvent(event)}</td>
                <td>
                  <Button onClick={() => setSelected(event)}>{ru.events.details}</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
        {events.hasNextPage ? (
          <div className={styles.more}>
            <Button loading={events.isFetchingNextPage} onClick={() => void events.fetchNextPage()}>
              {ru.events.loadMore}
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <h1 className={page.title}>{ru.events.title}</h1>

      <div className={page.toolbar}>
        <Select
          label={ru.events.channel}
          value={filters.channel}
          options={channelOptions}
          onChange={(event) => update({ channel: event.target.value })}
        />
        <Select
          label={ru.events.severity}
          value={filters.severity}
          options={severityOptions}
          onChange={(event) => update({ severity: event.target.value })}
        />
        <Select
          label={ru.events.period}
          value={filters.period}
          options={periodOptions}
          onChange={(event) => update({ period: event.target.value as EventFilters["period"] })}
        />
        <label className={styles.live}>
          <input
            type="checkbox"
            checked={filters.live}
            onChange={(event) => update({ live: event.target.checked })}
          />
          {ru.events.live}
        </label>
      </div>

      {filters.agentId ? <p className={styles.note}>{ru.events.agentFilter}</p> : null}

      {body()}

      <EventDetailPanel event={selected} onClose={() => setSelected(null)} />
    </>
  );
}
