import { Link } from "react-router-dom";

import type { IncidentDetail } from "../../api/types";
import { Button } from "../../components/Button";
import { Modal } from "../../components/Modal";
import { Spinner } from "../../components/Spinner";
import { ErrorState } from "../../components/States";
import { IncidentStatusBadge, SeverityBadge, VerdictBadge } from "../../components/StatusBadge";
import { Table } from "../../components/Table";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import { formatDateTime } from "../../lib/format";
import page from "../../styles/page.module.css";
import { Fact } from "../agents/Fact";
import styles from "./incidents.module.css";
import { useIncident, useUpdateIncident } from "./queries";

type Target = "acknowledged" | "closed";

export function IncidentDetailPanel({
  incidentId,
  onClose,
}: {
  incidentId: string | null;
  onClose: () => void;
}) {
  return (
    <Modal open={incidentId !== null} title={ru.incidents.detail.title} onClose={onClose}>
      {incidentId ? <Body incidentId={incidentId} /> : null}
    </Modal>
  );
}

function Body({ incidentId }: { incidentId: string }) {
  const incident = useIncident(incidentId);

  // Сбой фонового перечитывания не должен стирать уже показанный инцидент.
  if (incident.data) return <Detail incident={incident.data} />;
  if (incident.isError) {
    return <ErrorState error={incident.error} onRetry={() => void incident.refetch()} />;
  }

  return <Spinner label={ru.common.loading} />;
}

function Detail({ incident }: { incident: IncidentDetail }) {
  const update = useUpdateIncident(incident.id);
  const toast = useToast();
  const t = ru.incidents.detail;

  async function change(status: Target) {
    try {
      await update.mutateAsync(status);
      toast.notify(ru.incidents.toasts[status], "ok");
    } catch (failure) {
      // Включая 409: инцидент уже закрыт кем-то другим.
      toast.notify(describeError(failure), "danger");
    }
  }

  const pending = (target: Target) => update.isPending && update.variables === target;

  return (
    <>
      <dl className={page.dl}>
        <Fact label={t.severity}>
          <SeverityBadge severity={incident.severity} />
        </Fact>
        <Fact label={t.score}>{incident.score}</Fact>
        <Fact label={t.status}>
          <IncidentStatusBadge status={incident.status} />
        </Fact>
        <Fact label={t.agent}>
          <Link to={`/agents/${incident.agent_id}`}>{incident.hostname}</Link>
        </Fact>
        <Fact label={t.eventsCount}>{incident.events_count}</Fact>
        <Fact label={t.firstEvent}>{formatDateTime(incident.first_event_at)}</Fact>
        <Fact label={t.lastEvent}>{formatDateTime(incident.last_event_at)}</Fact>
        <Fact label={t.sha256}>
          <span className={styles.mono}>{incident.artifact_sha256}</span>
        </Fact>
        <Fact label={t.verdict}>
          {incident.verdict ? (
            <>
              <VerdictBadge status={incident.verdict.status} />{" "}
              {t.verdictScore(incident.verdict.score)}
            </>
          ) : (
            ru.common.none
          )}
        </Fact>
      </dl>

      <h3 className={page.sectionTitle}>{t.matches}</h3>
      {incident.matches.length === 0 ? (
        <p className={styles.muted}>{t.matchesEmpty}</p>
      ) : (
        <Table caption={t.matchesCaption}>
          <thead>
            <tr>
              <th>{t.matchColumns.rule}</th>
              <th>{t.matchColumns.count}</th>
              <th>{t.matchColumns.points}</th>
              <th>{t.matchColumns.samples}</th>
            </tr>
          </thead>
          <tbody>
            {incident.matches.map((match) => (
              <tr key={match.rule_key}>
                <td>{ru.incidents.rules[match.rule_key] ?? (match.rule_title || match.rule_key)}</td>
                <td>{match.count}</td>
                <td>{match.points}</td>
                <td className={styles.mono}>{match.samples.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <h3 className={page.sectionTitle}>{t.events}</h3>
      <Table caption={t.eventsCaption}>
        <thead>
          <tr>
            <th>{t.eventColumns.time}</th>
            <th>{t.eventColumns.action}</th>
            <th>{t.eventColumns.path}</th>
          </tr>
        </thead>
        <tbody>
          {incident.events.map((event) => (
            <tr key={event.event_id}>
              <td>{formatDateTime(event.occurred_at)}</td>
              <td>{event.action}</td>
              <td className={styles.mono}>{event.dst_path ?? ru.common.none}</td>
            </tr>
          ))}
        </tbody>
      </Table>

      {incident.status === "closed" ? null : (
        <div className={styles.actions}>
          {incident.status === "open" ? (
            <Button
              variant="primary"
              loading={pending("acknowledged")}
              disabled={update.isPending}
              onClick={() => void change("acknowledged")}
            >
              {ru.incidents.actions.acknowledge}
            </Button>
          ) : null}
          <Button
            loading={pending("closed")}
            disabled={update.isPending}
            onClick={() => void change("closed")}
          >
            {ru.incidents.actions.close}
          </Button>
        </div>
      )}
    </>
  );
}
