import type { ReactNode } from "react";

import { ApiError } from "../api/client";
import { ru } from "../i18n/ru";
import { describeError } from "../lib/errors";
import { Button } from "./Button";
import styles from "./States.module.css";

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className={styles.state}>
      <p className={styles.title}>{title}</p>
      {hint ? <p className={styles.hint}>{hint}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  // Повтор не вернёт права и не создаст объект, которого нет.
  const retryable = !(error instanceof ApiError && (error.status === 403 || error.status === 404));

  return (
    <div className={styles.state} role="alert">
      <p className={styles.title}>{describeError(error)}</p>
      {onRetry && retryable ? <Button onClick={onRetry}>{ru.common.retry}</Button> : null}
    </div>
  );
}
