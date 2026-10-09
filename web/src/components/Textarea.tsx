import { useId, type TextareaHTMLAttributes } from "react";

import styles from "./Field.module.css";

interface TextareaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> {
  label: string;
  error?: string;
  hint?: string;
}

export function Textarea({ label, error, hint, ...rest }: TextareaProps) {
  const id = useId();
  const noteId = `${id}-note`;
  const hasNote = Boolean(error || hint);

  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <textarea
        {...rest}
        id={id}
        className={styles.control}
        aria-invalid={error ? true : undefined}
        aria-describedby={hasNote ? noteId : undefined}
      />
      {error ? (
        <p id={noteId} className={styles.error} role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={noteId} className={styles.hint}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}
