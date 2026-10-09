import { Spinner } from "../../components/Spinner";
import { ErrorState } from "../../components/States";
import { Table } from "../../components/Table";
import { ru } from "../../i18n/ru";
import { formatDateTime } from "../../lib/format";
import page from "../../styles/page.module.css";
import styles from "./rules.module.css";
import { useVersions } from "./queries";

export function VersionsList({ ruleId }: { ruleId: string }) {
  const versions = useVersions(ruleId);
  const t = ru.rules.versions;

  return (
    <div>
      <h3 className={page.sectionTitle}>{t.title}</h3>
      {versions.isPending ? <Spinner label={ru.common.loading} /> : null}
      {versions.isError ? (
        <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />
      ) : null}
      {versions.data ? (
        versions.data.length === 0 ? (
          <p className={styles.muted}>{t.empty}</p>
        ) : (
          <Table caption={t.title}>
            <thead>
              <tr>
                <th>{t.version}</th>
                <th>{t.created}</th>
                <th>{t.params}</th>
              </tr>
            </thead>
            <tbody>
              {versions.data.map((item) => (
                <tr key={item.version}>
                  <td>{item.version}</td>
                  <td>{formatDateTime(item.created_at)}</td>
                  <td className={styles.mono}>{JSON.stringify(item.params)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )
      ) : null}
    </div>
  );
}
