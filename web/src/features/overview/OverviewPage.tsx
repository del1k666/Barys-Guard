import { Link } from "react-router-dom";

import { Spinner } from "../../components/Spinner";
import { ErrorState } from "../../components/States";
import { ru } from "../../i18n/ru";
import { formatNumber } from "../../lib/format";
import page from "../../styles/page.module.css";
import { useOverview } from "./useOverview";

function Stat({ label, value, to }: { label: string; value: number; to?: string }) {
  const body = (
    <>
      <div className={page.cardLabel}>{label}</div>
      <div className={page.cardValue}>{formatNumber(value)}</div>
    </>
  );

  return to ? (
    <Link to={to} className={page.card}>
      {body}
    </Link>
  ) : (
    <div className={page.card}>{body}</div>
  );
}

function Distribution({
  title,
  rows,
}: {
  title: string;
  rows: { value: string; count: number }[];
}) {
  return (
    <section className={page.section}>
      <h2 className={page.sectionTitle}>{title}</h2>
      {rows.length === 0 ? (
        <p className={page.muted}>{ru.overview.noData}</p>
      ) : (
        <ul className={page.list}>
          {rows.map((row) => (
            <li key={row.value} className={page.row}>
              <span>{row.value}</span>
              <span>{formatNumber(row.count)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const STATUSES = ["active", "offline", "pending", "quarantined", "revoked"] as const;

export function OverviewPage() {
  const overview = useOverview();

  if (overview.isPending) return <Spinner label={ru.common.loading} />;
  if (overview.isError) {
    return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />;
  }

  const data = overview.data;

  return (
    <>
      <h1 className={page.title}>{ru.overview.title}</h1>

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.overview.agents}</h2>
        <div className={page.grid}>
          <Stat label={ru.overview.total} value={data.agents.total} to="/agents" />
          {STATUSES.map((status) => (
            <Stat
              key={status}
              label={ru.status.agent[status] ?? status}
              value={data.agents[status]}
              to={`/agents?status=${status}`}
            />
          ))}
        </div>
      </section>

      <section className={page.section}>
        <h2 className={page.sectionTitle}>{ru.overview.commands}</h2>
        <div className={page.grid}>
          <Stat label={ru.overview.queued} value={data.commands.queued} />
          <Stat label={ru.overview.failed24h} value={data.commands.failed_24h} />
          <Stat label={ru.overview.certificates} value={data.certificates_expiring} />
          <Stat label={ru.overview.tokens} value={data.tokens_active} />
        </div>
      </section>

      <div className={page.grid}>
        <Distribution title={ru.overview.versions} rows={data.agent_versions} />
        <Distribution title={ru.overview.systems} rows={data.operating_systems} />
      </div>
    </>
  );
}
