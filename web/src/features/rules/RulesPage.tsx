import { useState } from "react";

import type { RuleSummary } from "../../api/types";
import { useSession } from "../../app/session";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { Table } from "../../components/Table";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import page from "../../styles/page.module.css";
import { CreateRuleDialog } from "./CreateRuleDialog";
import { GuideTab } from "./GuideTab";
import { RuleDialog } from "./RuleDialog";
import { useRules, useUpdateRule } from "./queries";
import styles from "./rules.module.css";

type Tab = "list" | "guide";

export function RulesPage() {
  const { user } = useSession();
  const [tab, setTab] = useState<Tab>("list");
  const [editing, setEditing] = useState<RuleSummary | null>(null);
  const [creating, setCreating] = useState(false);

  if (!user || user.role !== "admin") {
    return <p className={styles.note}>{ru.rules.adminOnly}</p>;
  }

  return (
    <>
      <div className={page.titleRow}>
        <h1 className={page.title}>{ru.rules.title}</h1>
        <Button variant="primary" onClick={() => setCreating(true)}>
          {ru.rules.create}
        </Button>
      </div>

      <div role="tablist" aria-label={ru.rules.tabs.label} className={styles.tabs}>
        {(["list", "guide"] as const).map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            id={`tab-${name}`}
            aria-selected={tab === name}
            aria-controls={`panel-${name}`}
            className={tab === name ? `${styles.tab} ${styles.tabActive}` : styles.tab}
            onClick={() => setTab(name)}
          >
            {ru.rules.tabs[name]}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "list" ? <RulesList onEdit={setEditing} /> : <GuideTab />}
      </div>

      <RuleDialog rule={editing} onClose={() => setEditing(null)} />
      <CreateRuleDialog open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function RulesList({ onEdit }: { onEdit: (rule: RuleSummary) => void }) {
  const rules = useRules();

  if (rules.isPending) return <Spinner label={ru.common.loading} />;
  if (rules.isError) {
    return <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />;
  }
  if (rules.data.length === 0) {
    return <EmptyState title={ru.rules.empty} hint={ru.rules.emptyHint} />;
  }

  return (
    <Table caption={ru.rules.caption}>
      <thead>
        <tr>
          <th>{ru.rules.columns.enabled}</th>
          <th>{ru.rules.columns.title}</th>
          <th>{ru.rules.columns.type}</th>
          <th>{ru.rules.columns.weight}</th>
          <th>{ru.rules.columns.cap}</th>
          <th>{ru.rules.columns.version}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {rules.data.map((rule) => (
          <RuleRow key={rule.id} rule={rule} onEdit={onEdit} />
        ))}
      </tbody>
    </Table>
  );
}

function RuleRow({ rule, onEdit }: { rule: RuleSummary; onEdit: (rule: RuleSummary) => void }) {
  const update = useUpdateRule(rule.id);
  const toast = useToast();
  // Показываем выбранное состояние сразу; при ошибке возвращаем серверное.
  const checked = update.isPending ? Boolean(update.variables?.enabled) : rule.enabled;

  async function toggle(enabled: boolean) {
    try {
      await update.mutateAsync({ enabled });
      toast.notify(enabled ? ru.rules.toggled.on : ru.rules.toggled.off, "ok");
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <tr>
      <td>
        <input
          type="checkbox"
          aria-label={ru.rules.toggle(rule.title)}
          checked={checked}
          disabled={update.isPending}
          onChange={(event) => void toggle(event.target.checked)}
        />
      </td>
      <td>
        {rule.title}
        {rule.builtin ? <span className={styles.builtin}>{ru.rules.builtin}</span> : null}
      </td>
      <td>{ru.rules.kinds[rule.kind] ?? rule.kind}</td>
      <td>{rule.weight}</td>
      <td>{rule.cap}</td>
      <td>{rule.version}</td>
      <td>
        <Button onClick={() => onEdit(rule)}>{ru.rules.edit}</Button>
      </td>
    </tr>
  );
}
