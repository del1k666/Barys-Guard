import { Link } from "react-router-dom";

import type { EventSummary } from "../../api/types";
import { Modal } from "../../components/Modal";
import { SeverityBadge } from "../../components/StatusBadge";
import { ru } from "../../i18n/ru";
import { describeEvent } from "../../lib/eventSummary";
import { formatDateTime } from "../../lib/format";
import page from "../../styles/page.module.css";
import { Fact } from "../agents/Fact";
import styles from "./events.module.css";

const str = (value: unknown): string => (typeof value === "string" ? value : "");

export function EventDetailPanel({
  event,
  onClose,
}: {
  event: EventSummary | null;
  onClose: () => void;
}) {
  return (
    <Modal open={event !== null} title={ru.events.detail.title} onClose={onClose}>
      {event ? <Body event={event} /> : null}
    </Modal>
  );
}

function Body({ event }: { event: EventSummary }) {
  const user = str(event.actor.user_name);
  const sid = str(event.actor.user_sid);
  const processName = str(event.process.name) || str(event.labels.process);

  return (
    <>
      <dl className={page.dl}>
        <Fact label={ru.events.columns.severity}>
          <SeverityBadge severity={event.severity} />
        </Fact>
        <Fact label={ru.events.columns.channel}>
          {ru.events.channels[event.channel] ?? event.channel} · {event.action}
        </Fact>
        <Fact label={ru.events.detail.agent}>
          <Link to={`/agents/${event.agent_id}`}>{event.hostname}</Link>
        </Fact>
        <Fact label={ru.events.detail.occurred}>{formatDateTime(event.occurred_at)}</Fact>
        <Fact label={ru.events.detail.received}>{formatDateTime(event.received_at)}</Fact>
        <Fact label={ru.events.detail.user}>{user || ru.common.none}</Fact>
        <Fact label={ru.events.detail.sid}>{sid || ru.common.none}</Fact>
        <Fact label={ru.events.detail.process}>{processName || ru.common.none}</Fact>
        <Fact label={ru.events.columns.subject}>
          <span className={styles.subject}>{describeEvent(event)}</span>
        </Fact>
        <Fact label={ru.events.detail.sha256}>
          {event.artifact_sha256 ? (
            <span className={styles.sha}>{event.artifact_sha256}</span>
          ) : (
            ru.common.none
          )}
        </Fact>
        {event.artifact_sha256 ? (
          <Fact label={ru.events.detail.content}>
            {event.artifact_uploaded ? ru.events.detail.stored : ru.events.detail.notStored}
          </Fact>
        ) : null}
      </dl>

      <h3 className={page.sectionTitle}>{ru.events.detail.raw}</h3>
      <pre className={styles.raw}>
        {JSON.stringify(
          {
            actor: event.actor,
            process: event.process,
            subject: event.subject,
            labels: event.labels,
          },
          null,
          2,
        )}
      </pre>
    </>
  );
}
