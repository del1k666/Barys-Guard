import { useState } from "react";

import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Spinner } from "../../components/Spinner";
import { ErrorState } from "../../components/States";
import { Textarea } from "../../components/Textarea";
import { useToast } from "../../components/Toast";
import { ru } from "../../i18n/ru";
import { describeError } from "../../lib/errors";
import page from "../../styles/page.module.css";
import styles from "./rules.module.css";
import { TERMS_PAGE_SIZE, useAddTerms, useDeleteTerm, useTerms } from "./queries";

export function TermsEditor({ ruleId }: { ruleId: string }) {
  const t = ru.rules.termsEditor;
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(TERMS_PAGE_SIZE);
  const [draft, setDraft] = useState("");
  const terms = useTerms(ruleId, q, limit);
  const add = useAddTerms(ruleId);
  const remove = useDeleteTerm(ruleId);
  const toast = useToast();

  async function submit() {
    const lines = draft.split("\n");
    try {
      const result = await add.mutateAsync(lines);
      toast.notify(t.added(result.added), "ok");
      setDraft("");
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  async function drop(termId: string) {
    try {
      await remove.mutateAsync(termId);
      toast.notify(t.removed, "ok");
    } catch (failure) {
      toast.notify(describeError(failure), "danger");
    }
  }

  return (
    <div>
      <h3 className={page.sectionTitle}>{t.title}</h3>
      <Input
        label={t.search}
        value={q}
        onChange={(event) => {
          setQ(event.target.value);
          setLimit(TERMS_PAGE_SIZE);
        }}
      />

      {terms.isPending ? <Spinner label={ru.common.loading} /> : null}
      {terms.isError ? (
        <ErrorState error={terms.error} onRetry={() => void terms.refetch()} />
      ) : null}
      {terms.data ? (
        terms.data.items.length === 0 ? (
          <p className={styles.muted}>{t.empty}</p>
        ) : (
          <>
            <p className={styles.muted}>{t.total(terms.data.total)}</p>
            <ul>
              {terms.data.items.map((item) => (
                <li key={item.id}>
                  {item.term}{" "}
                  <Button
                    aria-label={t.remove(item.term)}
                    disabled={remove.isPending}
                    onClick={() => void drop(item.id)}
                  >
                    ×
                  </Button>
                </li>
              ))}
            </ul>
            {terms.data.total > terms.data.items.length ? (
              <Button onClick={() => setLimit(limit + TERMS_PAGE_SIZE)}>{t.loadMore}</Button>
            ) : null}
          </>
        )
      ) : null}

      <Textarea
        label={t.add}
        rows={4}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <div className={styles.actions}>
        <Button
          variant="primary"
          loading={add.isPending}
          disabled={draft.trim() === ""}
          onClick={() => void submit()}
        >
          {t.addButton}
        </Button>
      </div>
    </div>
  );
}
