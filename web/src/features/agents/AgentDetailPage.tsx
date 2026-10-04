import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useSession } from "../../app/session";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { ErrorState } from "../../components/States";
import { AgentStatusBadge } from "../../components/StatusBadge";
import { ru } from "../../i18n/ru";
import { formatDateTime, formatSkew, relativeTime } from "../../lib/format";
import page from "../../styles/page.module.css";
import { CommandHistory } from "./CommandHistory";
import { Fact } from "./Fact";
import { RevokeDialog } from "./RevokeDialog";
import { SendCommandDialog } from "./SendCommandDialog";
import { useAgent } from "./queries";

export function AgentDetailPage() {
  const { id = "" } = useParams();
  const { user } = useSession();
  const agent = useAgent(id);
  const [sending, setSending] = useState(false);
  const [revoking, setRevoking] = useState(false);

  if (agent.isPending) return <Spinner label={ru.common.loading} />;
  if (agent.isError) {
    return <ErrorState error={agent.error} onRetry={() => void agent.refetch()} />;
  }

  const data = agent.data;
  const active = data.status !== "revoked";
  // Роль скрывает кнопку, но не защищает: отзыв сервер проверяет сам.
  const canRevoke = user?.role === "admin" && active;
  const certificate = data.certificate;
  const tags = Object.entries(data.tags);

  return (
    <>
      <Link to="/agents" className={page.back}>
        {ru.agent.back}
      </Link>
      <div className={page.titleRow}>
        <h1 className={page.title}>{data.hostname}</h1>
        <AgentStatusBadge status={data.status} />
      </div>

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.agent.facts}</h2>
        <dl className={page.dl}>
          <Fact label={ru.agent.os}>
            {data.os} {data.os_version}
          </Fact>
          <Fact label={ru.agent.arch}>{data.arch}</Fact>
          <Fact label={ru.agent.version}>{data.agent_version}</Fact>
          <Fact label={ru.agent.group}>{data.group_name ?? ru.agents.noGroup}</Fact>
          <Fact label={ru.agent.ip}>{data.last_ip ?? ru.common.none}</Fact>
          <Fact label={ru.agents.columns.heartbeat}>{relativeTime(data.last_heartbeat_at)}</Fact>
          <Fact label={ru.agent.skew}>{formatSkew(data.clock_skew_ms)}</Fact>
          <Fact label={ru.agent.configVersion}>{data.config_version}</Fact>
          <Fact label={ru.agent.enrolledAt}>{formatDateTime(data.enrolled_at)}</Fact>
          <Fact label={ru.agent.machineId}>
            <span className={page.mono}>{data.machine_id}</span>
          </Fact>
          <Fact label={ru.agent.tags}>
            {tags.length === 0
              ? ru.common.none
              : tags.map(([key, value]) => `${key}: ${String(value)}`).join(", ")}
          </Fact>
        </dl>
      </section>

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.agent.certificate}</h2>
        {certificate === null ? (
          <p className={page.muted}>{ru.agent.noCertificate}</p>
        ) : (
          <dl className={page.dl}>
            <Fact label={ru.agent.serial}>
              <span className={page.mono}>{certificate.serial}</span>
            </Fact>
            <Fact label={ru.agent.fingerprint}>
              <span className={page.mono}>{certificate.fingerprint_sha256}</span>
            </Fact>
            <Fact label={ru.agent.validFrom}>{formatDateTime(certificate.not_before)}</Fact>
            <Fact label={ru.agent.validTo}>{formatDateTime(certificate.not_after)}</Fact>
            {certificate.revoked_at ? (
              <Fact label={ru.agent.revokedAt}>{formatDateTime(certificate.revoked_at)}</Fact>
            ) : null}
          </dl>
        )}
      </section>

      {active ? (
        <section className={page.section}>
          <h2 className={page.sectionTitle}>{ru.agent.actions}</h2>
          <div className={page.actions}>
            <Button variant="primary" onClick={() => setSending(true)}>
              {ru.agent.sendCommand}
            </Button>
            {canRevoke ? (
              <Button variant="danger" onClick={() => setRevoking(true)}>
                {ru.agent.revoke}
              </Button>
            ) : null}
          </div>
        </section>
      ) : null}

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.agent.history}</h2>
        <CommandHistory agentId={id} />
      </section>

      <SendCommandDialog
        agentId={id}
        hostname={data.hostname}
        open={sending}
        onClose={() => setSending(false)}
      />
      <RevokeDialog
        agentId={id}
        hostname={data.hostname}
        open={revoking}
        onClose={() => setRevoking(false)}
      />
    </>
  );
}
